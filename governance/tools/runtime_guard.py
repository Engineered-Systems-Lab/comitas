"""The runtime guard: the one small module every governance command starts with (G043).

It is written in syntax that any Python 3 can parse, so that an interpreter that is too old fails with one clear
message instead of a syntax error, and it uses the standard library only. A thin launcher calls `enter` first, before it
imports anything else of the repository. `enter` does four things, in this order:

1. It checks that the running interpreter is Python 3.13 or later and otherwise stops with one message. It never searches
   for, discovers or downloads another interpreter: the operator runs the tool with the interpreter they want.
2. It sets `sys.dont_write_bytecode`, so a governed command leaves no compiled files behind.
3. It removes every inherited `GIT_*` variable from the process environment except exactly `GIT_PAGER=cat`, and reports
   the names it removed on standard error, never their values. A host that injects `GIT_EDITOR` therefore no longer
   stops a command, and no variable that could redirect the repository, the index or the configuration reaches Git.
4. It enforces the lifecycle gate. Each tool, and for a tool with subcommands each subcommand, is registered below with
   one of three modes. READ writes nothing and always passes. LOCAL writes only machine-local scratch, or a file the
   caller names explicitly, and decides nothing from Git state; it always passes. GATED writes shared state, or decides on Git state and then writes, and is
   refused at once while a worktree lifecycle operation (create, reconcile, integrate or remove) holds the lifecycle
   lock (`.local/locks/worktree-workflow.lock` of the repository's primary checkout). It does not wait. A tool or subcommand that is not registered is GATED.

The module also fingerprints the running interpreter (resolved path, size, SHA-256 of the file, version). The checkpoint
tool records the fingerprint at the first step of a transaction and compares it at the start of every later step through
`assert_pin`, so a long prepare is never started on an interpreter that changed and the change is never found at the
seal. A tool run outside a transaction needs only the version check.

`python -B governance/tools/runtime_guard.py --check` prints the fingerprint and the names of the scrubbed variables.
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

MINIMUM = (3, 13)

READ = 'READ'
LOCAL = 'LOCAL'
GATED = 'GATED'
MODES = (READ, LOCAL, GATED)

# A deliberate copy of the lifecycle lock's location in support/local_state.py (the folder of machine-local state in the
# primary checkout, the locks folder in it, the lock's name and the name of the Git directory whose parent is the primary):
# this module runs under a possibly older Python 3 and before any repository module is trusted. test_runtime_guard asserts
# that the copy agrees with local_state.
LOCAL_FOLDER = '.local'
LOCKS_FOLDER = 'locks'
LIFECYCLE_LOCK = 'worktree-workflow.lock'
GIT_DIRECTORY = '.git'

# The repository this guard belongs to: governance/tools/runtime_guard.py is two folders below the root.
ROOT = Path(__file__).resolve().parents[2]

PIN_DRIFT = 'the interpreter changed since the preview: re-run the preview, nothing was lost yet'

# THE MODE REGISTRY. One entry per thin launcher or tool entry, keyed by the file name without `.py`. A tool with
# subcommands registers `valued` (its global options that take a value, so that the value is not mistaken for the
# subcommand) and `modes` (subcommand -> mode); a subcommand that is not listed is GATED.
#
# READ   writes nothing. It may show stale or torn data while a lifecycle operation runs, which is acceptable because
#        its output is advisory.
# LOCAL  writes only machine-local scratch (`.local/`, the scratch folder beside the main checkout, the machine-local
#        timing record), or a file the caller names explicitly, and decides nothing from Git state, so a moving ref
#        does not change what it does.
# GATED  writes shared state, or decides on Git state and then writes. A torn read could close a Job wrongly or seal
#        a candidate against a half-changed ref set, so it is refused while the lifecycle lock exists.
REGISTRY = {
    # Run once in a new repository before its first commit: it reads that no HEAD exists and then writes the index and one
    # project file, so it decides on Git state and writes.
    'adopt': GATED,
    # Pure text matcher over standard input or --reply.
    'approval_words': READ,
    # Every checkpoint command builds the checkpoint store (a directory and a lock) and most write packets and receipts.
    'checkpoint': GATED,
    # Reads a transaction's refs and manifest and then writes the frozen worker context: it decides from Git state.
    'commit_metadata': GATED,
    'local_jobs': {
        'valued': ('--repo',),
        'modes': {
            'paths': READ, 'list': READ, 'pointer': READ, 'read': READ, 'queue': READ, 'kinds': READ, 'brief': READ,
            'put': GATED, 'transfer': GATED, 'prune': GATED, 'rename': GATED, 'note': GATED, 'complete': GATED,
            'dispose': GATED, 'claim-coordinator': GATED,
        },
    },
    # The policy commands run it for every check; it writes only its per-run folder in the scratch folder beside the
    # main checkout, and a prepare must not be refused because another mission is integrating.
    'run_tests': LOCAL,
    'runtime_guard': READ,
    'show_classification': READ,
    'timing_record': {
        'valued': ('--repo',),
        'modes': {'stats': READ, 'export': LOCAL, 'import': LOCAL},
    },
    # Also run as a policy check; it only reads.
    'validate_continuity': READ,
    'worktree': {
        'valued': ('--main',),
        'modes': {
            'paths': READ, 'execution-status': READ,
            'clear': GATED, 'doctor': GATED, 'create': GATED, 'integrate': GATED, 'remove': GATED,
            'reconcile': GATED, 'carry': GATED, 'carry-go': GATED,
        },
    },
}


class Drift(ValueError):
    """The running interpreter is not the one the transaction began with."""


def fail(message):
    print('FAIL: ' + message, file=sys.stderr)
    sys.exit(1)


def version_text():
    return sys.version.split()[0]


def check_version():
    """Stop with one clear message when the running interpreter is older than Python 3.13."""
    if sys.version_info[:2] < MINIMUM:
        fail('governance tools require Python %d.%d or later; this is Python %s (%s). Run the tool with a Python %d.%d '
             'or later interpreter, naming it by its path. Nothing was searched for or downloaded.'
             % (MINIMUM[0], MINIMUM[1], '.'.join(str(part) for part in sys.version_info[:3]), sys.executable or 'unknown path',
                MINIMUM[0], MINIMUM[1]))


def keeps(name, value):
    """The one reviewed Git setting that passes through the guard: disabling the pager."""
    return (name.upper(), value) == ('GIT_PAGER', 'cat')


def scrub(environ=None):
    """Remove the inherited GIT_* variables from `environ` (default os.environ); return the removed names, sorted."""
    environment = os.environ if environ is None else environ
    removed = sorted(name for name, value in list(environment.items())
                     if name.upper().startswith('GIT_') and not keeps(name, value))
    for name in removed:
        del environment[name]
    return removed


def subcommand(argv, valued):
    """The first positional token that is not the value of a global option; None when there is none.

    An abbreviation of a valued long option (argparse accepts `--rep` for `--repo`) counts as that option, so its value
    is never mistaken for the subcommand. Anything unclear ends in an unregistered name, which is GATED."""
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token.startswith('-'):
            if token in valued:
                skip = True
            elif token.startswith('--') and len(token) > 2 and '=' not in token and any(v.startswith(token) for v in valued):
                skip = True
            continue
        return token
    return None


def mode_of(tool, argv, extension_mode=None):
    """(mode, subcommand) for a tool invocation. Unregistered means GATED (fail closed).

    An extension is not named in this core registry (the core never depends on an extension, G042), so an extension
    script states its own mode when it enters. The registry always wins for a registered tool."""
    entry = REGISTRY.get(tool)
    if entry is None:
        return (extension_mode if extension_mode in MODES else GATED), None
    if isinstance(entry, str):
        return entry, None
    sub = subcommand(argv, entry['valued'])
    return entry['modes'].get(sub, GATED), sub


def common_directory(root):
    """The repository's common Git directory, as Git reports it for `root`; ValueError when Git cannot say."""
    try:
        result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('cannot run Git: ' + str(error))
    if result.returncode != 0:
        raise ValueError('cannot locate the common Git directory: ' + result.stderr.decode('utf-8', 'replace').strip())
    common = Path(result.stdout.decode('utf-8', 'replace').strip())
    if not common.is_absolute():
        raise ValueError('Git must return an absolute common directory')
    return common


def lifecycle_lock_path(root=None):
    """The lifecycle lock of the repository: `.local/locks/` of the primary checkout, the parent of the common directory.

    ValueError when the repository has no primary checkout (the common directory is not named .git): it is never guessed."""
    common = common_directory(ROOT if root is None else root).resolve()
    if common.name != GIT_DIRECTORY:
        raise ValueError('this repository has no primary checkout (its common Git directory is not a .git folder): '
                         + str(common))
    return common.parent / LOCAL_FOLDER / LOCKS_FOLDER / LIFECYCLE_LOCK


def lifecycle_active(root=None):
    """True while a worktree lifecycle operation holds the lifecycle lock of the repository."""
    return lifecycle_lock_path(root).exists()


def gate(tool, sub, root=None):
    """Refuse a GATED tool at once, with no wait, while the lifecycle lock exists."""
    name = tool if sub is None else tool + ' ' + sub
    try:
        active = lifecycle_active(root)
    except ValueError as error:
        fail('%s is GATED (it writes shared state or decides on Git state) and the lifecycle lock cannot be checked: %s. '
             'Nothing was started.' % (name, error))
    if active:
        fail('refused at once: a worktree lifecycle operation (create, reconcile, integrate or remove) holds the '
             'lifecycle lock, and %s is GATED (it writes shared state or decides on Git state). Nothing was started and '
             'nothing was waited for. Run it again after the operation finishes; READ and LOCAL tools are not affected.'
             % name)


def enter(tool, argv=None, extension_mode=None, root=None):
    """The first call of every launcher: version check, no bytecode, GIT_* scrub, lifecycle gate.

    Returns {'mode', 'subcommand', 'scrubbed'}. It exits the process with one message when the interpreter is too old
    or a GATED tool is refused."""
    check_version()
    sys.dont_write_bytecode = True
    removed = scrub()
    if removed:
        print('scrubbed inherited GIT_* variables: ' + ', '.join(removed), file=sys.stderr)
    arguments = sys.argv[1:] if argv is None else list(argv)
    mode, sub = mode_of(tool, arguments, extension_mode)
    if mode == GATED:
        gate(tool, sub, root)
    return {'mode': mode, 'subcommand': sub, 'scrubbed': removed}


def fingerprint():
    """The running interpreter: resolved executable path, size and SHA-256 of the file, and version."""
    executable = sys.executable
    if not executable:
        raise ValueError('cannot fingerprint the interpreter: it reports no executable')
    path = os.path.realpath(executable)
    if not os.path.isfile(path):
        raise ValueError('cannot fingerprint the interpreter: ' + path + ' is not a file')
    digest = hashlib.sha256()
    size = 0
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(1 << 20)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return {'executable': path, 'size': size, 'sha256': digest.hexdigest(), 'version': version_text()}


FIELDS = ('executable', 'size', 'sha256', 'version')


def differences(pinned, current):
    """The names of the fingerprint fields that differ; a malformed pin differs in every field."""
    if not isinstance(pinned, dict) or set(pinned) != set(FIELDS):
        return list(FIELDS)
    return [name for name in FIELDS if pinned[name] != current[name]]


def assert_pin(pinned, current=None):
    """Refuse, at the start of a step, when the running interpreter is not the pinned one.

    `pinned` None means the transaction carries no pin (a record made before the pin existed) and nothing is compared.
    The hash is the strict signal: a patch-version upgrade changes the file, so it is caught like any other change."""
    if pinned is None:
        return
    now = fingerprint() if current is None else current
    changed = differences(pinned, now)
    if changed:
        raise Drift('%s (changed: %s)' % (PIN_DRIFT, ', '.join(changed)))


def main(argv=None, scrubbed=()):
    parser = argparse.ArgumentParser(description='Check that this interpreter can run the governance tools and print its '
                                                 'fingerprint and the names of the scrubbed GIT_* variables.')
    parser.add_argument('--check', action='store_true', required=True,
                        help='print the interpreter fingerprint and the scrubbed names')
    parser.parse_args(argv)
    print(json.dumps({'minimum': '%d.%d' % MINIMUM, 'interpreter': fingerprint(), 'scrubbed': list(scrubbed)}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main(scrubbed=enter('runtime_guard')['scrubbed']))
