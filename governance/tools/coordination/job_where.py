"""`local_jobs.py where <handle>`: from a Job handle to the live work it stands for (Python 3.13+).

A read-only lookup over the sources the tools already keep, never a copy of them: the Job record, the claims database
(claims, windows and events), the worktree list and Git refs, each mission's review folder with its last-preview record
and its findings files, the mission's checkpoint store (an open transaction and the landing record) and the execution
status. It writes nothing, gives no approval or authority, and says plainly what it cannot find; it never guesses.

Every record read from a checkpoint store or a review folder is authenticated with the receipt key of that mission's
checkout, read in place: a record that does not verify is reported as unreadable and its content is not used. The command
is READ in the runtime guard, so it also answers while another owner holds a lifecycle window; its output then says so,
because the facts may be stale or torn meanwhile.

Findings files of a review are plain files in the mission's review folder named `review-<preview id>.md`, one per
reviewed preview; the lookup lists them and marks the one for the last preview, never showing an older one as current.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
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
    found = local_jobs.run_git(repo, '--no-optional-locks', *args)
    return found.stdout.decode('utf-8', 'replace').strip() if found.returncode == 0 else None


def section(work):
    """Run one gathering step; a failure becomes a statement that the section cannot be read, never an exception."""
    try:
        return work()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, json.JSONDecodeError) as error:
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
                          'folder_exists': bool(checkout and checkout.is_dir()), 'head': row.get('HEAD') if row else None}
    if checkout and checkout.is_dir():
        changes = git_text(checkout, 'status', '--porcelain')
        result['worktree']['uncommitted_paths'] = len(changes.splitlines()) if changes is not None else None
    if tip and main_tip:
        base = git_text(primary, 'merge-base', main_tip, tip)
        ahead = git_text(primary, 'rev-list', '--count', main_tip + '..' + tip)
        result['base'] = {'main_tip': main_tip, 'merge_base': base, 'commits_ahead_of_main': int(ahead) if ahead else None,
                          'main_moved_since_base': base != main_tip if base else None,
                          'tip_is_on_main': local_jobs.run_git(primary, 'merge-base', '--is-ancestor', tip, main_tip).returncode == 0}
    return result


def store_of(row):
    """The checkpoint store of a registered worktree, read from Git; None when its folder is gone."""
    checkout = Path(row['worktree']).resolve() if row else None
    if not checkout or not checkout.is_dir():
        return None
    gitdir = git_text(checkout, 'rev-parse', '--absolute-git-dir')
    return local_state.store_folder(Path(gitdir)) if gitdir else None


def preview_section(folder, store, head):
    """The last preview of a mission from its review folder: found, or why it was not."""
    if not folder.is_dir():
        return {'found': False, 'reason': 'the review folder does not exist'}
    path = folder / preview_record.FILE
    if not path.exists():
        return {'found': False, 'reason': 'no preview record was found in the review folder: no preview was made yet, or it '
                                          'was made before the record existed. Nothing is guessed from other files.'}
    if store is None:
        return {'found': False, 'reason': 'a record is there but cannot be authenticated: the mission\'s worktree is gone'}
    reader = StoreReader(store)
    try:
        body = early_approval.unsign(reader, json.loads(path.read_bytes()))
    except (ValueError, OSError, json.JSONDecodeError) as error:
        return {'found': False, 'reason': 'the preview record in the review folder is not authentic or not readable (%s); '
                                          'its content is not used' % error}
    output = Path(body['output'])
    file = {'path': body['output'], 'exists': output.is_file()}
    if file['exists']:
        try:
            file['matches_record'] = early_approval.unsign(reader, json.loads(output.read_bytes()))['id'] == body['preview_id']
        except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError):
            file['matches_record'] = False
    go = body.get('go')
    result = {'found': True, 'id': body['preview_id'], 'tree': body['tree'], 'parent': body['parent'],
              'eligible': body['eligible'], 'recorded_at': body['recorded_at'], 'file': file,
              'readable_file': {'path': body['readable'], 'exists': Path(body['readable']).is_file()},
              'parent_is_worktree_head': head == body['parent'] if head else None,
              'go': {'recorded': False} if not go else {'recorded': True, 'recorded_at': go['recorded_at'],
                                                        'file': go['file'], 'file_exists': Path(go['file']).is_file()}}
    return result


def findings_section(folder, last):
    """The findings files of the review folder, each with the preview id its name carries."""
    result = {'pattern': FINDINGS_PATTERN, 'response_pattern': RESPONSE_PATTERN, 'files': [], 'responses': []}
    if not folder.is_dir():
        return result
    responses = {}
    for entry in sorted(folder.iterdir()):
        if entry.name.startswith('response-') and entry.suffix == '.md':
            found = RESPONSE_NAME.fullmatch(entry.name)
            if found:
                responses[found.group(1)] = entry.name
            else:
                result['responses'].append({'name': entry.name, 'preview_id': None, 'status': 'name does not follow the pattern'})
    paired = set()
    for entry in sorted(folder.iterdir()):
        if not (entry.name.startswith('review-') and entry.suffix == '.md'):
            continue
        found = FINDINGS_NAME.fullmatch(entry.name)
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
        result['files'].append({'name': entry.name, 'preview_id': pid, 'status': status, 'response': responses.get(pid),
                                'response_status': 'answered' if pid in responses else 'no response yet'})
    for pid, name in responses.items():
        if pid not in paired:
            result['responses'].append({'name': name, 'preview_id': pid, 'status': 'no findings file for this preview'})
    return result


def transaction_section(store):
    if store is None:
        return {'found': False, 'reason': 'the worktree is gone'}
    path = store / anchors.TRANSACTION
    if not path.exists():
        return {'found': False, 'reason': 'no transaction is open in the checkpoint store'}
    reader = StoreReader(store)
    envelope = json.loads(path.read_bytes())
    packet = envelope['packet']
    expected = hmac.new(reader.key(), early_approval.canonical(packet), hashlib.sha256).hexdigest()
    early_approval.require(hmac.compare_digest(envelope['mac'], expected), 'the transaction record does not verify')
    early = packet.get('early_approval') or {}
    return {'found': True, 'state': packet.get('state'), 'id': packet.get('id'), 'parent': packet.get('parent'),
            'tree': packet.get('tree'), 'result_sha': packet.get('result_sha'), 'go_applied': early.get('applied')}


def landing_section(primary, owner, store, landed_events, main_tip):
    """The landing of a mission: its authenticated landing record while the worktree stands, else the claims database's event."""
    if store is not None and (store / anchors.LANDING).exists():
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
    store = store_of(row)
    result = {'owner': owner, 'state': 'live' if row else 'gone', 'claims': claims_summary(entry),
              'review_folder': {'path': str(folder), 'exists': folder.is_dir()}}
    result.update(repository_part(primary, owner, row, main_tip))
    head = result['worktree'].get('head')
    result['preview'] = section(lambda: preview_section(folder, store, head))
    last = result['preview'].get('id') if result['preview'].get('found') else None
    result['findings'] = section(lambda: findings_section(folder, last))
    result['transaction'] = section(lambda: transaction_section(store))
    result['landing'] = section(lambda: landing_section(primary, owner, store, entry['landed'], main_tip))
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
            lines.append('  worktree %s%s' % (item['worktree']['path'], '' if item['worktree']['folder_exists'] else ' (folder missing)'))
        lines.append('  review folder %s%s' % (item['review_folder']['path'], '' if item['review_folder']['exists'] else ' (missing)'))
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
                preview['file']['path'] + ('' if preview['file']['exists'] else ' (missing)')))
        else:
            lines.append('  last preview: ' + preview['reason'])
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
