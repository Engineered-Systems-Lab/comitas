"""Claims store: ref windows and path claims the tools write themselves; Python 3.13+, standard library only.

Best effort and advisory. The database is never a gate on starting, finishing or committing a Job: every
failure is reported as a loud notice and the callers carry on with the derived evidence (packets, locks and
the worktree list), which is the evidence that cannot lie. A claim is a report, not proof.
"""
import sqlite3
import sys

if sys.version_info < (3, 13):
    sys.exit('FAIL: governance/tools/claims.py requires Python 3.13+; detected Python '
             + '.'.join(str(part) for part in sys.version_info[:3]))

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

# MAJOR.MINOR, kept in PRAGMA user_version as MAJOR * 1000 + MINOR. A minor adds fields and is migrated
# additively; a different major is incompatible and leaves the file untouched.
SCHEMA_MAJOR, SCHEMA_MINOR = 1, 1
FOLDER = ('.local', 'claims')
DATABASE = 'claims.sqlite'
CLEARING_LOG = 'clearing.log'
BUSY_SECONDS = 5
WINDOW_ACTIONS = ('prepare', 'seal', 'resume')
CLOSING_ACTIONS = ('abandon', 'finish', 'commit', 'approve-consent', 'approve')

TABLES = (
    '''CREATE TABLE claims (
        id INTEGER PRIMARY KEY,
        kind TEXT NOT NULL,
        scope TEXT NOT NULL,
        owner TEXT NOT NULL,
        job_handle TEXT,
        state TEXT NOT NULL,
        since TEXT NOT NULL,
        worktree TEXT,
        pid INTEGER,
        pid_started TEXT,
        closed_at TEXT)''',
    "CREATE UNIQUE INDEX one_open_claim ON claims(kind, scope, owner) WHERE closed_at IS NULL",
    '''CREATE TABLE events (
        id INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        claim_id INTEGER,
        owner TEXT,
        event TEXT NOT NULL,
        detail TEXT)''',
)
# Additive steps by minor version: {(major, minor): [statements]}. 1.1 adds the path claims of slice two.
MIGRATIONS = {
    (1, 1): [
        '''CREATE TABLE claim_paths (
            claim_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            class TEXT,
            subclass TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (claim_id, path))''',
        "CREATE INDEX claim_paths_path ON claim_paths(path)",
    ],
}

_noticed = set()


class ClaimsError(Exception):
    """A database state that callers report and survive: missing, locked, unreadable, corrupt, refused or schema_incompatible."""

    def __init__(self, state, message):
        super().__init__(message)
        self.state = state
        self.message = message


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def notice(state, message):
    """One loud line per distinct failure and process; never raises."""
    key = (state, message)
    if key not in _noticed:
        _noticed.add(key)
        try:
            print('NOTICE: claims database ' + state + ': ' + message + '; continuing on derived evidence',
                  file=sys.stderr, flush=True)
        except Exception:
            pass


def classify(error):
    if isinstance(error, ClaimsError):
        return error
    text = str(error).lower()
    if isinstance(error, sqlite3.OperationalError):
        if 'locked' in text or 'busy' in text:
            return ClaimsError('locked', 'locked past the ' + str(BUSY_SECONDS) + ' s timeout')
        return ClaimsError('unreadable', str(error))
    if isinstance(error, sqlite3.DatabaseError):
        return ClaimsError('corrupt', str(error))
    return ClaimsError('unreadable', str(error))


def indirect(path):
    return path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir())


def primary_root(common):
    common = Path(common)
    if common.name != '.git':
        raise ClaimsError('refused', 'the git common directory is not a .git folder: ' + str(common))
    return common.parent


def folder_of(common, create):
    """The claims folder of the primary checkout, refusing indirect or unignored locations."""
    primary = primary_root(common)
    local, folder = primary / FOLDER[0], primary / FOLDER[0] / FOLDER[1]
    for part in (local, folder):
        if indirect(part):
            raise ClaimsError('refused', 'indirect local storage: ' + str(part))
    if create:
        probe = '/'.join(FOLDER) + '/probe.sqlite'
        result = subprocess.run(['git', '-C', str(primary), 'check-ignore', '--', probe], capture_output=True)
        if result.returncode != 0:
            raise ClaimsError('refused', 'the claims folder is not git-ignored in ' + str(primary))
        folder.mkdir(parents=True, exist_ok=True)
        if indirect(folder):
            raise ClaimsError('refused', 'indirect local storage: ' + str(folder))
    return folder


def version_of(con):
    value = con.execute('PRAGMA user_version').fetchone()[0]
    return divmod(value, 1000)


def rollback(con):
    try:
        con.execute('ROLLBACK')
    except sqlite3.Error:
        pass


def migrate(con):
    """Create a fresh database or add the fields a lower minor lacks, inside one immediate transaction."""
    con.execute('BEGIN IMMEDIATE')
    try:
        major, minor = version_of(con)
        if (major, minor) == (0, 0):
            if con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]:
                raise ClaimsError('schema_incompatible', 'the file holds tables but no schema version')
            for statement in TABLES:
                con.execute(statement)
            major, minor = SCHEMA_MAJOR, 0
        if major != SCHEMA_MAJOR:
            raise ClaimsError('schema_incompatible', 'database schema %d.%d, tool schema %d.%d'
                              % (major, minor, SCHEMA_MAJOR, SCHEMA_MINOR))
        for (m, n), statements in sorted(MIGRATIONS.items()):
            if m == major and minor < n <= SCHEMA_MINOR:
                for statement in statements:
                    con.execute(statement)
                minor = n
        con.execute('PRAGMA user_version = %d' % (major * 1000 + minor))
        con.execute('COMMIT')
    except BaseException:
        rollback(con)
        raise


def open_database(common, write):
    """A connection, or ClaimsError. The read side opens read-only and creates nothing."""
    folder = folder_of(common, create=write)
    path = folder / DATABASE
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
        raise ClaimsError('refused', 'indirect database file: ' + str(path))
    try:
        if write:
            con = sqlite3.connect(path, timeout=BUSY_SECONDS, isolation_level=None)
            try:
                con.execute('PRAGMA journal_mode=WAL')
                migrate(con)
            except BaseException:
                con.close()
                raise
        else:
            if not path.is_file():
                raise ClaimsError('missing', 'no claims database yet')
            con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=BUSY_SECONDS, isolation_level=None)
            try:
                major, minor = version_of(con)
                if major != SCHEMA_MAJOR:
                    raise ClaimsError('schema_incompatible', 'database schema %d.%d, tool schema %d.%d'
                                      % (major, minor, SCHEMA_MAJOR, SCHEMA_MINOR))
            except BaseException:
                con.close()
                raise
    except ClaimsError:
        raise
    except Exception as error:
        raise classify(error) from error
    con.row_factory = sqlite3.Row
    return con


def write(common, work):
    """Run work(con) in one BEGIN IMMEDIATE transaction; ClaimsError on any failure."""
    con = open_database(common, True)
    try:
        con.execute('BEGIN IMMEDIATE')
        try:
            result = work(con)
            con.execute('COMMIT')
            return result
        except BaseException:
            rollback(con)
            raise
    except ClaimsError:
        raise
    except Exception as error:
        raise classify(error) from error
    finally:
        con.close()


def event(con, claim_id, owner, name, **detail):
    con.execute('INSERT INTO events(at, claim_id, owner, event, detail) VALUES (?,?,?,?,?)',
                (now(), claim_id, owner, name, json.dumps(detail, sort_keys=True)))


def open_window(common, owner, scope, state, job_handle=None, worktree=None, pid=None, pid_started=None):
    """Declare an open ref window; one open window per owner and scope, an older one is closed as replaced."""
    def work(con):
        for row in con.execute("SELECT id FROM claims WHERE kind='window' AND scope=? AND owner=? AND closed_at IS NULL",
                               (scope, owner)).fetchall():
            con.execute('UPDATE claims SET closed_at=? WHERE id=?', (now(), row['id']))
            event(con, row['id'], owner, 'closed', reason='replaced')
        cursor = con.execute("INSERT INTO claims(kind, scope, owner, job_handle, state, since, worktree, pid, pid_started)"
                             " VALUES ('window',?,?,?,?,?,?,?,?)",
                             (scope, owner, job_handle, state, now(), worktree, pid, pid_started))
        event(con, cursor.lastrowid, owner, 'opened', scope=scope, state=state, job_handle=job_handle, pid=pid)
        return cursor.lastrowid
    return write(common, work)


def update_window(common, owner, scope, state, pid=None, pid_started=None):
    def work(con):
        for row in con.execute("SELECT id FROM claims WHERE kind='window' AND scope=? AND owner=? AND closed_at IS NULL",
                               (scope, owner)).fetchall():
            con.execute('UPDATE claims SET state=?, pid=?, pid_started=? WHERE id=?', (state, pid, pid_started, row['id']))
            event(con, row['id'], owner, 'state', state=state)
    write(common, work)


def close_windows(common, owner, scope=None, reason='closed'):
    def work(con):
        query = "SELECT id FROM claims WHERE kind='window' AND owner=? AND closed_at IS NULL"
        args = [owner]
        if scope:
            query, args = query + ' AND scope=?', args + [scope]
        for row in con.execute(query, args).fetchall():
            con.execute('UPDATE claims SET closed_at=? WHERE id=?', (now(), row['id']))
            event(con, row['id'], owner, 'closed', reason=reason)
    write(common, work)


def read_windows(common):
    """The open declared windows and the database state; never raises, creates nothing."""
    try:
        con = open_database(common, False)
    except ClaimsError as error:
        return {'state': error.state, 'message': error.message, 'windows': []}
    try:
        rows = con.execute("SELECT * FROM claims WHERE kind='window' AND closed_at IS NULL ORDER BY since, id").fetchall()
        return {'state': 'ok', 'message': '', 'windows': [dict(row) for row in rows]}
    except Exception as error:
        failure = classify(error)
        return {'state': failure.state, 'message': failure.message, 'windows': []}
    finally:
        con.close()


# ---- Path claims, landings and events (schema 1.1) -----------------------------------------------

def tokens(text):
    return [token for token in (text or '').split(',') if token]


def claim_paths(con, claim_id):
    return [dict(row) for row in con.execute('SELECT path, class, subclass FROM claim_paths WHERE claim_id=? ORDER BY path',
                                             (claim_id,)).fetchall()]


def shared(a_rows, b_rows):
    """(files, subclasses) that two path lists have in common; paths compare without regard to case."""
    b_paths = {row['path'].casefold() for row in b_rows}
    files = sorted({row['path'] for row in a_rows if row['path'].casefold() in b_paths})
    subclasses = sorted({token for row in a_rows for token in tokens(row['subclass'])} &
                        {token for row in b_rows for token in tokens(row['subclass'])})
    return files, subclasses


def release(con, owner, reason):
    """Close the owner's open path claims inside a transaction; the paths go with them and the events keep the record."""
    for row in con.execute("SELECT id FROM claims WHERE kind='paths' AND owner=? AND closed_at IS NULL", (owner,)).fetchall():
        con.execute('UPDATE claims SET closed_at=? WHERE id=?', (now(), row['id']))
        con.execute('DELETE FROM claim_paths WHERE claim_id=?', (row['id'],))
        event(con, row['id'], owner, 'closed', reason=reason)


def declare_paths(common, owner, job_handle, worktree, entries):
    """Declare what a mission is about to change: entries are (path, class, [subclass, ...]); a newer declaration replaces."""
    def work(con):
        release(con, owner, 'replaced')
        cursor = con.execute("INSERT INTO claims(kind, scope, owner, job_handle, state, since, worktree)"
                             " VALUES ('paths','paths',?,?,'declared',?,?)", (owner, job_handle, now(), worktree))
        for path, klass, subclasses in entries:
            con.execute('INSERT OR REPLACE INTO claim_paths(claim_id, path, class, subclass) VALUES (?,?,?,?)',
                        (cursor.lastrowid, path, klass, ','.join(subclasses)))
        event(con, cursor.lastrowid, owner, 'opened', scope='paths', files=len(entries), job_handle=job_handle)
        return cursor.lastrowid
    return write(common, work)


def release_paths(common, owner, reason='released'):
    write(common, lambda con: release(con, owner, reason))


def note_event(common, owner, name, **detail):
    """Append one event that belongs to no claim, such as attention or reconciled."""
    write(common, lambda con: event(con, None, owner, name, **detail))


def landed(common, owner, job_handle, commit):
    """A guarded integration finished: write the landed event, name the owners it overlapped and release the claims."""
    def work(con):
        overlapped = []
        mine = con.execute("SELECT id FROM claims WHERE kind='paths' AND owner=? AND closed_at IS NULL", (owner,)).fetchone()
        if mine:
            mine_rows = claim_paths(con, mine['id'])
            for other in con.execute("SELECT id, owner FROM claims WHERE kind='paths' AND owner<>? AND closed_at IS NULL",
                                     (owner,)).fetchall():
                files, subclasses = shared(mine_rows, claim_paths(con, other['id']))
                if files or subclasses:
                    overlapped.append({'owner': other['owner'], 'claim': other['id']})
        event(con, mine['id'] if mine else None, owner, 'landed', commit=commit, job_handle=job_handle, overlapped=overlapped)
        release(con, owner, 'landed')
        return overlapped
    return write(common, work)


def decode(row):
    value = dict(row)
    try:
        value['detail'] = json.loads(value.get('detail') or '{}')
    except ValueError:
        value['detail'] = {}
    return value


def read_state(common):
    """Windows, path claims and recent events with the database state; never raises, creates nothing."""
    result = {'state': 'ok', 'message': '', 'windows': [], 'paths': [], 'landed': [], 'reconciled': []}
    try:
        con = open_database(common, False)
    except ClaimsError as error:
        return dict(result, state=error.state, message=error.message)
    try:
        result['windows'] = [dict(row) for row in con.execute(
            "SELECT * FROM claims WHERE kind='window' AND closed_at IS NULL ORDER BY since, id").fetchall()]
        if version_of(con)[1] >= 1:
            for row in con.execute("SELECT * FROM claims WHERE kind='paths' AND closed_at IS NULL ORDER BY since, id").fetchall():
                result['paths'].append(dict(row, paths=claim_paths(con, row['id'])))
        result['landed'] = [decode(row) for row in con.execute(
            "SELECT * FROM events WHERE event='landed' ORDER BY id DESC LIMIT 50").fetchall()]
        result['reconciled'] = [decode(row) for row in con.execute(
            "SELECT * FROM events WHERE event='reconciled' ORDER BY id DESC LIMIT 50").fetchall()]
        return result
    except Exception as error:
        failure = classify(error)
        return dict(result, state=failure.state, message=failure.message, windows=[], paths=[])
    finally:
        con.close()


def events_after(common, last_id):
    """(events newer than last_id in order, newest id, database state); a first read passes None and gets only the id."""
    try:
        con = open_database(common, False)
    except ClaimsError as error:
        return [], last_id, error.state + ': ' + error.message
    try:
        newest = con.execute('SELECT COALESCE(MAX(id), 0) FROM events').fetchone()[0]
        if last_id is None:
            return [], newest, 'ok'
        rows = [decode(row) for row in con.execute('SELECT * FROM events WHERE id>? ORDER BY id', (last_id,)).fetchall()]
        return rows, newest, 'ok'
    except Exception as error:
        failure = classify(error)
        return [], last_id, failure.state + ': ' + failure.message
    finally:
        con.close()


def compute_overlaps(active):
    """Pairs of owners on the same ground: the same file first, else the same subclass. A prediction, never a decision."""
    found = []
    for i, a in enumerate(active):
        for b in active[i + 1:]:
            if a['owner'] == b['owner']:
                continue
            files, subclasses = shared(a['paths'], b['paths'])
            if files:
                found.append({'owners': [a['owner'], b['owner']], 'kind': 'same_file', 'files': files, 'subclasses': subclasses})
            elif subclasses:
                found.append({'owners': [a['owner'], b['owner']], 'kind': 'same_subclass', 'files': [], 'subclasses': subclasses})
    return found


def overlap_text(overlap, limit=3):
    if overlap['kind'] == 'same_file':
        files = overlap['files']
        more = ' (+%d more)' % (len(files) - limit) if len(files) > limit else ''
        return 'same file(s): ' + ', '.join(files[:limit]) + more
    return 'same subclass, different files: ' + ', '.join(overlap['subclasses'])


def base_moved(active, landed_events, reconciled_events):
    """For each active claim that a landing overlapped: main moved under it, so its approval may be carried.

    Informational only. `worktree.py execution-status` words the note from cheap, read-only facts (the carry switch and
    the mission's landing record) and never promises a carry. The note ends when the owner declares again (a new
    claim), reconciles or carries.
    """
    notes = []
    for claim in active:
        for row in landed_events:
            detail = row['detail']
            hit = any(o.get('owner') == claim['owner'] and o.get('claim') == claim['id'] for o in detail.get('overlapped', []))
            if hit and not any(r['owner'] == claim['owner'] and r['id'] > row['id'] for r in reconciled_events):
                notes.append({'owner': claim['owner'], 'main': detail.get('commit'), 'by': row['owner'],
                              'job_handle': detail.get('job_handle'), 'at': row['at']})
    return notes


# ---- Clearing and the doctor ---------------------------------------------------------------------

def open_claims(common):
    """Every open claim, windows and paths, with its path rows; raises ClaimsError."""
    con = open_database(common, False)
    try:
        modern = version_of(con)[1] >= 1
        rows = con.execute('SELECT * FROM claims WHERE closed_at IS NULL ORDER BY id').fetchall()
        return [dict(row, paths=claim_paths(con, row['id']) if modern else []) for row in rows]
    except ClaimsError:
        raise
    except Exception as error:
        raise classify(error) from error
    finally:
        con.close()


def clear_claims(common, evidence_by_id):
    """Close the named open claims and record each clearing with its evidence; returns the claims it cleared."""
    def work(con):
        cleared = []
        for claim_id, evidence in sorted(evidence_by_id.items()):
            row = con.execute('SELECT * FROM claims WHERE id=? AND closed_at IS NULL', (claim_id,)).fetchone()
            if row is None:
                continue
            con.execute('UPDATE claims SET closed_at=? WHERE id=?', (now(), claim_id))
            con.execute('DELETE FROM claim_paths WHERE claim_id=?', (claim_id,))
            event(con, claim_id, row['owner'], 'cleared', **evidence)
            cleared.append(dict(row))
        return cleared
    return write(common, work)


def clearing_log(common, line):
    """One line in the small clearing log beside the database."""
    path = folder_of(common, create=True) / CLEARING_LOG
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
        raise ClaimsError('refused', 'indirect clearing log: ' + str(path))
    with path.open('a', encoding='utf-8', newline='\n') as stream:
        stream.write(line + '\n')


QUARANTINE = ('move %s and its WAL files aside as %s.corrupt-<time> and start an empty database '
              '(windows re-derive from Git; path claims return at the next preview)' % (DATABASE, DATABASE))


def doctor(common):
    """Check the database and say what a repair would do. Changes nothing."""
    report = {'state': 'ok', 'checks': [], 'repairs': []}

    def check(name, ok, detail):
        report['checks'].append({'name': name, 'ok': ok, 'detail': detail})

    try:
        folder = folder_of(common, create=False)
        path = folder / DATABASE
        check('folder', True, str(folder))
    except ClaimsError as error:
        check('folder', False, error.message)
        return dict(report, state=error.state)
    if not path.exists():
        check('database', True, 'no database yet; the first write creates it')
        return dict(report, state='missing')
    con = None
    try:
        con = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=BUSY_SECONDS, isolation_level=None)
        con.row_factory = sqlite3.Row
        major, minor = version_of(con)
        if major != SCHEMA_MAJOR:
            check('schema', False, 'database schema %d.%d, tool schema %d.%d: another major version, an operator decision'
                  % (major, minor, SCHEMA_MAJOR, SCHEMA_MINOR))
            return dict(report, state='schema_incompatible')
        older = minor < SCHEMA_MINOR
        check('schema', True, 'schema %d.%d' % (major, minor) +
              ('; older than the tool, the next write migrates it' if older else ''))
        answer = con.execute('PRAGMA integrity_check').fetchone()[0]
        check('integrity', answer == 'ok', answer)
        if answer != 'ok':
            report['repairs'].append({'action': 'quarantine', 'description': QUARANTINE})
            return dict(report, state='corrupt')
        if minor >= 1:
            orphans = con.execute('SELECT count(*) FROM claim_paths WHERE claim_id NOT IN '
                                  '(SELECT id FROM claims WHERE closed_at IS NULL)').fetchone()[0]
            empty = [row['id'] for row in con.execute(
                "SELECT id FROM claims WHERE kind='paths' AND closed_at IS NULL AND id NOT IN (SELECT claim_id FROM claim_paths)").fetchall()]
            check('path rows', not orphans, '%d path row(s) belong to no open claim' % orphans)
            check('path claims', not empty, '%d open path claim(s) hold no paths' % len(empty))
            if orphans:
                report['repairs'].append({'action': 'delete_orphan_paths', 'description': 'delete %d orphan path row(s)' % orphans})
            if empty:
                report['repairs'].append({'action': 'close_empty_claims', 'ids': empty,
                                          'description': 'close the empty path claim(s) ' + ', '.join('c%d' % i for i in empty)})
        dangling = con.execute('SELECT count(*) FROM events WHERE claim_id IS NOT NULL AND claim_id NOT IN '
                               '(SELECT id FROM claims)').fetchone()[0]
        check('events', not dangling, '%d event(s) name a missing claim (the log is append-only; nothing to repair)' % dangling)
        return dict(report, state='repairable' if report['repairs'] else 'ok')
    except Exception as error:
        failure = classify(error)
        check('database', False, failure.message)
        if failure.state == 'corrupt':
            report['repairs'].append({'action': 'quarantine', 'description': QUARANTINE})
        return dict(report, state=failure.state)
    finally:
        if con is not None:
            con.close()


def repair(common, report):
    """Apply exactly the repairs the report lists; returns what was done."""
    done = []
    for item in report['repairs']:
        if item['action'] == 'quarantine':
            folder = folder_of(common, create=False)
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
            for suffix in ('', '-wal', '-shm'):
                source = folder / (DATABASE + suffix)
                if source.exists():
                    source.rename(folder / (DATABASE + suffix + '.corrupt-' + stamp))
            done.append('moved ' + DATABASE + ' aside as ' + DATABASE + '.corrupt-' + stamp)
        elif item['action'] == 'delete_orphan_paths':
            write(common, lambda con: con.execute('DELETE FROM claim_paths WHERE claim_id NOT IN '
                                                  '(SELECT id FROM claims WHERE closed_at IS NULL)'))
            done.append(item['description'])
        elif item['action'] == 'close_empty_claims':
            def close(con, ids=item['ids']):
                for claim_id in ids:
                    row = con.execute('SELECT owner FROM claims WHERE id=? AND closed_at IS NULL', (claim_id,)).fetchone()
                    if row:
                        con.execute('UPDATE claims SET closed_at=? WHERE id=?', (now(), claim_id))
                        event(con, claim_id, row['owner'], 'closed', reason='doctor: no paths')
            write(common, close)
            done.append(item['description'])
    return done


# ---- Process liveness ---------------------------------------------------------------------------

def process_info(pid):
    """(start token, alive) of a process, or None when no such process exists."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            code = wintypes.DWORD()
            if not kernel.GetProcessTimes(handle, created, exited, kernel_time, user_time) \
                    or not kernel.GetExitCodeProcess(handle, code):
                return None
            return str(created.dwHighDateTime << 32 | created.dwLowDateTime), code.value == 259  # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    token = None
    try:
        token = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[19]
    except (OSError, IndexError):
        pass
    return token, True


def process_start(pid):
    try:
        info = process_info(pid)
        return info[0] if info else None
    except Exception:
        return None


def pid_state(pid, started):
    """True while the command runs, False when it is gone or the pid was reused, None when unknown."""
    if pid is None:
        return None
    try:
        info = process_info(int(pid))
    except Exception:
        return None
    if info is None or not info[1]:
        return False
    if started and info[0] and info[0] != started:
        return False
    return True


# ---- Hooks: best effort, never raise ------------------------------------------------------------

def attempt(work):
    """Run a hook; any failure becomes one notice. Returns whether it succeeded."""
    try:
        work()
        return True
    except Exception as error:
        failure = classify(error)
        notice(failure.state, failure.message)
        return False


def owner_of(tool):
    branch = tool.branch()
    return branch.removeprefix('refs/heads/tx/').removeprefix('refs/heads/')


def declare_manifest(tool, profile, job_handle=None):
    """The preview's hook: claim the manifest's paths and subclasses for this mission. Best effort, never raises."""
    def declaring():
        entries = []
        for path, view in sorted(profile['artifact_classification'].items()):
            subclasses = view.get('subclass') or []
            entries.append((path, view.get('class'), tokens(subclasses) if isinstance(subclasses, str) else list(subclasses)))
        declare_paths(tool.common, owner_of(tool), job_handle, str(tool.root), entries)
    return attempt(declaring)


@contextmanager
def command_window(tool, action, job_handle=None):
    """Declare the prepare/seal window around a checkpoint command and settle it on the way out."""
    owner, job, failure = [None], [job_handle], []

    def opening():
        owner[0] = owner_of(tool)
        if job[0] is None:
            try:
                job[0] = tool.load().get('job_handle')
            except Exception:
                job[0] = None
        if action in WINDOW_ACTIONS:
            pid = os.getpid()
            open_window(tool.common, owner[0], 'prepare', 'validating', job[0], str(tool.root), pid, process_start(pid))

    def settling():
        if owner[0] is None:
            return
        if action in WINDOW_ACTIONS:
            try:
                state = tool.load()['state']
            except Exception:
                state = None
            if state == 'validated':
                update_window(tool.common, owner[0], 'prepare', 'validated')
            else:
                close_windows(tool.common, owner[0], 'prepare', 'settled')
            if state == 'prepare-failed':
                note_event(tool.common, owner[0], 'attention', reason=action + ' failed (packet prepare-failed)', job_handle=job[0])
            elif failure and state != 'prepared':
                note_event(tool.common, owner[0], 'attention', reason=action + ' stopped: ' + failure[0], job_handle=job[0])
        elif action in CLOSING_ACTIONS:
            close_windows(tool.common, owner[0], 'prepare', action)
            if action == 'abandon' and not failure:
                release_paths(tool.common, owner[0], 'abandon')
                note_event(tool.common, owner[0], 'attention', reason='abandoned', job_handle=job[0])

    if action in WINDOW_ACTIONS or action in CLOSING_ACTIONS:
        attempt(opening)
    try:
        yield
    except BaseException as error:
        failure.append((str(error).splitlines() or [type(error).__name__])[0][:200])
        raise
    finally:
        if action in WINDOW_ACTIONS or action in CLOSING_ACTIONS:
            attempt(settling)


@contextmanager
def lifecycle_window(common, operation, owner, worktree=None, job_handle=None):
    """Declare a lifecycle operation (create, reconcile, integrate, remove) while its lock is held."""
    declared = [False]

    def opening():
        pid = os.getpid()
        open_window(common, owner or operation, 'lifecycle', operation, job_handle, worktree, pid, process_start(pid))
        declared[0] = True
    attempt(opening)
    try:
        yield
    except BaseException as error:
        if operation == 'integrate':  # An integration that stopped needs a look; the other operations fail in the caller's face.
            reason = (str(error).splitlines() or [type(error).__name__])[0][:200]
            attempt(lambda: note_event(common, owner or operation, 'attention', reason='integration stopped: ' + reason,
                                       job_handle=job_handle))
        raise
    finally:
        if declared[0]:
            attempt(lambda: close_windows(common, owner or operation, 'lifecycle', operation))
