"""The project's declared commit history: its boundaries, its exceptions and the range they bound.

The history test and Mission Control read the declaration through this module, so both apply the same exceptions
and refuse a faulty file, its shape or its boundaries and exception commits, with the same messages. Only the history
test reads each commit strictly as well, which tells whether an exception is stale or broader than it needs.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from governance.tools.continuity import artifact_classification as ac
from governance.tools.lifecycle import commit_metadata as m


class HistoryUnavailable(Exception):
    """Expected absence of complete history, never a configuration or command failure."""


def history_settings(root):
    """Validate a present declaration before inspecting history; only absence returns None."""
    path = Path(root) / ac.COMMIT_HISTORY
    for part in (path, *list(path.parents)[:2]):
        if part.is_symlink() or part.is_junction():
            raise ValueError(ac.COMMIT_HISTORY + ': indirect configuration path')
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(ac.COMMIT_HISTORY + ': unreadable configuration: ' + str(error)) from error
    if not stat.S_ISREG(mode):
        raise ValueError(ac.COMMIT_HISTORY + ': nonregular configuration file')
    try:
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError) as error:
        raise ValueError(ac.COMMIT_HISTORY + ': unreadable or invalid JSON: ' + str(error)) from error
    return parse_settings(text)


def parse_settings(text):
    """The declaration's text as (convention_after, trailer_allowlist_after, exceptions); any fault refuses it."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise ValueError(ac.COMMIT_HISTORY + ': unreadable or invalid JSON: ' + str(error)) from error
    required = {'version', 'convention_after'}
    allowed = required | {'trailer_allowlist_after', 'exceptions'}
    if (not isinstance(data, dict) or not required <= data.keys() <= allowed
            or type(data['version']) is not int or data['version'] != 1):
        raise ValueError(ac.COMMIT_HISTORY + ': expected version 1, convention_after and optional'
                         ' trailer_allowlist_after and exceptions')
    for key in ('convention_after', 'trailer_allowlist_after'):
        if key in data and (not isinstance(data[key], str) or not re.fullmatch(r'[0-9a-f]{40}', data[key])):
            raise ValueError(ac.COMMIT_HISTORY + ': ' + key + ' must be a full lowercase 40-hex commit ID')
    return (data['convention_after'], data.get('trailer_allowlist_after', data['convention_after']),
            history_exceptions(data.get('exceptions', [])))


def history_exceptions(value):
    """The declared exceptions as {commit: (waived rules, reason)}; any fault refuses the whole file."""
    if not isinstance(value, list):
        raise ValueError(ac.COMMIT_HISTORY + ': exceptions must be a list')
    exceptions = {}
    for index, entry in enumerate(value):
        where = ac.COMMIT_HISTORY + ': exception ' + str(index + 1)
        if not isinstance(entry, dict) or set(entry) != {'commit', 'rules', 'reason'}:
            raise ValueError(where + ': expected exactly commit, rules and reason')
        commit, rules, reason = entry['commit'], entry['rules'], entry['reason']
        if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
            raise ValueError(where + ': commit must be a full lowercase 40-hex commit ID')
        if commit in exceptions:
            raise ValueError(where + ': duplicate exception for ' + commit)
        if (not isinstance(rules, list) or not rules
                or not all(isinstance(rule, str) and rule in m.WAIVABLE_RULES for rule in rules)
                or len(rules) != len(set(rules))):
            raise ValueError(where + ': rules must be a nonempty list of distinct rules from '
                             + ', '.join(m.WAIVABLE_RULES))
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(where + ': reason must be nonempty text')
        exceptions[commit] = (frozenset(rules), reason)
    return exceptions


def git(root, *args, accepted=(0,)):
    """Preserve exit codes; callers name only expected Git predicate outcomes."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env['LC_ALL'] = 'C'
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env)
    except OSError as error:
        raise ValueError('Git executable unavailable: ' + str(error)) from error
    if result.returncode not in accepted:
        raise ValueError('git ' + ' '.join(args) + ' failed (' + str(result.returncode) + '): '
                         + result.stderr.decode('utf-8', 'replace').strip())
    return result


def history_range(root):
    """Capture HEAD once, validate both epochs, then return H, checked IDs and old-trailer IDs."""
    settings = history_settings(root)
    inside = git(root, 'rev-parse', '--is-inside-work-tree', accepted=(0, 128))
    if inside.returncode:
        if b'not a git repository' in inside.stderr:
            raise HistoryUnavailable('not a Git checkout')
        raise ValueError('Git repository discovery failed: ' + inside.stderr.decode('utf-8', 'replace').strip())
    if inside.stdout.strip() == b'false':
        raise HistoryUnavailable('not a Git worktree')
    if inside.stdout.strip() != b'true':
        raise ValueError('Unexpected Git worktree status')
    shallow = git(root, 'rev-parse', '--is-shallow-repository').stdout.strip()
    if shallow == b'true':
        raise HistoryUnavailable('shallow clone: complete ancestry unavailable; obtain full history')
    if shallow != b'false':
        raise ValueError('Unexpected Git shallow status')
    head_result = git(root, 'rev-parse', '--verify', '-q', 'HEAD', accepted=(0, 1))
    if head_result.returncode:
        branch = git(root, 'symbolic-ref', '-q', 'HEAD').stdout.decode('utf-8').strip()
        if git(root, 'show-ref', '--verify', '--quiet', branch, accepted=(0, 1)).returncode == 1:
            raise HistoryUnavailable('unborn repository: no first commit')
        raise ValueError('HEAD cannot be resolved')
    head = head_result.stdout.decode('ascii').strip()
    if settings is None:
        raise ValueError(ac.COMMIT_HISTORY + ': missing; initialize reviewed repository boundaries after the first commit')
    ids, old, exceptions = declared_range(root, settings, head)
    return head, ids, old, exceptions


def declared_range(root, settings, head, run=None):
    """Check the declaration's boundaries and exceptions against `head`; return the checked IDs, the old-trailer IDs
    and the exceptions. Mission Control calls it with the revision it shows, so both refuse with these messages, and
    passes as `run` its own isolated Git, which takes the same arguments and keeps the same exit codes and messages."""
    run = run or git
    boundary, trailer, exceptions = settings
    for label, commit in (('convention_after', boundary), ('trailer_allowlist_after', trailer)):
        kind = run(root, 'cat-file', '-t', commit).stdout.strip()
        if kind != b'commit':
            raise ValueError(label + ': boundary object is not a commit')
        if run(root, 'merge-base', '--is-ancestor', commit, head, accepted=(0, 1)).returncode:
            raise ValueError(label + ': boundary is not an ancestor of captured HEAD')
    if run(root, 'merge-base', '--is-ancestor', boundary, trailer, accepted=(0, 1)).returncode:
        raise ValueError('convention_after must be an ancestor of or equal to trailer_allowlist_after')
    ids = run(root, 'rev-list', '--reverse', boundary + '..' + head).stdout.decode('ascii').split()
    old = set(run(root, 'rev-list', boundary + '..' + trailer).stdout.decode('ascii').split())
    checked = set(ids)
    for commit in exceptions:
        where = 'exception for ' + commit
        if run(root, 'cat-file', '-t', commit, accepted=(0, 128)).stdout.strip() != b'commit':
            raise ValueError(where + ': object is not a commit')
        if run(root, 'merge-base', '--is-ancestor', commit, head, accepted=(0, 1)).returncode:
            raise ValueError(where + ': commit is not an ancestor of captured HEAD')
        if commit not in checked:
            raise ValueError(where + ': convention_after already exempts it')
    return ids, old, exceptions
