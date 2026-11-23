"""`local_jobs.py where <handle>`: from a Job handle to the live work it stands for (Python 3.13+).

A read-only lookup over the sources the tools already keep, never a copy of them: the Job record, the claims database
(claims, windows and events), the worktree list and Git refs, each mission's review folder with its last-preview record
and its findings files, the mission's checkpoint store (an open transaction and the landing record) and the execution
status. It writes nothing, gives no approval or authority, and says plainly what it cannot find; it never guesses.

Every record read from a checkpoint store or a review folder is authenticated with the receipt key of that mission's
checkout, read in place: a record that does not verify is reported as unreadable and its content is not used. The command
is READ in the runtime guard, so it also answers while another owner holds a lifecycle window; its output then says so,
because the facts may be stale or torn meanwhile.

The preview history is read through `preview_record.history`, which verifies each `history-<preview id>.json` with the same key: a
file it refuses is listed as refused with its reason and never used. Each listed preview says whether a findings file and a
response file for its id exist in the review folder.

Findings files of a review are plain files in the mission's review folder named `review-<preview id>.md`, one per
reviewed preview; the lookup lists them and marks the one for the last preview, never showing an older one as current.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import stat
import subprocess

from governance.tools.continuity import artifact_classification as anchors
from governance.tools.coordination import local_jobs
from governance.tools.lifecycle import early_approval, preview_record
from governance.tools.support import local_state, mission_layout
from governance.tools.worktrees import claims

SCHEMA = '1'
FINDINGS_PATTERN = 'review-<preview id>.md'
FINDINGS_NAME = re.compile(r'review-([0-9a-f]{32})\.md')
RESPONSE_PATTERN = 'response-<preview id>.md'
RESPONSE_NAME = re.compile(r'response-([0-9a-f]{32})\.md')
PREVIEW_ID = re.compile(r'[0-9a-f]{32}')
LIFECYCLE_NOTE = ('a worktree lifecycle operation (create, reconcile, integrate or remove) holds the lifecycle lock: '
                  'the facts below may be stale or torn until it finishes')


class StoreReader:
    """Just enough of a checkout's checkpoint tool to authenticate its records: the receipt key, read in place.

    Unlike the tool itself it creates nothing, not even the store folder."""

    def __init__(self, store):
        self.store = Path(store)

    def key(self):
        path = self.store / anchors.RECEIPT_KEY
        early_approval.require(path.is_file() and not path.is_symlink(), 'no receipt key in ' + str(self.store))
        value = path.read_bytes()
        early_approval.require(len(value) == 32, 'invalid receipt key in ' + str(self.store))
        return value


def git_text(repo, *args):
    """The output of a read-only Git command, or None when it fails."""
    try:
        found = local_jobs.run_git(repo, '--no-optional-locks', *args)
    except OSError:
        return None
    return found.stdout.decode('utf-8', 'replace').strip() if found.returncode == 0 else None


def git_result(repo, *args):
    """(output, None) of a read-only Git command, or (None, the first line of Git's own message) when it fails."""
    try:
        found = local_jobs.run_git(repo, '--no-optional-locks', *args)
    except OSError as error:
        return None, 'git could not be started: %s' % error
    if found.returncode == 0:
        return found.stdout.decode('utf-8', 'replace').strip(), None
    lines = found.stderr.decode('utf-8', 'replace').strip().splitlines()
    return None, 'git failed (exit %d): %s' % (found.returncode, lines[0] if lines else 'no message')


def present(path, folder=False):
    """Whether `path` exists (and is a folder when `folder`): False only when it is confirmed absent.

    `Path.exists` and `Path.is_dir` turn every OS error, a refused access included, into False, which would report an
    inaccessible source as absent. Any other OSError is raised here and becomes `cannot be read` in the section that asked."""
    try:
        found = Path(path).stat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return stat.S_ISDIR(found.st_mode) if folder else True


def file_state(path):
    """{'exists': True | False | None} for a file the preview record names; None with a reason when access to it fails."""
    try:
        return {'exists': present(path) and Path(path).is_file()}
    except OSError as error:
        return {'exists': None, 'reason': 'cannot be read: %s' % error}


def plain_file(path):
    """True for a regular file that is not a link, False when it is absent or is anything else; an access error is raised."""
    if not present(path) or Path(path).is_symlink() or Path(path).is_junction():
        return False
    return stat.S_ISREG(Path(path).stat().st_mode)


def section(work):
    """Run one gathering step; a failure becomes a statement that the section cannot be read, never an exception."""
    try:
        return work()
    except (ValueError, OSError, KeyError, TypeError, RecursionError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        return {'found': False, 'reason': 'cannot be read: ' + str(error)}


def read_claims(common, name):
    """(rows and events by mission, landed events by mission, database state) for the claims that name this Job.

    A mission is listed when any of its claims or windows, open or closed, or a landed event names the handle. Another
    handle on a mission's claims is reported, never merged. A database that does not exist yet names no mission."""
    try:
        con = claims.open_database(common, False)
    except claims.ClaimsError as error:
        return {}, {'state': error.state, 'message': error.message}
    try:
        rows = [dict(row) for row in con.execute(
            'SELECT id, kind, scope, owner, job_handle, state, since, worktree, closed_at FROM claims '
            'WHERE owner IS NOT NULL ORDER BY id').fetchall()]
        landed = [claims.decode(row) for row in con.execute("SELECT * FROM events WHERE event='landed' ORDER BY id").fetchall()]
        owners = {row['owner'] for row in rows if same(row['job_handle'], name)}
        owners.update(event['owner'] for event in landed if event['owner'] and same(event['detail'].get('job_handle'), name))
        found = {owner: {'claims': [row for row in rows if row['owner'] == owner], 'events': [], 'landed': []} for owner in owners}
        for event in landed:
            if event['owner'] in found:
                found[event['owner']]['landed'].append(event)
        for owner in owners:
            found[owner]['events'] = [claims.decode(row) for row in con.execute(
                "SELECT * FROM events WHERE owner=? AND event<>'landed' ORDER BY id", (owner,)).fetchall()]
        return found, {'state': 'ok', 'message': ''}
    except Exception as error:
        failure = claims.classify(error)
        return {}, {'state': failure.state, 'message': failure.message}
    finally:
        con.close()


def same(value, name):
    return local_jobs.same_job(value, name)


def job_section(directory, name):
    path = directory / (name + '.json')
    if not path.exists():
        return {'found': False, 'reason': 'no record %s.json in primary storage (the Job is closed and pruned, or never recorded)' % name}
    record = local_jobs.snapshot(path)['record']
    return {'found': True, 'status': record['status'], 'activity': record['activity'], 'worker': record['worker'],
            'step': record.get('step'), 'waiting': record.get('waiting'), 'awaiting_lead': record.get('awaiting_lead'),
            'commits': record['commits'], 'updated_at': record['updated_at'], 'pending_entries': len(record['pending']),
            'sha256': local_jobs.snapshot(path)['sha256']}


def worktree_row(primary, owner):
    for row in local_jobs.listed_worktrees(primary):
        if row.get('branch') == 'refs/heads/tx/' + owner or row.get('locked') == 'transaction:' + owner:
            return row
    return None


def repository_part(primary, owner, row, main_tip):
    tip = git_text(primary, 'rev-parse', '--verify', '-q', 'refs/heads/tx/' + owner + '^{commit}')
    result = {'branch': {'ref': 'refs/heads/tx/' + owner, 'exists': tip is not None, 'tip': tip}}
    checkout = Path(row['worktree']).resolve() if row else None
    result['worktree'] = {'registered': row is not None, 'path': str(checkout) if checkout else None,
                          'folder_exists': False if checkout else None, 'head': row.get('HEAD') if row else None}
    folder_present = False
    if checkout:
        try:
            folder_present = present(checkout, folder=True)
            result['worktree']['folder_exists'] = folder_present
        except OSError as error:
            result['worktree']['folder_exists'] = None
            result['worktree']['folder_reason'] = 'cannot be accessed: %s' % error
            result['worktree']['uncommitted_paths'] = None
            result['worktree']['uncommitted_paths_reason'] = 'cannot be read: the worktree folder cannot be accessed: %s' % error
    if folder_present:
        changes, failure = git_result(checkout, 'status', '--porcelain')
        result['worktree']['uncommitted_paths'] = len(changes.splitlines()) if changes is not None else None
        if failure:
            result['worktree']['uncommitted_paths_reason'] = 'cannot be read inside the worktree: ' + failure
    if tip and main_tip:
        base = git_text(primary, 'merge-base', main_tip, tip)
        ahead = git_text(primary, 'rev-list', '--count', main_tip + '..' + tip)
        result['base'] = {'main_tip': main_tip, 'merge_base': base, 'commits_ahead_of_main': int(ahead) if ahead else None,
                          'main_moved_since_base': base != main_tip if base else None,
                          'tip_is_on_main': local_jobs.run_git(primary, 'merge-base', '--is-ancestor', tip, main_tip).returncode == 0}
    return result


def registered_gitdir(common, checkout):
    """The Git directory that the primary's own data (`<common>/worktrees/<name>/gitdir`) gives a worktree; None when none names it.

    Read as plain files from the primary, never by running Git inside the worktree, which can refuse a folder owned by
    another user. A relative pointer (Git's relative-path mode) is read from the worktree's own metadata folder, as Git
    reads it. An error reading the folder of the worktrees is raised, never taken for absence."""
    base = Path(common) / 'worktrees'
    if not present(base, folder=True):
        return None
    for entry in sorted(base.iterdir()):
        try:
            pointer = Path((entry / 'gitdir').read_text(encoding='utf-8').strip())
        except (OSError, UnicodeError):
            continue
        if not pointer.is_absolute():
            pointer = entry / pointer
        if pointer.parent.resolve() == checkout and not local_state.indirect(entry):
            return entry.resolve()
    return None


def checkout_pointer(checkout):
    """(Git directory, None) that the worktree's `.git` entry names; (None, None) when the entry cannot be read, so it cannot
    cross-check; (None, why) when it is readable and names none, which refuses the store."""
    entry = checkout / local_state.GIT_DIRECTORY
    try:
        if not present(entry):
            return None, None
        if not present(entry, folder=True):
            entry.read_bytes()
    except OSError:
        return None, None
    found = local_state.checkout_git_directory(checkout)
    if found is None:
        return None, 'the worktree\'s .git file is readable but names no Git directory, so its checkpoint store is not used'
    return found, None


def store_of(common, row):
    """(checkpoint store, why not) of a mission's worktree, found without running Git inside it.

    Sources: the primary's own Git data (the registered worktree's gitdir under the common directory) decides, because it
    is the checkout every sandbox trusts, and Git run inside a linked worktree can fail (for example, Git refuses a folder owned
    by another user). The worktree's `.git` file is only a cross-check: when it can be read it must agree, and without the
    primary's data no store is used. A store that is a link is never followed. 'Gone' is only a worktree that is not registered or whose folder is missing."""
    if not row:
        return None, 'the worktree is gone: it is not registered'
    checkout = Path(row['worktree']).resolve()
    try:
        folder_present = present(checkout, folder=True)
    except OSError as error:
        return None, 'the worktree folder cannot be accessed: %s' % error
    if not folder_present:
        return None, 'the worktree is gone: its folder is missing'
    try:
        from_primary = registered_gitdir(common, checkout)
    except OSError as error:
        return None, 'the primary\'s worktree data cannot be read: %s' % error
    if from_primary is None:
        return None, ('the primary\'s own data does not identify a Git directory for this registered worktree, so its checkpoint '
                      'store is not used (the worktree\'s .git file alone does not authenticate it)')
    from_checkout, problem = checkout_pointer(checkout)
    if problem:
        return None, problem
    if from_checkout is not None and from_checkout != from_primary:
        return None, 'the Git directory named by the primary (%s) and by the worktree (%s) differ' % (from_primary, from_checkout)
    store = local_state.store_folder(from_primary)
    if local_state.indirect(store):
        return None, 'the checkpoint store %s is a link or not a plain folder, so it is not followed' % store
    return store, None


def preview_section(folder, store, head, why=None):
    """The last preview of a mission from its review folder: found, or why it was not."""
    if not present(folder, folder=True):
        return {'found': False, 'reason': 'the review folder does not exist'}
    path = folder / preview_record.FILE
    if not present(path):
        return {'found': False, 'reason': 'no preview record was found in the review folder: no preview was made yet, or it '
                                          'was made before the record existed. Nothing is guessed from other files.'}
    if store is None:
        return {'found': False, 'reason': 'a record is there but cannot be authenticated: ' + (why or 'no checkpoint store')}
    reader = StoreReader(store)
    try:
        body = early_approval.unsign(reader, json.loads(path.read_bytes()))
    except (ValueError, OSError, json.JSONDecodeError) as error:
        return {'found': False, 'reason': 'the preview record in the review folder is not authentic or not readable (%s); '
                                          'its content is not used' % error}
    output = Path(body['output'])
    file = dict(file_state(output), path=body['output'])
    if file['exists']:
        try:
            file['matches_record'] = early_approval.unsign(reader, json.loads(output.read_bytes()))['id'] == body['preview_id']
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
            file['matches_record'] = False
    go = body.get('go')
    result = {'found': True, 'id': body['preview_id'], 'tree': body['tree'], 'parent': body['parent'],
              'eligible': body['eligible'], 'recorded_at': body['recorded_at'], 'file': file,
              'readable_file': dict(file_state(body['readable']), path=body['readable']),
              'parent_is_worktree_head': head == body['parent'] if head else None,
              'go': {'recorded': False} if not go else {'recorded': True, 'recorded_at': go['recorded_at'],
                                                        'file': go['file'], 'file_exists': file_state(go['file'])['exists'],
                                                        'file_reason': file_state(go['file']).get('reason')}}
    return result


def history_section(folder, store, last, why=None):
    """Every preview of a mission, in its order, from the signed history records; refused records are listed apart, never trusted."""
    if not present(folder, folder=True):
        return {'found': False, 'reason': 'the review folder does not exist'}
    names = sorted(entry.name for entry in folder.iterdir()
                   if entry.name.startswith(preview_record.HISTORY_PREFIX) and entry.name.endswith('.json'))
    if not names:
        return {'found': False, 'reason': 'no history record was found in the review folder: no preview was made since the history '
                                          'existed. Nothing is guessed from other files.'}
    if store is None:
        return {'found': False, 'reason': 'history records are there but cannot be authenticated: ' + (why or 'no checkpoint store')}
    try:
        entries, problems = preview_record.history(StoreReader(store), folder)
    except RecursionError:
        return {'found': False, 'reason': 'cannot be read: a history file is nested too deeply to be parsed, and the reader of the '
                                          'history (preview_record.history) does not refuse a single file, so none is listed'}

    names = {entry.name for entry in folder.iterdir()}

    def plain(name, unreadable):
        """True or False for a plain file of exactly this name (a case-insensitive file system matches others too), None
        (with the reason added to `unreadable`) when access to it fails."""
        if name not in names:
            return False
        try:
            return plain_file(folder / name)
        except OSError as error:
            unreadable.append('%s cannot be read: %s' % (name, error))
            return None

    listed = []
    for body in entries:
        unreadable = []
        listed.append({'seq': body['seq'], 'preview_id': body['preview_id'], 'tree': body['tree'], 'parent': body['parent'],
                       'recorded_at': body.get('recorded_at'), 'is_last': None if last is None else body['preview_id'] == last,
                       'findings_file': plain('review-%s.md' % body['preview_id'], unreadable),
                       'response_file': plain('response-%s.md' % body['preview_id'], unreadable),
                       'unreadable': unreadable})
    refused = []
    for problem in problems:
        name, _, reason = problem.partition(': ')
        refused.append({'name': name, 'status': 'refused, not trusted', 'reason': reason})
    return {'found': True, 'entries': listed, 'refused': refused,
            'last_in_history': None if last is None else any(item['is_last'] for item in listed)}


def file_problem(entry):
    """None for a plain file, else why a findings or response file is not used: not a plain file, or cannot be read."""
    try:
        return None if plain_file(entry) else 'not a plain file'
    except OSError as error:
        return 'cannot be read: %s' % error


def findings_section(folder, last):
    """The findings files of the review folder, each with the preview id its name carries."""
    result = {'pattern': FINDINGS_PATTERN, 'response_pattern': RESPONSE_PATTERN, 'files': [], 'responses': []}
    if not present(folder, folder=True):
        return result
    responses, unreadable_responses, unreadable_findings = {}, {}, set()
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith('response-') and entry.suffix == '.md':
            found = RESPONSE_NAME.fullmatch(entry.name)
            kind = file_problem(entry)
            if kind:
                result['responses'].append({'name': entry.name, 'preview_id': found.group(1) if found else None, 'status': kind})
                if found and kind.startswith('cannot be read'):
                    unreadable_responses[found.group(1)] = entry.name
            elif found:
                responses[found.group(1)] = entry.name
            else:
                result['responses'].append({'name': entry.name, 'preview_id': None, 'status': 'name does not follow the pattern'})
    paired = set()
    for entry in sorted(folder.iterdir()):
        if not (entry.name.startswith('review-') and entry.suffix == '.md'):
            continue
        found = FINDINGS_NAME.fullmatch(entry.name)
        kind = file_problem(entry)
        if kind:
            result['files'].append({'name': entry.name, 'preview_id': found.group(1) if found else None, 'status': kind,
                                    'response': None})
            if found and kind.startswith('cannot be read'):
                unreadable_findings.add(found.group(1))
            continue
        if not found:
            result['files'].append({'name': entry.name, 'preview_id': None, 'status': 'name does not follow the pattern',
                                    'response': None})
            continue
        pid = found.group(1)
        if last is None:
            status = 'last preview unknown'
        else:
            status = 'for the last preview' if pid == last else 'for an older preview, not current'
        paired.add(pid)
        if pid in responses:
            response_status = 'answered'
        elif pid in unreadable_responses:
            response_status = 'unknown: the response %s cannot be read' % unreadable_responses[pid]
        else:
            response_status = 'no response yet'
        result['files'].append({'name': entry.name, 'preview_id': pid, 'status': status, 'response': responses.get(pid),
                                'response_status': response_status})
    for pid, name in responses.items():
        if pid not in paired:
            status = ('the findings file for this preview cannot be read' if pid in unreadable_findings
                      else 'no findings file for this preview')
            result['responses'].append({'name': name, 'preview_id': pid, 'status': status})
    return result


def unreadable(why):
    """The reason of a section that needs the checkpoint store: absence only for a worktree that is gone, else 'cannot be read'."""
    if not why:
        return 'cannot be read: no checkpoint store'
    return why if why.startswith('the worktree is gone') else 'cannot be read: ' + why


def transaction_section(store, why=None):
    if store is None:
        return {'found': False, 'reason': unreadable(why)}
    path = store / anchors.TRANSACTION
    if not present(path):
        return {'found': False, 'reason': 'no transaction is open in the checkpoint store'}
    reader = StoreReader(store)
    envelope = json.loads(path.read_bytes())
    packet = envelope['packet']
    expected = hmac.new(reader.key(), early_approval.canonical(packet), hashlib.sha256).hexdigest()
    early_approval.require(hmac.compare_digest(envelope['mac'], expected), 'the transaction record does not verify')
    early = packet.get('early_approval') or {}
    return {'found': True, 'state': packet.get('state'), 'id': packet.get('id'), 'parent': packet.get('parent'),
            'tree': packet.get('tree'), 'result_sha': packet.get('result_sha'), 'go_applied': early.get('applied')}


def landing_section(primary, owner, store, landed_events, main_tip, why=None):
    """The landing of a mission: its authenticated landing record while the worktree stands, else the claims database's event."""
    if store is not None and present(store / anchors.LANDING):
        data = early_approval.unsign(StoreReader(store), json.loads((store / anchors.LANDING).read_bytes()))
        commit = data['result_sha']
        source = 'landing record in the worktree\'s checkpoint store'
        facts = {'commit': commit, 'parent': data['parent'], 'tree': data['tree'], 'branch': data['branch'],
                 'subject': data['subject'], 'approval_path': data['approval_path']}
    elif landed_events:
        commit = landed_events[-1]['detail'].get('commit')
        source = 'claims database (landed event of %s)' % landed_events[-1]['at']
        facts = {'commit': commit, 'branch': 'refs/heads/tx/' + owner}
        if commit and git_text(primary, 'cat-file', '-e', commit + '^{commit}') is not None:
            facts['parent'] = git_text(primary, 'rev-parse', commit + '^')
            facts['tree'] = git_text(primary, 'rev-parse', commit + '^{tree}')
            facts['subject'] = git_text(primary, 'log', '-1', '--format=%s', commit)
    elif store is None and why and not why.startswith('the worktree is gone'):
        return {'found': False, 'reason': unreadable(why) + ' (no landed event is recorded either)'}
    else:
        return {'found': False, 'reason': 'no landing is recorded for this mission'}
    on_main = None
    if commit and main_tip:
        on_main = local_jobs.run_git(primary, 'merge-base', '--is-ancestor', commit, main_tip).returncode == 0
    return dict(facts, found=True, source=source, on_main=on_main)


def claims_summary(entry):
    rows = entry['claims']
    other = sorted({row['job_handle'] for row in rows if row['job_handle'] and not same(row['job_handle'], entry['handle'])})
    closed = [event for event in entry['events'] if event['event'] == 'closed']
    result = {'open': [{'kind': row['kind'], 'scope': row['scope'], 'state': row['state'], 'since': row['since']}
                       for row in rows if row['closed_at'] is None],
              'closed_count': sum(1 for row in rows if row['closed_at'] is not None),
              'last_closed': {'at': closed[-1]['at'], 'reason': closed[-1]['detail'].get('reason')} if closed else None}
    if other:
        result['also_named_under_other_handles'] = other
    return result


def mission(primary, common, name, owner, entry, main_tip):
    entry = dict(entry, handle=name)
    row = worktree_row(primary, owner)
    folder = Path(mission_layout.layout(primary, owner)['review'])
    store, why = store_of(common, row)
    result = {'owner': owner, 'state': 'live' if row else 'gone', 'claims': claims_summary(entry),
              'review_folder': {'path': str(folder), 'exists': False}}
    try:
        result['review_folder']['exists'] = present(folder, folder=True)
    except OSError as error:
        result['review_folder'].update(exists=None, reason='cannot be accessed: %s' % error)
    result.update(repository_part(primary, owner, row, main_tip))
    head = result['worktree'].get('head')
    result['preview'] = section(lambda: preview_section(folder, store, head, why))
    last = result['preview'].get('id') if result['preview'].get('found') else None
    result['history'] = section(lambda: history_section(folder, store, last, why))
    result['findings'] = section(lambda: findings_section(folder, last))
    result['transaction'] = section(lambda: transaction_section(store, why))
    result['landing'] = section(lambda: landing_section(primary, owner, store, entry['landed'], main_tip, why))
    return result


def where(primary, name):
    """The lookup as one JSON-ready dictionary. Reads only; creates no folder, record or database."""
    local_jobs.handle(name)
    primary = Path(primary).resolve()
    common = Path(local_jobs.git(primary, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    from governance.tools.worktrees import guard
    status = guard.execution_status(primary)
    lifecycle = [w for w in status['windows'] if w.get('scope') == 'lifecycle']
    active = bool(lifecycle) or status['derived'].get('lifecycle_lock', False)
    main_tip = git_text(primary, 'rev-parse', '--verify', '-q', 'refs/heads/main^{commit}')
    found, database = read_claims(common, name)
    windows = [w for w in status['windows'] if w.get('owner') and same(w.get('job_handle'), name)]
    for window in windows:
        found.setdefault(window['owner'], {'claims': [], 'events': [], 'landed': []})
    result = {'schema': SCHEMA, 'handle': name, 'read_at': datetime.now(timezone.utc).isoformat(), 'main_tip': main_tip,
              'lifecycle_operation': {'active': active, 'note': LIFECYCLE_NOTE if active else None,
                                      'windows': [{'owner': w.get('owner'), 'state': w.get('state'), 'since': w.get('since')}
                                                  for w in lifecycle]},
              'execution_status': {'level': status['level'], 'line': status['line']},
              'claims_database': database,
              'job': section(lambda: job_section(local_jobs.safe_directory(primary), name))}
    result['missions'] = [section(lambda o=owner: mission(primary, common, name, o, found[o], main_tip)) | {'owner': owner}
                          for owner in sorted(found)]
    cannot = []
    if database['state'] not in ('ok', 'missing'):
        cannot.append('the claims database cannot be read (%s: %s), so missions named only there are not listed'
                      % (database['state'], database['message']))
    if not found:
        cannot.append('no mission is claimed under this handle: a mission is linked to a Job when its preview declares the '
                      'handle (--job-handle), so a worktree that has made no preview, or one previewed without the handle, '
                      'is not found from here')
    for item in result['missions']:
        if item.get('found') is False:
            cannot.append('mission %s: %s' % (item['owner'], item['reason']))
            continue
        for entry in item['history'].get('entries', []):
            for reason in entry['unreadable']:
                cannot.append('mission %s, history #%d: %s' % (item['owner'], entry['seq'], reason))
        if item['worktree'].get('uncommitted_paths_reason'):
            cannot.append('mission %s, worktree: uncommitted paths %s' % (item['owner'], item['worktree']['uncommitted_paths_reason']))
        for refused in item['history'].get('refused', []):
            cannot.append('mission %s, history: %s refused (%s), not used' % (item['owner'], refused['name'], refused['reason']))
        if item['history'].get('found') is False and ('cannot be authenticated' in item['history'].get('reason', '')
                                                      or item['history'].get('reason', '').startswith('cannot')):
            cannot.append('mission %s, history: %s' % (item['owner'], item['history']['reason']))
        for key in ('preview', 'transaction', 'landing'):
            if item[key].get('found') is False and (key == 'preview' or item[key].get('reason', '').startswith('cannot')):
                cannot.append('mission %s, %s: %s' % (item['owner'], key, item[key]['reason']))
    result['cannot_find'] = cannot
    return result


def short(value, size=12):
    return value[:size] if isinstance(value, str) else str(value)


def render_text(result):
    """The short human form: one block per mission, then what could not be found. The JSON follows it."""
    lines = []
    if result['lifecycle_operation']['active']:
        lines.append('NOTE: ' + LIFECYCLE_NOTE + '.')
    job = result['job']
    if job.get('found'):
        lines.append('Job %s: %s, worker %s (%s), step: %s' % (result['handle'], job['status'], job['worker'], job['activity'],
                                                                job.get('step') or 'none recorded'))
    else:
        lines.append('Job %s: %s' % (result['handle'], job.get('reason')))
    lines.append('Execution status: ' + result['execution_status']['line'])
    for item in result['missions']:
        if item.get('found') is False:
            lines.append('Mission %s: %s' % (item['owner'], item['reason']))
            continue
        tip = item['branch']['tip']
        lines.append('Mission %s (%s): branch %s%s' % (item['owner'], item['state'], item['branch']['ref'],
                                                        ' at ' + short(tip) if tip else ' is gone'))
        if item['worktree']['registered']:
            folder_state = {True: '', False: ' (folder missing)', None: ' (folder ' + item['worktree'].get('folder_reason', 'cannot be accessed') + ')'}
            lines.append('  worktree %s%s' % (item['worktree']['path'], folder_state[item['worktree']['folder_exists']]))
        if item['worktree'].get('uncommitted_paths_reason'):
            lines.append('  uncommitted paths: unknown, ' + item['worktree']['uncommitted_paths_reason'])
        review_state = {True: '', False: ' (missing)', None: ' (' + item['review_folder'].get('reason', 'cannot be accessed') + ')'}
        lines.append('  review folder %s%s' % (item['review_folder']['path'], review_state[item['review_folder']['exists']]))
        base = item.get('base')
        if base:
            lines.append('  based on main %s%s, %s commit(s) ahead' % (
                short(base['merge_base']), ' (main has moved to %s)' % short(base['main_tip']) if base['main_moved_since_base'] else '',
                base['commits_ahead_of_main']))
        preview = item['preview']
        if preview.get('found'):
            go = 'go recorded' if preview['go']['recorded'] else 'no go recorded'
            lines.append('  last preview %s: tree %s on parent %s, %s, file %s' % (
                short(preview['id']), short(preview['tree']), short(preview['parent']), go,
                preview['file']['path'] + {True: '', False: ' (missing)', None: ' (%s)' % preview['file'].get('reason')}[preview['file']['exists']]))
        else:
            lines.append('  last preview: ' + preview['reason'])
        history = item['history']
        if history.get('found'):
            lines.append('  preview history (%d):' % len(history['entries']))
            for entry in history['entries']:
                lines.append('    #%d %s: tree %s on parent %s, %s%s; findings %s, response %s' % (
                    entry['seq'], short(entry['preview_id']), short(entry['tree']), short(entry['parent']), entry['recorded_at'],
                    ' (last preview)' if entry['is_last'] else '', {True: 'yes', False: 'no', None: 'unknown'}[entry['findings_file']],
                    {True: 'yes', False: 'no', None: 'unknown'}[entry['response_file']]))
                for reason in entry['unreadable']:
                    lines.append('      ' + reason)
            for entry in history['refused']:
                lines.append('    %s: %s (%s)' % (entry['name'], entry['status'], entry['reason']))
        else:
            lines.append('  preview history: ' + history['reason'])
        for entry in item['findings'].get('files', []):
            lines.append('  findings %s: %s; %s' % (entry['name'], entry['status'], entry.get('response_status') or 'no response pairing'))
        for entry in item['findings'].get('responses', []):
            lines.append('  response %s: %s' % (entry['name'], entry['status']))
        transaction = item['transaction']
        if transaction.get('found'):
            lines.append('  transaction %s: %s' % (short(transaction['id']), transaction['state']))
        landing = item['landing']
        if landing.get('found'):
            lines.append('  landed %s (parent %s, tree %s, %s), %s; on main: %s' % (
                short(landing['commit']), short(landing.get('parent')), short(landing.get('tree')), landing['branch'],
                landing['source'], landing['on_main']))
    for line in result['cannot_find']:
        lines.append('Cannot find: ' + line)
    return '\n'.join(lines)
