"""Worktree isolation and guarded lifecycle in disposable repositories only."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
import checkpoint as c
from governance.tools.coordination import local_jobs
from governance.tools.worktrees import claims
from governance.tools.lifecycle import early_approval as ea
from governance.tools.support import local_state
from governance.tests.lifecycle import test_checkpoint as fixtures
from governance.tools.continuity import validate_continuity as continuity
from governance.tools.worktrees import guard as w
import repo_paths as rp

REAL_CARRY_SWITCH = ea.carry_switch  # the tests below patch the module attribute


# Runs as a fixture check inside each mission's own checkout: it names itself by that folder, then waits for the
# other mission's check to name itself too.
BARRIER = '''import pathlib, sys, time
meeting = pathlib.Path(sys.argv[1])
(meeting / pathlib.Path.cwd().name).write_text('here')
deadline = time.monotonic() + 120
while time.monotonic() < deadline:
    if len(list(meeting.iterdir())) >= 2:
        print('PASS: both missions are validating')
        sys.exit(0)
    time.sleep(0.1)
sys.exit('the other mission never started its check')
'''


# The command line of the real tool, with the same two project-evidence stand-ins the fixture declares in its setUp.
PREPARE_DRIVER = '''import sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import checkpoint as c
sys.argv = ['checkpoint.py'] + sys.argv[2:]
with patch.object(c.Checkpoint, 'committed_checkpoint', autospec=True, side_effect=lambda tool: tool.git(
        'log', '-1', '--format=%H', 'HEAD', '--', 'runtime.txt').decode().strip()), \\
        patch.object(c.Checkpoint, 'durable_continuity', return_value=None):
    sys.exit(c.main())
'''


def write_checker(root, text):
    """Put a (fake) continuity checker where the committed layout keeps it, creating its package folder."""
    path = root / rp.VALIDATE_CONTINUITY
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class PythonMinimumTest(unittest.TestCase):
    def test_old_python_stops_before_repository_setup_or_locking(self):
        for version in ((3, 12, 14), (3, 11, 9)):
            with self.subTest(version=version), patch.object(sys, 'version_info', version), \
                    patch.object(sys, 'argv', [str(rp.ROOT / rp.WORKTREE), '--main', 'nonexistent-main', 'create',
                                              '--path', 'nonexistent-mission', '--owner', 'mission',
                                              '--expected-main', '0' * 40]), \
                    patch.object(c, 'Checkpoint', side_effect=AssertionError('repository setup reached')) as setup, \
                    patch.object(os, 'open', side_effect=AssertionError('lock reached')) as lock, \
                    patch.object(Path, 'mkdir', side_effect=AssertionError('scratch creation reached')) as mkdir, \
                    patch.object(subprocess, 'run', side_effect=AssertionError('Git mutation reached')) as run:
                message = io.StringIO()
                with redirect_stderr(message), self.assertRaises(SystemExit) as stopped:
                    runpy.run_path(str(rp.ROOT / rp.WORKTREE), run_name='__main__')
                self.assertEqual(stopped.exception.code, 1)
                self.assertIn('governance tools require Python 3.13 or later; this is Python '
                              + '.'.join(str(part) for part in version), message.getvalue())
                setup.assert_not_called()
                lock.assert_not_called()
                mkdir.assert_not_called()
                run.assert_not_called()

    def test_old_python_cli_exits_nonzero_before_every_action(self):
        loader = '''
import runpy, sys
sys.path.insert(0, sys.argv[1])
sys.version_info = (3, 12, 14)
sys.argv = sys.argv[2:]
runpy.run_path(sys.argv[0], run_name='__main__')
'''
        for action in ('create', 'reconcile', 'integrate', 'remove'):
            with self.subTest(action=action):
                result = subprocess.run([sys.executable, '-I', '-B', '-c', loader,
                                         str(Path(c.__file__).parent), str(rp.ROOT / rp.WORKTREE),
                                         '--main', 'nonexistent-main', action],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr.startswith(
                    'FAIL: governance tools require Python 3.13 or later; this is Python 3.12.14 ('), result.stderr)
                self.assertIn('Nothing was searched for or downloaded.', result.stderr)

    def test_minimum_and_newer_python_admit_lifecycle_module(self):
        for version in ((3, 13, 0), (3, 14, 0)):
            with self.subTest(version=version), patch.object(sys, 'version_info', version):
                module = runpy.run_path(w.__file__)
                self.assertTrue(callable(module['Worktrees']))
                self.assertTrue(callable(module['main']))


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CheckpointTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.root = self.f.root
        (self.root / 'a.txt').write_text('old\n')
        self.flow = w.Worktrees(self.root)
        self.owner = 'mission-1'
        self.path = Path(w.layout(self.root, self.owner)['worktree'])
        self.base = self.f.parent
        # Generic Git fixtures declare their stand-ins here, never in production guards.
        self.real_continuity = self.flow.continuity
        self.real_candidate = self.flow.candidate_continuity
        def fixture_continuity(tool):
            if (tool.root / rp.VALIDATE_CONTINUITY).is_file():
                return self.real_continuity(tool)
            policy, _ = tool.data()
            result = tool.checks([{'id': 'continuity', 'argv': policy['commands']['continuity']}])[0]
            tool.completed_continuity(result)
            return result
        def fixture_candidate(source, expected_hash, target=None):
            if (source.root / rp.VALIDATE_CONTINUITY).is_file():
                return self.real_candidate(source, expected_hash, target)
            self.assertIsNone(expected_hash)
            return fixture_continuity(source if target is None else target)
        for name, replacement in (('continuity', fixture_continuity),
                                  ('candidate_continuity', fixture_candidate)):
            mock = patch.object(self.flow, name, side_effect=replacement)
            mock.start()
            self.addCleanup(mock.stop)


    def create(self):
        self.flow.create(None, self.owner, self.base)
        return c.Checkpoint(self.path)

    def prepared(self):
        t = self.create()
        (self.path / 'a.txt').write_text('owned edit\n')
        p = t.prepare(['a.txt'], self.f.message, 'test', self.base,
                      'refs/heads/tx/' + self.owner, 'Disposable fixture scope')
        return t, p

    def committed(self):
        t, p = self.prepared()
        with redirect_stdout(io.StringIO()):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        result = t.commit()
        self.assertEqual(result['state'], 'verified')
        t.finish()
        return t, result['result_sha']

    def test_two_missions_prepare_at_the_same_time_in_separate_processes(self):
        # The one check of the fixture profile waits until the other mission's check is running too, so the
        # prepares can only pass when they really overlap; a prepare that made the other wait would time out.
        meeting = Path(self.f.temp.name) / 'meeting'
        meeting.mkdir()
        barrier = Path(self.f.temp.name) / 'barrier.py'
        barrier.write_text(BARRIER, encoding='utf-8', newline='\n')
        policy, profiles = self.f.policy, self.f.profiles
        policy['commands']['regressions'] = ['{python}', '-B', str(barrier), str(meeting)]
        profiles['profiles']['test']['checks'] = [{'id': name, 'argv': argv} for name, argv in policy['commands'].items()]
        self.f.write_json(c.POLICY, policy)
        self.f.write_json(c.PROFILES, profiles)
        self.f.git('add', '--', c.POLICY, c.PROFILES)
        self.f.git('commit', '-m', 'Overlap fixture checks')
        self.base = self.f.parent = self.f.git('rev-parse', 'HEAD').strip()
        owners = ('mission-1', 'mission-2')
        for owner in owners:  # a lifecycle operation refuses a prepare, so both worktrees exist before either starts
            self.flow.create(None, owner, self.base)
        running = []
        for owner in owners:
            path = Path(w.layout(self.root, owner)['worktree'])
            (path / 'a.txt').write_text('edit by ' + owner + '\n')
            manifest = Path(self.f.temp.name) / (owner + '-scope.json')
            manifest.write_text('["a.txt"]', encoding='utf-8', newline='\n')
            argv = [sys.executable, '-B', '-c', PREPARE_DRIVER, str(Path(c.__file__).parent), '--repo', str(path),
                    'prepare', '--manifest', str(manifest),
                    '--message', str(self.f.message), '--profile', 'test', '--expected-head', self.base,
                    '--expected-branch', 'refs/heads/tx/' + owner, '--delegation', 'Disposable fixture scope']
            running.append((owner, path, subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)))
        for owner, path, process in running:
            stdout, stderr = process.communicate(timeout=300)
            self.assertEqual(process.returncode, 0, owner + ': ' + stderr.decode(errors='replace'))
            self.assertEqual(json.loads(stdout)['state'], 'prepared', owner)
            self.assertEqual(c.Checkpoint(path).load()['state'], 'prepared', owner)
        self.assertEqual(sorted(p.name for p in meeting.iterdir()), ['mission-1', 'mission-2'])

    def test_same_tree_ref_churn_during_integration_is_audited(self):
        old = 'refs/codex/turn-diffs/captures/old/base'
        new = 'refs/codex/turn-diffs/captures/new/base'
        tree = self.f.git('rev-parse', 'HEAD^{tree}').strip()
        self.f.git('update-ref', old, tree)
        t, tip = self.committed()
        continuity = self.flow.continuity
        def churn(tool):
            result = continuity(tool)
            if self.f.git('for-each-ref', old).strip():
                self.f.git('update-ref', '-d', old)
                self.f.git('update-ref', new, tree)
            return result
        with patch.object(self.flow, 'continuity', side_effect=churn):
            result = self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertTrue(result['transient_ref_audits'])
        self.assertEqual(self.flow.main.head(), tip)
        self.assertEqual(self.f.git('rev-parse', new).strip(), tree)

    def test_protected_ref_churn_stops_integration_before_promotion(self):
        t, tip = self.committed()
        continuity = self.flow.continuity
        for name in ('refs/heads/protected', 'refs/tags/protected', 'refs/unknown/protected',
                     'refs/replace/' + '1' * 40, 'refs/notes/protected', 'refs/heads/feature/protected'):
            with self.subTest(name=name):
                def drift(tool):
                    result = continuity(tool)
                    self.f.git('update-ref', name, tip)
                    return result
                with patch.object(self.flow, 'continuity', side_effect=drift):
                    with self.assertRaisesRegex(ValueError, 'Shared refs/stash/configuration changed'):
                        self.flow.integrate(self.path, self.owner, self.base, tip)
                self.assertEqual(self.flow.main.head(), self.base)
                self.assertEqual(t.head(), tip)
                self.f.git('update-ref', '-d', name)

    def test_unrelated_branches_worktrees_and_stash_may_change_during_integration(self):
        for name in ('refs/heads/tx/other', 'refs/heads/claude/session'):
            self.f.git('update-ref', name, self.base)
        t, tip = self.committed()
        continuity = self.flow.continuity
        def churn(tool):
            result = continuity(tool)
            for name in ('refs/remotes/origin/topic', 'refs/codex/anything', 'refs/stash'):  # created
                self.f.git('update-ref', name, self.base)
            self.f.git('update-ref', 'refs/heads/claude/session', tip)  # moved
            self.f.git('update-ref', '-d', 'refs/heads/tx/other')  # deleted
            return result
        with patch.object(self.flow, 'continuity', side_effect=churn):
            result = self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), tip)
        self.assertEqual(self.f.git('rev-parse', 'refs/heads/claude/session').strip(), tip)
        self.assertEqual(self.f.git('rev-parse', 'refs/stash').strip(), self.base)
        audit = result['transient_ref_audits'][-1]
        self.assertEqual(audit['policy'], c.REF_POLICY_V2)
        self.assertEqual((audit['removed'], audit['moved']), (['refs/heads/tx/other'], ['refs/heads/claude/session']))

    def test_another_worktree_created_and_removed_during_integration_is_tolerated(self):
        t, tip = self.committed()
        continuity = self.flow.continuity
        def churn(tool):
            result = continuity(tool)
            if not other.exists():
                self.f.git('worktree', 'add', '-b', 'claude/session-fixture', str(other), tip)
            return result
        other = self.root.parent / 'session-fixture'
        with patch.object(self.flow, 'continuity', side_effect=churn):
            self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), tip)
        self.f.git('worktree', 'remove', str(other))
        self.f.git('branch', '-D', 'claude/session-fixture')

    def test_configuration_change_stops_integration(self):
        t, tip = self.committed()
        continuity = self.flow.continuity
        def drift(tool):
            result = continuity(tool)
            self.f.git('config', 'fixture.drift', 'true')
            return result
        with patch.object(self.flow, 'continuity', side_effect=drift):
            with self.assertRaisesRegex(ValueError, 'Shared refs/stash/configuration changed'):
                self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), self.base)
        self.f.git('config', '--unset', 'fixture.drift')

    def test_owner_branch_or_another_missions_exact_ref_change_stops_integration(self):
        t, tip = self.committed()
        continuity = self.flow.continuity
        def drift(tool):
            result = continuity(tool)
            self.f.git('update-ref', 'refs/heads/' + 'tx/' + self.owner, self.base)
            return result
        with patch.object(self.flow, 'continuity', side_effect=drift):
            with self.assertRaises(ValueError):
                self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), self.base)

    def test_shared_state_without_an_owner_keeps_every_transaction_branch_exact(self):
        own = 'refs/heads/tx/' + self.owner
        anonymous, named = self.flow.shared_state(), self.flow.shared_state(own)
        self.f.git('update-ref', 'refs/heads/tx/anyone', self.base)
        with self.assertRaisesRegex(ValueError, 'drift'):
            c.check_ref_context(anonymous, self.flow.shared_state(), 'drift', [])
        c.check_ref_context(named, self.flow.shared_state(own), 'drift', [])

    def test_prepares_of_different_missions_overlap(self):
        # Each mission has its own worktree, private index and operation lock; neither holds the lifecycle lock
        # while validating, and the other's creation, branch and registration are tolerated by the guard.
        first = self.create()
        (self.path / 'a.txt').write_text('first edit\n')
        second_owner = 'mission-2'
        second_path = Path(w.layout(self.root, second_owner)['worktree'])
        runner = first.runner
        seen = {}
        def run(argv, root):
            result = runner(argv, root)
            if not seen:
                seen['first'] = True  # mission 2 is created, prepared and abandoned inside mission 1's validation window
                self.flow.create(None, second_owner, self.base)
                second = c.Checkpoint(second_path)
                (second_path / 'a.txt').write_text('second edit\n')
                other = second.prepare(['a.txt'], self.f.message, 'test', self.base,
                                       'refs/heads/tx/' + second_owner, 'Disposable fixture scope')
                seen['second'] = other
            return result
        first.runner = run
        packet = first.prepare(['a.txt'], self.f.message, 'test', self.base,
                               'refs/heads/tx/' + self.owner, 'Disposable fixture scope')
        self.assertEqual(packet['state'], 'prepared')
        self.assertEqual(seen['second']['state'], 'prepared')
        self.assertEqual(packet['ref_policy'], c.REF_POLICY_V2)
        self.assertTrue(packet['transient_ref_audits'])

    def test_independent_index_dirty_state_and_exact_lifecycle(self):
        t, p = self.prepared()
        self.assertEqual(p['repo']['common'], str(self.root / '.git'))
        self.assertNotEqual(t.gitdir, self.f.tool.gitdir)
        self.assertEqual(self.f.tool.inventory(), [])
        (self.root / 'b.txt').write_text('other mission must remain\n')
        self.f.git('add', 'b.txt')
        self.assertEqual(t.inventory(), ['a.txt'])
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            t.commit()
        with redirect_stdout(io.StringIO()):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        result = t.commit()
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(t.git('cat-file', 'commit', result['result_sha']).split(b'\n\n', 1)[1],
                         self.f.message.read_bytes())
        t.finish()
        self.assertEqual(self.f.tool.inventory(), ['b.txt'])
        self.assertEqual(self.f.tool.head(), self.base)
        self.assertEqual((self.root / 'b.txt').read_text(), 'other mission must remain\n')

    def test_fast_forward_and_remove_preserve_stash(self):
        (self.root / 'b.txt').write_text('preserved stash\n')
        self.f.git('stash', 'push', '-m', 'disposable preserved state')
        stash = self.f.git('rev-parse', 'refs/stash')
        t, tip = self.committed()
        store = t.store
        self.assertEqual(store, t.gitdir / 'governance')
        self.assertTrue(store.is_dir())
        self.assertEqual(self.flow.integrate(self.path, self.owner, self.base, tip)['main'], tip)
        self.assertEqual(self.f.git('rev-parse', 'refs/stash'), stash)
        self.flow.remove(self.path, self.owner, tip, tip)
        self.assertFalse(self.path.exists())
        self.assertFalse(store.exists(), 'the store dies with its worktree')
        self.assertEqual(len(w.inventory(self.flow.main)), 1)
        self.assertEqual(self.f.git('rev-parse', 'refs/stash'), stash)

    def test_duplicate_branch_and_owner_rejected(self):
        self.create()
        self.f.git('branch', 'tx/second')
        with self.assertRaisesRegex(ValueError, 'Branch already exists'):
            self.flow.create(None, 'second', self.base)
        with self.assertRaisesRegex(ValueError, 'Branch does not belong'):
            self.flow.source(self.path, 'someone-else')
        with self.assertRaisesRegex(ValueError, 'absent path'):
            self.flow.create(None, self.owner, self.base)

    def test_unowned_and_reassigned_worktree_rejected(self):
        t = self.create()
        self.f.git('worktree', 'unlock', str(self.path))
        with self.assertRaisesRegex(ValueError, 'ownership lock'):
            t.quiet_operation()
        self.f.git('worktree', 'lock', '--reason', 'transaction:other', str(self.path))
        with self.assertRaisesRegex(ValueError, 'ownership/branch mismatch'):
            t.quiet_operation()

    def test_main_preparation_blocked_when_worktree_exists(self):
        self.create()
        (self.root / 'a.txt').write_text('main scratch\n')
        with self.assertRaisesRegex(ValueError, 'integration territory'):
            self.f.prepare()
        self.assertEqual(self.f.tool.inventory(), [])

    def test_shared_hook_rejected_in_linked_checkout(self):
        t = self.create()
        (self.root / '.git/hooks/commit-msg').write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'hooks'):
            t.quiet_operation()

    def test_shared_operation_lock_blocks_checkpoint_and_lifecycle(self):
        t = self.create()
        with w.lifecycle_lock(self.flow.main):
            with self.assertRaisesRegex(ValueError, 'lifecycle operation active'):
                with t.lock():
                    self.fail('lock admitted')
            with self.assertRaisesRegex(ValueError, 'already active'):
                self.flow.create(None, 'second', self.base)

    def test_source_checkpoint_lock_blocks_integration(self):
        t = self.create()
        with t.lock():
            with self.assertRaisesRegex(ValueError, 'Operation lock exists'):
                self.flow.integrate(self.path, self.owner, self.base, self.base)
        self.assertEqual(self.flow.main.head(), self.base)

    def test_stale_parent_and_dirty_main_rejected(self):
        t = self.create()
        with self.assertRaisesRegex(ValueError, 'Expected full HEAD'):
            self.flow.integrate(self.path, self.owner, '0' * 40, self.base)
        (self.root / 'b.txt').write_text('unrelated main edit\n')
        with self.assertRaisesRegex(ValueError, 'must be clean'):
            self.flow.integrate(self.path, self.owner, self.base, self.base)
        self.assertEqual(self.flow.main.head(), self.base)

    def test_unfinished_checkpoint_blocks_integration_and_removal(self):
        t = self.create()
        (t.store / 'pending.json').write_text('{}')
        for action in (self.flow.integrate, self.flow.remove):
            with self.assertRaisesRegex(ValueError, 'scratch not idle'):
                action(self.path, self.owner, self.base, self.base)

    def test_divergence_blocks_without_merge_or_rewrite(self):
        t, tip = self.committed()
        (self.root / 'b.txt').write_text('disposable main change\n')
        self.f.git('add', 'b.txt')
        self.f.git('commit', '-m', 'Disposable divergence fixture')
        current = self.flow.main.head()
        with self.assertRaisesRegex(ValueError, 'Git failed: merge-base'):
            self.flow.integrate(self.path, self.owner, current, tip)
        self.assertEqual(self.flow.main.head(), current)
        self.assertEqual(t.head(), tip)

    def test_ignored_files_are_disposable_by_gitignore_and_reported(self):
        # .gitignore is the only declaration of disposable local state; a mission worktree starts with
        # none, so anything ignored was generated there. The result says what was deleted.
        self.create()
        (self.path / 'tmp').mkdir()
        (self.path / 'tmp/generated.txt').write_text('regenerable')
        (self.path / 'ignored').mkdir()
        (self.path / 'ignored/workspace.xml').write_text('<ide/>')
        (self.path / '__pycache__').mkdir()
        (self.path / '__pycache__/module.pyc').write_bytes(b'\x00')
        ignored = subprocess.check_output(['git', '-C', str(self.path), 'ls-files', '--others', '--ignored',
                                           '--exclude-standard']).decode().split()
        self.assertIn('tmp/generated.txt', ignored)
        self.assertIn('ignored/workspace.xml', ignored)
        result = self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertEqual(result['state'], 'removed')
        self.assertEqual(result['ignored_files_deleted']['count'], len(ignored))
        self.assertIn('tmp/generated.txt', result['ignored_files_deleted']['sample'])
        self.assertFalse(self.path.exists())

    def test_local_coordination_blocks_removal_until_moved(self):
        (self.root / '.git/info/exclude').write_text('/.local/\n')
        self.create()
        notes = self.path / '.local/coordination'
        notes.mkdir(parents=True)
        path = notes / 'pending.json'
        path.write_text('pending handoff')
        with self.assertRaisesRegex(ValueError, 'Local coordination remains'):
            self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertEqual(path.read_text(), 'pending handoff')
        path.rename(self.root / 'recovered.json')
        (self.root / 'recovered.json').unlink()
        self.assertEqual(self.flow.remove(self.path, self.owner, self.base, self.base)['state'], 'removed')

    def test_removal_deletes_the_missions_own_review_folder_last_and_no_other(self):
        self.create()
        review = Path(w.layout(self.root, self.owner)['review'])
        other = Path(w.layout(self.root, 'mission-2')['review'])
        for folder in (review, other):
            folder.mkdir(parents=True)
            (folder / 'scope.json').write_text('["a.txt"]')
            (folder / 'message.txt').write_text('message\n')
        result = self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertEqual(result['review_folder'], {'folder': str(review), 'removed': True,
                                                   'files': ['message.txt', 'scope.json']})
        self.assertFalse(review.exists())
        self.assertFalse(self.path.exists())
        self.assertEqual(sorted(p.name for p in other.iterdir()), ['message.txt', 'scope.json'])
        self.create()  # a removal with no review folder has nothing to delete
        self.assertEqual(self.flow.remove(self.path, self.owner, self.base, self.base)['review_folder'],
                         {'folder': str(review), 'removed': False, 'files': []})

    def test_a_review_folder_with_more_than_plain_files_refuses_removal_and_changes_nothing(self):
        self.create()
        review = Path(w.layout(self.root, self.owner)['review'])
        (review / 'nested').mkdir(parents=True)
        (review / 'scope.json').write_text('["a.txt"]')
        with self.assertRaisesRegex(ValueError, r'holds more than plain files \(nested\)'):
            self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertTrue(self.path.exists())
        self.assertEqual(w.inventory(self.flow.main)[1]['locked'], 'transaction:' + self.owner)
        self.f.git('rev-parse', '--verify', 'refs/heads/tx/' + self.owner)
        self.assertEqual(sorted(p.name for p in review.iterdir()), ['nested', 'scope.json'])
        (review / 'nested').rmdir()
        self.assertTrue(self.flow.remove(self.path, self.owner, self.base, self.base)['review_folder']['removed'])

    def test_a_review_file_that_cannot_be_deleted_is_reported_after_the_removal(self):
        self.create()
        review = Path(w.layout(self.root, self.owner)['review'])
        review.mkdir(parents=True)
        (review / 'scope.json').write_text('["a.txt"]')
        unlink = Path.unlink

        def held(path, *args, **kwargs):
            if path.parent == review:
                raise PermissionError('in use')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', held):
            result = self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertEqual(result['state'], 'removed')
        self.assertFalse(self.path.exists())
        self.assertFalse(result['review_folder']['removed'])
        self.assertIn('in use', result['review_folder']['left'])
        self.assertTrue((review / 'scope.json').exists())

    def test_non_ignored_untracked_content_still_blocks_removal(self):
        self.create()
        (self.path / 'precious.txt').write_text('not ignored, not committed')
        with self.assertRaises(ValueError):
            self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertTrue((self.path / 'precious.txt').exists())
        self.assertEqual(w.inventory(self.flow.main)[1]['locked'], 'transaction:' + self.owner)

    def test_unintegrated_tip_cannot_be_removed(self):
        t, tip = self.committed()
        with self.assertRaisesRegex(ValueError, 'Git failed: merge-base'):
            self.flow.remove(self.path, self.owner, self.base, tip)
        self.assertTrue(self.path.exists())

    def superseded_case(self, landed_job='job_x', on_main=True):
        """mission-1 committed and finished under job_x, never integrated; a replacement commit landed by mission-2."""
        (self.root / '.git/info/exclude').write_text('/.local/\n')
        self.jobs = local_jobs.safe_directory(self.root, create=True)
        local_jobs.put(self.jobs, {'version': 1, 'handle': 'job_x', 'status': 'WIP', 'worker': 'w', 'activity': 'ACTIVE',
                                   'updated_at': '2026-10-19T00:00:00+00:00', 'step': 's', 'waiting': None,
                                   'pending': [], 'commits': [], 'records': []}, None)
        t, tip = self.committed()
        claims.declare_paths(self.flow.main.common, self.owner, 'job_x', str(self.path), [('a.txt', 'record', [])])
        (self.root / 'a.txt').write_text('replacement\n')
        self.f.git('add', 'a.txt')
        if on_main:
            self.f.git('commit', '-m', 'Replacement landed by mission-2')
            replacement = main = self.flow.main.head()
        else:
            tree = self.f.git('write-tree').strip()
            replacement = self.f.git('commit-tree', tree, '-p', self.base, '-m', 'Replacement not on main').strip()
            self.f.git('reset', '-q')
            (self.root / 'a.txt').write_text('old\n')
            main = self.base
        claims.landed(self.flow.main.common, 'mission-2', landed_job, replacement)
        return tip, replacement, main

    def assert_nothing_removed(self, tip):
        self.assertTrue(self.path.exists())
        self.assertEqual(self.f.git('rev-parse', 'refs/heads/tx/' + self.owner).strip(), tip)
        self.assertEqual(local_jobs.read(self.jobs / 'job_x.json')['pending'], [])

    def test_a_superseded_mission_is_removed_after_its_replacement_landed_for_the_same_job(self):
        tip, replacement, main = self.superseded_case()
        review = Path(w.layout(self.root, self.owner)['review'])
        review.mkdir(parents=True)
        (review / 'scope.json').write_text('["a.txt"]')
        result = self.flow.remove(self.path, self.owner, main, tip, replacement)
        self.assertEqual(result['state'], 'removed')
        self.assertEqual({k: result['superseded_by'][k] for k in ('job_handle', 'replacement', 'replacement_owner', 'old_tip')},
                         {'job_handle': 'job_x', 'replacement': replacement, 'replacement_owner': 'mission-2', 'old_tip': tip})
        self.assertFalse(self.path.exists())
        self.assertTrue(result['review_folder']['removed'])
        self.assertEqual(self.f.git('for-each-ref', 'refs/heads/tx/').strip(), '')
        self.assertEqual(self.flow.main.head(), main)
        note = local_jobs.read(self.jobs / 'job_x.json')['pending'][-1]
        for text in (self.owner, tip, replacement, 'mission-2', result['superseded_by']['subject']):
            self.assertIn(text, note['text'])

    def test_superseded_removal_refuses_a_replacement_that_is_not_on_main(self):
        tip, replacement, main = self.superseded_case(on_main=False)
        with self.assertRaisesRegex(ValueError, 'is not on main'):
            self.flow.remove(self.path, self.owner, main, tip, replacement)
        self.assert_nothing_removed(tip)

    def test_superseded_removal_refuses_a_replacement_landed_for_another_job(self):
        tip, replacement, main = self.superseded_case(landed_job='job_y')
        with self.assertRaisesRegex(ValueError, 'No landing of [0-9a-f]{40} is recorded under the Job job_x'):
            self.flow.remove(self.path, self.owner, main, tip, replacement)
        self.assert_nothing_removed(tip)

    def test_superseded_removal_refuses_a_live_packet_and_another_owner(self):
        tip, replacement, main = self.superseded_case()
        store = c.Checkpoint(self.path).store
        (store / c.TRANSACTION).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'scratch not idle'):
            self.flow.remove(self.path, self.owner, main, tip, replacement)
        self.assert_nothing_removed(tip)
        (store / c.TRANSACTION).unlink()
        self.flow.create(None, 'mission-3', main)
        with self.assertRaisesRegex(ValueError, 'Branch does not belong'):
            self.flow.remove(self.path, 'mission-3', main, tip, replacement)
        self.assert_nothing_removed(tip)
        self.assertEqual(self.flow.remove(self.path, self.owner, main, tip, replacement)['state'], 'removed')

    def test_nested_path_rejected(self):
        # The container sits outside every checkout, so only a changed layout can reach the nesting guard.
        nested = self.root / 'nested'
        with patch.object(w, 'layout', return_value={'worktree': str(nested), 'review': str(self.root / 'r')}):
            with self.assertRaisesRegex(ValueError, 'must not nest'):
                self.flow.create(None, self.owner, self.base)
        self.assertFalse(nested.exists())

    def test_layout_is_derived_from_the_main_folder_and_touches_nothing(self):
        paths = w.layout(self.root, self.owner)
        container = self.root.parent / '_worktrees' / self.root.name
        self.assertEqual(paths, {'container': str(container), 'worktree': str(container / self.owner),
                                 'review': str(container / '_review' / self.owner)})
        self.assertFalse((self.root.parent / '_worktrees').exists())
        for bad in ('_review', 'Mission', 'a/b', '', 'x' * 65):
            with self.subTest(owner=bad), self.assertRaisesRegex(ValueError, 'lowercase mission ID'):
                w.layout(self.root, bad)

    def test_paths_command_prints_the_layout_and_creates_nothing(self):
        out = io.StringIO()
        with patch.object(sys, 'argv', [str(rp.ROOT / rp.WORKTREE), '--main', str(self.root), 'paths', '--owner', self.owner]), \
                redirect_stdout(out):
            self.assertEqual(w.main(), 0)
        self.assertEqual(json.loads(out.getvalue()), w.layout(self.root, self.owner))
        self.assertFalse((self.root.parent / '_worktrees').exists())

    def test_create_derives_the_path_and_makes_only_the_container_folders(self):
        result = self.flow.create(None, self.owner, self.base)
        paths = w.layout(self.root, self.owner)
        self.assertEqual(result['worktree'], str(self.path.resolve()))
        self.assertEqual(result['review'], paths['review'])
        container = Path(paths['container'])
        self.assertEqual(sorted(p.name for p in container.iterdir()), ['_review', self.owner])
        self.assertEqual(list((container / '_review').iterdir()), [])
        self.assertEqual(self.f.git('rev-parse', '--abbrev-ref', 'HEAD').strip(), 'main')

    def test_explicit_path_must_equal_the_derived_one(self):
        elsewhere = self.root.parent / 'mission'
        for bad in (elsewhere, self.path.parent / 'other', self.path.parent / '_review' / self.owner):
            with self.subTest(path=bad), self.assertRaisesRegex(ValueError, 'live in the container'):
                self.flow.create(bad, self.owner, self.base)
            self.assertFalse(bad.exists())
        self.assertFalse((self.root.parent / '_worktrees').exists())
        self.assertEqual(self.flow.create(self.path, self.owner, self.base)['branch'], 'refs/heads/tx/' + self.owner)

    def test_refused_create_leaves_no_container_folders(self):
        (self.root / 'a.txt').write_text('dirty main\n')
        with self.assertRaisesRegex(ValueError, 'clean'):
            self.flow.create(None, self.owner, self.base)
        with self.assertRaisesRegex(ValueError, 'Expected full HEAD'):
            self.f.git('checkout', '--', 'a.txt')
            self.flow.create(None, self.owner, '0' * 40)
        self.assertFalse((self.root.parent / '_worktrees').exists())

    def test_symlinked_container_is_refused(self):
        real = self.root.parent / 'real'
        real.mkdir()
        try:
            (self.root.parent / '_worktrees').symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest('symbolic links unavailable')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.flow.create(None, self.owner, self.base)
        self.assertEqual(list(real.iterdir()), [])

    def test_continuity_failure_prevents_integration(self):
        self.create()
        with patch.object(self.flow, 'continuity', side_effect=ValueError('continuity failed')):
            with self.assertRaisesRegex(ValueError, 'continuity failed'):
                self.flow.integrate(self.path, self.owner, self.base, self.base)
        self.assertEqual(self.flow.main.head(), self.base)

    def test_an_early_approved_tip_needs_the_switch_at_integration(self):
        t, tip = self.committed()
        ea.write_landing(t, None, dict(ea.read_landing(t), approval_path='early'))
        with patch.object(ea, 'switch', return_value=('off', 'off for the test')),                 self.assertRaisesRegex(ValueError, 'seal must be redone'):
            self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), self.base)
        with patch.object(ea, 'switch', return_value=('low', 'reach low')):
            self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), tip)

    def test_a_sealed_tip_integrates_whatever_the_switch_reads(self):
        t, tip = self.committed()
        self.assertEqual(ea.read_landing(t)['approval_path'], 'sealed')
        with patch.object(ea, 'switch', return_value=('off', 'off for the test')):
            self.flow.integrate(self.path, self.owner, self.base, tip)
        self.assertEqual(self.flow.main.head(), tip)

    def test_shared_integration_hook_rejected(self):
        self.create()
        (self.root / '.git/hooks/post-merge').write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'hooks'):
            self.flow.integrate(self.path, self.owner, self.base, self.base)

    def test_packet_cannot_move_between_worktrees(self):
        t, p = self.prepared()
        # Share the key deliberately; repository identity must still reject the packet.
        for name in (c.RECEIPT_KEY, c.TRANSACTION):
            (self.f.tool.store / name).write_bytes((t.store / name).read_bytes())
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.f.tool.load()

    def test_linked_stale_working_content_invalidates_approval(self):
        t, p = self.prepared()
        (self.path / 'b.txt').write_text('changed after seal\n')
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'Stale working'):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        self.assertNotIn('approval', t.load())

    def test_forced_duplicate_branch_registration_rejected(self):
        t = self.create()
        self.f.git('worktree', 'add', '--force', str(self.root.parent / 'duplicate'), 'tx/' + self.owner)
        with self.assertRaisesRegex(ValueError, 'same branch'):
            t.quiet_operation()
        with self.assertRaisesRegex(ValueError, 'conflicting worktree ownership'):
            self.flow.source(self.path, self.owner)

    def test_runtime_continuation_stays_in_owned_worktree(self):
        t = self.create()
        (self.path / 'runtime.txt').write_text('runtime change\n')
        p = t.prepare(['runtime.txt'], self.f.message, 'test', self.base,
                      'refs/heads/tx/' + self.owner, 'Disposable runtime scope')
        with redirect_stdout(io.StringIO()):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        # Generic fixture has no actual service. Exercise lifecycle, not runtime acceptance.
        with patch.object(c.Checkpoint, 'runtime_pending', return_value='pending'):
            previous = t.commit()
        with self.assertRaisesRegex(ValueError, 'Continuity pending'):
            t.finish()
        t.continue_records()
        with self.assertRaisesRegex(ValueError, 'only continuity records'):
            t.prepare(['a.txt'], self.f.message, 'test', t.head(), t.branch(), 'Unrelated scope')
        (self.path / 'devlog').mkdir()
        (self.path / 'devlog/runtime.md').write_text(previous['result_sha'])
        p = t.prepare(['devlog/runtime.md'], self.f.message, 'test', t.head(), t.branch(),
                      'Separately delegated disposable records')
        with redirect_stdout(io.StringIO()):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        result = t.commit()
        t.finish()
        self.assertEqual(result['parent'], previous['result_sha'])
        self.assertEqual(sorted(p.name for p in t.store.iterdir()), sorted([c.RECEIPT_KEY, ac.LANDING]))
        self.assertEqual(self.flow.main.head(), self.base)

    def test_main_branch_change_during_validation_rejected(self):
        self.create()
        def change_branch(t):
            if t.root == self.root:
                self.f.git('switch', '-c', 'other')
        with patch.object(self.flow, 'continuity', side_effect=change_branch):
            with self.assertRaisesRegex(ValueError, 'no longer on main'):
                self.flow.integrate(self.path, self.owner, self.base, self.base)

    def test_dirty_source_and_unknown_scratch_are_preserved(self):
        t = self.create()
        (self.path / 'unrelated.txt').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'must be clean'):
            self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertEqual((self.path / 'unrelated.txt').read_text(), 'keep')
        (self.path / 'unrelated.txt').unlink()
        (t.store / 'unknown.json').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'scratch not idle'):
            self.flow.integrate(self.path, self.owner, self.base, self.base)
        self.assertTrue((t.store / 'unknown.json').exists())

    def test_removal_reference_hook_rejected(self):
        self.create()
        (self.root / '.git/hooks/reference-transaction').write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'hooks'):
            self.flow.remove(self.path, self.owner, self.base, self.base)
        self.assertTrue(self.path.exists())

    def migration(self, checker=None):
        t = self.create()
        (self.path / continuity.CONTRACT).write_text('{}')
        (self.path / 'new-governance').mkdir()
        (self.path / 'new-governance/rule.md').write_text('candidate layout\n')
        (self.path / 'devlog').mkdir(exist_ok=True)
        (self.path / 'devlog/log.md').write_text('Repository checkpoint: `' + self.base + '`\n')
        (self.path / 'devlog/README.md').write_text('[log](log.md)\n')
        checker = checker or '''
def verify_repository(root):
    assert (root / 'new-governance/rule.md').is_file(), 'wrong layout'
    return {}, {'implementation_checkpoint': %r}
def validate_docs(root):
    return ['devlog/log.md']
def read(root, name):
    return (root / name).read_text()
''' % self.base
        write_checker(self.path, checker)
        # Candidate lifecycle automation must never execute inside the destination guard.
        (self.path / rp.CHECKPOINT).write_text('raise RuntimeError("malicious candidate checkpoint imported")\n')
        (self.path / rp.WORKTREE).write_text('raise RuntimeError("malicious candidate guard executed")\n')
        t.git('add', '--', rp.TOOLS, 'governance/config', 'new-governance', 'devlog')
        t.git('commit', '-m', 'Disposable structural migration')
        return t, t.head(), c.digest((self.path / rp.VALIDATE_CONTINUITY).read_bytes())

    def test_candidate_layout_and_hostile_lifecycle_modules_are_isolated(self):
        t, tip, checker_hash = self.migration()
        # Old-main compatibility check must never parse the candidate layout.
        def old_main(tool):
            self.assertEqual(tool.root, self.root)
            self.assertEqual(tool.head(), self.base)
        with patch.object(self.flow, 'continuity', side_effect=old_main):
            result = self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(result['main'], tip)
        self.assertEqual(result['candidate_validator_sha256'], checker_hash)
        self.assertEqual(self.flow.main.head(), tip)
        self.assertEqual(t.head(), tip)

    def test_unreviewed_validator_stop_and_exact_retry(self):
        t, tip, checker_hash = self.migration()
        before = self.flow.shared_state()
        for supplied in (None, '0' * 64):
            with self.assertRaisesRegex(ValueError, 'explicitly reviewed'):
                self.flow.integrate(self.path, self.owner, self.base, tip, supplied)
            self.assertEqual(self.flow.main.head(), self.base)
            self.assertEqual(self.flow.shared_state(), before)
            self.assertFalse(local_state.lifecycle_lock(t.common).exists())
            self.assertFalse((t.store / c.OPERATION_LOCK).exists())
        self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(self.flow.shared_state(), before)

    def test_invalid_candidate_continuity_preserves_main_and_refs(self):
        t, tip, checker_hash = self.migration('raise ValueError("candidate continuity invalid")\n')
        before = self.flow.shared_state()
        with self.assertRaisesRegex(ValueError, 'Candidate continuity failed'):
            self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(self.flow.main.head(), self.base)
        self.assertEqual(t.head(), tip)
        self.assertEqual(self.flow.shared_state(), before)

    def test_candidate_checker_cannot_override_tip_dirty_main_or_ownership_guards(self):
        t, tip, checker_hash = self.migration()
        with self.assertRaisesRegex(ValueError, 'Expected full HEAD'):
            self.flow.integrate(self.path, self.owner, self.base, self.base, checker_hash)
        (self.root / 'b.txt').write_text('preserve unrelated main change\n')
        with self.assertRaisesRegex(ValueError, 'must be clean'):
            self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(self.flow.main.head(), self.base)
        (self.root / 'b.txt').write_text('old\n')
        t.git('worktree', 'unlock', str(self.path))
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)

    def test_changed_shared_ref_during_validation_blocks_promotion(self):
        t, tip, checker_hash = self.migration()
        check = self.flow.candidate_continuity
        def changing(source, expected_hash, target=None):
            result = check(source, expected_hash, target)
            self.f.git('branch', 'unrelated-fixture', self.base)
            return result
        with patch.object(self.flow, 'candidate_continuity', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'Shared refs/stash/configuration changed'):
                self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(self.flow.main.head(), self.base)
        self.assertEqual(t.head(), tip)

    def test_guard_authority_binds_committed_pair_and_rejects_mixed_or_changed_drivers(self):
        guard = self.root / 'governance' / 'tools' / 'worktrees' / 'guard.py'
        tools = Path(c.__file__).parent
        for name in (*w.GUARD_MODULES, *c.DRIVER_MODULES, 'lifecycle/commit_metadata', 'worktrees/claims'):
            (self.root / 'governance' / 'tools' / (name + '.py')).parent.mkdir(parents=True, exist_ok=True)
            (self.root / 'governance' / 'tools' / (name + '.py')).write_bytes((tools / (name + '.py')).read_bytes())
        self.f.git('add', rp.WORKTREE, rp.CLAIMS, rp.TOOLS)
        self.f.git('commit', '-m', 'Disposable committed guard fixture')
        self.base = self.flow.main.head()
        t = self.create()
        driver = self.root / rp.CHECKPOINT
        metadata = sys.modules['governance.tools.lifecycle.commit_metadata']
        metadata_path = self.root / 'governance/tools/lifecycle/commit_metadata.py'
        claims_path = self.root / rp.CLAIMS
        with patch.object(w, '__file__', str(guard)), patch.object(c, '__file__', str(driver)), \
                patch.object(metadata, '__file__', str(metadata_path)), patch.object(claims, '__file__', str(claims_path)):
            authority = self.flow.tooling_authority(t)
            self.assertEqual(authority['revision'], self.base)
            self.assertEqual(authority['metadata_sha256'], c.digest(metadata_path.read_bytes()))
            self.assertEqual(authority['claims_sha256'], c.digest(claims_path.read_bytes()))
            original_claims = claims_path.read_bytes()
            claims_path.write_bytes(original_claims + b'\n# unreviewed store\n')
            with self.assertRaisesRegex(ValueError, 'reviewed committed tooling'):
                self.flow.tooling_authority(t)
            claims_path.write_bytes(original_claims)
            original = metadata_path.read_bytes()
            metadata_path.write_bytes(original + b'\n# unreviewed dependency\n')
            with self.assertRaisesRegex(ValueError, 'reviewed committed tooling'):
                self.flow.tooling_authority(t)
            metadata_path.write_bytes(original)
            guard.write_bytes(guard.read_bytes() + b'\n# uncommitted guard\n')
            with self.assertRaisesRegex(ValueError, 'reviewed committed tooling'):
                self.flow.tooling_authority(t)
        with patch.object(w, '__file__', str(guard)), patch.object(c, '__file__', str(self.path / rp.CHECKPOINT)):
            with self.assertRaisesRegex(ValueError, 'Mixed'):
                self.flow.tooling_authority(t)

    def test_destination_and_candidate_use_their_own_incompatible_parsers(self):
        (self.root / continuity.CONTRACT).write_text('{}')
        (self.root / 'old-governance').mkdir()
        (self.root / 'old-governance/rule.md').write_text('old layout\n')
        (self.root / 'devlog').mkdir()
        (self.root / 'devlog/log.md').write_text('Repository checkpoint: `' + self.base + '`\n')
        (self.root / 'devlog/README.md').write_text('[log](log.md)\n')
        checker = '''
def verify_repository(root):
    assert (root / 'old-governance/rule.md').is_file(), 'old parser cannot read candidate'
    return {}, {'implementation_checkpoint': %r}
def validate_docs(root):
    return ['devlog/log.md']
def read(root, name):
    return (root / name).read_text()
''' % self.base
        write_checker(self.root, checker)
        self.f.git('add', rp.TOOLS, 'governance/config', 'old-governance', 'devlog')
        self.f.git('commit', '-m', 'Disposable old layout')
        self.base = self.flow.main.head()
        t, tip, checker_hash = self.migration()
        (self.path / 'old-governance/rule.md').unlink()
        t.git('add', '--', 'old-governance/rule.md')
        t.git('commit', '-m', 'Disposable removal of old layout')
        tip = t.head()
        # Old destination parser cannot read candidate; candidate parser cannot read old main.
        old_result = t.runner([sys.executable, '-I', '-B', '-c', w.CANDIDATE_CONTINUITY,
                               str(self.root / rp.VALIDATE_CONTINUITY), str(self.path)], self.path)
        self.assertNotEqual(old_result['exit_code'], 0)
        new_result = t.runner([sys.executable, '-I', '-B', '-c', w.CANDIDATE_CONTINUITY,
                               str(self.path / rp.VALIDATE_CONTINUITY), str(self.root)], self.root)
        self.assertNotEqual(new_result['exit_code'], 0)
        result = self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(result['main'], tip)

    def test_promoted_crlf_checkout_uses_exact_reviewed_source_checker(self):
        t, tip, checker_hash = self.migration()
        path = self.path / rp.VALIDATE_CONTINUITY
        path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n'))
        checker_hash = c.digest(path.read_bytes())
        t.git('add', '--', rp.VALIDATE_CONTINUITY)  # Refresh stat-only conversion.
        # Working bytes are LF, committed bytes identical, promoted checkout is CRLF.
        self.assertFalse(t.status())
        result = self.flow.integrate(self.path, self.owner, self.base, tip, checker_hash)
        self.assertEqual(result['main'], tip)
        destination = self.root / rp.VALIDATE_CONTINUITY
        self.assertIn(b'\r\n', destination.read_bytes())
        self.assertNotEqual(c.digest(destination.read_bytes()), checker_hash)
        self.assertEqual(result['candidate_evidence']['argv'][-2:], [str(path), str(self.root)])


    def test_missing_checker_cannot_fall_back_to_policy_pass(self):
        with self.assertRaisesRegex(ValueError, 'Destination continuity checker missing'):
            self.real_continuity(self.flow.main)
        self.assertEqual(self.flow.main.head(), self.base)
        self.assertFalse(self.path.exists())

    def test_required_contract_missing_or_malformed_blocks_real_checker(self):
        # Exercise the isolated committed checker, not the synthetic policy PASS.
        for name in ('validate_continuity.py', 'artifact_classification.py'):
            (self.root / 'governance' / 'tools' / 'continuity' / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / 'governance' / 'tools' / 'continuity' / name).write_bytes((Path(c.__file__).parent / 'continuity' / name).read_bytes())
        self.f.git('add', '--', rp.TOOLS)
        self.f.git('commit', '-m', 'Install real checker for missing evidence regression')
        from governance.tools.continuity import validate_continuity as continuity
        contract = self.root / continuity.CONTRACT
        for data in (None, '{', '{}'):
            with self.subTest(contract=data):
                if data is not None:
                    contract.write_text(data)
                with self.assertRaisesRegex(ValueError, 'Candidate continuity failed'):
                    self.real_continuity(self.flow.main)
        self.assertFalse(self.path.exists())

    def test_integration_always_requires_reviewed_candidate_without_contract(self):
        t = self.create()
        with patch.object(self.flow, 'continuity', return_value=None), \
                patch.object(self.flow, 'candidate_continuity', side_effect=ValueError('required candidate check')) as check:
            with self.assertRaisesRegex(ValueError, 'required candidate check'):
                self.flow.integrate(self.path, self.owner, self.base, self.base)
        check.assert_called_once()
        self.assertEqual(self.flow.main.head(), self.base)
        self.assertEqual(t.head(), self.base)


class CarryTest(unittest.TestCase):
    """The carry of an approval across a moved base (diff-bound approval), in disposable repositories only."""
    create = WorktreeTest.create
    prepared = WorktreeTest.prepared
    committed = WorktreeTest.committed

    def setUp(self):
        WorktreeTest.setUp(self)
        switch = patch.object(ea, 'carry_switch', return_value=(True, 'diff_bound_approval is true'))
        self.switch = switch.start()
        self.addCleanup(switch.stop)

    def advance(self, name='b.txt', text='upstream edit\n'):
        """Main moves on a file the candidate does not touch."""
        (self.root / name).write_text(text)
        self.f.git('add', '--', name)
        self.f.git('commit', '-m', 'Upstream edit of ' + name)
        return self.flow.main.head()

    def carry(self, main, tip):
        return self.flow.carry(self.path, self.owner, main, tip, 'diff_bound_approval')

    def refused(self, gate, main, tip, pattern=''):
        with self.assertRaises(w.CarryRefused) as raised:
            self.carry(main, tip)
        self.assertEqual(raised.exception.gate, gate)
        self.assertRegex(str(raised.exception), pattern)
        self.assertIn('Nothing was moved', str(raised.exception))
        self.assertEqual(c.Checkpoint(self.path).head(), tip, 'the owned branch is untouched')
        self.assertEqual(self.flow.main.head(), main)

    def resign(self, mutate):
        """Rewrite the authenticated landing record, as a test of what each gate does with a changed record."""
        t = c.Checkpoint(self.path)
        landing = ea.read_landing(t)
        mutate(landing)
        ea.write_landing(t, None, landing)

    def test_a_clean_carry_keeps_the_diff_and_lands_through_the_usual_guard(self):
        t, tip = self.committed()
        main = self.advance()
        with self.assertRaisesRegex(ValueError, 'Main is not an ancestor of the source tip.*diff_bound_approval is on'):
            self.flow.integrate(self.path, self.owner, main, tip)
        result = self.carry(main, tip)
        new = result['new_tip']
        self.assertEqual((result['state'], result['old_tip']), ('carried', tip))
        self.assertNotEqual(new, tip)
        self.assertEqual(t.head(), new)
        self.assertEqual(self.f.git('rev-parse', new + '^').strip(), main)
        self.assertEqual(self.f.git('show', new + ':a.txt'), 'owned edit\n')
        self.assertEqual(self.f.git('show', new + ':b.txt'), 'upstream edit\n')
        self.assertEqual(self.f.git('cat-file', 'commit', new).split('\n\n', 1)[1],
                         self.f.git('cat-file', 'commit', tip).split('\n\n', 1)[1], 'message and trailers are byte for byte the same')
        self.assertEqual(t.status(), '')
        audit = result['audit']
        for field in ('kind', 'at', 'owner', 'job_handle', 'key', 'old_tip', 'new_tip', 'old_base', 'new_base', 'carry_number',
                      'upstream', 'overlap', 'commits', 'tooling'):
            self.assertIn(field, audit)
        [commit] = audit['commits']
        self.assertEqual((commit['old'], commit['new'], commit['new_parent']), (tip, new, main))
        self.assertEqual(commit['patch_id'], commit['approved_patch_id'])
        self.assertTrue(all(check['exit_code'] == 0 for check in commit['checks']) and commit['checks'])
        self.assertEqual((audit['carry_number'], audit['overlap'], audit['upstream']['paths']), (1, [], ['b.txt']))
        self.assertIn('Carry notice', result['notice'])
        self.assertIn(tip[:7], result['notice'])
        self.assertIn(new[:7], result['notice'])
        landing = ea.read_landing(t)
        self.assertEqual((landing['result_sha'], landing['approval_path'], landing['original_approval_path']),
                         (new, 'carried', 'sealed'))
        self.assertEqual(landing['carried_from'], audit)
        entries = {e['result_sha']: e for e in landing['approvals']}
        self.assertEqual(set(entries), {tip, new}, 'the old entry stays as history')
        self.assertEqual(entries[new]['original']['result_sha'], tip)
        self.assertEqual(entries[new]['carries'][0]['from'], tip)
        self.assertIn('Carry notice', t.landing())
        # The guarded integration then fast-forwards the carried tip, reading the switch again.
        with patch.object(ea, 'carry_switch', return_value=(False, 'off for the test')), \
                self.assertRaisesRegex(ValueError, 'carry switch is off'):
            self.flow.integrate(self.path, self.owner, main, new)
        self.assertEqual(self.flow.main.head(), main)
        self.flow.integrate(self.path, self.owner, main, new)
        self.assertEqual(self.flow.main.head(), new)

    def test_each_gate_fails_closed_on_its_own(self):
        t, tip = self.committed()
        # A conflicting upstream: the same line of the same file.
        main = self.advance('a.txt', 'upstream wrote this line first\n')
        self.refused('merge-tree', main, tip, 'does not merge cleanly.*a\\.txt')
        # The upstream already made the same change: the merge is clean and the carried diff is empty, not the approved one.
        self.f.git('reset', '--hard', self.base)
        main = self.advance('a.txt', 'owned edit\n')
        self.refused('diff', main, tip, 'differs from the approved diff')
        # The switch is read from main: a key that is off stops the carry before anything else.
        self.f.git('reset', '--hard', self.base)
        main = self.advance()
        self.switch.return_value = (False, 'diff_bound_approval is not the boolean true')
        self.refused('switch', main, tip, 'not the boolean true')
        self.switch.return_value = (True, 'on')
        # Message: the approved message is part of the record, and a commit whose record does not describe it is refused.
        self.resign(lambda landing: landing['approvals'][0]['original'].update(message_sha256='0' * 64))
        self.refused('message', main, tip, 'message differs from the approved message')

    def test_the_selection_the_new_base_derives_must_equal_the_sealed_one(self):
        t, tip = self.committed()
        policy = dict(self.f.policy, note='a rule of the new base')
        self.f.write_json(c.POLICY, policy)
        self.f.git('add', '--', c.POLICY)
        self.f.git('commit', '-m', 'Upstream changes the checkpoint policy')
        self.refused('selection', self.flow.main.head(), tip, 'selection derived on the new base.*differs from the sealed one')

    def test_every_selected_check_runs_again_on_the_carried_tree(self):
        gate = Path(self.f.temp.name) / 'gate.py'
        gate.write_text('import pathlib, sys\nsys.exit(1 if pathlib.Path("b.txt").read_text().startswith("broken") else 0)\n',
                        encoding='utf-8', newline='\n')
        policy, profiles = self.f.policy, self.f.profiles
        policy['commands']['regressions'] = ['{python}', '-B', str(gate)]
        profiles['profiles']['test']['checks'] = [{'id': n, 'argv': a} for n, a in policy['commands'].items()]
        self.f.write_json(c.POLICY, policy)
        self.f.write_json(c.PROFILES, profiles)
        self.f.git('add', '--', c.POLICY, c.PROFILES)
        self.f.git('commit', '-m', 'A check that reads the tree')
        self.base = self.f.parent = self.f.git('rev-parse', 'HEAD').strip()
        t, tip = self.committed()
        main = self.advance('b.txt', 'broken upstream\n')
        self.refused('checks', main, tip, 'selected check failed on the carried tree: regressions')
        self.f.git('reset', '--hard', self.base)
        main = self.advance()
        self.assertEqual([x['id'] for x in self.carry(main, tip)['audit']['commits'][0]['checks']].count('regressions'), 1)

    def test_the_old_rule_still_applies_to_what_is_not_an_approved_commit(self):
        t = self.create()
        (self.path / 'a.txt').write_text('a commit made by hand\n')
        subprocess.run(['git', '-C', str(self.path), 'add', '--', 'a.txt'], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(self.path), 'commit', '-q', '-m', 'Not through the checkpoint tool'],
                       check=True, capture_output=True)
        tip = t.head()
        main = self.advance()
        self.refused('record', main, tip, 'no landing record')

    def test_a_branch_whose_records_predate_the_carry_is_not_carried(self):
        t, tip = self.committed()
        main = self.advance()
        self.resign(lambda landing: landing.pop('approvals'))
        self.refused('record', main, tip, 'no approval record with a sealed selection')

    def test_a_runtime_commit_and_its_records_commit_carry_in_order_with_an_id_map(self):
        t = self.create()
        (self.path / 'runtime.txt').write_text('runtime change\n')
        p = t.prepare(['runtime.txt'], self.f.message, 'test', self.base, 'refs/heads/tx/' + self.owner, 'Disposable runtime scope')
        with redirect_stdout(io.StringIO()):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        with patch.object(c.Checkpoint, 'runtime_pending', return_value='pending'):
            runtime = t.commit()
        t.continue_records()
        (self.path / 'devlog').mkdir()
        cites = runtime['result_sha']
        (self.path / 'devlog/runtime.md').write_text(
            'commit %s\nshort %s\ntree %s\nparent %s\n' % (cites, cites[:7], runtime['tree'][:7], runtime['parent']))
        p = t.prepare(['devlog/runtime.md'], self.f.message, 'test', t.head(), t.branch(), 'Disposable records')
        with redirect_stdout(io.StringIO()):
            t.approve(lambda prompt: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        records = t.commit()
        t.finish()
        tip = records['result_sha']
        main = self.advance()
        entries = {e['result_sha']: e for e in ea.read_landing(t)['approvals']}
        self.assertEqual(set(entries), {runtime['result_sha'], tip}, 'both commits of the continuity pair are recorded')
        self.assertTrue(entries[runtime['result_sha']]['runtime'])
        result = self.carry(main, tip)
        first, second = result['audit']['commits']
        self.assertEqual((first['old'], second['old']), (runtime['result_sha'], tip))
        self.assertEqual(first['new_parent'], main)
        self.assertEqual(second['new_parent'], first['new'])
        self.assertEqual(first['id_map'], {})
        self.assertEqual(second['id_map'], {runtime['result_sha']: first['new'], runtime['tree']: first['new_tree'],
                                            runtime['parent']: main})
        text = self.f.git('show', result['new_tip'] + ':devlog/runtime.md')
        self.assertEqual(text, 'commit %s\nshort %s\ntree %s\nparent %s\n' % (
            first['new'], first['new'][:7], first['new_tree'][:7], main), 'the records cite the carried commit')
        self.assertIn('these ids were replaced', result['notice'])
        self.assertEqual(self.f.git('rev-parse', result['new_tip'] + '^').strip(), first['new'])
        # A records commit whose approved diff the map does not account for is refused, and so is everything after it.
        def tamper(landing):
            entry = next(e for e in landing['approvals'] if e['result_sha'] == result['new_tip'])
            entry['original']['change'] = ea.pack(ea.unpack(entry['original']['change']).replace(b'+commit ', b'+commit x'))
        self.resign(tamper)
        main2 = self.advance('b.txt', 'second upstream edit\n')
        self.refused('diff', main2, result['new_tip'], 'differs from the approved diff')

    def test_a_repeated_carry_is_verified_against_the_original_approval(self):
        t, tip = self.committed()
        first = self.carry(self.advance(), tip)
        main = self.advance('b.txt', 'a second upstream edit\n')
        second = self.carry(main, first['new_tip'])
        self.assertEqual(second['audit']['carry_number'], 2)
        [commit] = second['audit']['commits']
        self.assertEqual(commit['original_commit'], tip, 'the second carry compares with the approval of the first commit')
        entry = {e['result_sha']: e for e in ea.read_landing(t)['approvals']}[second['new_tip']]
        self.assertEqual(len(entry['carries']), 2)
        self.assertEqual(entry['original']['result_sha'], tip)
        self.flow.integrate(self.path, self.owner, main, second['new_tip'])
        self.assertEqual(self.flow.main.head(), second['new_tip'])

    def test_a_branch_main_already_holds_has_nothing_to_carry(self):
        t, tip = self.committed()
        with self.assertRaisesRegex(ValueError, 'nothing to carry'):
            self.carry(self.base, tip)

    def test_the_status_note_says_what_is_known_and_promises_no_carry(self):
        t, tip = self.committed()
        self.advance()
        note = w.carry_note(self.root, self.owner, self.path)
        self.assertEqual(note, '`worktree.py carry` can try its approval of %s (diff_bound_approval is on; the merge, diff, '
                         'selection and check gates decide)' % tip[:7])
        self.assertIn('no committed, approved candidate', w.carry_note(self.root, self.owner, None))
        self.resign(lambda landing: landing.pop('approvals'))
        self.assertIn('record gate would refuse', w.carry_note(self.root, self.owner, self.path))
        self.switch.return_value = (False, 'off')
        self.assertIn('cannot be carried (diff_bound_approval is off)', w.carry_note(self.root, self.owner, self.path))

    def test_the_switch_reads_only_the_boolean_true_from_the_tip_of_main(self):
        t = c.Checkpoint(self.root)
        features = self.root / ac.FEATURES
        features.parent.mkdir(parents=True, exist_ok=True)
        for text, expected in (('diff_bound_approval = true\n', True), ('diff_bound_approval = false\n', False),
                               ('diff_bound_approval = "true"\n', False), ('diff_bound_approval = 1\n', False),
                               ('early_approval_reach = "medium"\n', False), ('this is not toml\n', False)):
            with self.subTest(text=text):
                features.write_text(text, encoding='utf-8', newline='\n')
                self.f.git('add', '--', ac.FEATURES)
                self.f.git('commit', '-q', '-m', 'Switch fixture')
                self.assertEqual(REAL_CARRY_SWITCH(t)[0], expected)
        self.f.git('rm', '-q', '--', ac.FEATURES)
        self.f.git('commit', '-q', '-m', 'No switch file')
        self.assertFalse(REAL_CARRY_SWITCH(t)[0])

    def test_the_substitution_replaces_whole_ids_in_full_and_short_form_only(self):
        old, new = 'a' * 40, 'b' * 40
        text = ('%s %s %s %sc %s\n' % (old, old[:7], old[:12], old[:12], old[:6])).encode()
        result = ea.substitute(text, {old: new}).decode()
        self.assertEqual(result, '%s %s %s %sc %s\n' % (new, new[:7], new[:12], old[:12], old[:6]))
        self.assertEqual(ea.substitute(text, {old: old}), text)
        self.assertEqual(ea.stripped(b'diff --git a/x b/x\nindex 1..2 100644\n@@ -1 +1 @@\n-a\n+b\n'),
                         b'diff --git a/x b/x\n-a\n+b\n')


class GoCarryTest(unittest.TestCase):
    """The carry of a go (diff-bound approval, slice 2): a moved main neither voids a go given at a preview nor makes the
    worker recreate the worktree. Disposable repositories only; the real switch is read from the fixture's own main."""
    create = WorktreeTest.create

    LINES = ''.join('line %d\n' % n for n in range(1, 11))

    def setUp(self):
        WorktreeTest.setUp(self)
        (self.root / 'a.txt').write_text(self.LINES)
        features = self.root / ac.FEATURES
        features.parent.mkdir(parents=True, exist_ok=True)
        features.write_text('early_approval_reach = "low"\ndiff_bound_approval = true\n', encoding='utf-8', newline='\n')
        self.f.git('add', '--', 'a.txt', ac.FEATURES)
        self.f.git('commit', '-m', 'Fixture: ten lines and the switches')
        self.base = self.f.parent = self.f.git('rev-parse', 'HEAD').strip()
        self.branch = 'refs/heads/tx/' + self.owner
        self.moves = 0

    def edit(self, line=9, text='owned edit'):
        lines = self.LINES.splitlines(keepends=True)
        lines[line - 1] = text + '\n'
        (self.path / 'a.txt').write_text(''.join(lines))

    def go_record(self, t, preview, reply='go'):
        body = preview['body']
        consent = {'source': 'active-task-user', 'conversation_id': 'disposable-active-task', 'user_message_id': 'fixture-turn-1',
                   'channel': 'text-token', 'response': reply, 'preview_id': body['id'],
                   'preview_sha256': ea.digest(ea.canonical(body))}
        return ea.record_go(t, preview, consent)

    def previewed(self, manifest=('a.txt',), edit=True, stage=False):
        t = self.create()
        if edit:
            self.edit()
        preview = t.preview(list(manifest), self.f.message, None, self.base, self.branch)
        self.assertTrue(preview['body']['eligible'], preview['body']['reasons'])
        go = self.go_record(t, preview)
        packet = t.prepare(list(manifest), self.f.message, 'test', self.base, self.branch, 'Disposable fixture scope',
                           early_go=go) if stage else None
        if stage:
            self.assertEqual(packet['state'], 'approved')
        return t, self.save(go, 'go-%d.json' % self.moves)

    def save(self, envelope, name):
        path = Path(self.f.temp.name) / name
        path.write_bytes(c.canonical(envelope))
        return path

    def advance(self, name='b.txt', text='upstream edit\n'):
        (self.root / name).write_text(text)
        self.f.git('add', '--', name)
        self.f.git('commit', '-m', 'Upstream edit of ' + name)
        return self.flow.main.head()

    def carry(self, main, go, message=None):
        self.moves += 1
        out = Path(self.f.temp.name) / ('carried-%d.json' % self.moves)
        result = self.flow.carry_go(self.path, self.owner, main, go, message or self.f.message, None, out, 'diff_bound_approval')
        return result, out

    def refused(self, gate, main, go, pattern='', message=None):
        t = c.Checkpoint(self.path)
        before = (t.head(), (self.path / 'a.txt').read_bytes(), t.status(), (t.store / ea.EARLY_USES).exists())
        self.moves += 1
        out = Path(self.f.temp.name) / ('carried-%d.json' % self.moves)
        with self.assertRaises(w.GoCarryRefused) as raised:
            self.flow.carry_go(self.path, self.owner, main, go, message or self.f.message, None, out, 'diff_bound_approval')
        self.assertEqual(raised.exception.gate, gate)
        self.assertRegex(str(raised.exception), pattern)
        self.assertIn('Nothing was moved; show a fresh preview and take a fresh go', str(raised.exception))
        self.assertEqual((t.head(), (self.path / 'a.txt').read_bytes(), t.status(), (t.store / ea.EARLY_USES).exists()), before)
        self.assertFalse(out.exists(), 'no go is written')
        self.assertEqual(self.flow.main.head(), main)

    def dirty(self, t):
        return [e for e in t.status().split('\0') if e]

    def reset_main(self):
        self.f.git('reset', '--hard', self.base)

    def test_a_go_at_a_preview_survives_a_moved_main_and_carries_to_the_landing(self):
        t, go = self.previewed()
        preview = ea.unsign(t, json.loads(go.read_text()))['preview']
        main = self.advance()
        result, out = self.carry(main, go)
        self.assertEqual((result['state'], result['was'], result['old_parent'], result['new_parent']),
                         ('go-carried', 'preview', self.base, main))
        self.assertTrue(self.path.is_dir() and t.head() == main, 'the worktree was moved, not recreated')
        self.assertEqual((self.path / 'a.txt').read_text(), self.LINES.replace('line 9\n', 'owned edit\n'))
        self.assertEqual(self.dirty(t), [' M a.txt'], 'the candidate is uncommitted and unstaged on the new main')
        body = ea.unsign(t, json.loads(out.read_text()))
        self.assertEqual((body['preview']['parent'], body['preview']['tree']), (main, ea.candidate_tree(t, ['a.txt'])))
        self.assertTrue(body['preview']['eligible'])
        self.assertEqual(body['go']['preview_id'], body['preview']['id'])
        self.assertEqual(body['go']['preview_sha256'], ea.digest(ea.canonical(body['preview'])))
        self.assertEqual(body['rebound']['original']['preview'], preview)
        audit = result['audit']
        for field in ('kind', 'at', 'owner', 'job_handle', 'key', 'move_number', 'state', 'old_parent', 'new_parent', 'old_tree',
                      'new_tree', 'old_preview', 'new_preview', 'original_preview', 'upstream', 'overlap', 'patch_id',
                      'original_patch_id', 'change_sha256', 'selection', 'tooling'):
            self.assertIn(field, audit)
        self.assertEqual((audit['move_number'], audit['overlap'], audit['upstream']['paths'], audit['state']),
                         (1, [], ['b.txt'], 'preview'))
        self.assertEqual(audit['patch_id'], audit['original_patch_id'])
        self.assertEqual(body['rebound']['moves'], [audit], 'the audit is inside the authenticated go')
        self.assertIn('Go carry notice', result['notice'])
        self.assertIn(self.base[:7], result['notice'])
        self.assertIn(main[:7], result['notice'])
        spent = ea.uses(t)[ea.go_id(ea.unsign(t, json.loads(go.read_text())))]
        self.assertEqual((spent['committed'], spent['rebound_to']), (True, ea.go_id(body)))
        # The carried go approves the exact candidate at the seal, and the usual lifecycle follows.
        packet = t.prepare(['a.txt'], self.f.message, 'test', main, self.branch, 'Disposable fixture scope',
                           early_go=json.loads(out.read_text()))
        self.assertEqual((packet['state'], packet['early_approval']['applied']), ('approved', True))
        tip = t.commit()['result_sha']
        t.finish()
        self.assertEqual(ea.read_landing(t)['approval_path'], 'early')
        self.flow.integrate(self.path, self.owner, main, tip)
        self.assertEqual(self.flow.main.head(), tip)

    def test_a_staged_transaction_is_unstaged_and_sealed_again_with_the_carried_go(self):
        t, go = self.previewed(stage=True)
        main = self.advance()
        result, out = self.carry(main, go)
        self.assertEqual(result['was'], 'staged')
        self.assertFalse((t.store / c.TRANSACTION).exists(), 'the old seal is withdrawn')
        self.assertEqual((t.inventory(), t.head()), ([], main))
        stale = t.prepare(['a.txt'], self.f.message, 'test', main, self.branch, 'Disposable fixture scope', early_go=json.loads(go.read_text()))
        self.assertEqual(stale['state'], 'prepared', 'the spent go carries nothing')
        self.assertFalse(stale['early_approval']['applied'])
        t.abandon('the old go is spent')
        body = ea.unsign(t, json.loads(out.read_text()))
        self.assertEqual(ea.uses(t)[ea.go_id(body)]['attempts'], 1, 'the attempts of the go carry over, so the cap stays')
        packet = t.prepare(['a.txt'], self.f.message, 'test', main, self.branch, 'Disposable fixture scope', early_go=json.loads(out.read_text()))
        self.assertEqual((packet['state'], packet['early_approval']['applied'], packet['early_approval']['attempt']), ('approved', True, 2))

    def test_a_shared_file_that_merges_cleanly_is_a_notice_and_a_new_folder_moves_with_the_candidate(self):
        t, go = self.previewed(stage=True)
        main = self.advance('a.txt', self.LINES.replace('line 1\n', 'upstream line\n'))
        result, out = self.carry(main, go)
        self.assertEqual(result['audit']['overlap'], ['a.txt'])
        self.assertIn('`a.txt`', result['notice'])
        merged = (self.path / 'a.txt').read_text()
        self.assertTrue(merged.startswith('upstream line\n') and 'owned edit\n' in merged and 'line 9\n' not in merged)
        self.assertEqual(t.prepare(['a.txt'], self.f.message, 'test', main, self.branch, 'Disposable fixture scope',
                                   early_go=json.loads(out.read_text()))['state'], 'approved')
        # A transaction that adds a folder: reconcile refused it, and the carried go takes it along.
        t.abandon('start again')
        t.git('checkout', '--', 'a.txt')
        folder = self.path / 'new-component'
        folder.mkdir()
        (folder / 'x.txt').write_text('new file\n')
        preview = t.preview(['new-component/x.txt'], self.f.message, None, main, self.branch)
        go2 = self.save(self.go_record(t, preview), 'go-folder.json')
        packet = t.prepare(['new-component/x.txt'], self.f.message, 'test', main, self.branch, 'Disposable fixture scope',
                           early_go=json.loads(go2.read_text()))
        self.assertEqual((packet['state'], packet['residual']), ('approved', ['?? new-component/']))
        later = self.advance('b.txt', 'a later upstream edit\n')
        result, out = self.carry(later, go2)
        self.assertEqual(result['was'], 'staged')
        self.assertEqual((self.path / 'new-component/x.txt').read_text(), 'new file\n')
        self.assertIn('?? new-component/x.txt', t.git('status', '--porcelain=v1', '-uall').decode())
        self.assertEqual(t.prepare(['new-component/x.txt'], self.f.message, 'test', later, self.branch, 'Disposable fixture scope',
                                   early_go=json.loads(out.read_text()))['state'], 'approved')

    def test_each_gate_fails_closed_on_its_own(self):
        t, go = self.previewed()
        # merge-tree: the same line of the same file.
        main = self.advance('a.txt', self.LINES.replace('line 9\n', 'upstream line 9\n'))
        self.refused('merge-tree', main, go, 'does not merge cleanly.*a\\.txt')
        # diff: upstream already made the same change, so the carried diff is empty and not the previewed one.
        self.reset_main()
        main = self.advance('a.txt', self.LINES.replace('line 9\n', 'owned edit\n'))
        self.refused('diff', main, go, 'differs from the previewed diff|other paths')
        # message: the message must be the previewed one, byte for byte.
        self.reset_main()
        main = self.advance()
        other = Path(self.f.temp.name) / 'other-message.txt'
        other.write_bytes(b'Another subject\n')
        self.refused('message', main, go, 'not the previewed message', message=other)
        # selection: the sensitivity of the path, then the checks, as the new main derives them.
        self.reset_main()
        text = (self.root / c.TABLE).read_text()
        row = '| `a.txt` | tooling | test | - | - | - | - | - | - |'
        self.assertIn(row, text)
        (self.root / c.TABLE).write_text(text.replace(row, row[:-3] + 'medium |'), newline='\n')
        self.f.git('add', '--', c.TABLE)
        self.f.git('commit', '-m', 'Upstream grades the file medium')
        self.refused('selection', self.flow.main.head(), go, 'differs from the previewed sensitive_paths')
        self.reset_main()
        policy, profiles = self.f.policy, self.f.profiles
        policy['commands']['extra'] = ['{python}', '-B', '-c', 'print("PASS")']
        profiles['profiles']['test']['checks'] = [{'id': n, 'argv': a} for n, a in policy['commands'].items()]
        self.f.write_json(c.POLICY, policy)
        self.f.write_json(c.PROFILES, profiles)
        self.f.git('add', '--', c.POLICY, c.PROFILES)
        self.f.git('commit', '-m', 'Upstream adds a check')
        self.refused('selection', self.flow.main.head(), go, 'differs from the previewed .*checks')
        # eligibility: the reach reads off on the new main, so the go no longer carries there.
        self.reset_main()
        (self.root / ac.FEATURES).parent.mkdir(parents=True, exist_ok=True)
        (self.root / ac.FEATURES).write_text('early_approval_reach = "off"\ndiff_bound_approval = true\n', newline='\n')
        self.f.git('add', '--', ac.FEATURES)
        self.f.git('commit', '-m', 'Upstream turns the early approval off')
        self.refused('eligibility', self.flow.main.head(), go, 'switch is off')
        # switch: with the key off a moved base takes a fresh preview and a fresh go, as before the carry existed.
        self.reset_main()
        (self.root / ac.FEATURES).parent.mkdir(parents=True, exist_ok=True)
        (self.root / ac.FEATURES).write_text('early_approval_reach = "low"\ndiff_bound_approval = false\n', newline='\n')
        self.f.git('add', '--', ac.FEATURES)
        self.f.git('commit', '-m', 'Upstream turns the carry off')
        self.refused('switch', self.flow.main.head(), go, 'not the boolean true')
        # record: an altered go is not authentic.
        self.reset_main()
        main = self.advance()
        forged = json.loads(go.read_text())
        forged['body']['go']['response'] = 'PROCEED'
        self.refused('record', main, self.save(forged, 'forged.json'), 'missing, altered or not a go')
        # state: unrelated dirty state, and a seal that was not prepared with this go.
        (self.path / 'stray.txt').write_text('unrelated\n')
        self.refused('state', main, go, 'unrelated dirty state: stray.txt')
        (self.path / 'stray.txt').unlink()
        t.prepare(['a.txt'], self.f.message, 'test', self.base, self.branch, 'Disposable fixture scope')
        self.refused('state', main, go, 'not prepared with this go')

    def test_repeated_moves_are_each_verified_against_the_original_preview(self):
        t, go = self.previewed()
        first, out1 = self.carry(self.advance(), go)
        second, out2 = self.carry(self.advance('b.txt', 'a second upstream edit\n'), out1)
        body1, body2 = (ea.unsign(t, json.loads(p.read_text())) for p in (out1, out2))
        self.assertEqual((second['audit']['move_number'], len(body2['rebound']['moves'])), (2, 2))
        self.assertEqual(body2['rebound']['original'], body1['rebound']['original'], 'the original is never replaced by a copy')
        self.assertEqual(second['audit']['original_preview'], first['audit']['original_preview'])
        self.assertEqual(second['audit']['old_preview'], first['audit']['new_preview'])
        self.assertEqual(second['audit']['patch_id'], body2['rebound']['original']['patch_id'])
        self.assertEqual(t.prepare(['a.txt'], self.f.message, 'test', t.head(), self.branch, 'Disposable fixture scope',
                                   early_go=json.loads(out2.read_text()))['state'], 'approved')

    def test_a_failure_while_moving_puts_the_worktree_back_as_it_was(self):
        t, go = self.previewed()
        main = self.advance()
        before = (self.path / 'a.txt').read_bytes()
        real = ea.candidate_tree
        def flaky(tool, manifest):
            return 'bad' if tool.root == t.root and tool.head() == main else real(tool, manifest)
        out = Path(self.f.temp.name) / 'never.json'
        with patch.object(ea, 'candidate_tree', side_effect=flaky), \
                self.assertRaisesRegex(ValueError, 'put back as it was.*does not hold the merged candidate'):
            self.flow.carry_go(self.path, self.owner, main, go, self.f.message, None, out, None)
        self.assertEqual((t.head(), (self.path / 'a.txt').read_bytes(), self.dirty(t)), (self.base, before, [' M a.txt']))
        self.assertFalse(out.exists())
        self.assertEqual(real(t, ['a.txt']), ea.unsign(t, json.loads(go.read_text()))['preview']['tree'])
        self.assertFalse(ea.uses(t).get(ea.go_id(ea.unsign(t, json.loads(go.read_text()))), {}).get('committed'), 'the go is not spent')

    def test_the_status_note_names_the_go_carry_when_nothing_is_committed(self):
        self.assertIn('`worktree.py carry-go`', w.carry_note(self.root, self.owner, None))


class WaitClearTest(unittest.TestCase):
    """execution-status --wait-clear against a disposable repository and a disposable lifecycle lock."""

    def setUp(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='wait-clear-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        subprocess.run(['git', '-C', str(self.root), 'init', '-q', '-b', 'main'], check=True, capture_output=True)
        self.lock = self.root / '.local' / 'locks' / local_state.LIFECYCLE_LOCK
        self.lock.parent.mkdir(parents=True)
        claims._noticed.clear()

    def wait(self, timeout, **options):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), patch.object(sys, 'stderr', err):
            code = w.wait_clear(self.root, timeout, interval=0.05, **options)
        return code, out.getvalue(), err.getvalue()

    def cli(self, *arguments):
        return subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.WORKTREE), '--main', str(self.root), 'execution-status', *arguments],
                              capture_output=True, text=True, timeout=60)

    def test_a_clear_repository_answers_at_once_with_its_one_line(self):
        with patch.object(w.time, 'sleep', side_effect=AssertionError('must not wait')):
            code, out, err = self.wait(5)
        self.assertEqual((code, out, err), (0, 'CLEAR: no ref window is open.\n', ''))

    def test_an_overlap_counts_as_clear(self):
        overlap = {'level': 'OVERLAP', 'line': 'OVERLAP: m1 with m2, same file(s): a.py'}
        with patch.object(w, 'execution_status', return_value=overlap):
            self.assertEqual(self.wait(5), (0, overlap['line'] + '\n', ''))

    def test_it_clears_after_a_delay_and_says_so_on_stderr_only(self):
        self.lock.write_text('')
        timer = threading.Timer(4, self.lock.unlink)  # longer than a first status read under the parallel load of a seal
        timer.start()
        self.addCleanup(timer.join)
        code, out, err = self.wait(30)
        self.assertEqual((code, out), (0, 'CLEAR: no ref window is open.\n'))
        self.assertIn('waiting up to 30 s for CLEAR or OVERLAP: REFS FROZEN: lifecycle operation', err)
        self.assertIn('execution status is clear; go on', err)

    def test_it_never_clears_and_exits_with_the_timeout_code_naming_who_holds_the_window(self):
        self.lock.write_text('')
        code, out, err = self.wait(0.3)
        self.assertEqual(code, w.WAIT_TIMEOUT_EXIT)
        self.assertNotIn(code, (0, 1, 2))
        self.assertTrue(out.startswith('REFS FROZEN: lifecycle operation, since '), out)
        self.assertIn('FAIL: execution status was not CLEAR or OVERLAP within 0.3 s', err)

    def test_a_timeout_of_zero_reads_once(self):
        self.lock.write_text('')
        with patch.object(w.time, 'sleep', side_effect=AssertionError('must not wait')):
            self.assertEqual(self.wait(0)[0], w.WAIT_TIMEOUT_EXIT)

    def test_unknown_is_not_clear(self):
        unknown = {'level': 'UNKNOWN', 'line': 'STATUS UNKNOWN: cannot read'}
        with patch.object(w, 'execution_status', return_value=unknown):
            code, out, err = self.wait(0.2)
        self.assertEqual((code, out), (w.WAIT_TIMEOUT_EXIT, 'STATUS UNKNOWN: cannot read\n'))
        missing = Path(self.temp.name) / 'missing'
        result = subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.WORKTREE), '--main', str(missing), 'execution-status',
                                 '--wait-clear', '--timeout', '0'], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, w.WAIT_TIMEOUT_EXIT, result.stderr)
        self.assertTrue(result.stdout.startswith('STATUS UNKNOWN'), result.stdout)

    def test_the_command_exits_zero_when_clear_and_with_the_timeout_code_when_not(self):
        clear = self.cli('--wait-clear')
        self.assertEqual((clear.returncode, clear.stdout), (0, 'CLEAR: no ref window is open.\n'), clear.stderr)
        self.lock.write_text('')
        held = self.cli('--wait-clear', '--timeout', '0.3')
        self.assertEqual(held.returncode, w.WAIT_TIMEOUT_EXIT, held.stderr)
        self.assertTrue(held.stdout.startswith('REFS FROZEN: lifecycle operation'), held.stdout)
        self.assertTrue(self.lock.exists(), 'it only reads and never clears the lock')
        self.assertFalse((self.root / '.local' / 'claims').exists())

    def test_the_options_are_checked(self):
        for arguments in (['--timeout', '5'], ['--wait-clear', '--watch'], ['--wait-clear', '--json-only'],
                          ['--wait-clear', '--timeout', '-1']):
            with self.subTest(arguments=arguments):
                result = self.cli(*arguments)
                self.assertEqual(result.returncode, 2, result.stdout)


if __name__ == '__main__':
    unittest.main()
