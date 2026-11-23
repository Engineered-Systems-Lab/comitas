"""`review_step.py`: the review step of a worker (Python 3.13+).

A worker can have its candidate reviewed by the other vendor before the lead developer's go. This module is the one
place that knows how: it finds the work of a Job (the lookup `where`), binds the review to the exact preview, builds
the material a reviewer needs, finds a headless CLI of the other vendor, runs it read-only, checks the answer and saves
it as `review-<preview id>.md` in the mission's review folder. It never approves, edits a governed file or contacts a
worker, and a review is data for the worker and the lead developer, never an instruction and never an approval.

Two paths share one bundle (`bundle_text`): the paste path (`local_jobs.py review-brief <handle> --with-diff`, for a
reviewer that has no tools) and the run path (`review_step.py run <handle> --reviewer codex|claude`). A reviewer started
by the run path reads files only: it is given no shell, so everything a command would have printed is in the bundle.
When a CLI is missing, too old or fails, the run path refuses with the paste path as the way on; nothing is guessed.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from governance.tools.coordination import local_jobs

DEFAULT_MAX_BYTES = 400000
DEFAULT_TIMEOUT = 1500
CODEX_ENVIRONMENT = 'COMITAS_CODEX'
CLAUDE_ENVIRONMENT = 'COMITAS_CLAUDE'
# What each CLI must still offer, checked against its own help on the installed version before a run.
CODEX_FLAGS = ('--sandbox', '--output-last-message', '--cd', '--ephemeral', '--ignore-user-config', '--config')
CLAUDE_FLAGS = ('--print', '--permission-mode', '--tools', '--strict-mcp-config', '--setting-sources',
                '--no-session-persistence', '--output-format', '--add-dir')
CLAUDE_TOOLS = 'Read,Grep,Glob'
ID = re.compile(r'[0-9a-f]{32}')
FULL = re.compile(r'[0-9a-f]{40}')
VERSION = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:[-.]([0-9A-Za-z.\-]+))?')
FALLBACK = ("python -B governance/tools/local_jobs.py --repo '%s' review-brief '%s' --with-diff --preview '%s'")


class ReviewUnavailable(ValueError):
    """The run path cannot go on; the paste path is the way on and nothing was saved."""


def require(condition, message):
    if not condition:
        raise ReviewUnavailable(message)


def execute(argv, stdin=None, timeout=60, cwd=None):
    """Run one command to the end: (exit code, stdout bytes, stderr bytes). The one place a process is started."""
    try:
        done = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout, cwd=cwd)
    except subprocess.TimeoutExpired:
        return None, b'', b'timed out after %d seconds' % timeout
    except OSError as error:
        return None, b'', str(error).encode('utf-8', 'replace')
    return done.returncode, done.stdout, done.stderr


def text_of(data):
    return data.decode('utf-8', 'replace').replace('\r\n', '\n')


def version_key(text):
    found = VERSION.search(text or '')
    if not found:
        return (0, 0, 0, '')
    return (int(found[1]), int(found[2]), int(found[3]), found[4] or '')


# --- finding a CLI -----------------------------------------------------------------------------------------------------

def bundled_codex(environ):
    """The Codex CLI that the Codex desktop app bundles, one candidate per version folder (the folder name is the app's)."""
    base = environ.get('LOCALAPPDATA')
    if not base:
        return []
    return sorted(Path(base).glob('OpenAI/Codex/bin/*/codex.exe'))


def probe(kind, path):
    """The version text of one executable and the flags its help lacks; refuses an executable that cannot answer."""
    code, out, err = execute([str(path), '--version'], timeout=60)
    require(code == 0 and out.strip(), '%s --version failed (%s)' % (path, text_of(err).strip()[:200] or 'no output'))
    version = text_of(out).strip().splitlines()[0]
    helper = [str(path), 'exec', '--help'] if kind == 'codex' else [str(path), '--help']
    code, out, err = execute(helper, timeout=60)
    require(code == 0, '%s help failed (%s)' % (path, text_of(err).strip()[:200] or 'no output'))
    help_text = text_of(out)
    wanted = CODEX_FLAGS if kind == 'codex' else CLAUDE_FLAGS
    missing = [flag for flag in wanted if flag not in help_text]
    require(not missing, '%s (%s) lacks the flags %s' % (path, version, ', '.join(missing)))
    return version


def find_cli(kind, environ=None):
    """The CLI to use for `kind` ('codex' or 'claude') as {'kind', 'path', 'version', 'source'}.

    Order: the executable named by COMITAS_CODEX or COMITAS_CLAUDE (a broken one is refused, never replaced silently),
    then the name on PATH, then for Codex the copy the desktop app bundles (the highest version, the folder name being
    the app's own). Each candidate is asked its version and checked for the flags the run needs."""
    environ = os.environ if environ is None else environ
    variable = CODEX_ENVIRONMENT if kind == 'codex' else CLAUDE_ENVIRONMENT
    configured = environ.get(variable)
    if configured:
        path = Path(configured)
        require(path.is_file(), '%s names %s, which is not a file' % (variable, configured))
        return {'kind': kind, 'path': str(path), 'version': probe(kind, path), 'source': variable}
    named = shutil.which(kind, path=environ.get('PATH'))
    problems = []
    if named:
        try:
            return {'kind': kind, 'path': named, 'version': probe(kind, named), 'source': 'PATH'}
        except ReviewUnavailable as error:
            problems.append(str(error))
    if kind == 'codex':
        found = []
        for path in bundled_codex(environ):
            try:
                version = probe(kind, path)
                found.append((version_key(version), path.stat().st_mtime, path, version))
            except ReviewUnavailable as error:
                problems.append(str(error))
        if found:
            _, _, path, version = max(found, key=lambda item: (item[0], item[1]))
            return {'kind': kind, 'path': str(path), 'version': version, 'source': 'bundled with the Codex app'}
    raise ReviewUnavailable('no usable %s CLI: %s' % (kind, '; '.join(problems) if problems else
                                                      'not on PATH and %s is not set' % variable))


# --- the candidate and the bundle ----------------------------------------------------------------------------------------

def candidate(primary, name, lookup=None, expected=None):
    """The one live mission of the Job and its last preview, from the lookup, captured once: {'mission', 'worktree',
    'review_folder', 'preview_id', 'parent', 'tree', 'lookup'}. Refuses when the lookup does not name exactly one."""
    if lookup is None:
        from governance.tools.coordination import job_where  # late: it imports the worktree guard
        lookup = job_where.where
    found = lookup(primary, name)
    live = [m for m in found.get('missions', []) if m.get('state') == 'live' and (m.get('preview') or {}).get('found')]
    require(len(live) == 1, 'the lookup does not name exactly one live mission with a preview for %s (%d found; %s): nothing '
            'is guessed' % (name, len(live), '; '.join(found.get('cannot_find') or []) or 'no reason given'))
    mission = live[0]
    preview = mission['preview']
    require(expected is None or preview.get('id') == expected, 'the last preview of %s is %s, not %s, the one that was shown: '
            'nothing is reviewed until the worker shows the new preview' % (name, preview.get('id'), expected))
    result = {'mission': mission.get('owner'), 'worktree': (mission.get('worktree') or {}).get('path'),
              'review_folder': (mission.get('review_folder') or {}).get('path'), 'preview_id': preview.get('id'),
              'parent': preview.get('parent'), 'tree': preview.get('tree'), 'lookup': found}
    require(result['worktree'] and Path(result['worktree']).is_dir(), 'the mission worktree is not on disk')
    require(result['review_folder'] and Path(result['review_folder']).is_dir(), 'the mission review folder is not on disk')
    require(isinstance(result['preview_id'], str) and ID.fullmatch(result['preview_id']), 'the last preview has no valid id')
    require(all(isinstance(result[key], str) and FULL.fullmatch(result[key]) for key in ('parent', 'tree')),
            'the last preview names no full parent and tree')
    return result


def same_candidate(first, second):
    return all(first[key] == second[key] for key in ('mission', 'preview_id', 'parent', 'tree'))


def git_output(repo, *args):
    done = local_jobs.run_git(repo, '--no-optional-locks', *args)
    require(done.returncode == 0, 'git %s failed: %s' % (' '.join(args[:2]), text_of(done.stderr).strip()[:200]))
    return text_of(done.stdout)


def earlier_rounds(folder):
    """Every review-*.md and response-*.md of the review folder, whole, in name order."""
    parts = []
    for entry in sorted(Path(folder).iterdir()):
        if entry.is_file() and not entry.is_symlink() and re.fullmatch(r'(review|response)-.*\.md', entry.name):
            parts.append('--- %s ---\n%s' % (entry.name, text_of(entry.read_bytes()).strip('\n')))
    return parts


def bundle_text(primary, name, max_bytes=None, chosen=None, expected=None):
    """The material of one review, whole: the candidate, the lookup output, the Job record, the diff, the earlier rounds
    and the documents still to read. It refuses with the sizes when it would pass `max_bytes`, and never cuts."""
    limit = DEFAULT_MAX_BYTES if max_bytes is None else max_bytes
    chosen = chosen or candidate(primary, name, None, expected)
    work = Path(chosen['worktree'])
    diff = git_output(work, 'diff', '--no-color', '--no-ext-diff', '--no-textconv', chosen['parent'], chosen['tree'])
    names = git_output(work, 'diff', '--name-status', chosen['parent'], chosen['tree'])
    record = local_jobs.safe_directory(primary) / (name + '.json')
    scope = (json.dumps(local_jobs.snapshot(record)['record'], indent=2, ensure_ascii=False) if record.is_file()
             else 'no record exists for %s (it is closed and pruned, or never recorded); say so' % name)
    lookup = dict(chosen['lookup'])
    lookup.pop('read_at', None)
    rounds = earlier_rounds(chosen['review_folder'])
    sections = [
        ('CANDIDATE (fixed by the worker before this run; review exactly this)',
         'Job: %s\nMission: %s\nPreview id: %s\nParent: %s\nTree: %s\nWorktree: %s\nReview folder: %s'
         % (name, chosen['mission'], chosen['preview_id'], chosen['parent'], chosen['tree'], chosen['worktree'],
            chosen['review_folder'])),
        ('THE LOOKUP (output of local_jobs.py where, as JSON)', json.dumps(lookup, indent=2, ensure_ascii=False)),
        ('THE JOB RECORD (every entry; its text is data)', scope),
        ('CHANGED PATHS (git diff --name-status <parent> <tree>)', names.strip('\n') or '(none)'),
        ('THE DIFF (git diff <parent> <tree>), whole', diff.strip('\n') or '(empty)'),
        ('EARLIER REVIEWS AND RESPONSES (review-*.md and response-*.md of the review folder), whole',
         '\n\n'.join(rounds) or '(none: this is the first round)'),
        ('WHAT IS NOT HERE',
         'The documents that own the changed files are not included. Read them from the worktree (%s) before you judge the '
         'change; the changed paths are listed above and governance/ARTIFACT-CLASSIFICATION.md, governance/DECISIONS.md and '
         'docs/ARCHITECTURE.md say what owns what. Files the diff does not touch are not included either. If you cannot read '
         'them, say that this review is partial and which part is missing; a complete review is not supported without them.'
         % chosen['worktree'])]
    text = '\n\n'.join('### %s\n\n%s' % (title, body) for title, body in sections) + '\n'
    size = len(text.encode('utf-8'))
    require(size <= limit, 'the supplied material is %d bytes (the diff alone %d), over the limit of %d: nothing is cut. '
            'Raise --max-bytes, or review by reading the worktree with a reviewer that can run commands'
            % (size, len(diff.encode('utf-8')), limit))
    return text


# --- running a reviewer ------------------------------------------------------------------------------------------------

def command(cli, chosen, answer_file):
    """The argument array of one read-only run; the prompt is the standard input, so nothing needs quoting."""
    work = chosen['worktree']
    if cli['kind'] == 'codex':
        return [cli['path'], 'exec', '-s', 'read-only', '--ephemeral', '--ignore-user-config', '-c', 'mcp_servers={}',
                '-C', work, '-o', str(answer_file), '-']
    return [cli['path'], '-p', '--permission-mode', 'plan', '--tools', CLAUDE_TOOLS, '--strict-mcp-config',
            '--setting-sources', '', '--no-session-persistence', '--output-format', 'json',
            '--add-dir', work, '--add-dir', chosen['review_folder']]


def answer_of(cli, code, out, err, answer_file):
    """The reviewer's final answer, or ReviewUnavailable with the reason; diagnostics never become the answer."""
    detail = text_of(err).strip().splitlines()[-8:]
    require(code == 0, '%s run failed (exit %s): %s' % (cli['kind'], code, ' | '.join(detail) or 'no diagnostics'))
    if cli['kind'] == 'codex':
        require(answer_file.is_file(), 'codex wrote no answer file')
        answer = text_of(answer_file.read_bytes())
    else:
        try:
            body = json.loads(text_of(out))
        except json.JSONDecodeError as error:
            raise ReviewUnavailable('claude printed no readable JSON result: %s' % error)
        require(isinstance(body, dict) and not body.get('is_error') and isinstance(body.get('result'), str),
                'claude reported an error result: %s' % (str(body.get('result'))[:200] if isinstance(body, dict) else 'unreadable'))
        answer = body['result']
    answer = answer.strip('\n').strip()
    if answer.startswith('```') and answer.endswith('```') and answer.count('```') == 2:
        answer = answer.split('\n', 1)[1].rsplit('\n', 1)[0].strip()
    require(answer, 'the reviewer returned an empty answer')
    return answer


def check_answer(answer, name, chosen):
    """The answer must be in the findings format and name exactly the captured candidate."""
    lines = [line.rstrip() for line in answer.split('\n') if line.strip()]
    head = 'Review of %s, preview %s' % (name, chosen['preview_id'])
    ident = 'Candidate: parent %s, tree %s' % (chosen['parent'], chosen['tree'])
    require(len(lines) >= 3 and lines[0] == head, 'the answer does not begin with "%s"; it is not saved' % head)
    require(lines[1] == ident, 'the answer does not name the captured candidate ("%s" on its second line); it is not saved' % ident)
    require(re.fullmatch(r'Total: \d+ findings?, \d+ blocker, \d+ major, \d+ minor, \d+ note', lines[-1]) or lines[-1] == 'Total: 0 findings',
            'the answer does not end with a Total line of the findings format; it is not saved')


def publish(folder, preview_id, answer, replace):
    """Write review-<preview id>.md atomically with LF line ends; an existing file stays unless `replace`. A saved file is
    never deleted or replaced afterwards by this tool."""
    target = Path(folder) / ('review-%s.md' % preview_id)
    require(replace or not target.exists(), '%s already exists; it is kept (use --replace to write another round of the '
            'same preview)' % target.name)
    handle, temporary = tempfile.mkstemp(prefix='.review-', suffix='.tmp', dir=str(folder))
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write((answer.strip('\n') + '\n').encode('utf-8'))
        try:
            if replace:
                os.replace(temporary, target)
            elif os.name == 'nt':
                os.rename(temporary, target)  # Windows: an atomic move that fails when the name exists
            else:
                os.link(temporary, target)  # POSIX: rename would overwrite; a hard link fails when the name exists
        except FileExistsError:
            raise ReviewUnavailable('%s was written by another run meanwhile; it is kept' % target.name)
        except OSError as error:
            raise ReviewUnavailable('%s could not be written (%s); nothing was saved' % (target.name, error))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def lifecycle_active(primary):
    """True while a worktree lifecycle operation holds the lifecycle lock of the repository of `primary`."""
    import runtime_guard  # the launcher's folder is on the path; the guard owns where the lock lives
    return runtime_guard.lifecycle_active(primary)


@contextmanager
def publishing(primary, mission, name):
    """Hold the repository's lifecycle lock (as a short `review-publish` window) across the last check and the save, so
    that no create, reconcile, integrate or remove runs between them; refuses at once, with no wait, when it is held."""
    from types import SimpleNamespace
    from governance.tools.worktrees import worktree_support  # late: it imports the worktree guard
    common = Path(local_jobs.git(primary, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    try:
        with worktree_support.lifecycle_lock(SimpleNamespace(common=common, root=Path(primary)), 'review-publish',
                                             mission, name):
            yield
    except ValueError as error:
        if isinstance(error, ReviewUnavailable):
            raise
        raise ReviewUnavailable('the findings are not saved: %s. Run the review again when it has finished' % error)


def run(primary, name, kind, preview, replace=False, timeout=DEFAULT_TIMEOUT, max_bytes=None, environ=None, lookup=None):
    """One review of the preview `preview` (the id the worker showed) by the CLI of `kind`: bind, run read-only, check,
    publish. Returns what the worker reports."""
    require(isinstance(preview, str) and ID.fullmatch(preview), '--preview must be the 32-character id of the preview that was shown')
    # The launcher gates the checkout that holds the script, as every launcher does; the repository that --repo names is
    # gated here, and its lock is held across the last check and the save below.
    require(not lifecycle_active(primary), 'a worktree lifecycle operation holds the lifecycle lock of %s; nothing was started'
            % Path(primary).as_posix())
    chosen = candidate(primary, name, lookup, preview)
    target = Path(chosen['review_folder']) / ('review-%s.md' % chosen['preview_id'])
    require(replace or not target.exists(), '%s already exists; it is kept (use --replace to write another round of the same '
            'preview)' % target.name)
    cli = find_cli(kind, environ)
    prompt = local_jobs.review_brief(primary, name, bundle_text(primary, name, max_bytes, chosen))
    with tempfile.TemporaryDirectory(prefix='review-step-') as scratch:
        answer_file = Path(scratch) / 'answer.txt'
        started = time.time()
        code, out, err = execute(command(cli, chosen, answer_file), prompt.encode('utf-8'), timeout, chosen['worktree'])
        answer = answer_of(cli, code, out, err, answer_file)
    check_answer(answer, name, chosen)
    with publishing(primary, chosen['mission'], name):
        after = candidate(primary, name, lookup)
        require(same_candidate(chosen, after), 'the last preview changed during the run (now %s); the findings are for %s and '
                'are not saved' % (after['preview_id'], chosen['preview_id']))
        # The check above and this save are one step for every lifecycle operation. A preview takes no lock, so the
        # mission's one owner makes no preview and saves no other findings while its own review runs (G025, G063).
        saved = publish(chosen['review_folder'], chosen['preview_id'], answer, replace)
    return {'saved': str(saved), 'executable': cli['path'], 'version': cli['version'], 'found_by': cli['source'],
            'reviewer': kind, 'preview_id': chosen['preview_id'], 'seconds': round(time.time() - started), 'answer': answer}


def main(argv=None):
    parser = argparse.ArgumentParser(description='The review step: a read-only review of a Job candidate by the other vendor.')
    parser.add_argument('--repo', default='.', help='Any checkout of the repository (the primary is found from it)')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('tools', help='Read-only: which Codex and Claude Code CLIs a run would use')
    go = sub.add_parser('run', help='Run a reviewer on the last preview of a Job and save its findings')
    go.add_argument('name')
    go.add_argument('--preview', required=True, help='The 32-character id of the preview that was shown')
    go.add_argument('--reviewer', choices=('codex', 'claude'), required=True)
    go.add_argument('--replace', action='store_true', help='Replace an existing findings file of the same preview')
    go.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT)
    go.add_argument('--max-bytes', type=int, default=None)
    args = parser.parse_args(argv)
    root, primary = local_jobs.roots(args.repo)
    if args.command == 'tools':
        result = {}
        for kind in ('codex', 'claude'):
            try:
                result[kind] = find_cli(kind)
            except ReviewUnavailable as error:
                result[kind] = {'found': False, 'reason': str(error)}
        print(json.dumps(result, indent=2))
        return 0
    try:
        done = run(primary, args.name, args.reviewer, args.preview, args.replace, args.timeout, args.max_bytes)
    except (ReviewUnavailable, ValueError, OSError) as error:
        print('REVIEW NOT SAVED: %s' % error, file=sys.stderr)
        print('The paste path still works: ' + FALLBACK % (Path(primary).as_posix(), args.name, args.preview), file=sys.stderr)
        return 2
    print('Reviewer: %s %s (found by %s: %s)' % (done['reviewer'], done['version'], done['found_by'], done['executable']))
    print('Saved: %s (%d seconds)' % (done['saved'], done['seconds']))
    print('--- findings, verbatim ---')
    sys.stdout.buffer.write((done['answer'] + '\n').encode('utf-8'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
