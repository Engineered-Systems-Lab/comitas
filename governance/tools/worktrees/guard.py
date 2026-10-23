"""Guard native Git worktree lifecycle; Python 3.13+, no commit or approval authority."""
import sys

# Enforce G024 before importing repository tooling or reaching any lifecycle side effect.
if sys.version_info < (3, 13):
    sys.exit('FAIL: governance/tools/worktree.py requires Python 3.13+; detected Python '
             + '.'.join(str(part) for part in sys.version_info[:3]))

import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import tempfile
import time

from governance.tools.continuity import artifact_classification
from governance.tools.continuity.artifact_classification import CONTAINER, EARLY_USES, LANDING, REVIEW
from governance.tools.worktrees import claims
from governance.tools.support import local_state
from governance.tools.lifecycle import early_approval
from governance.tools.coordination import local_jobs
from governance.tools.worktrees.worktree_carry import Carry, CarryRefused, GoCarryRefused, carry_note  # noqa: F401
from governance.tools.worktrees.worktree_support import checkpoint_driver, remove_tree, tools_root, identity_environment, ReadOnlyGit, lifecycle_lock  # noqa: F401
from checkpoint import Checkpoint, require, digest, canonical, exclusive_evidence, POLICY, PROFILES, TRANSACTION, RECEIPT_KEY, OPERATION_LOCK, check_ref_context

MISSION_ID = r'[a-z0-9][a-z0-9-]{0,63}'
GUARD_MODULES = ('worktree', 'worktrees/guard', 'worktrees/worktree_carry', 'worktrees/worktree_support')


def guard_paths():
    """The files that make up the lifecycle guard, side by side: this one and the two modules it is composed of."""
    folder = Path(__file__).resolve().parents[1]
    return [folder / (name + '.py') for name in GUARD_MODULES]


def guard_digest():
    """One digest of the whole guard, so a change to any of its files changes what an integration recorded."""
    return digest(canonical({path.name: digest(path.read_bytes()) for path in guard_paths()}))


def layout(main_root, owner):
    """The one container layout, derived from the main checkout's folder alone and touching nothing."""
    require(re.fullmatch(MISSION_ID, owner), 'Use a unique lowercase mission ID')
    main_root = Path(main_root).resolve()
    container = main_root.parent / CONTAINER / main_root.name
    return {'container': str(container), 'worktree': str(container / owner),
            'review': str(container / REVIEW / owner)}


def review_inputs(folder):
    """The plain files of a mission's review folder, which removal deletes after the worktree and branch.

    The folder holds review inputs and copies of what Git, the conversation and the Job record keep, so it dies with
    its worktree. Anything but plain files (a subfolder, a link, a junction) is refused before anything is removed,
    because deleting it would need a recursive or link-following delete.
    """
    if not folder.exists() and not folder.is_symlink():
        return []
    require(folder.is_dir() and not folder.is_symlink() and not folder.is_junction(),
            'Review folder %s is not a plain folder; inspect it before removal' % folder)
    entries = sorted(folder.iterdir())
    odd = [p.name for p in entries if p.is_symlink() or p.is_junction() or not p.is_file()]
    require(not odd, 'Review folder %s holds more than plain files (%s); move them out before removal' % (
        folder, ', '.join(odd)))
    return entries


def superseding_claims(common, owner, commit):
    """(Job handles the owner's claims and windows named, the `landed` events of commit), open or closed, for the removal
    of a superseded mission. Read-only and creates nothing; ClaimsError when the database cannot be read, because that
    removal is a proof and never runs on missing evidence, although the database is otherwise never a gate."""
    con = claims.open_database(common, False)
    try:
        handles = {row['job_handle'] for row in con.execute(
            'SELECT DISTINCT job_handle FROM claims WHERE owner=? AND job_handle IS NOT NULL', (owner,)).fetchall()}
        landings = [event for event in (claims.decode(row) for row in con.execute(
            "SELECT * FROM events WHERE event='landed' ORDER BY id").fetchall()) if event['detail'].get('commit') == commit]
        return handles, landings
    except Exception as error:
        raise claims.classify(error) from error
    finally:
        con.close()


def superseded_note(owner, proof):
    """The Job note written before a superseded mission is removed, so its unmerged work stays traceable (G048)."""
    return ('SUPERSEDED MISSION REMOVED (written by worktree.py remove --superseded-by just before the removal; its '
            'output confirms it). Mission %s, branch tx/%s, old tip %s (%s), never integrated; superseded by %s, which '
            'mission %s landed for this Job. The old commit stays readable by its id until Git prunes unreachable '
            'objects.' % (owner, owner, proof['old_tip'], proof['subject'], proof['replacement'], proof['replacement_owner']))


def remove_review(folder, files):
    """Delete exactly the files seen before removal, then the folder. A failure is reported, never raised, because
    the worktree and branch are already gone; a file added meanwhile keeps the folder, and that is reported too."""
    if not files and not folder.exists():
        return {'folder': str(folder), 'removed': False, 'files': []}
    try:
        for path in files:
            path.unlink()
        folder.rmdir()
    except OSError as error:
        return {'folder': str(folder), 'removed': False, 'files': [p.name for p in files], 'left': str(error)}
    return {'folder': str(folder), 'removed': True, 'files': [p.name for p in files]}


# Isolated stdlib loader: never import candidate checkpoint/worktree/policy commands.
CANDIDATE_CONTINUITY = '''
import importlib.util, pathlib, subprocess, sys, types
path, root = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
# The checker imports its parser by package name. Provide only the parser, loaded from the verified file beside it,
# under empty stand-in packages, so no package file of the candidate runs here.
parser_path = path.parent / 'artifact_classification.py'
if parser_path.is_file():
    for name in ('governance', 'governance.tools', 'governance.tools.continuity'):
        sys.modules[name] = types.ModuleType(name)
        sys.modules[name].__path__ = []
    parser_spec = importlib.util.spec_from_file_location('governance.tools.continuity.artifact_classification', parser_path)
    parser = importlib.util.module_from_spec(parser_spec)
    sys.modules[parser_spec.name] = parser
    parser_spec.loader.exec_module(parser)
    sys.modules['governance.tools.continuity'].artifact_classification = parser
spec = importlib.util.spec_from_file_location('candidate_continuity', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
_, facts = module.verify_repository(root)
def git(*args):
    return subprocess.check_output(['git', '-C', str(root), *args])
assert not git('diff', '--name-only', 'HEAD', '--', 'docs/PROGRESS.md')
if 'implementation_checkpoint' not in facts:
    print('PASS: candidate committed continuity (no runtime checkpoint is declared)')
    raise SystemExit(0)
devlog_settings = getattr(getattr(module, 'artifact_classification', None), 'devlog_settings', None)  # a checker without it keeps the log required
if devlog_settings is not None and not devlog_settings(root).get('enabled', True):
    print('PASS: candidate committed continuity (the project keeps no devlog)')
    raise SystemExit(0)
checkpoint = facts['implementation_checkpoint']
logs = [name for name in module.validate_docs(root)
        if f'Repository checkpoint: `{checkpoint}`' in module.read(root, name)]
assert len(logs) == 1, 'Durable continuity requires one matching log'
assert git('show', 'HEAD:' + logs[0]).decode().replace('\\r\\n', '\\n') == module.read(root, logs[0])
assert '](' + pathlib.Path(logs[0]).name + ')' in git('show', 'HEAD:' + pathlib.PurePosixPath(logs[0]).parent.as_posix() + '/README.md').decode()
print('PASS: candidate committed continuity')
'''


def inventory(tool):
    entries = []
    for record in tool.git('worktree', 'list', '--porcelain', '-z').decode().split('\0\0'):
        if record:
            entries.append(dict(field.split(' ', 1) if ' ' in field else (field, '')
                                for field in record.split('\0') if field))
    return entries


EXECUTION_SCHEMA = '1.1'
# Seconds after which an open window or path claim only shows an age flag; nothing is ever cleared because of age.
AGE_LIMITS = {'prepare': 3600, 'lifecycle': 600, 'paths': 86400}
WATCH_SECONDS = 5
WAIT_SECONDS = 300
WAIT_TIMEOUT_EXIT = 3
CLEAR_LEVELS = ('CLEAR', 'OVERLAP')
PREPARE_STATES = ('preparing', 'validated')


def stamp(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def read_packet_state(scratch):
    """Unauthenticated packet fields for a report; the HMAC is not checked because nothing here decides anything."""
    try:
        packet = json.loads((scratch / TRANSACTION).read_bytes())['packet']
        return packet.get('state'), packet.get('job_handle'), (scratch / TRANSACTION).stat().st_mtime
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, None, None


def derived_evidence(root):
    """Open windows from what cannot lie: the worktree list, packets, operation locks and the lifecycle lock."""
    def git(*args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
        require(result.returncode == 0, 'Git failed: ' + ' '.join(args))
        return result.stdout
    common = Path(git('rev-parse', '--path-format=absolute', '--git-common-dir').decode().strip()).resolve()
    found, inspected = [], 0
    for record in git('worktree', 'list', '--porcelain', '-z').decode().split('\0\0'):
        fields = dict(f.split(' ', 1) if ' ' in f else (f, '') for f in record.split('\0') if f)
        if 'worktree' not in fields or 'bare' in fields:
            continue
        path = Path(fields['worktree'])
        scratch = local_state.checkout_store(path)
        if scratch is None or not scratch.is_dir():
            continue
        inspected += 1
        reason = fields.get('locked', '')
        owner = reason.removeprefix('transaction:') if reason.startswith('transaction:') \
            else fields.get('branch', '').removeprefix('refs/heads/tx/').removeprefix('refs/heads/')
        state, job, packet_time = read_packet_state(scratch)
        lock = scratch / OPERATION_LOCK
        locked = lock.is_file()
        if state in PREPARE_STATES:
            found.append({'owner': owner, 'scope': 'prepare', 'state': state, 'job_handle': job, 'worktree': str(path),
                          'since': stamp(lock.stat().st_mtime if locked else packet_time),
                          'evidence': ['packet state ' + state] + (['operation lock held'] if locked else [])})
        elif locked and state is None:
            found.append({'owner': owner, 'scope': 'prepare', 'state': 'starting', 'job_handle': None, 'worktree': str(path),
                          'since': stamp(lock.stat().st_mtime), 'evidence': ['operation lock held, no packet yet']})
    try:
        lifecycle = local_state.lifecycle_lock(common)
    except local_state.LocalStateError:
        lifecycle = None  # no primary checkout, so no lifecycle lock can exist
    if lifecycle is not None and lifecycle.is_file():
        found.append({'owner': None, 'scope': 'lifecycle', 'state': 'lifecycle', 'job_handle': None, 'worktree': None,
                      'since': stamp(lifecycle.stat().st_mtime), 'evidence': ['lifecycle lock held']})
    return common, found, inspected


def age_flag(scope, since):
    try:
        opened = datetime.strptime(since, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - opened).total_seconds() > AGE_LIMITS[scope]
    except (TypeError, ValueError, KeyError):
        return False


def execution_status(root, owner=None):
    """One payload for the Coordinator, workers and Mission Control: derived evidence cross-checked with the claims.

    With an owner, the overlap and base-moved parts describe that mission only; a frozen window still shows for all.
    """
    payload = {'schema': EXECUTION_SCHEMA, 'level': 'UNKNOWN', 'line': '', 'read_at': claims.now(), 'windows': [],
               'declared_without_evidence': [], 'database': {'state': 'unreadable', 'message': ''},
               'derived': {'worktrees_inspected': 0, 'lifecycle_lock': False},
               'owner': owner, 'claims': [], 'overlaps': [], 'base_moved': [], 'stale_claims': 0, 'notes': []}
    try:
        common, evidence, inspected = derived_evidence(root)
        declared = claims.read_state(common)
        payload['database'] = {'state': declared['state'], 'message': declared['message']}
        payload['derived'] = {'worktrees_inspected': inspected,
                              'lifecycle_lock': any(e['scope'] == 'lifecycle' for e in evidence)}
        rows = [dict(d) for d in declared['windows']]
        for entry in evidence:
            match = next((d for d in rows if d['scope'] == entry['scope'] and
                          (entry['scope'] == 'lifecycle' or d['owner'] == entry['owner'])), None)
            flags = []
            command = {'pid': None, 'running': None}
            if match is not None:
                rows.remove(match)
                command = {'pid': match['pid'], 'running': claims.pid_state(match['pid'], match['pid_started'])}
                if command['running'] is False:
                    flags.append('dead_command')
                entry = dict(entry, owner=match['owner'] if entry['owner'] is None else entry['owner'],
                             job_handle=match['job_handle'] or entry['job_handle'], since=match['since'] or entry['since'],
                             worktree=match['worktree'] or entry['worktree'],
                             state=entry['state'] if entry['scope'] == 'prepare' else match['state'])
            elif declared['state'] == 'ok':
                flags.append('undeclared')
            if age_flag(entry['scope'], entry['since']):
                flags.append('age')
            payload['windows'].append(dict(entry, declared=match is not None, command=command, flags=flags,
                                           age_flag='age' in flags))
        payload['declared_without_evidence'] = [
            {'owner': d['owner'], 'scope': d['scope'], 'state': d['state'], 'since': d['since'],
             'job_handle': d['job_handle'],
             'command': {'pid': d['pid'], 'running': claims.pid_state(d['pid'], d['pid_started'])}} for d in rows]
        windows = payload['windows']
        # Path claims count only while their transaction exists; a claim whose worktree is gone is stale and ignored.
        registered = registered_owners(root) if declared['state'] == 'ok' else {}
        active = [c for c in declared.get('paths', []) if c['owner'] in registered]
        overlaps = claims.compute_overlaps(active)
        moved = claims.base_moved(active, declared.get('landed', []), declared.get('reconciled', []))
        frozen_owners = {w['owner'] for w in windows if w['owner']}
        overlap_owners = {o for overlap in overlaps for o in overlap['owners']}
        owners = []
        for claim in active:
            subclasses = sorted({t for p in claim['paths'] for t in claims.tokens(p['subclass'])})
            owners.append({'owner': claim['owner'], 'job_handle': claim['job_handle'], 'subclasses': subclasses,
                           'files': len(claim['paths']), 'since': claim['since'],
                           'flags': ['age'] if age_flag('paths', claim['since']) else []})
        for window in windows:
            if window['owner'] and not any(o['owner'] == window['owner'] for o in owners):
                owners.append({'owner': window['owner'], 'job_handle': window['job_handle'], 'subclasses': [], 'files': 0,
                               'since': window['since'], 'flags': [f for f in window['flags'] if f != 'undeclared']})
        for row in owners:
            row['level'] = 'FROZEN' if row['owner'] in frozen_owners else 'OVERLAP' if row['owner'] in overlap_owners else 'CLEAR'
        if owner is not None:
            overlaps = [o for o in overlaps if owner in o['owners']]
            moved = [m for m in moved if m['owner'] == owner]
            partners = {o for overlap in overlaps for o in overlap['owners']} | {owner}
            owners = [row for row in owners if row['owner'] in partners]
        stale = len(declared.get('paths', [])) - len(active)
        payload.update(owner=owner, claims=owners, stale_claims=stale,
                       overlaps=[dict(o, text=claims.overlap_text(o)) for o in overlaps], base_moved=moved)
        notes = ['overlap: %s with %s, %s' % (o['owners'][0], o['owners'][1], claims.overlap_text(o)) for o in overlaps]
        notes += ['base moved: main advanced to %s by %s; %s' % ((m['main'] or 'a new commit')[:12], m['by'],
                  carry_note(root, m['owner'], registered.get(m['owner']))) + (' (for ' + m['owner'] + ')' if owner is None else '')
                  for m in moved]
        if stale:
            notes.append('%d stale claim(s): a claim whose worktree is gone; report it to the lead developer, who runs clear' % stale)
        payload['notes'] = notes
        payload['level'] = 'FROZEN' if windows else 'OVERLAP' if overlaps else 'CLEAR'
        if windows:
            first = windows[0]
            payload['line'] = 'REFS FROZEN: %s, since %s%s' % (
                first['owner'] or 'lifecycle operation', first['since'],
                ' (+%d more)' % (len(windows) - 1) if len(windows) > 1 else '')
        elif overlaps:
            first = overlaps[0]
            payload['line'] = 'OVERLAP: %s with %s, %s%s' % (
                first['owners'][0], first['owners'][1], claims.overlap_text(first),
                ' (+%d more)' % (len(overlaps) - 1) if len(overlaps) > 1 else '')
        else:
            payload['line'] = 'CLEAR: no ref window is open.'
        if declared['state'] not in ('ok', 'missing'):
            payload['line'] += ' [claims database ' + declared['state'] + '; derived evidence only]'
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, sqlite3.Error) as error:
        first_line = str(error).splitlines()[0][:200] if str(error) else ''
        payload.update(level='UNKNOWN', line='STATUS UNKNOWN: ' + first_line if first_line else 'STATUS UNKNOWN')
    return payload


def registered_owners(root):
    """{owner: worktree path} of every registered, present worktree, named as the claims name their owners."""
    result = subprocess.run(['git', '-C', str(root), 'worktree', 'list', '--porcelain', '-z'], capture_output=True)
    require(result.returncode == 0, 'Git failed: worktree list')
    found = {}
    for record in result.stdout.decode().split('\0\0'):
        fields = dict(f.split(' ', 1) if ' ' in f else (f, '') for f in record.split('\0') if f)
        if 'worktree' not in fields or 'bare' in fields or 'prunable' in fields:
            continue
        reason = fields.get('locked', '')
        owner = reason.removeprefix('transaction:') if reason.startswith('transaction:') \
            else fields.get('branch', '').removeprefix('refs/heads/tx/').removeprefix('refs/heads/')
        if owner:
            found[owner] = fields['worktree']
    return found


def job_state(common, handle):
    """(status, activity) of a Job from the primary's local records, or None; never raises and never writes."""
    try:
        directory = local_jobs.safe_directory(Path(common).parent)
        record = local_jobs.read(directory / (handle.split('--')[-1] + '.json'))
        return record.get('status'), record.get('activity')
    except Exception:
        return None


# ---- The watch ------------------------------------------------------------------------------------

def packet_failures(root):
    """Packets that ended prepare-failed, read from the worktree list: the fallback when no database can be read."""
    found = []
    for owner, path in registered_owners(root).items():
        scratch = local_state.checkout_store(path)
        if scratch is not None and scratch.is_dir():
            state, job, when = read_packet_state(scratch)
            if state == 'prepare-failed':
                found.append((owner, job, stamp(when)))
    return found


def main_tip(root):
    result = subprocess.run(['git', '-C', str(root), 'rev-parse', '--verify', '-q', 'refs/heads/main'],
                            capture_output=True, text=True)
    return result.stdout.strip() or None


class Watch:
    """One line per landing or per thing that needs attention; routine events stay quiet unless asked for.

    Each poll returns the lines to print. The events table supplies landings and failures; the derived evidence and
    the Job records supply the rest. Without a readable database it falls back to packets, locks, the worktree
    list and the tip of main, and says so once.
    """

    def __init__(self, root, everything=False):
        self.root, self.everything = Path(root), everything
        self.common = derived_evidence(root)[0]
        self.last_event, self.tip, self.problem = None, None, None
        self.reported, self.current = set(), set()

    def line(self, kind, text, owner=None, job=None, at=None):
        return '%s %s: owner %s, job %s, at %s' % (kind, text, owner or '-', job or '-', at or claims.now())

    def once(self, key, text, owner, job, at=None):
        self.current.add(key)
        if key not in self.reported:
            self.reported.add(key)
            return [self.line('ATTENTION', text, owner, job, at)]
        return []

    def event_lines(self, row):
        detail, owner, name = row['detail'], row['owner'], row['event']
        if name == 'landed':
            return [self.line('LANDED', 'guarded integration finished, commit ' + (detail.get('commit') or 'unknown')[:12],
                              owner, detail.get('job_handle'), row['at'])]
        if name == 'attention':
            return [self.line('ATTENTION', detail.get('reason') or 'needs attention', owner, detail.get('job_handle'), row['at'])]
        if self.everything:
            return ['EVENT %s: owner %s, at %s, %s' % (name, owner or '-', row['at'], json.dumps(detail, sort_keys=True))]
        return []

    def step(self):
        lines, self.current = [], set()
        status = execution_status(self.root)
        events, newest, problem = claims.events_after(self.common, self.last_event)
        if problem != 'ok':
            if problem != self.problem:
                self.problem = problem
                lines.append('NOTICE: claims database ' + problem +
                             '; watching the derived evidence only (packets, locks, worktree list, tip of main)')
            tip = main_tip(self.root)
            if self.tip is not None and tip and tip != self.tip:
                lines.append(self.line('LANDED', '(inferred, claims database unavailable) main advanced %s..%s; owner and job unknown'
                                       % (self.tip[:12], tip[:12])))
            self.tip = tip or self.tip
            for owner, job, at in packet_failures(self.root):
                lines += self.once(('failed', owner, at), 'prepare failed (packet prepare-failed)', owner, job, at)
        else:
            if self.problem:
                lines.append('NOTICE: claims database ok again')
            self.problem, self.tip = None, None
            if self.last_event is not None:
                for row in events:
                    lines += self.event_lines(row)
            self.last_event = newest
        for window in status['windows']:
            if 'dead_command' in window['flags']:
                lines += self.once(('dead', window['owner'], window['scope'], window['since']),
                                   '%s window command died (pid %s is not running) and the window is still open'
                                   % (window['scope'], window['command']['pid']), window['owner'], window['job_handle'])
        for window in status['declared_without_evidence']:
            if window['command']['running'] is False:
                lines += self.once(('stale', window['owner'], window['scope'], window['since']),
                                   '%s window is declared but its command died and no transaction shows it' % window['scope'],
                                   window['owner'], window['job_handle'])
        for window in status['windows']:
            job = window['job_handle']
            state = job_state(self.common, job) if job else None
            if state and (state[0] == 'BLOCKED' or state[1] == 'STOPPED'):
                lines += self.once(('job', job, state), 'Job record is %s (activity %s) while its window is open' % state,
                                   window['owner'], job)
        self.reported &= self.current
        return lines


def watch(root, interval, everything, polls=0):
    """Print the lines of each poll at once, until interrupted (or after `polls` polls when a count is given)."""
    state, count = None, 0
    try:
        while True:
            try:
                state = state or Watch(root, everything)
                lines = state.step()
            except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError, sqlite3.Error) as error:
                lines = ['NOTICE: the watch could not read the repository: ' + (str(error).splitlines() or [''])[0][:160]]
            for line in lines:
                print(line, flush=True)
            count += 1
            if polls and count >= polls:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def wait_clear(root, timeout, owner=None, interval=WATCH_SECONDS, sleep=time.sleep, clock=time.monotonic):
    """Poll the status until the level is CLEAR or OVERLAP: exit 0, or WAIT_TIMEOUT_EXIT after `timeout` seconds.

    Read-only like the status itself. UNKNOWN and FROZEN both mean not clear; the last line says who holds the window.
    """
    deadline, announced = clock() + timeout, False
    while True:
        status = execution_status(root, owner)
        if status['level'] in CLEAR_LEVELS:
            print(status['line'], flush=True)
            if announced:
                print('execution status is clear; go on', file=sys.stderr, flush=True)
            return 0
        remaining = deadline - clock()
        if remaining <= 0:
            print(status['line'], flush=True)
            print('FAIL: execution status was not CLEAR or OVERLAP within %g s; wait and read it again, never retry blindly'
                  % timeout, file=sys.stderr, flush=True)
            return WAIT_TIMEOUT_EXIT
        if not announced:
            announced = True
            print('waiting up to %g s for CLEAR or OVERLAP: %s' % (timeout, status['line']), file=sys.stderr, flush=True)
        sleep(min(interval, remaining))


# ---- Clearing claims and the doctor: commands the lead developer runs ------------------------------------

def claim_evidence(root, common, rows):
    """Say, for each open claim, whether its transaction still exists and why: the transaction wins over the Job record."""
    registered = registered_owners(root)
    derived = derived_evidence(root)[1]
    result = []
    for row in rows:
        owner, facts = row['owner'], []
        present = owner in registered
        facts.append('worktree ' + ('registered' if present else 'not registered'))
        branch = any(subprocess.run(['git', '-C', str(root), 'show-ref', '--verify', '--quiet', ref]).returncode == 0
                     for ref in ('refs/heads/tx/' + owner, 'refs/heads/' + owner))
        facts.append('branch ' + ('exists' if branch else 'gone'))
        packet = None
        if present:
            store = local_state.checkout_store(registered[owner])
            packet = read_packet_state(store)[0] if store is not None else None
            facts.append('packet ' + (packet or 'none'))
        if row['kind'] == 'paths':
            live = present and branch
        else:
            wanted = [e for e in derived if e['scope'] == row['scope'] and (row['scope'] == 'lifecycle' or e['owner'] == owner)]
            live = bool(wanted)
            facts.append('derived window ' + ('shows it' if wanted else 'absent'))
        running = claims.pid_state(row['pid'], row['pid_started']) if row['pid'] else None
        if row['pid']:
            facts.append('command pid %s %s' % (row['pid'], {True: 'running', False: 'not running', None: 'unknown'}[running]))
            if live and running is False:
                facts.append('the derived evidence still freezes the refs: clearing the claim does not unfreeze them')
        job = job_state(common, row['job_handle']) if row['job_handle'] else None
        if row['job_handle']:
            facts.append('Job %s %s' % (row['job_handle'], job[0] if job else 'has no readable record'))
            if live and job and job[0] in ('COMPLETE', 'CANCELLED'):
                facts.append('CONFLICT: the Job record says %s while its transaction exists; the transaction wins' % job[0])
        result.append({'id': 'c%d' % row['id'], 'number': row['id'], 'owner': owner, 'kind': row['kind'], 'scope': row['scope'],
                       'state': row['state'], 'since': row['since'], 'status': 'LIVE' if live else 'STALE', 'evidence': facts})
    return result


def claim_table(rows):
    lines = []
    for r in rows:
        lines.append('%-5s %-8s %-7s %s since %s' % (r['id'], r['status'], r['kind'] if r['kind'] == 'paths' else r['scope'],
                                                     r['owner'], r['since']))
        lines.append('      ' + '; '.join(r['evidence']))
    return lines


def clear_command(root, apply, wanted, even_if_live, ask=input, interactive=None):
    """The lead developer's `clear`: list every open claim, dry run by default, and clear only what was asked for."""
    common = derived_evidence(root)[0]
    try:
        rows = claim_evidence(root, common, claims.open_claims(common))
    except claims.ClaimsError as error:
        print('FAIL: claims database %s: %s' % (error.state, error.message), file=sys.stderr)
        return 1
    known = {r['id']: r for r in rows}
    ids = []
    for name in wanted:
        key = name if name.startswith('c') else 'c' + name
        if key not in known:
            print('FAIL: no open claim ' + name + '; the open claims are: ' + (', '.join(known) or 'none'), file=sys.stderr)
            return 1
        ids.append(key)
    print('%d open claim(s); STALE means its transaction is gone, LIVE means it still exists' % len(rows))
    for line in claim_table(rows):
        print(line)
    targets = ids or [r['id'] for r in rows if r['status'] == 'STALE']
    live = [i for i in targets if known[i]['status'] == 'LIVE']
    if even_if_live and not ids:
        print('FAIL: --even-if-live needs the id of the claim to clear', file=sys.stderr)
        return 1
    if not apply:
        print('DRY RUN: would clear %s' % (', '.join(targets) or 'nothing') +
              ('; LIVE claims also need --even-if-live: ' + ', '.join(live) if live else '') +
              '. Nothing was cleared; add --apply to clear.')
        return 0
    if live and not even_if_live:
        print('FAIL: %s is LIVE (its transaction exists); add --even-if-live to clear it knowingly' % ', '.join(live), file=sys.stderr)
        return 1
    for claim_id in live:
        print('%s is LIVE: %s' % (claim_id, '; '.join(known[claim_id]['evidence'])))
        tty = sys.stdin.isatty() if interactive is None else interactive
        if not tty:
            print('FAIL: clearing a LIVE claim needs a terminal where you can type its id again', file=sys.stderr)
            return 1
        if ask('Type %s again to clear it: ' % claim_id).strip() != claim_id:
            print('FAIL: the id did not match; nothing was cleared', file=sys.stderr)
            return 1
    if not targets:
        print('Nothing to clear.')
        return 0
    try:
        cleared = claims.clear_claims(common, {known[i]['number']: {'status': known[i]['status'], 'forced': known[i]['status'] == 'LIVE',
                                                                    'evidence': known[i]['evidence']} for i in targets})
        for item in cleared:
            summary = known['c%d' % item['id']]
            print('cleared %s (%s, %s): %s' % (summary['id'], summary['owner'], summary['status'], '; '.join(summary['evidence'])))
            claims.clearing_log(common, '%s cleared %s owner=%s kind=%s status=%s forced=%s evidence=%s' % (
                claims.now(), summary['id'], summary['owner'], summary['kind'], summary['status'],
                str(summary['status'] == 'LIVE').lower(), ' | '.join(summary['evidence'])))
        print('%d claim(s) cleared' % len(cleared))
    except claims.ClaimsError as error:
        print('FAIL: claims database %s: %s' % (error.state, error.message), file=sys.stderr)
        return 1
    return 0


def doctor_command(root, repair):
    common = derived_evidence(root)[0]
    report = claims.doctor(common)
    print('claims database: %s' % report['state'])
    for item in report['checks']:
        print('  %-12s %s  %s' % (item['name'], 'ok  ' if item['ok'] else 'FAIL', item['detail']))
    for item in report['repairs']:
        print('would repair: ' + item['description'])
    if not report['repairs']:
        print('nothing to repair' if report['state'] in ('ok', 'missing') else 'no repair is available for this state')
    elif repair:
        try:
            for done in claims.repair(common, report):
                print('repaired: ' + done)
            return 0
        except (claims.ClaimsError, OSError) as error:
            print('FAIL: repair stopped: ' + str(getattr(error, 'message', error)), file=sys.stderr)
            return 1
    else:
        print('Nothing was changed; add --repair to apply exactly these repairs.')
    return 0 if report['state'] in ('ok', 'missing', 'repairable') else 1


class Worktrees(Carry):
    def guard_digest(self):
        """The digest of the guard that recorded an approval's carry or an integration; the carry mixin calls it."""
        return guard_digest()

    def __init__(self, main):
        # A per-root trust override cannot describe both roots. Never strip or broaden it.
        overrides = {k: v for k, v in os.environ.items() if k.upper().startswith('GIT_')}
        require(not overrides or overrides == {'GIT_PAGER': 'cat'},
                'Worktree lifecycle requires a clean Git environment (optional GIT_PAGER=cat)')
        self.main = Checkpoint(main)
        require(self.main.branch() == 'refs/heads/main', 'Supply the checkout of main')

    def idle(self, tool, inert=()):
        """Clean checkout and no live checkpoint scratch. `inert` names records that may remain, for removal only."""
        tool.quiet_operation()
        require(not tool.status() and not tool.inventory(), 'Checkout/index must be clean: ' + str(tool.root))
        allowed = {RECEIPT_KEY, OPERATION_LOCK, LANDING, *inert}
        require(all(p.name in allowed and p.is_file() and not p.is_symlink()
                    for p in tool.store.iterdir()), 'Checkpoint scratch not idle; finish its lifecycle first')

    def continuity(self, tool):
        # Required evidence never selects a weaker fallback when it disappears.
        # Use this root's committed parser, independently of the launched guard's imports.
        path = tool.root / artifact_classification.CONTINUITY_CHECKER
        require(path.is_file() and not path.is_symlink(), 'Destination continuity checker missing/indirect')
        return self.candidate_continuity(tool, digest(path.read_bytes()))

    def candidate_continuity(self, source, expected_hash, target=None):
        target = source if target is None else target
        path = source.root / artifact_classification.CONTINUITY_CHECKER
        require(path.is_file() and not path.is_symlink(), 'Candidate continuity checker missing/indirect')
        data = path.read_bytes()
        require(re.fullmatch(r'[0-9a-f]{64}', expected_hash or '') and digest(data) == expected_hash,
                'Candidate validator requires its explicitly reviewed exact SHA-256')
        # Clean checkout alone permits ignored substitutions; require the committed blob too.
        require(data.replace(b'\r\n', b'\n') == source.git('show', 'HEAD:' + artifact_classification.CONTINUITY_CHECKER),
                'Candidate validator differs from its committed bytes')
        # The validator reads the classification table through the parser that sits beside it.
        parser = source.root / artifact_classification.CLASSIFICATION_PARSER
        if parser.exists():
            require(parser.is_file() and not parser.is_symlink() and
                    parser.read_bytes().replace(b'\r\n', b'\n') ==
                    source.git('show', 'HEAD:' + artifact_classification.CLASSIFICATION_PARSER),
                    'Candidate classification parser differs from its committed bytes')
        result = source.runner([sys.executable, '-I', '-B', '-c', CANDIDATE_CONTINUITY,
                                str(path), str(target.root)], target.root)
        require(result['exit_code'] == 0, 'Candidate continuity failed: ' + result['stderr'])
        require(digest(path.read_bytes()) == expected_hash, 'Candidate validator changed during execution')
        return result

    def shared_state(self, owner_branch=''):
        # Only main advances. The one protected-ref definition decides what else must stay exact; this site adds the configuration.
        return dict(self.main.ref_snapshot(include_main=False, owner=owner_branch), configuration=self.main.configuration())

    def tooling_authority(self, source):
        """The reviewed committed guard owns promotion, never a candidate re-exec."""
        driver, driver_hash = checkpoint_driver()
        paths = [*guard_paths(), *driver,
                 Path(sys.modules['governance.tools.lifecycle.commit_metadata'].__file__).resolve(), Path(claims.__file__).resolve()]
        roots = {tools_root(p) for p in paths}
        require(len(roots) == 1, 'Mixed integration/checkpoint tooling roots')
        root = roots.pop()
        require(root in (self.main.root, source.root), 'Guard must run from the reviewed main or candidate checkout')
        tool = self.main if root == self.main.root else source
        for path in paths:
            name = path.relative_to(root).as_posix()
            require(not path.is_symlink() and path.read_bytes().replace(b'\r\n', b'\n') ==
                    tool.git('show', 'HEAD:' + name), 'Integration guard differs from reviewed committed tooling')
        return {'root': str(root), 'revision': tool.head(),
                'guard_sha256': guard_digest(),
                'checkpoint_sha256': driver_hash,
                'metadata_sha256': digest(paths[-2].read_bytes()), 'claims_sha256': digest(paths[-1].read_bytes())}

    def exact(self, tool, expected):
        if tool.root == self.main.root:
            require(tool.branch() == 'refs/heads/main', 'Integration checkout is no longer on main')
        require(re.fullmatch(r'[0-9a-f]{40}', expected) and tool.head() == expected,
                'Expected full HEAD does not match; inspect and review again')

    def owner(self, owner):
        require(re.fullmatch(MISSION_ID, owner), 'Use a unique lowercase mission ID')
        return 'refs/heads/tx/' + owner

    def source(self, path, owner):
        branch = self.owner(owner)
        source = Checkpoint(path)
        require(source.root != self.main.root and source.common == self.main.common,
                'Source must be a distinct worktree of this repository')
        require(source.branch() == branch, 'Branch does not belong to the supplied mission')
        rows = [r for r in inventory(self.main) if r.get('branch') == branch]
        require(len(rows) == 1 and Path(rows[0]['worktree']).resolve() == source.root
                and rows[0].get('locked') == 'transaction:' + owner,
                'Missing, duplicate or conflicting worktree ownership')
        return source

    def hooks(self):
        t = self.main
        hookdir = Path(t.git('rev-parse', '--git-path', 'hooks').decode().strip())
        hookdir = (t.root / hookdir).resolve()
        require(not any((hookdir / name).exists() for name in
                        ('post-checkout', 'pre-merge-commit', 'post-merge', 'reference-transaction')),
                'Active worktree/integration hooks require separate review')

    def create(self, path, owner, expected_main):
        branch = self.owner(owner)
        paths = layout(self.main.root, owner)
        target = Path(paths['worktree']).absolute()
        # A transition option: an explicit path may only restate the derived one, never place a worktree elsewhere.
        require(path is None or Path(path).absolute() == target,
                'Worktrees live in the container; omit --path or use ' + str(target))
        require(not target.exists() and not target.is_symlink(), 'New worktree requires an absent path')
        require(target == target.resolve(), 'Symlink/indirect worktree path prohibited')
        with lifecycle_lock(self.main, 'create', owner), self.main.lock(_lifecycle=True):
            self.idle(self.main)
            self.exact(self.main, expected_main)
            self.hooks()
            self.continuity(self.main)
            require(not self.main.git('for-each-ref', '--format=%(refname)', branch),
                    'Branch already exists; never reuse or reset another mission')
            require(all(not target.is_relative_to(Path(r['worktree']).resolve()) and
                        not Path(r['worktree']).resolve().is_relative_to(target)
                        for r in inventory(self.main)), 'Worktrees must not nest')
            self.idle(self.main)
            self.exact(self.main, expected_main)
            self.hooks()
            # Only the container folders are made, and only after every guard has passed.
            Path(paths['review']).parent.mkdir(parents=True, exist_ok=True)
            self.main.git('worktree', 'add', '--lock', '--reason', 'transaction:' + owner,
                          '-b', branch.removeprefix('refs/heads/'), str(target), expected_main)
            source = self.source(target, owner)
            self.exact(source, expected_main)
            return {'worktree': str(source.root), 'review': paths['review'], 'branch': branch, 'base': expected_main}

    def reconcile_index(self, source, packet, new):
        """Compute the exact successor index without touching the private live index."""
        with tempfile.TemporaryDirectory(prefix='reconcile-', dir=source.gitdir) as folder:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(folder) / 'index'))
            def git(*args, data=None):
                result = subprocess.run(['git', '-C', str(source.root), '--literal-pathspecs', *args],
                                        env=env, input=data, capture_output=True)
                require(result.returncode == 0, 'Cannot construct reconciliation index: ' +
                        result.stderr.decode('utf-8', 'replace'))
                return result.stdout
            git('read-tree', new)
            entries = {e.split(b'\t', 1)[1].decode(): e for e in
                       source.git('ls-files', '--stage', '-z').split(b'\0') if e}
            data = b''.join(entries.get(n, b'0 ' + b'0' * 40 + b'\t' + n.encode()) + b'\0'
                            for n in packet['manifest'])
            git('update-index', '-z', '--index-info', data=data)
            return {'tree': git('write-tree').decode().strip(),
                    'index': digest(git('ls-files', '--stage', '-z'))}

    def reconcile(self, path, owner, expected_old_main, expected_main):
        """Bounded base update of an uncommitted, authenticated owned transaction."""
        require(all(re.fullmatch(r'[0-9a-f]{40}', s) for s in (expected_old_main, expected_main)),
                'Reconciliation requires full old/new main SHAs')
        with lifecycle_lock(self.main, 'reconcile', owner):
            source = self.source(path, owner)
            ref_audits = []
            with self.main.lock(_lifecycle=True), source.lock(_lifecycle=True):
                self.idle(self.main)
                self.exact(self.main, expected_main)
                self.hooks()
                source.quiet_operation()
                packet = source.assert_pinned(source.load())
                name = 'reconcile-' + expected_old_main + '-' + expected_main + '.json'
                journal = source.store / name
                allowed = {TRANSACTION, RECEIPT_KEY, OPERATION_LOCK,
                           *packet.get('reconciliation_audits', [])}
                allowed.add(name)
                allowed.add('stale-' + digest((source.store / TRANSACTION).read_bytes()) + '.json')
                if journal.exists():
                    allowed.add(source.load_path(journal)['archive'])
                require(all(p.name in allowed and p.is_file() and not p.is_symlink()
                            for p in source.store.iterdir()), 'Unexpected checkpoint scratch')
                if journal.exists():
                    plan = source.load_path(journal)
                    require(plan['old'] == expected_old_main and plan['new'] == expected_main,
                            'Reconciliation journal range mismatch')
                    archive = source.store / plan['archive']
                    require(digest(archive.read_bytes()) == plan['original_sha256'],
                            'Stale packet audit changed')
                    if packet.get('reconciliation') == name:
                        source.assert_prepared(packet)
                        check_ref_context(plan['after_context'], source.reconciliation_context(), 'Unexpected refs/worktrees after reconciliation', ref_audits)
                        return {'state': 'reconciled', 'old_main': expected_old_main,
                                'main': expected_main, 'audit': name, 'idempotent': True, 'transient_ref_audits': ref_audits}
                else:
                    require(packet['state'] in ('preparing', 'prepare-failed', 'validated', 'prepared', 'approved'),
                            'Only uncommitted transactions may reconcile')
                    require(packet['parent'] == expected_old_main, 'Expected old base differs from packet')
                    source.assert_prepared(packet)
                    # The residual taken before staging lists a brand-new directory as one untracked entry, which
                    # never matches a manifest path; the status taken after staging lists its files one by one.
                    residual = source.residual(packet['manifest'], packet['prepared_status']) \
                        if packet.get('prepared_status') is not None else packet['residual']
                    require(not residual, 'Unrelated dirty state requires human review')
                    check_ref_context(packet.get('reconciliation_context'), source.reconciliation_context(), 'Unexpected refs/worktrees since preparation', ref_audits)
                    if expected_old_main == expected_main:
                        return {'state': 'current', 'main': expected_main, 'transient_ref_audits': ref_audits}
                    source.git('merge-base', '--is-ancestor', expected_old_main, expected_main)
                    commits = source.git('rev-list', '--reverse', expected_old_main + '..' + expected_main)
                    delta = set()
                    for sha in commits.decode().splitlines():
                        require(len(source.git('rev-list', '--parents', '-n', '1', sha).split()) == 2,
                                'Nonlinear upstream range requires human review')
                        delta.update(n.decode() for n in source.git('diff-tree', '--no-commit-id',
                                     '--name-only', '--no-renames', '-r', '-z', sha).split(b'\0') if n)
                    def overlaps(a, b):
                        a, b = a.casefold(), b.casefold()
                        return a == b or a.startswith(b + '/') or b.startswith(a + '/')
                    require(not any(overlaps(a, b) for a in delta for b in packet['manifest']),
                            'Upstream delta overlaps owned manifest; human review required')
                    require(not any(Path(n).name in ('.gitattributes', '.gitignore') for n in delta),
                            'Upstream checkout rules require human review')
                    require(not delta.intersection({POLICY, PROFILES, artifact_classification.PROJECT_POLICY,
                                                    artifact_classification.PROJECT_PROFILES,
                                                    *artifact_classification.CHECKPOINT_DRIVER,
                                                    *artifact_classification.WORKTREE_GUARD, 'AGENTS.md',
                                                    'governance/CONSTRAINTS.md', 'governance/DECISIONS.md',
                                                    'governance/WORKTREE-WORKFLOW.md',
                                                    'governance/CHECKPOINT-WORKFLOW.md',
                                                    'governance/CHECKPOINT-TOOL.md'}),
                            'Upstream transaction authority requires human review')
                    attributes = source.git('check-attr', '-z', 'filter', '--',
                                            *sorted(delta | set(packet['manifest']))).split(b'\0')
                    require(not source.configured('core.sparseCheckout') and
                            all(v in (b'unspecified', b'unset') for v in attributes[2::3]),
                            'Sparse checkout/custom filters require human review')
                    working = dict(packet['working'])
                    untracked = {n.decode() for n in source.git('ls-files', '--others', '-z').split(b'\0') if n}
                    for n in sorted(delta):
                        require(not any(c in n for c in '\\:\x00\n\r\t') and
                                not any(p.casefold() in packet['policy']['prohibited_components']
                                        for p in Path(n).parts) and
                                (source.root / n).resolve().is_relative_to(source.root) and
                                not any((source.root / n).parents[i].is_symlink()
                                        for i in range(len(Path(n).parts) - 1)), 'Indirect/unsupported upstream path')
                        require(not any(overlaps(n, p) for p in untracked),
                                'Upstream delta collides with untracked/ignored content')
                        row = source.git('ls-tree', expected_main, '--', n).decode().strip()
                        if not row:
                            if n in source.bound_paths(packet['policy']):
                                working[n] = None
                            else:
                                working.pop(n, None)
                            continue
                        require(row.startswith(('100644 blob ', '100755 blob ')) and '\t' + n == row[row.index('\t'):],
                                'Upstream nonregular path requires human review')
                        require(not (source.root / n).is_symlink(), 'Indirect upstream checkout path')
                        working[n] = digest(source.git('cat-file', '--filters', '--path=' + n, expected_main + ':' + n))
                    successor = self.reconcile_index(source, packet, expected_main)
                    after = copy.deepcopy(packet['reconciliation_context'])
                    branch = packet['branch']
                    after['refs'] = [branch + ' ' + expected_main if r == branch + ' ' + expected_old_main
                                     else r for r in after['refs']]
                    rows = after['worktrees'].split('\0\0')
                    after['worktrees'] = '\0\0'.join(row.replace('HEAD ' + expected_old_main, 'HEAD ' + expected_main)
                            if 'branch ' + branch in row.split('\0') else row for row in rows)
                    raw = (source.store / TRANSACTION).read_bytes()
                    archive = 'stale-' + digest(raw) + '.json'
                    plan = {'repo': source.identity(), 'state': 'stale-base',
                            'old': expected_old_main, 'new': expected_main,
                            'commits': commits.decode().splitlines(), 'paths': sorted(delta),
                            'delta_sha256': digest(source.git('diff', '--binary', '--no-ext-diff', '--no-textconv',
                                                            expected_old_main, expected_main)),
                            'archive': archive, 'original_sha256': digest(raw),
                            'before_context': packet['reconciliation_context'], 'after_context': after,
                            'configuration': source.configuration(), 'successor': successor, 'working': working,
                            'transient_ref_audits': list(ref_audits)}
                    self.continuity(self.main)
                    source.assert_prepared(packet)
                    self.idle(self.main)
                    self.exact(self.main, expected_main)
                    self.source(path, owner)
                    self.hooks()
                    check_ref_context(plan['before_context'], source.reconciliation_context(), 'Shared state changed during range inspection', ref_audits)
                    plan['transient_ref_audits'] = list(ref_audits)
                    exclusive_evidence(source.store / archive, raw)
                    # The authenticated journal precedes every live index/ref mutation.
                    source.ensure_key()
                    body = canonical(plan)
                    import hmac, hashlib
                    exclusive_evidence(journal, canonical({'packet': plan,
                        'mac': hmac.new(source.key(), body, hashlib.sha256).hexdigest()}))
                require(source.configuration() == plan['configuration'], 'Reconciliation configuration changed')
                original = source.load_path(source.store / plan['archive'])
                require(canonical(packet) == canonical(original), 'Intermediate packet changed')
                head = source.head()
                require(head in (expected_old_main, expected_main), 'Unexpected intermediate HEAD')
                require(not journal.is_symlink(), 'Indirect reconciliation journal')
                index = digest(source.git('ls-files', '--stage', '-z'))
                if index == original['index'] and head == expected_old_main:
                    source.assert_prepared(original)
                    check_ref_context(plan['before_context'], source.reconciliation_context(), 'Intermediate shared state changed', ref_audits)
                    source.git('read-tree', '-m', '-u', expected_old_main, expected_main)
                require(digest(source.git('ls-files', '--stage', '-z')) == plan['successor']['index'] and
                        source.git('write-tree').decode().strip() == plan['successor']['tree'] and
                        source.snapshot(original['manifest'], original['policy']) == plan['working'] and
                        sorted(n.decode() for n in source.git('diff', '--cached', '--name-only',
                               '--no-renames', '-z', expected_main).split(b'\0') if n) == original['manifest'] and
                        digest(Path(original['message_file']).read_bytes()) == original['message_sha256'],
                        'Intermediate index/working bytes/message drift; inspect without rollback')
                self.idle(self.main)
                self.exact(self.main, expected_main)
                self.source(path, owner)
                self.hooks()
                check_ref_context(plan['before_context' if head == expected_old_main
                                                               else 'after_context'], source.reconciliation_context(), 'Intermediate shared state changed', ref_audits)
                if head == expected_old_main:
                    source.git('update-ref', original['branch'], expected_main, expected_old_main)
                check_ref_context(plan['after_context'], source.reconciliation_context(), 'Post-reconciliation shared state changed', ref_audits)
                packet = copy.deepcopy(original)
                packet['transient_ref_audits'] = [*packet.get('transient_ref_audits', []),
                    *plan.get('transient_ref_audits', []), *ref_audits]
                for key in ('approval', 'error', 'actual_inventory'):
                    packet.pop(key, None)
                packet.update(parent=expected_main, state='validated', **plan['successor'],
                              working=plan['working'], reconciliation=name,
                              reconciliation_context=plan['after_context'],
                              reconciliation_audits=[*packet.get('reconciliation_audits', []), plan['archive'], name])
                if 'validation_ref_context' in packet:
                    packet['validation_ref_context'] = {k: v for k, v in plan['after_context'].items()
                                                        if k != 'worktrees'}
                packet['stat'] = source.git('diff', '--cached', '--stat', '--no-renames').decode()
                packet['diff'] = source.git('diff', '--cached', '--no-ext-diff', '--no-textconv', '--binary').decode('utf-8', 'replace')
                source.assert_prepared(packet)
                source.save(packet)
                return {'state': 'reconciled', 'old_main': expected_old_main, 'main': expected_main,
                        'commits': plan['commits'], 'paths': plan['paths'], 'audit': name,
                        'next': 'seal, review fresh evidence, obtain fresh G018 exact approval'}

    def integrate(self, path, owner, expected_main, expected_tip, candidate_validator_sha256=None, job_handle=None):
        early_approval.assert_landing_pin(self.source(path, owner))  # At the start: before the lock is taken.
        with lifecycle_lock(self.main, 'integrate', owner, job_handle):
            source = self.source(path, owner)
            ref_audits = []
            with self.main.lock(_lifecycle=True), source.lock(_lifecycle=True):
                shared = self.shared_state(source.branch())
                for t, sha in ((self.main, expected_main), (source, expected_tip)):
                    self.idle(t)
                    self.exact(t, sha)
                self.hooks()
                early_approval.assert_integration(source, self.main, expected_tip)  # Switches read from main's tip.
                try:
                    self.main.git('merge-base', '--is-ancestor', expected_main, expected_tip)
                except ValueError as error:
                    on, _ = early_approval.carry_switch(self.main)
                    raise ValueError(str(error) + '\nMain is not an ancestor of the source tip. ' + (
                        'diff_bound_approval is on: when the branch is finished and approved, `worktree.py carry` may carry its '
                        'approval to the new main; otherwise' if on else
                        'diff_bound_approval is off, so') + ' prepare a new reconciliation mission and take a fresh Approve.') from error
                self.continuity(self.main)  # Destination validated with destination-compatible tooling.
                candidate_result = self.candidate_continuity(source, candidate_validator_sha256)
                # Recheck after validation; no auto-stash, merge commit, rebase or conflict resolver.
                for t, sha in ((self.main, expected_main), (source, expected_tip)):
                    self.idle(t)
                    self.exact(t, sha)
                self.source(path, owner)
                self.hooks()
                check_ref_context(shared, self.shared_state(source.branch()), 'Shared refs/stash/configuration changed during validation', ref_audits)
                self.main.git('merge', '--ff-only', '--no-autostash', '--no-edit', expected_tip)
                self.exact(self.main, expected_tip)
                self.idle(self.main)
                # Destination now has candidate layout; validate with the same reviewed checker.
                candidate_result = self.candidate_continuity(source, candidate_validator_sha256, self.main)
                check_ref_context(shared, self.shared_state(source.branch()), 'Shared refs/stash/configuration changed during integration', ref_audits)
                self.exact(source, expected_tip)
                self.idle(source)
                self.source(path, owner)
                return {'state': 'integrated', 'main': expected_tip, 'transient_ref_audits': ref_audits,
                        'candidate_validator_sha256': candidate_validator_sha256,
                        'candidate_evidence': candidate_result,
                        'guard_sha256': guard_digest(),
                        'checkpoint_sha256': checkpoint_driver()[1]}

    def superseded(self, source, owner, expected_main, expected_tip, replacement):
        """The proof that an unintegrated tip was superseded (G048), or a refusal: the replacement commit is on main and
        its landing is recorded under the old mission's own Job, which its landing record and its claims name. The old
        packet is not live because removal requires idle scratch, and `source` proves the owner. Reads only."""
        require(re.fullmatch(r'[0-9a-f]{40}', replacement or ''),
                '--superseded-by needs the full id of the landed replacement commit')
        try:
            self.main.git('merge-base', '--is-ancestor', expected_tip, expected_main)
        except ValueError:
            pass
        else:
            raise ValueError('The tip %s is on main; remove it without --superseded-by' % expected_tip)
        try:
            self.main.git('merge-base', '--is-ancestor', replacement, expected_main)
        except ValueError as error:
            raise ValueError('The replacement %s is not on main; the superseded mission stays until it lands'
                             % replacement) from error
        landing = early_approval.read_landing(source) or {}
        try:
            handles, landings = superseding_claims(self.main.common, owner, replacement)
        except claims.ClaimsError as error:
            raise ValueError('The claims database cannot prove the landing (%s: %s); nothing was removed'
                             % (error.state, error.message)) from error
        jobs = {value.split('--')[-1] for value in handles | {landing.get('job_handle')} if value}
        require(len(jobs) == 1, 'The superseded mission must name exactly one Job in its landing record and claims; found '
                + (', '.join(sorted(jobs)) or 'none'))
        job = jobs.pop()
        landed = [event for event in landings if event.get('owner') != owner
                  and isinstance(event['detail'].get('job_handle'), str)
                  and event['detail']['job_handle'].split('--')[-1] == job]
        require(landed, 'No landing of %s is recorded under the Job %s; only a replacement for the same Job supersedes '
                'a mission' % (replacement, job))
        return {'job_handle': job, 'replacement': replacement, 'replacement_owner': landed[0]['owner'],
                'old_tip': expected_tip, 'subject': self.main.git('log', '-1', '--format=%s', expected_tip).decode().strip()}

    def remove(self, path, owner, expected_main, expected_tip, superseded_by=None):
        with lifecycle_lock(self.main, 'remove', owner):
            source = self.source(path, owner)
            # source.lock is released before native removal; the shared lifecycle lock stays held.
            with self.main.lock(_lifecycle=True), source.lock(_lifecycle=True):
                # The go-attempt record of an abandoned prepare is spent and authorizes nothing; the worktree is going.
                inert = (EARLY_USES,)
                for t, sha in ((self.main, expected_main), (source, expected_tip)):
                    self.idle(t, inert if t is source else ())
                    self.exact(t, sha)
                    self.continuity(t)
                if superseded_by is None:
                    self.main.git('merge-base', '--is-ancestor', expected_tip, expected_main)
                    proof = None
                else:  # The one path that deletes an unmerged branch, and only with every proof (G048).
                    proof = self.superseded(source, owner, expected_main, expected_tip, superseded_by)
                # .gitignore is the single declaration of disposable local state. A mission worktree starts
                # without any dirty or ignored files, so whatever is ignored here was generated here; the
                # checkpoint store (in the worktree's Git directory, not in its tree) is checked separately by idle().
                # The result reports what is deleted.
                ignored = sorted(p for p in set(source.git('ls-files', '--others', '--ignored', '--exclude-standard',
                                                           '-z').decode().split('\0'))
                                 if p)
                self.source(path, owner)
                self.hooks()
                self.idle(self.main)
                self.idle(source, inert)
                self.exact(self.main, expected_main)
                self.exact(source, expected_tip)
                # Local coordination notes are the one non-disposable ignored namespace.
                # Move pending notes to primary and prune terminal jobs before native removal.
                local = source.root / '.local'
                require(not local.is_symlink() and not local.is_junction() and
                        (not local.exists() or (local.is_dir() and not any(
                            not p.is_dir() or p.is_symlink() or p.is_junction()
                            for p in local.rglob('*')))),
                        'Local coordination remains; transfer/prune .local before worktree removal')
                # The mission's own review folder dies with its worktree: checked here, deleted last.
                review = Path(layout(self.main.root, owner)['review'])
                files = review_inputs(review)
                if proof:  # Traceable before anything goes: a Job that cannot take the note refuses the removal.
                    local_jobs.note(local_jobs.safe_directory(self.main.root), proof['job_handle'], 'finding',
                                    'worktree.py remove --superseded-by (G048)', superseded_note(owner, proof))
            self.main.git('worktree', 'unlock', str(source.root))
            try:
                self.main.git('worktree', 'remove', str(source.root))
            except ValueError:
                self.main.git('worktree', 'lock', '--reason', 'transaction:' + owner, str(source.root))
                raise
            self.exact(self.main, expected_main)
            require(self.main.git('rev-parse', 'refs/heads/tx/' + owner).decode().strip() == expected_tip,
                    'Source branch changed during removal; retain it for review')
            if proof:  # Compare and swap: only the proven tip is deleted, and never by `branch -D`.
                self.main.git('update-ref', '-d', 'refs/heads/tx/' + owner, expected_tip)
            else:
                self.main.git('branch', '-d', 'tx/' + owner)
            result = {'state': 'removed', 'worktree': str(source.root), 'branch': 'tx/' + owner,
                      'ignored_files_deleted': {'count': len(ignored), 'sample': ignored[:20]},
                      'review_folder': remove_review(review, files)}
            if proof:
                result['superseded_by'] = proof
            return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main', required=True, type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    p = sub.add_parser('paths', help='print the container, workspace and review folder of a mission; read-only')
    p.add_argument('--owner', required=True)
    p = sub.add_parser('execution-status', help='read-only: the level (CLEAR, OVERLAP, FROZEN), the open windows, the claims and how derived evidence and the claims agree; always exits 0')
    p.add_argument('--json-only', action='store_true', help='print the JSON payload without the leading human line')
    p.add_argument('--owner', help='describe the overlap and base-moved notes of this mission only; a frozen window still shows for all')
    p.add_argument('--watch', action='store_true',
                   help='print one flushed line per landing (LANDED) or per thing that needs attention (ATTENTION) and nothing else, until interrupted')
    p.add_argument('--wait-clear', action='store_true',
                   help='poll until the level is CLEAR or OVERLAP (exit 0, one human line); after --timeout exit %d with the line naming who holds the window; UNKNOWN is not clear' % WAIT_TIMEOUT_EXIT)
    p.add_argument('--timeout', type=float, default=WAIT_SECONDS, help='with --wait-clear: seconds to wait (default %d)' % WAIT_SECONDS)
    p.add_argument('--all', action='store_true', help='with --watch: also print every routine event, for diagnosing')
    p.add_argument('--interval', type=float, default=WATCH_SECONDS, help='with --watch: seconds between polls (default %d)' % WATCH_SECONDS)
    p.add_argument('--polls', type=int, default=0, help='with --watch: stop after this many polls (default 0: run until interrupted)')
    p = sub.add_parser('clear', help='the lead developer runs this in a terminal: list every open claim as STALE or LIVE; a dry run unless --apply')
    p.add_argument('ids', nargs='*', help='claim ids such as c7; with --apply clears exactly these, otherwise every STALE claim')
    p.add_argument('--apply', action='store_true', help='clear what the dry run listed (all STALE claims, or exactly the named ids)')
    p.add_argument('--even-if-live', action='store_true', help='allow a claim whose transaction still exists: prints the evidence and asks for its id typed again')
    p = sub.add_parser('doctor', help='check the claims database (schema, integrity, orphan rows) and say what a repair would do')
    p.add_argument('--repair', action='store_true', help='apply exactly the repairs listed')
    for action in ('create', 'integrate', 'remove', 'reconcile', 'carry'):
        p = sub.add_parser(action, help='carry the approval of a finished, committed branch onto a moved main (diff-bound approval); '
                           'changes nothing unless every gate holds' if action == 'carry' else None)
        p.add_argument('--path', required=action != 'create', type=Path)
        p.add_argument('--owner', required=True)
        p.add_argument('--expected-main', required=True)
        if action in ('integrate', 'remove', 'carry'):
            p.add_argument('--expected-tip', required=True)
        if action == 'reconcile':
            p.add_argument('--expected-old-main', required=True)
        if action == 'integrate':
            p.add_argument('--candidate-validator-sha256')
        if action == 'remove':
            p.add_argument('--superseded-by', help='full id of the landed commit that replaced this unintegrated mission '
                           'for the same Job; removes it only when every proof holds (G048)')
        if action in ('integrate', 'carry'):
            p.add_argument('--job-handle', help='Declared handle of the Job being delivered; names it in the LANDED line')
    p = sub.add_parser('carry-go', help='carry the go given at a preview, or at a staged transaction, onto a moved main (diff-bound '
                       'approval, slice 2); changes nothing unless every gate holds')
    p.add_argument('--path', required=True, type=Path)
    p.add_argument('--owner', required=True)
    p.add_argument('--expected-main', required=True)
    p.add_argument('--go', required=True, type=Path, help='the go record written by preview-go (or by an earlier carry-go)')
    p.add_argument('--message', required=True, type=Path, help='the message file of the preview')
    p.add_argument('--worker-context', type=Path, help='the worker context file of the preview')
    p.add_argument('--output', required=True, type=Path, help='a new file for the carried go')
    p.add_argument('--job-handle', help='Declared handle of the Job being delivered; names it in the lifecycle window')
    args = parser.parse_args()
    if args.action == 'execution-status':
        if args.wait_clear and (args.watch or args.json_only):
            parser.error('--wait-clear cannot be combined with --watch or --json-only')
        if args.timeout != WAIT_SECONDS and not args.wait_clear:
            parser.error('--timeout needs --wait-clear')
        if args.timeout < 0:
            parser.error('--timeout must not be negative')
        if args.wait_clear:
            try:
                return wait_clear(args.main, args.timeout, args.owner)
            except KeyboardInterrupt:
                return 130
        if args.watch:
            try:
                return watch(args.main, max(args.interval, 0.05), args.all, args.polls)
            except (ValueError, OSError, subprocess.SubprocessError) as error:
                print('FAIL: ' + str(error), file=sys.stderr)
                return 1
        status = execution_status(args.main, args.owner)
        if not args.json_only:
            print(status['line'])
            for note in status['notes']:
                print(note)
        print(json.dumps(status, indent=2))
        return 0
    if args.action in ('clear', 'doctor'):
        try:
            if args.action == 'clear':
                return clear_command(args.main, args.apply, args.ids, args.even_if_live)
            return doctor_command(args.main, args.repair)
        except (ValueError, OSError, subprocess.SubprocessError) as error:
            print('FAIL: ' + str(error), file=sys.stderr)
            return 1
    try:
        if args.action == 'paths':
            print(json.dumps(layout(args.main, args.owner), indent=2))
            return 0
        flow = Worktrees(args.main)
        if args.action in ('integrate', 'carry', 'carry-go'):
            source = flow.source(args.path, args.owner)
            authority = flow.tooling_authority(source)
        params = [args.path, args.owner, args.expected_main]
        if args.action in ('integrate', 'remove', 'carry'):
            params.append(args.expected_tip)
        if args.action == 'reconcile':
            params = [args.path, args.owner, args.expected_old_main, args.expected_main]
        if args.action == 'integrate':
            params += [args.candidate_validator_sha256, args.job_handle]
        if args.action == 'remove':
            params.append(args.superseded_by)
        if args.action == 'carry':
            params.append(args.job_handle)
        if args.action == 'carry-go':
            params += [args.go, args.message, args.worker_context, args.output, args.job_handle]
        if args.action in ('integrate', 'carry', 'carry-go') and args.job_handle is not None:
            local_jobs.job_reference(args.job_handle)
        result = getattr(flow, args.action.replace('-', '_'))(*params)
        common = flow.main.common
        # Best effort and after the operation: what the claims should say once it has succeeded.
        if args.action == 'integrate':
            result['tooling_authority'] = authority
            claims.attempt(lambda: claims.landed(common, args.owner, args.job_handle, args.expected_tip))
        elif args.action == 'remove':
            claims.attempt(lambda: claims.release_paths(common, args.owner, 'removed'))
        elif args.action == 'reconcile':
            claims.attempt(lambda: claims.note_event(common, args.owner, 'reconciled', main=args.expected_main))
        elif args.action in ('carry', 'carry-go'):
            result['tooling_authority'] = authority
            claims.attempt(lambda: claims.note_event(common, args.owner, 'reconciled', main=args.expected_main, via=args.action))
            print(result.pop('notice'))
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print('FAIL: ' + str(error) + '\nState is preserved; inspect before any recovery.', file=sys.stderr)
        if args.action == 'integrate':
            print(json.dumps({'action': 'integrate', 'expected_main': args.expected_main,
                              'expected_tip': args.expected_tip, 'owner': args.owner,
                              'candidate_validator_sha256': args.candidate_validator_sha256,
                              'guard_sha256': guard_digest(),
                              'checkpoint_sha256': checkpoint_driver()[1]}),
                  file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
