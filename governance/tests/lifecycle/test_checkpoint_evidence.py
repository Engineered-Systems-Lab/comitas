"""Transaction-local evidence reuse; all Git mutations use disposable fixtures."""
from contextlib import redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import checkpoint as c
from governance.tools.lifecycle import checkpoint_state
from governance.tests.lifecycle import test_checkpoint as fixtures
import repo_paths as rp


class EvidenceTest(unittest.TestCase):
    def test_repository_policy_classifies_evidence_tests_as_tooling(self):
        root = rp.ROOT
        tool = object.__new__(c.Checkpoint)
        tool.root = root
        policy = json.loads((root / c.POLICY).read_bytes())
        profiles = json.loads((root / c.PROFILES).read_bytes())
        name, profile = tool.select_profile(['governance/tests/lifecycle/test_checkpoint_evidence.py'], policy, profiles, 'auto')
        self.assertEqual(name, 'tooling')
        self.assertEqual(profile['change_classification'], 'tooling')

    def setUp(self):
        self.f = fixtures.CheckpointTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.tool = self.f.tool
        self.check = {'id': 'governance-regressions', 'argv': [
            '{python}', '-B', rp.RUN_TESTS, '-s', 'governance/tests', '-s', 'governance/config',
            '-p', 'test_checkpoint*.py', '-v']}
        policy = self.f.policy
        policy['commands']['governance-regressions'] = self.check['argv']
        policy['required_checks'] = ['continuity', 'governance-regressions']
        self.f.profiles['profiles']['test']['checks'] = [
            {'id': 'continuity', 'argv': policy['commands']['continuity']}, self.check]
        self.f.write_json(c.POLICY, policy)
        self.f.write_json(c.PROFILES, self.f.profiles)
        self.calls = []

        def runner(argv, root):
            self.calls.append(argv)
            return {'argv': argv, 'exit_code': 0, 'stdout': 'PASS\n', 'stderr': '',
                    'duration_seconds': 123.0}
        self.tool.runner = runner

    def v1(self, snapshot):
        """A context as an old packet sealed it, under the same-tree policy."""
        return c.restamp(snapshot, {'ref_policy': c.REF_POLICY_V1})

    def prepare(self):
        return self.tool.prepare(['a.txt'], self.f.message, 'test', self.f.parent,
                                 'refs/heads/main', 'Fixture delegation', validate_only=True)

    def test_v1_ref_projection_preserves_names_by_default_and_rejects_new_targets(self):
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        prefix = 'refs/codex/turn-diffs/captures/'
        self.tool.git('update-ref', prefix + 'old/base', tree)
        before = self.v1(self.tool.ref_snapshot())
        self.tool.git('update-ref', '-d', prefix + 'old/base')
        self.tool.git('update-ref', prefix + 'new/base', tree)
        after = self.v1(self.tool.ref_snapshot())
        audits = []
        c.check_ref_context(before, after, 'fixture', audits)
        self.assertEqual(len(audits), 1)
        self.assertEqual(c.ref_projection(before), c.ref_projection(self.v1(after)))
        self.tool.git('add', 'a.txt')
        changed_tree = self.tool.git('write-tree').decode().strip()
        self.assertNotEqual(tree, changed_tree)
        self.tool.git('update-ref', prefix + 'new/base', changed_tree)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            c.check_ref_context(after, self.tool.ref_snapshot(), 'fixture', [])
        self.tool.git('update-ref', prefix + 'new/base', tree)
        for name in ('refs/heads/protected', 'refs/tags/protected', 'refs/remotes/origin/protected',
                     'refs/stash', 'refs/unknown/protected', 'refs/codex/other/protected'):
            with self.subTest(name=name):
                self.tool.git('update-ref', name, self.f.parent)
                baseline = self.v1(self.tool.ref_snapshot())
                self.tool.git('update-ref', '-d', name)
                with self.assertRaisesRegex(ValueError, 'fixture'):
                    c.check_ref_context(baseline, self.tool.ref_snapshot(), 'fixture', [])

    def test_v1_transient_count_prefix_symbolic_and_surviving_target_drift_stop(self):
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        self.tool.git('add', 'a.txt')
        other = self.tool.git('write-tree').decode().strip()
        prefix = 'refs/codex/turn-diffs/captures/'
        self.tool.git('update-ref', prefix + 'one', tree)
        self.tool.git('update-ref', prefix + 'two', other)
        before = self.v1(self.tool.ref_snapshot())
        self.tool.git('update-ref', prefix + 'one', other)
        self.tool.git('update-ref', prefix + 'two', tree)
        # Same aggregate targets do not permit retargeting surviving names.
        self.assertEqual(c.ref_projection(before), c.ref_projection(self.v1(self.tool.ref_snapshot())))
        with self.assertRaisesRegex(ValueError, 'fixture'):
            c.check_ref_context(before, self.tool.ref_snapshot(), 'fixture', [])
        self.tool.git('update-ref', prefix + 'one', tree)
        self.tool.git('update-ref', prefix + 'two', other)
        self.tool.git('update-ref', prefix + 'extra', tree)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            c.check_ref_context(before, self.tool.ref_snapshot(), 'fixture', [])
        self.tool.git('update-ref', '-d', prefix + 'extra')
        self.tool.git('update-ref', '-d', prefix + 'one')
        self.tool.git('update-ref', 'refs/codex/turn-diffs/checkpoints/one', tree)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            c.check_ref_context(before, self.tool.ref_snapshot(), 'fixture', [])
        self.tool.git('update-ref', '-d', 'refs/codex/turn-diffs/checkpoints/one')
        self.tool.git('update-ref', prefix + 'one', tree)
        self.tool.git('symbolic-ref', prefix + 'symbolic', prefix + 'one')
        actual = self.tool.ref_snapshot()
        self.assertNotIn(prefix + 'symbolic ' + tree, actual['transient_refs'])
        with self.assertRaisesRegex(ValueError, 'fixture'):
            c.check_ref_context(before, actual, 'fixture', [])

    def snapshot(self, owner='refs/heads/tx/mine'):
        return self.tool.ref_snapshot(owner=owner)

    def test_v2_tolerates_other_missions_sessions_remotes_codex_and_stash(self):
        head = self.f.parent
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        other = self.tool.git('commit-tree', tree, '-m', 'other').decode().strip()
        self.tool.git('update-ref', 'refs/heads/tx/mine', head)
        before = self.snapshot()
        self.assertEqual(before['ref_policy'], c.REF_POLICY_V2)
        for name in ('refs/heads/tx/other', 'refs/heads/claude/session', 'refs/remotes/origin/topic',
                     'refs/codex/anything/at/all', 'refs/codex/turn-diffs/captures/x', 'refs/stash'):
            with self.subTest(name=name):
                audits = []
                self.tool.git('update-ref', name, head)  # created
                c.check_ref_context(before, self.snapshot(), 'fixture', audits)
                self.assertEqual(audits[-1]['added'], [name])
                known = self.snapshot()
                self.tool.git('update-ref', name, other)  # moved to another object
                c.check_ref_context(known, self.snapshot(), 'fixture', audits)
                self.assertEqual(audits[-1]['moved'], [name])
                self.tool.git('update-ref', '-d', name)  # deleted
                c.check_ref_context(known, self.snapshot(), 'fixture', audits)
                self.assertEqual(audits[-1]['removed'], [name])
                self.assertEqual(c.ref_projection(before), c.ref_projection(self.snapshot()))
        audits = []  # Unchanged tolerated refs record no audit.
        c.check_ref_context(before, self.snapshot(), 'fixture', audits)
        self.assertEqual(audits, [])

    def test_v2_keeps_the_dependencies_exact(self):
        head = self.f.parent
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        other = self.tool.git('commit-tree', tree, '-m', 'other').decode().strip()
        self.tool.git('update-ref', 'refs/heads/tx/mine', head)
        for name in ('refs/tags/protected', 'refs/replace/' + '1' * 40, 'refs/notes/commits',
                     'refs/heads/feature/other', 'refs/unknown/protected'):
            with self.subTest(name=name):
                baseline = self.snapshot()
                self.tool.git('update-ref', name, tree if name.startswith('refs/replace') else head)
                with self.assertRaisesRegex(ValueError, 'fixture'):
                    c.check_ref_context(baseline, self.snapshot(), 'fixture', [])
                self.tool.git('update-ref', '-d', name)
        for name in ('refs/heads/tx/mine', 'refs/heads/main'):  # The owner's branch, and main as the parent.
            with self.subTest(name=name):
                baseline = self.tool.ref_snapshot(owner='refs/heads/tx/mine')
                old = self.tool.git('rev-parse', name).decode().strip()
                self.tool.git('update-ref', name, other)
                with self.assertRaisesRegex(ValueError, 'fixture'):
                    c.check_ref_context(baseline, self.tool.ref_snapshot(owner='refs/heads/tx/mine'), 'fixture', [])
                self.tool.git('update-ref', name, old)
        baseline = self.tool.ref_snapshot(owner='')  # Without an owner every transaction branch stays exact.
        self.tool.git('update-ref', 'refs/heads/tx/other', head)
        with self.assertRaisesRegex(ValueError, 'fixture'):
            c.check_ref_context(baseline, self.tool.ref_snapshot(owner=''), 'fixture', [])

    def test_v2_worktree_rows_keep_only_the_owners_exact(self):
        own, other = 'refs/heads/tx/mine', 'refs/heads/tx/other'
        rows = lambda *r: '\0\0'.join(r)
        mine = 'worktree /w/mine\0HEAD ' + 'a' * 40 + '\0branch ' + own + '\0locked transaction:mine'
        theirs = 'worktree /w/other\0HEAD ' + 'b' * 40 + '\0branch ' + other
        base = dict(self.snapshot(), worktrees=rows(mine))
        c.check_ref_context(base, dict(base, worktrees=rows(mine, theirs)), 'fixture', [])  # created
        c.check_ref_context(dict(base, worktrees=rows(mine, theirs)), base, 'fixture', [])  # removed
        c.check_ref_context(dict(base, worktrees=rows(mine, theirs)),
                            dict(base, worktrees=rows(mine, theirs.replace('b' * 40, 'c' * 40))), 'fixture', [])  # moved
        with self.assertRaisesRegex(ValueError, 'fixture'):  # the owner's own registration moved
            c.check_ref_context(base, dict(base, worktrees=rows(mine.replace('a' * 40, 'c' * 40))), 'fixture', [])
        with self.assertRaisesRegex(ValueError, 'fixture'):  # and its lock
            c.check_ref_context(base, dict(base, worktrees=rows(mine.replace('\0locked transaction:mine', ''))),
                                'fixture', [])
        with self.assertRaisesRegex(ValueError, 'fixture'):  # configuration beside the refs stays exact
            c.check_ref_context(dict(base, configuration='1'), dict(base, configuration='2'), 'fixture', [])

    def test_old_policies_keep_their_own_comparison(self):
        head = self.f.parent
        for template in ({}, {'ref_policy': c.REF_POLICY_V1}):
            for name in ('refs/heads/claude/session', 'refs/heads/tx/other', 'refs/remotes/origin/topic',
                         'refs/stash', 'refs/codex/anything'):
                with self.subTest(template=template, name=name):
                    baseline = c.restamp(self.tool.ref_snapshot(), template)
                    self.tool.git('update-ref', name, head)
                    with self.assertRaisesRegex(ValueError, 'fixture'):
                        c.check_ref_context(baseline, self.tool.ref_snapshot(), 'fixture', [])
                    self.tool.git('update-ref', '-d', name)

    def test_v2_validation_window_tolerates_unrelated_refs_and_stops_on_protected_ones(self):
        head = self.f.parent
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        runner = self.tool.runner
        for name, tolerated in (('refs/heads/claude/session', True), ('refs/heads/tx/other', True),
                                ('refs/remotes/origin/topic', True), ('refs/stash', True),
                                ('refs/tags/protected', False), ('refs/replace/' + '1' * 40, False),
                                ('refs/notes/commits', False), ('refs/heads/feature/other', False)):
            with self.subTest(name=name):
                changed = False

                def churn(argv, root):
                    nonlocal changed
                    result = runner(argv, root)
                    if not changed:
                        self.tool.git('update-ref', name, tree if name.startswith('refs/replace') else head)
                        changed = True
                    return result
                self.tool.runner = churn
                try:
                    if tolerated:
                        packet = self.prepare()
                        self.assertEqual(packet['ref_policy'], c.REF_POLICY_V2)
                        self.assertEqual(packet['transient_ref_audits'][-1]['added'], [name])
                    else:
                        with self.assertRaisesRegex(ValueError, 'Refs changed during validation'):
                            self.prepare()
                finally:
                    self.tool.runner = runner
                    self.tool.git('update-ref', '-d', name)
                    self.tool.abandon('fixture reset')

    def test_base_binding_ignores_only_reviewed_same_tree_ref_names(self):
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        old, new = ('refs/codex/turn-diffs/checkpoints/' + n for n in ('old', 'new'))
        self.tool.git('update-ref', old, tree)
        packet = self.prepare()
        bound = self.tool.validation_binding(packet, self.check)
        self.tool.git('update-ref', '-d', old)
        self.tool.git('update-ref', new, tree)
        self.assertEqual(bound, self.tool.validation_binding(packet, self.check))
        legacy = dict(packet)
        legacy.pop('ref_policy')
        bound_legacy = self.tool.validation_binding(legacy, self.check)
        self.tool.git('update-ref', '-d', new)
        self.tool.git('update-ref', old, tree)
        self.assertNotEqual(bound_legacy, self.tool.validation_binding(legacy, self.check))
        self.tool.git('update-ref', 'refs/tags/protected', self.f.parent)
        self.assertNotEqual(bound, self.tool.validation_binding(packet, self.check))

    def test_v1_validation_ref_churn_audit_and_changed_target_refusal(self):
        with patch.object(checkpoint_state, 'REF_POLICY', c.REF_POLICY_V1):  # An old packet keeps its policy through the whole transaction.
            self.v1_validation_ref_churn()

    def v1_validation_ref_churn(self):
        prefix = 'refs/codex/turn-diffs/captures/'
        tree = self.tool.git('rev-parse', 'HEAD^{tree}').decode().strip()
        self.tool.git('update-ref', prefix + 'old/base', tree)
        runner = self.tool.runner
        changed = False
        def churn(argv, root):
            nonlocal changed
            result = runner(argv, root)
            if not changed:
                self.tool.git('update-ref', '-d', prefix + 'old/base')
                self.tool.git('update-ref', prefix + 'new/base', tree)
                changed = True
            return result
        self.tool.runner = churn
        packet = self.prepare()
        self.assertTrue(packet['transient_ref_audits'])
        self.tool.runner = runner
        # New-target drift during a later check must fail even for content-only evidence.
        changed = False
        def retarget(argv, root):
            nonlocal changed
            result = runner(argv, root)
            if not changed:
                self.tool.git('update-ref', prefix + 'new/base', self.tool.git('write-tree').decode().strip())
                changed = True
            return result
        self.tool.runner = retarget
        with self.assertRaisesRegex(ValueError, 'Refs changed during validation'):
            self.tool.seal()

    def expensive_calls(self):
        return sum('test_checkpoint*.py' in argv for argv in self.calls)

    def test_exact_bytes_reuse_fresh_cheap_checks_and_g018(self):
        p = self.prepare()
        self.assertEqual(p['state'], 'validated')
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.tool.commit()
        self.tool = c.Checkpoint(self.f.root, runner=self.tool.runner)  # Process restart.
        sealed = self.tool.seal()
        self.assertEqual(self.expensive_calls(), 1)
        self.assertEqual(len(self.calls), 5)  # Two whitespace, two continuity, one fixture suite.
        self.assertEqual(sealed['validation_runs'][-1]['action'], 'reused')
        self.assertEqual(sealed['tree'], p['tree'])
        with redirect_stdout(io.StringIO()):
            self.tool.approve(lambda _: 'APPROVE ' + sealed['id'] + ' ' + c.digest(c.canonical(sealed)))
        verified = self.tool.commit()
        self.assertEqual(verified['tree'], sealed['tree'])
        self.assertEqual(verified['parent'], self.f.parent)
        self.assertEqual(self.tool.git('cat-file', 'commit', verified['result_sha']).split(b'\n\n', 1)[1],
                         self.f.message.read_bytes())

    def test_the_test_runner_command_replays_like_the_unittest_command(self):
        every = dict({n: (*v, None) for n, v in c.REPLAYABLE.items()}, **c.IMPACT_REPLAYABLE)
        for name, (starts, pattern, test_filter) in every.items():
            tail = [w for s in starts for w in ('-s', s)] + ['-p', pattern, '-v'] + (['-k', test_filter] if test_filter else [])
            with self.subTest(check=name):
                # The unittest command is only the same discovery when there is one folder to discover in.
                heads = [['{python}', '-B', rp.RUN_TESTS]]
                if len(starts) == 1:
                    heads.append(['{python}', '-B', '-m', 'unittest', 'discover'])
                else:
                    self.assertFalse(self.tool.replayable({'id': name, 'argv': ['{python}', '-B', '-m', 'unittest', 'discover'] + tail}))
                for head in heads:
                    self.assertTrue(self.tool.replayable({'id': name, 'argv': head + tail}))
                    self.assertFalse(self.tool.replayable({'id': name, 'argv': head + tail + ['-k', 'skip']}))
                self.assertFalse(self.tool.replayable({'id': name, 'argv': ['{python}', '-B', 'other/run_tests.py'] + tail}))
        self.assertFalse(self.tool.replayable({'id': 'test-runner-tests', 'argv': [
            '{python}', '-B', rp.RUN_TESTS, '-s', 'governance/tests', '-p', 'test_run_tests.py', '-v']}))

    def test_impact_groups_reuse_exact_content_results_and_refresh_cheap_gates(self):
        checks = []
        for name, (starts, pattern, test_filter) in c.IMPACT_REPLAYABLE.items():
            argv = ['{python}', '-B', rp.RUN_TESTS] + [w for s in starts for w in ('-s', s)] + ['-p', pattern, '-v']
            if test_filter:
                argv += ['-k', test_filter]
            check = {'id': name, 'argv': argv}
            self.assertTrue(self.tool.replayable(check))
            self.assertFalse(self.tool.replayable(dict(check, argv=argv + ['-k', 'skip'])))
            self.f.policy['commands'][name] = argv
            checks.append(check)
        self.f.profiles['profiles']['test']['checks'] += checks
        self.f.write_json(c.POLICY, self.f.policy)
        self.f.write_json(c.PROFILES, self.f.profiles)
        self.prepare()
        before = len(self.calls)
        sealed = self.tool.seal()
        self.assertEqual(len(self.calls) - before, 2)  # fresh whitespace and continuity only
        last = {r['id']: r['action'] for r in sealed['validation_runs'][-7:]}
        for name in c.IMPACT_REPLAYABLE:
            self.assertEqual(last[name], 'reused')
            entry = next(e for e in sealed['evidence'] if e['id'] == name)
            self.assertEqual(entry['binding_kind'], 'content-v1')
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.tool.commit()

    def test_stale_missing_ambiguous_and_altered_result_rerun(self):
        p = self.prepare()
        original = copy.deepcopy(p['evidence'])
        for defect in ('binding', 'missing', 'duplicate', 'output', 'exit', 'argv', 'failed'):
            with self.subTest(defect=defect):
                p['state'] = 'validated'
                p['evidence'] = copy.deepcopy(original)
                entry = p['evidence'][-1]
                if defect == 'binding':
                    entry['binding'] = 'stale'
                elif defect == 'missing':
                    p['evidence'].pop()
                elif defect == 'duplicate':
                    p['evidence'].append(copy.deepcopy(entry))
                elif defect == 'failed':
                    entry['result']['exit_code'] = 1
                    entry['result_sha256'] = c.digest(c.canonical(entry['result']))
                else:
                    key = {'output': 'stdout', 'exit': 'exit_code', 'argv': 'argv'}[defect]
                    entry['result'][key] = 'altered'
                self.tool.save(p)
                before = self.expensive_calls()
                self.tool.seal()
                self.assertEqual(self.expensive_calls(), before + 1)

    def test_environment_or_interpreter_version_change_forces_execution(self):
        self.prepare()
        with patch.dict(os.environ, {'EVIDENCE_FIXTURE_CHANGED': 'true'}):
            self.tool.seal()
        self.assertEqual(self.expensive_calls(), 2)
        packet = self.tool.load()
        packet['state'] = 'validated'
        self.tool.save(packet)
        with patch.object(c.sys, 'version', c.sys.version + ' fixture version'):
            self.tool.seal()
        self.assertEqual(self.expensive_calls(), 3)

    def test_unauthenticated_evidence_is_never_adopted(self):
        self.prepare()
        path = self.tool.store / c.TRANSACTION
        envelope = json.loads(path.read_bytes())
        envelope['packet']['evidence'][-1]['stdout'] = 'forged PASS'
        path.write_bytes(c.canonical(envelope))
        with self.assertRaisesRegex(ValueError, 'seal mismatch'):
            self.tool.seal()
        self.assertEqual(self.expensive_calls(), 1)

    def test_changed_candidate_fails_closed_without_refreshing_g018(self):
        p = self.prepare()
        (self.f.root / 'a.txt').write_text('different bytes\n')
        self.f.git('add', 'a.txt')
        with self.assertRaisesRegex(ValueError, 'Stale staged tree'):
            self.tool.seal()
        self.assertEqual(self.tool.load()['tree'], p['tree'])
        self.assertEqual(self.tool.head(), self.f.parent)

    def test_bindings_cover_manifest_tree_profile_tool_config_argv_environment(self):
        p = self.prepare()
        baseline = self.tool.validation_binding(p, self.check)
        for key in ('manifest', 'profile_data', 'policy'):
            changed = copy.deepcopy(p)
            if key == 'manifest':
                changed[key] = ['b.txt']
            elif key == 'policy':
                changed[key]['required_checks'] = ['continuity']  # a different policy
            else:
                changed[key] = {'changed': True}
            self.assertNotEqual(self.tool.validation_binding(changed, self.check), baseline)
        other = dict(self.check, argv=self.check['argv'] + ['changed'])
        self.assertNotEqual(self.tool.validation_binding(p, other), baseline)
        with patch.dict(os.environ, {'EVIDENCE_FIXTURE': 'different'}):
            self.assertNotEqual(self.tool.validation_binding(p, self.check), baseline)
        self.f.git('config', 'fixture.changed', 'true')
        self.assertNotEqual(self.tool.validation_binding(p, self.check), baseline)
        self.f.git('config', '--unset', 'fixture.changed')
        path = self.f.root / rp.CHECKPOINT_EVIDENCE
        path.write_bytes(path.read_bytes() + b'\n# changed tool\n')
        self.assertNotEqual(self.tool.validation_binding(p, self.check), baseline)
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.seal()

    def test_interruption_resumes_completed_results_without_inventing_pass(self):
        run = self.tool.runner
        def interrupted(argv, root):
            if 'test_checkpoint*.py' in argv:
                raise KeyboardInterrupt('fixture interruption')
            return run(argv, root)
        self.tool.runner = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.prepare()
        p = self.tool.load()
        self.assertEqual(p['state'], 'preparing')
        self.assertNotIn('governance-regressions', [e['id'] for e in p['evidence']])
        resumed = c.Checkpoint(self.f.root, runner=run).seal()
        self.assertEqual(resumed['state'], 'prepared')
        self.assertEqual(self.expensive_calls(), 1)

    def test_completed_expensive_result_survives_interrupt_before_final_seal(self):
        original = self.tool.assert_prepared
        count = 0
        def interrupt(packet):
            nonlocal count
            count += 1
            if count == 2:
                raise KeyboardInterrupt('after validation')
            original(packet)
        with patch.object(self.tool, 'assert_prepared', side_effect=interrupt), self.assertRaises(KeyboardInterrupt):
            self.prepare()
        self.tool.seal()
        self.assertEqual(self.expensive_calls(), 1)

    def test_unrelated_work_is_bound_and_preserved(self):
        path = self.f.root / 'b.txt'
        path.write_text('unrelated edit\n')
        self.prepare()
        self.tool.seal()
        self.assertEqual(path.read_text(), 'unrelated edit\n')
        self.assertEqual(self.tool.inventory(), ['a.txt'])
        p = self.tool.load()
        p['state'] = 'validated'
        self.tool.save(p)
        path.write_text('unrelated changed after evidence\n')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.seal()  # Already sealed; no automatic new approval state.


if __name__ == '__main__':
    unittest.main()
