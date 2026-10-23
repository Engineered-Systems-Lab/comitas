"""The test runner end to end: a tiny synthetic test set on a disposable repository, run in both modes.

The mode comes from the feature file on the repository's main, so each case commits its own file. The reader's
inputs are pinned in test_early_approval; here the runs show that both modes give the same results, that a failure
exits non-zero and is named, and that the worker cap is respected.
"""
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import repo_paths as rp
from governance.tests import run_tests as runner

RUNNER = rp.ROOT / rp.RUN_TESTS
FEATURES = 'project-governance/config/checkpoint-features.toml'

ALPHA = '''
import os, time, unittest


def note(kind):
    with open(os.path.join(os.environ['SYNTH_NOTES'], '%s-%d.txt' % (kind, os.getpid())), 'a') as stream:
        stream.write('x\\n')
    time.sleep(0.4)


class Plain(unittest.TestCase):
    def test_one(self):
        note('pid')

    def test_two(self):
        note('pid')

    def test_three(self):
        note('pid')

    @unittest.skip('skipped on purpose')
    def test_skipped(self):
        pass


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        note('fixture')

    def test_a(self):
        note('pid')

    def test_b(self):
        note('pid')
'''

BETA = '''
import unittest


class Other(unittest.TestCase):
    def test_four(self):
        self.assertEqual(4, 2 + 2)

    def test_five(self):
        self.assertTrue(True)
'''

BROKEN = '''
import unittest


class Broken(unittest.TestCase):
    def test_passes(self):
        pass

    def test_fails(self):
        self.fail('failed on purpose')
'''


def remove_tree(folder):
    """Remove a folder with its Git repositories (read-only object files included) and require that it is gone."""
    def writable(function, path, _error):
        os.chmod(path, stat.S_IWRITE)
        function(path)
    shutil.rmtree(folder, onerror=writable)
    if Path(folder).exists():
        raise AssertionError('a scratch folder was left behind: %s' % (folder,))


class RunTestsTest(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix='rt-'))
        self.addCleanup(remove_tree, self.folder)
        self.repo, self.notes = self.folder / 'repo', self.folder / 'notes'
        self.repo.mkdir()
        self.notes.mkdir()

    def environment(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(extra, SYNTH_NOTES=str(self.notes))
        return env

    def git(self, *args):
        env = self.environment(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
                               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
        subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, env=env)

    def make(self, features, **suites):
        """Commit the feature file and the named synthetic suites (folder name -> {file: text}) on main."""
        self.git('init', '-b', 'main')
        (self.repo / 'project-governance' / 'config').mkdir(parents=True, exist_ok=True)
        (self.repo / FEATURES).write_text(textwrap.dedent(features), encoding='utf-8', newline='\n')
        for name, files in suites.items():
            (self.repo / name).mkdir()
            for file, text in files.items():
                (self.repo / name / file).write_text(text, encoding='utf-8', newline='\n')
        self.git('add', '-A')
        self.git('commit', '-m', 'Synthetic set')

    def run_runner(self, start, *extra):
        starts = [start] if isinstance(start, str) else list(start)
        done = subprocess.run([sys.executable, '-B', str(RUNNER), *[x for s in starts for x in ('-s', s)],
                               '-p', 'test_*.py', '-v', *extra],
                              cwd=self.repo, capture_output=True, text=True, env=self.environment(), timeout=300)
        return done.returncode, done.stdout + done.stderr

    @staticmethod
    def outcome(output):
        """What both modes must agree on: the number of tests and the final verdict line."""
        return (re.search(r'Ran (\d+) tests', output).group(1),
                re.search(r'^(OK.*|FAILED.*)$', output, re.M).group(1))

    def test_both_modes_give_the_same_results(self):
        passing = {'synth': {'test_alpha.py': ALPHA, 'test_beta.py': BETA}}
        self.make('parallel_tests = false\n', **passing)
        code, sequential = self.run_runner('synth')
        self.assertEqual(code, 0, sequential)
        self.assertIn('test-runner: mode=sequential', sequential)

        self.git('rm', '-q', '--cached', FEATURES)
        (self.repo / FEATURES).write_text('parallel_tests = true\ntest_workers = 0\n', newline='\n')
        self.git('add', FEATURES)
        self.git('commit', '-m', 'Switch on')
        code, parallel = self.run_runner('synth')
        self.assertEqual(code, 0, parallel)
        self.assertIn('test-runner: mode=parallel workers=', parallel)
        self.assertIn('module test_alpha: 6 tests', parallel)
        self.assertIn('module test_beta: 2 tests', parallel)
        self.assertEqual(self.outcome(sequential), ('8', 'OK (skipped=1)'))
        self.assertEqual(self.outcome(parallel), self.outcome(sequential))
        # A class fixture runs once per run, wherever its tests run: one pass for each mode, in one process.
        self.assertEqual(len(list(self.notes.glob('fixture-*.txt'))), 2)

    def test_several_start_folders_discover_together_in_both_modes(self):
        self.make('parallel_tests = false\n', synth={'test_alpha.py': ALPHA, 'test_beta.py': BETA},
                  second={'test_gamma.py': BETA})
        code, sequential = self.run_runner(['synth', 'second'])
        self.assertEqual(code, 0, sequential)
        self.assertIn('test-runner: mode=sequential', sequential)
        self.git('rm', '-q', '--cached', FEATURES)
        (self.repo / FEATURES).write_text('parallel_tests = true\n', newline='\n')
        self.git('add', FEATURES)
        self.git('commit', '-m', 'Switch on')
        code, parallel = self.run_runner(['synth', 'second'])
        self.assertEqual(code, 0, parallel)
        self.assertIn('module test_gamma: 2 tests', parallel)
        self.assertEqual(self.outcome(sequential), ('10', 'OK (skipped=1)'))
        self.assertEqual(self.outcome(parallel), self.outcome(sequential))
        # One folder alone finds only its own tests.
        self.assertEqual(self.outcome(self.run_runner('second')[1])[0], '2')

    def test_the_worker_cap_is_respected(self):
        self.make('parallel_tests = true\ntest_workers = 2\n', synth={'test_alpha.py': ALPHA, 'test_beta.py': BETA})
        code, output = self.run_runner('synth')
        self.assertEqual(code, 0, output)
        expected = min(2, os.cpu_count() or 1)
        self.assertIn('mode=parallel workers=%d ' % expected, output)
        pids = {p.name.split('-')[1] for p in self.notes.glob('pid-*.txt')}
        self.assertLessEqual(len(pids), expected)
        self.assertGreaterEqual(len(pids), 1)

    def test_a_failing_test_is_named_and_exits_non_zero_in_both_modes(self):
        self.make('parallel_tests = false\n', synth={'test_broken.py': BROKEN})
        code, sequential = self.run_runner('synth')
        self.git('rm', '-q', '--cached', FEATURES)
        (self.repo / FEATURES).write_text('parallel_tests = true\n', newline='\n')
        self.git('add', FEATURES)
        self.git('commit', '-m', 'Switch on')
        parallel_code, parallel = self.run_runner('synth')
        self.assertEqual((code, parallel_code), (1, 1))
        for output in (sequential, parallel):
            self.assertIn('test_fails', output)
            self.assertRegex(output, r'failed on purpose')
            self.assertEqual(self.outcome(output)[0], '2')
            self.assertTrue(self.outcome(output)[1].startswith('FAILED'))

    def test_a_missing_key_runs_sequentially_and_says_why(self):
        self.make('early_approval_reach = "low"\n', synth={'test_beta.py': BETA})
        code, output = self.run_runner('synth')
        self.assertEqual(code, 0, output)
        self.assertIn('mode=sequential (parallel_tests is not the boolean true)', output)

    def test_no_tests_found_is_a_failure(self):
        self.make('parallel_tests = true\n', synth={'readme.txt': 'nothing\n'})
        code, output = self.run_runner('synth')
        self.assertNotEqual(code, 0, output)


class ScratchFolderTest(unittest.TestCase):
    def test_the_scratch_folder_is_tmp_beside_the_main_checkout_from_a_checkout_a_worktree_and_a_clone(self):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid',
                   GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')
        base = Path(tempfile.mkdtemp(prefix='rt-')).resolve()
        self.addCleanup(remove_tree, base)

        def git(*args):
            subprocess.run(['git', *args], check=True, capture_output=True, env=env, cwd=base)

        main, linked, clone = base / 'main', base / 'linked', base / 'clone'
        main.mkdir()
        git('-C', str(main), 'init', '-b', 'main')
        git('-C', str(main), 'commit', '--allow-empty', '-m', 'First')
        git('-C', str(main), 'worktree', 'add', str(linked), '-b', 'tx/one')
        git('clone', '-q', str(main), str(clone))
        for root, owner in ((main, main), (main / '.', main), (linked, main), (clone, clone)):
            with self.subTest(root=root.name):
                self.assertEqual(runner.main_checkout_root(root), owner)
                self.assertEqual(runner.scratch_folder(root), owner.parent / 'tmp')
        self.assertTrue((base / 'tmp').is_dir())
        self.assertEqual(sorted(p.name for p in base.iterdir()), ['clone', 'linked', 'main', 'tmp'])


if __name__ == '__main__':
    unittest.main()
