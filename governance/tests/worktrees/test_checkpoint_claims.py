"""The claims store in disposable repositories only: schema versions, concurrency, degraded states and liveness."""
from contextlib import closing, redirect_stderr
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.worktrees import claims

WRITER = '''import sys
sys.path.insert(0, sys.argv[1])
from governance.tools.worktrees import claims
from pathlib import Path
common, owner, rounds = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
for i in range(rounds):
    claims.open_window(common, owner, 'prepare', 'validating', 'job_' + owner.replace('-', '_'), 'w', 1, 'x')
    claims.close_windows(common, owner, 'prepare', 'done')
claims.open_window(common, owner, 'prepare', 'validating')
'''


def git_repo(root, ignore=True):
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True, capture_output=True)
    if ignore:
        (root / '.gitignore').write_text('/.local/\n', newline='\n')
    return root / '.git'


class StoreCase(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='claims-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.common = git_repo(self.root)
        self.database = self.root / '.local/claims/claims.sqlite'
        claims._noticed.clear()

    def raw(self):
        return sqlite3.connect(self.database)


class SchemaTest(StoreCase):
    def test_first_write_creates_the_store_in_the_primary_checkout_at_version_1_1(self):
        self.assertFalse((self.root / '.local').exists(), 'a read creates nothing')
        self.assertEqual(claims.read_windows(self.common)['state'], 'missing')
        self.assertFalse((self.root / '.local').exists())
        claims.open_window(self.common, 'm1', 'prepare', 'validating', 'job_a', str(self.root), 1, 'x')
        self.assertTrue(self.database.is_file())
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 1001)
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({'claims', 'claim_paths', 'events'} <= tables)
        status = claims.read_windows(self.common)
        self.assertEqual(status['state'], 'ok')
        self.assertEqual([(w['owner'], w['scope'], w['state'], w['job_handle']) for w in status['windows']],
                         [('m1', 'prepare', 'validating', 'job_a')])

    def test_events_are_append_only_and_a_reopened_window_replaces_the_old_one(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        claims.update_window(self.common, 'm1', 'prepare', 'validated')
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        claims.close_windows(self.common, 'm1', 'prepare', 'settled')
        with closing(self.raw()) as con:
            names = [row[0] for row in con.execute('SELECT event FROM events ORDER BY id')]
            self.assertEqual(names, ['opened', 'state', 'closed', 'opened', 'closed'])
            self.assertEqual(con.execute('SELECT count(*) FROM claims WHERE closed_at IS NULL').fetchone()[0], 0)
        self.assertEqual(claims.read_windows(self.common)['windows'], [])

    def test_one_open_window_per_owner_and_scope_but_two_owners_coexist(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        claims.open_window(self.common, 'm2', 'prepare', 'validating')
        claims.open_window(self.common, 'm1', 'lifecycle', 'integrate')
        self.assertEqual(len(claims.read_windows(self.common)['windows']), 3)

    def test_a_higher_minor_is_read_and_left_untouched(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        with closing(self.raw()) as con:
            con.execute('PRAGMA user_version = 1007')
        self.assertEqual(claims.read_windows(self.common)['state'], 'ok')
        claims.close_windows(self.common, 'm1', 'prepare')
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 1007)

    def test_a_lower_minor_is_migrated_additively_and_keeps_its_rows(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        with patch.object(claims, 'SCHEMA_MINOR', 2), \
                patch.object(claims, 'MIGRATIONS', {**claims.MIGRATIONS, (1, 2): ['ALTER TABLE claims ADD COLUMN note TEXT']}):
            claims.open_window(self.common, 'm2', 'prepare', 'validating')
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 1002)
            self.assertIn('note', [row[1] for row in con.execute('PRAGMA table_info(claims)')])
            self.assertEqual(con.execute('SELECT count(*) FROM claims').fetchone()[0], 2)

    def test_a_version_1_0_file_from_slice_one_gains_the_path_table_and_keeps_its_rows(self):
        with patch.object(claims, 'SCHEMA_MINOR', 0), patch.object(claims, 'MIGRATIONS', {}):
            claims.open_window(self.common, 'm1', 'prepare', 'validating', 'job_a')
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 1000)
            self.assertEqual(con.execute("SELECT count(*) FROM sqlite_master WHERE name='claim_paths'").fetchone()[0], 0)
        state = claims.read_state(self.common)
        self.assertEqual((state['state'], state['paths'], len(state['windows'])), ('ok', [], 1), 'a 1.0 file reads without the table')
        claims.declare_paths(self.common, 'm2', 'job_b', 'w', [('a.txt', 'record', ['x'])])
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 1001)
            self.assertEqual(con.execute('SELECT count(*) FROM claims').fetchone()[0], 2)
            self.assertEqual(con.execute('SELECT count(*) FROM claim_paths').fetchone()[0], 1)

    def test_a_slice_one_tool_ignores_path_claims_it_does_not_know(self):
        claims.declare_paths(self.common, 'm1', 'job_a', 'w', [('a.txt', 'record', ['x'])])
        with patch.object(claims, 'SCHEMA_MINOR', 0), patch.object(claims, 'MIGRATIONS', {}):
            self.assertEqual(claims.read_windows(self.common)['windows'], [])
            claims.open_window(self.common, 'm2', 'prepare', 'validating')
            claims.close_windows(self.common, 'm2', 'prepare')
        self.assertEqual(len(claims.read_state(self.common)['paths']), 1)

    def test_a_different_major_is_incompatible_and_the_file_is_not_modified(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        with closing(self.raw()) as con:
            con.execute('PRAGMA user_version = 2000')
        before = self.database.read_bytes()
        self.assertEqual(claims.read_windows(self.common)['state'], 'schema_incompatible')
        with self.assertRaises(claims.ClaimsError) as caught:
            claims.open_window(self.common, 'm2', 'prepare', 'validating')
        self.assertEqual(caught.exception.state, 'schema_incompatible')
        self.assertIn('2.0', caught.exception.message)
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA user_version').fetchone()[0], 2000)
            self.assertEqual(con.execute('SELECT count(*) FROM claims').fetchone()[0], 1)
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.database.read_bytes(), before)

    def test_a_foreign_database_without_a_version_is_refused_not_adopted(self):
        self.database.parent.mkdir(parents=True)
        with closing(self.raw()) as con:
            con.execute('CREATE TABLE other (x)')
        with self.assertRaises(claims.ClaimsError) as caught:
            claims.open_window(self.common, 'm1', 'prepare', 'validating')
        self.assertEqual(caught.exception.state, 'schema_incompatible')


class ConcurrencyTest(StoreCase):
    def test_eight_processes_write_at_once_without_loss_or_corruption(self):
        folder = str(Path(claims.__file__).parents[3])
        claims.open_window(self.common, 'seed', 'prepare', 'validating')  # create the store before the race
        processes = [subprocess.Popen([sys.executable, '-B', '-c', WRITER, folder, str(self.common), 'owner-%d' % n, '12'],
                                      stderr=subprocess.PIPE) for n in range(8)]
        for process in processes:
            _, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            opened = con.execute("SELECT count(*) FROM events WHERE event='opened'").fetchone()[0]
            self.assertEqual(opened, 1 + 8 * 13)
            self.assertEqual(con.execute('SELECT count(*) FROM claims WHERE closed_at IS NULL').fetchone()[0], 9)
        owners = sorted(w['owner'] for w in claims.read_windows(self.common)['windows'])
        self.assertEqual(owners, sorted(['seed'] + ['owner-%d' % n for n in range(8)]))


class DegradedModeTest(StoreCase):
    def states(self):
        with self.assertRaises(claims.ClaimsError) as caught:
            claims.open_window(self.common, 'm1', 'prepare', 'validating')
        return caught.exception.state

    def test_a_missing_database_is_reported_by_the_read_side_and_created_by_the_write_side(self):
        self.assertEqual(claims.read_windows(self.common)['state'], 'missing')
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        self.assertEqual(claims.read_windows(self.common)['state'], 'ok')

    def test_a_corrupt_database_is_reported_on_both_sides(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b'this is not a database' * 200)
        self.assertEqual(claims.read_windows(self.common)['state'], 'corrupt')
        self.assertEqual(self.states(), 'corrupt')

    def test_a_locked_database_times_out_with_a_notice_and_no_exception_from_the_hook(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        blocker = sqlite3.connect(self.database, isolation_level=None)
        blocker.execute('BEGIN IMMEDIATE')
        self.addCleanup(blocker.close)
        with patch.object(claims, 'BUSY_SECONDS', 0.2):
            self.assertEqual(self.states(), 'locked')
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                self.assertFalse(claims.attempt(lambda: claims.open_window(self.common, 'm2', 'prepare', 'validating')))
            self.assertIn('NOTICE: claims database locked', stderr.getvalue())
            self.assertEqual(claims.read_windows(self.common)['state'], 'ok', 'readers continue in WAL mode')

    def test_a_repository_that_does_not_ignore_the_folder_is_refused_and_nothing_is_created(self):
        other = Path(self.temp.name) / 'unignored'
        other.mkdir()
        common = git_repo(other, ignore=False)
        with self.assertRaises(claims.ClaimsError) as caught:
            claims.open_window(common, 'm1', 'prepare', 'validating')
        self.assertEqual(caught.exception.state, 'refused')
        self.assertFalse((other / '.local').exists())

    def test_an_indirect_local_or_claims_folder_is_refused(self):
        target = Path(self.temp.name) / 'elsewhere'
        target.mkdir()
        link = self.root / '.local'
        if os.name == 'nt':
            made = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True).returncode == 0
        else:
            os.symlink(target, link)
            made = True
        if not made:
            self.skipTest('cannot create a junction or symlink here')
        self.assertEqual(self.states(), 'refused')
        self.assertEqual(claims.read_windows(self.common)['state'], 'refused')
        self.assertEqual(list(target.iterdir()), [])

    def test_a_common_directory_that_is_not_a_git_folder_is_refused(self):
        with self.assertRaises(claims.ClaimsError) as caught:
            claims.open_window(self.root / 'not-dot-git', 'm1', 'prepare', 'validating')
        self.assertEqual(caught.exception.state, 'refused')

    def test_a_notice_is_printed_once_per_failure(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            for _ in range(3):
                claims.attempt(lambda: claims.open_window(self.root / 'not-dot-git', 'm1', 'prepare', 'validating'))
        self.assertEqual(stderr.getvalue().count('NOTICE: claims database refused'), 1)


class FakeTool:
    """The few members the hooks use, over a real common directory."""
    def __init__(self, common, root, branch='refs/heads/tx/m1', packet_state='validated'):
        self.common, self.root, self._branch, self.packet_state = common, root, branch, packet_state

    def branch(self):
        return self._branch

    def load(self):
        return {'state': self.packet_state, 'job_handle': 'job_x'}


class HookTest(StoreCase):
    def windows(self):
        return claims.read_windows(self.common)['windows']

    def test_prepare_opens_a_window_and_a_validated_packet_keeps_it_open_without_a_pid(self):
        tool = FakeTool(self.common, self.root)
        with claims.command_window(tool, 'prepare'):
            [window] = self.windows()
            self.assertEqual((window['owner'], window['state'], window['job_handle'], window['pid']),
                             ('m1', 'validating', 'job_x', os.getpid()))
        [window] = self.windows()
        self.assertEqual((window['state'], window['pid']), ('validated', None))

    def test_a_prepared_or_failed_packet_closes_the_window(self):
        for state in ('prepared', 'prepare-failed', None):
            with self.subTest(state=state):
                tool = FakeTool(self.common, self.root, packet_state=state)
                if state is None:
                    tool.load = lambda: (_ for _ in ()).throw(ValueError('no packet'))
                with claims.command_window(tool, 'seal'):
                    self.assertEqual(len(self.windows()), 1)
                self.assertEqual(self.windows(), [])

    def test_an_exception_in_the_command_still_settles_the_window(self):
        tool = FakeTool(self.common, self.root, packet_state='prepare-failed')
        with self.assertRaises(ValueError):
            with claims.command_window(tool, 'prepare'):
                raise ValueError('prepare failed')
        self.assertEqual(self.windows(), [])

    def test_abandon_and_finish_close_a_validated_window(self):
        for action in ('abandon', 'finish', 'commit'):
            with self.subTest(action=action):
                claims.open_window(self.common, 'm1', 'prepare', 'validated')
                with claims.command_window(FakeTool(self.common, self.root), action):
                    pass
                self.assertEqual(self.windows(), [])

    def test_read_only_commands_touch_nothing(self):
        with claims.command_window(FakeTool(self.common, self.root), 'inspect'):
            pass
        self.assertFalse(self.database.exists())

    def test_the_lifecycle_window_is_open_while_the_lock_is_held_and_closed_after_an_error(self):
        with self.assertRaises(RuntimeError):
            with claims.lifecycle_window(self.common, 'integrate', 'm1', str(self.root)):
                [window] = self.windows()
                self.assertEqual((window['scope'], window['state'], window['owner']), ('lifecycle', 'integrate', 'm1'))
                raise RuntimeError('integration failed')
        self.assertEqual(self.windows(), [])

    def test_hooks_never_raise_into_the_command_when_the_store_is_broken(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b'garbage' * 500)
        stderr, ran = io.StringIO(), []
        with redirect_stderr(stderr):
            with claims.command_window(FakeTool(self.common, self.root), 'prepare'):
                ran.append('prepare')
            with claims.lifecycle_window(self.common, 'create', 'm1'):
                ran.append('create')
        self.assertEqual(ran, ['prepare', 'create'])
        self.assertIn('NOTICE: claims database corrupt', stderr.getvalue())

    def test_a_tool_that_cannot_name_its_branch_does_not_break_the_command(self):
        tool = FakeTool(self.common, self.root)
        tool.branch = lambda: (_ for _ in ()).throw(ValueError('detached'))
        ran = []
        with redirect_stderr(io.StringIO()):
            with claims.command_window(tool, 'prepare'):
                ran.append(True)
        self.assertEqual(ran, [True])


class LivenessTest(unittest.TestCase):
    def test_a_running_process_is_alive_and_a_finished_one_is_not(self):
        self.assertTrue(claims.pid_state(os.getpid(), claims.process_start(os.getpid())))
        child = subprocess.Popen([sys.executable, '-B', '-c', 'import time; time.sleep(30)'])
        started = claims.process_start(child.pid)
        try:
            self.assertTrue(claims.pid_state(child.pid, started))
        finally:
            child.kill()
            child.wait()
        self.assertFalse(claims.pid_state(child.pid, started))

    def test_a_reused_pid_with_another_start_time_is_not_the_command(self):
        self.assertFalse(claims.pid_state(os.getpid(), 'not-the-start-token'))

    def test_unknown_inputs_are_unknown_or_dead_never_an_exception(self):
        self.assertIsNone(claims.pid_state(None, None))
        self.assertFalse(claims.pid_state(2 ** 22 + 12345, None))
        with patch.object(claims, 'process_info', side_effect=OSError('denied')):
            self.assertIsNone(claims.pid_state(1, None))


if __name__ == '__main__':
    unittest.main()
