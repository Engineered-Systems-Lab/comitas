"""execution-status in disposable repositories: derived evidence, the cross-check and the exit code."""
from contextlib import closing
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
from governance.tools.support import local_state
from governance.tests.worktrees import test_checkpoint_worktrees as fixtures
from governance.tools.worktrees import guard as w
import repo_paths as rp

IDENTITY = ['-c', 'user.name=Isolated Test', '-c', 'user.email=test@example.invalid']


class StatusCase(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='status-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-q', '-b', 'main')
        (self.root / '.gitignore').write_text('/.local/\ntmp/\n', newline='\n')
        self.git('add', '.gitignore')
        self.git(*IDENTITY, 'commit', '-q', '-m', 'Fixture')
        self.common = self.root / '.git'
        claims._noticed.clear()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True).stdout.decode()

    def hold_lock(self):
        """A lifecycle lock that some operation left in the primary checkout's locks folder."""
        lock = local_state.lifecycle_lock(self.common)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('')

    def mission(self, owner, state=None, lock=False, job='job_a'):
        """A transaction worktree with the scratch a running tool would leave."""
        path = Path(self.temp.name) / ('wt-' + owner)
        self.git('worktree', 'add', '-q', '--lock', '--reason', 'transaction:' + owner, '-b', 'tx/' + owner, str(path))
        scratch = local_state.checkout_store(path)
        scratch.mkdir(parents=True, exist_ok=True)
        if state:
            (scratch / 'transaction.json').write_text(json.dumps({'packet': {'state': state, 'job_handle': job}, 'mac': 'x'}),
                                                      newline='\n')
        if lock:
            (scratch / 'operation.lock').write_text('')
        return path

    def declare(self, owner, scope='prepare', state='validating', pid=None, started=None, job='job_a'):
        claims.open_window(self.common, owner, scope, state, job, None, pid, started)

    def status(self):
        return w.execution_status(self.root)


class DerivedEvidenceTest(StatusCase):
    def test_a_quiet_repository_is_clear(self):
        status = self.status()
        self.assertEqual((status['level'], status['schema'], status['windows']), ('CLEAR', '1.1', []))
        self.assertEqual(status['line'], 'CLEAR: no ref window is open.')
        self.assertEqual(status['database']['state'], 'missing')

    def test_a_preparing_packet_freezes_with_no_database_at_all(self):
        self.mission('m1', 'preparing', lock=True)
        status = self.status()
        self.assertEqual(status['level'], 'FROZEN')
        [window] = status['windows']
        self.assertEqual((window['owner'], window['scope'], window['state'], window['job_handle'], window['declared']),
                         ('m1', 'prepare', 'preparing', 'job_a', False))
        self.assertNotIn('undeclared', window['flags'], 'no database means no declaration to compare')
        self.assertTrue(status['line'].startswith('REFS FROZEN: m1, since '))

    def test_a_validated_packet_is_open_until_it_is_sealed_or_abandoned(self):
        self.mission('m1', 'validated')
        self.assertEqual(self.status()['windows'][0]['state'], 'validated')

    def test_an_operation_lock_without_a_packet_is_a_starting_prepare(self):
        self.mission('m1', None, lock=True)
        self.assertEqual(self.status()['windows'][0]['state'], 'starting')

    def test_sealed_review_waiting_is_not_a_window_even_while_a_quick_command_holds_the_lock(self):
        for state in ('prepared', 'approved', 'prepare-failed', 'verified'):
            with self.subTest(state=state):
                self.mission('m-' + state, state, lock=True)
        status = self.status()
        self.assertEqual((status['level'], status['windows']), ('CLEAR', []))

    def test_the_lifecycle_lock_freezes(self):
        self.hold_lock()
        status = self.status()
        self.assertEqual(status['level'], 'FROZEN')
        self.assertEqual(status['windows'][0]['scope'], 'lifecycle')
        self.assertIn('lifecycle operation', status['line'])
        self.assertTrue(status['derived']['lifecycle_lock'])

    def test_a_second_owner_is_counted_in_the_line(self):
        self.mission('m1', 'preparing', lock=True)
        self.mission('m2', 'validated')
        status = self.status()
        self.assertEqual(len(status['windows']), 2)
        self.assertIn('(+1 more)', status['line'])


class CrossCheckTest(StatusCase):
    def test_a_declared_window_with_live_evidence_is_matched_and_names_its_running_command(self):
        self.mission('m1', 'preparing', lock=True)
        self.declare('m1', pid=os.getpid(), started=claims.process_start(os.getpid()))
        [window] = self.status()['windows']
        self.assertTrue(window['declared'])
        self.assertEqual(window['command'], {'pid': os.getpid(), 'running': True})
        self.assertEqual(window['flags'], [])

    def test_live_evidence_with_no_declared_window_is_flagged_when_the_database_is_healthy(self):
        self.mission('m1', 'preparing', lock=True)
        self.declare('someone-else')
        status = self.status()
        self.assertEqual(status['level'], 'FROZEN')
        self.assertIn('undeclared', status['windows'][0]['flags'])
        self.assertEqual([d['owner'] for d in status['declared_without_evidence']], ['someone-else'])

    def test_a_declared_window_with_no_evidence_is_flagged_and_does_not_freeze(self):
        self.declare('m1', pid=os.getpid())
        status = self.status()
        self.assertEqual((status['level'], status['windows']), ('CLEAR', []))
        self.assertEqual([(d['owner'], d['state']) for d in status['declared_without_evidence']], [('m1', 'validating')])

    def test_an_unknown_or_dead_command_with_evidence_is_flagged_and_the_window_stays_frozen(self):
        self.mission('m1', 'preparing', lock=True)
        child = subprocess.Popen([sys.executable, '-B', '-c', 'pass'])
        started = claims.process_start(child.pid)
        child.wait()
        self.declare('m1', pid=child.pid, started=started)
        [window] = self.status()['windows']
        self.assertEqual(window['command']['running'], False)
        self.assertIn('unknown_or_dead_command', window['flags'])
        self.assertEqual(self.status()['level'], 'FROZEN')

    def test_the_lifecycle_window_names_its_owner_and_operation(self):
        self.hold_lock()
        self.declare('m9', scope='lifecycle', state='integrate', pid=os.getpid())
        [window] = self.status()['windows']
        self.assertEqual((window['owner'], window['state'], window['declared']), ('m9', 'integrate', True))
        self.assertIn('REFS FROZEN: m9', self.status()['line'])

    def test_an_old_window_only_gets_an_age_flag_and_is_never_cleared(self):
        self.mission('m1', 'preparing', lock=True)
        self.declare('m1')
        with closing(sqlite3.connect(self.common.parent / '.local/claims/claims.sqlite')) as con:
            con.execute("UPDATE claims SET since='2020-01-01T00:00:00Z'")
            con.commit()
        [window] = self.status()['windows']
        self.assertTrue(window['age_flag'])
        self.assertIn('age', window['flags'])
        self.assertEqual(self.status()['level'], 'FROZEN')

    def test_a_corrupt_database_leaves_the_derived_level_and_says_so(self):
        self.mission('m1', 'preparing', lock=True)
        database = self.common.parent / '.local/claims/claims.sqlite'
        database.parent.mkdir(parents=True)
        database.write_bytes(b'not a database' * 100)
        status = self.status()
        self.assertEqual((status['level'], status['database']['state']), ('FROZEN', 'corrupt'))
        self.assertIn('claims database corrupt; derived evidence only', status['line'])
        self.assertEqual(status['windows'][0]['declared'], False)

    def test_the_status_never_creates_the_database_or_its_folder(self):
        self.mission('m1', 'preparing', lock=True)
        self.status()
        self.assertFalse((self.root / '.local').exists())


class CommandLineTest(StatusCase):
    def run_cli(self, main, *extra):
        return subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.WORKTREE), '--main', str(main), 'execution-status', *extra],
                              capture_output=True, text=True, cwd=self.temp.name)

    def test_the_command_prints_one_human_line_then_json_and_exits_zero(self):
        result = self.run_cli(self.root)
        self.assertEqual(result.returncode, 0, result.stderr)
        line, rest = result.stdout.split('\n', 1)
        self.assertEqual(line, 'CLEAR: no ref window is open.')
        self.assertEqual(json.loads(rest)['level'], 'CLEAR')

    def test_json_only_prints_just_the_payload(self):
        result = self.run_cli(self.root, '--json-only')
        self.assertEqual(json.loads(result.stdout)['schema'], '1.1')

    def test_it_exits_zero_with_an_unknown_level_when_the_repository_cannot_be_read(self):
        for target in (Path(self.temp.name) / 'missing', Path(self.temp.name)):
            with self.subTest(target=target.name):
                result = self.run_cli(target, '--json-only')
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads(result.stdout)
                self.assertEqual(status['level'], 'UNKNOWN')
                self.assertTrue(status['line'].startswith('STATUS UNKNOWN'))

    def test_it_runs_from_a_linked_worktree_too(self):
        path = self.mission('m1', 'preparing', lock=True)
        self.assertEqual(json.loads(self.run_cli(path, '--json-only').stdout)['level'], 'FROZEN')


class LifecycleHookTest(StatusCase):
    def test_the_lock_lives_in_the_locks_folder_of_the_primary_not_in_the_git_directory(self):
        tool = type('Tool', (), {'common': self.common, 'root': self.root})()
        expected = self.root / '.local' / 'locks' / 'worktree-workflow.lock'
        self.assertFalse(expected.parent.exists(), 'a missing locks folder simply means no lock')
        with w.lifecycle_lock(tool, 'integrate', 'm1'):
            self.assertTrue(expected.is_file())
            self.assertFalse((self.common / 'worktree-workflow.lock').exists())
            with self.assertRaisesRegex(ValueError, 'already active'):
                with w.lifecycle_lock(tool, 'remove', 'm2'):
                    self.fail('the lock is exclusive')
            self.assertTrue(expected.is_file(), 'the refused second operation did not remove the first one\'s lock')
        self.assertFalse(expected.exists())

    def test_the_lock_is_refused_and_nothing_is_created_when_the_local_folder_is_not_ignored(self):
        (self.root / '.gitignore').write_text('tmp/\n', newline='\n')
        tool = type('Tool', (), {'common': self.common, 'root': self.root})()
        with self.assertRaisesRegex(ValueError, 'not git-ignored'):
            with w.lifecycle_lock(tool, 'integrate', 'm1'):
                self.fail('no operation may start without a place for its lock')
        self.assertFalse((self.root / '.local' / 'locks').exists())

    def test_the_lifecycle_lock_declares_its_window_while_held_and_closes_it(self):
        tool = type('Tool', (), {'common': self.common, 'root': self.root})()
        with w.lifecycle_lock(tool, 'integrate', 'm1'):
            [window] = self.status()['windows']
            self.assertEqual((window['owner'], window['state'], window['declared']), ('m1', 'integrate', True))
            self.assertEqual(window['command']['running'], True)
        self.assertFalse(local_state.lifecycle_lock(self.common).exists())
        status = self.status()
        self.assertEqual((status['level'], status['declared_without_evidence']), ('CLEAR', []))

    def test_a_broken_database_never_stops_the_lifecycle_lock(self):
        database = self.common.parent / '.local/claims/claims.sqlite'
        database.parent.mkdir(parents=True)
        database.write_bytes(b'garbage' * 100)
        tool = type('Tool', (), {'common': self.common, 'root': self.root})()
        ran = []
        with patch('sys.stderr'), w.lifecycle_lock(tool, 'remove', 'm1'):
            ran.append(True)
        self.assertEqual(ran, [True])
        self.assertFalse(local_state.lifecycle_lock(self.common).exists())

    def test_a_second_lifecycle_operation_still_fails_on_the_lock_whatever_the_database_says(self):
        tool = type('Tool', (), {'common': self.common, 'root': self.root})()
        with w.lifecycle_lock(tool, 'create', 'm1'):
            with self.assertRaisesRegex(ValueError, 'already active'):
                with w.lifecycle_lock(tool, 'remove', 'm2'):
                    pass


class RealCommandTest(unittest.TestCase):
    """The real command line of the checkpoint tool and the real lifecycle guard, in a disposable repository."""
    def setUp(self):
        self.base = fixtures.WorktreeTest('setUp')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        f = self.base.f
        (f.root / '.gitignore').write_text('ignored/\n__pycache__/\ntmp/\n/.local/\n', newline='\n')
        f.git('add', '--', '.gitignore')
        f.git('commit', '-m', 'Ignore the local folder')
        self.base.base = f.parent = f.git('rev-parse', 'HEAD').strip()
        self.owner = self.base.owner

    def status(self):
        return w.execution_status(self.base.root)

    def events(self):
        with closing(sqlite3.connect(self.base.root / '.local/claims/claims.sqlite')) as con:
            return [(row[0], json.loads(row[1]).get('state') or json.loads(row[1]).get('reason'))
                    for row in con.execute('SELECT event, detail FROM events ORDER BY id')]

    def edit(self):
        (self.base.path / 'a.txt').write_text('owned edit\n', newline='\n')

    def run_tool(self, *args):
        argv = [sys.executable, '-B', '-c', fixtures.PREPARE_DRIVER, str(Path(w.__file__).parents[1]),
                '--repo', str(self.base.path), *args]
        result = subprocess.run(argv, capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def prepare_arguments(self, *extra):
        manifest = Path(self.base.f.temp.name) / 'scope.json'
        manifest.write_text('["a.txt"]', encoding='utf-8', newline='\n')
        return ['prepare', *extra, '--manifest', str(manifest), '--message', str(self.base.f.message), '--profile', 'test',
                '--expected-head', self.base.base, '--expected-branch', 'refs/heads/tx/' + self.owner,
                '--delegation', 'Disposable fixture scope', '--job-handle', 'worker_claims']

    def test_create_declares_and_closes_a_lifecycle_window(self):
        self.base.create()
        self.assertEqual(self.events(), [('opened', 'create'), ('closed', 'create')])
        self.assertEqual(self.status()['level'], 'CLEAR')

    def test_a_validate_only_prepare_keeps_the_window_open_until_seal_and_a_seal_closes_it(self):
        self.base.create()
        self.edit()
        self.run_tool(*self.prepare_arguments('--validate-only'))
        status = self.status()
        self.assertEqual(status['level'], 'FROZEN')
        [window] = status['windows']
        self.assertEqual((window['owner'], window['state'], window['job_handle'], window['declared'], window['flags']),
                         (self.owner, 'validated', 'worker_claims', True, []))
        self.run_tool('seal')
        status = self.status()
        self.assertEqual((status['level'], status['windows'], status['declared_without_evidence']), ('CLEAR', [], []))

    def test_a_one_step_prepare_leaves_a_sealed_review_waiting_and_that_is_not_a_window(self):
        self.base.create()
        self.edit()
        self.run_tool(*self.prepare_arguments())
        status = self.status()
        self.assertEqual((status['level'], status['windows']), ('CLEAR', []))
        self.assertIn(('opened', 'validating'), self.events())
        self.assertEqual(self.events()[-1], ('closed', 'settled'))

    def test_abandon_closes_a_validated_window(self):
        self.base.create()
        self.edit()
        self.run_tool(*self.prepare_arguments('--validate-only'))
        self.assertEqual(self.status()['level'], 'FROZEN')
        self.run_tool('abandon')
        self.assertEqual(self.status()['level'], 'CLEAR')

    def test_a_broken_database_never_stops_the_real_prepare_and_the_status_still_freezes_on_evidence(self):
        self.base.create()
        database = self.base.root / '.local/claims/claims.sqlite'
        for suffix in ('', '-wal', '-shm'):
            Path(str(database) + suffix).unlink(missing_ok=True)
        database.write_bytes(b'garbage' * 300)
        self.edit()
        result = self.run_tool(*self.prepare_arguments('--validate-only'))
        self.assertIn('NOTICE: claims database corrupt', result.stderr)
        status = self.status()
        self.assertEqual((status['level'], status['database']['state']), ('FROZEN', 'corrupt'))
        self.run_tool('abandon')
        self.assertEqual(self.status()['level'], 'CLEAR')


if __name__ == '__main__':
    unittest.main()
