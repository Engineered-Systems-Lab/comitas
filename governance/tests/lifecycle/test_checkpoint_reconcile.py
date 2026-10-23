"""Bounded stale-base reconciliation; all mutations stay in disposable repositories."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
import checkpoint as c
from governance.tools.worktrees import guard as w
from governance.tests.worktrees import test_checkpoint_worktrees as fixtures
import repo_paths as rp


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.WorktreeTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.flow = self.f.flow
        self.base = self.f.base
        self.path = self.f.path
        self.owner = self.f.owner
        self.t = self.f.create()
        (self.path / 'a.txt').write_bytes(b'owned edit\r\n')
        self.p = self.t.prepare(['a.txt'], self.f.f.message, 'test', self.base,
                               'refs/heads/tx/' + self.owner, 'Fixture scope', validate_only=True)
        self.raw = (self.t.store / c.TRANSACTION).read_bytes()
        self.owned = (self.path / 'a.txt').read_bytes()
        self.blob = self.t.git('show', ':a.txt')

    def test_observed_six_ref_same_tree_churn_reconciles_with_authenticated_audit(self):
        # Exact names from the saved 2026-10-08 failure; fixture targets are disposable.
        removed = ['refs/codex/turn-diffs/captures/1791493370083/f6cc3ff6-1f67-45a3-9154-de3a1280ebdd/base', 'refs/codex/turn-diffs/checkpoints/0ffbdcd6f70c87bce378fa4d5cce3525/e8a91779bf9d17b204453f67cf21d489/1791493309800/ce3dfd9c-6138-4df1-97c1-5f7eda987978', 'refs/codex/turn-diffs/checkpoints/efb6bd169b50b2e8afe1c6b736722b5d/be518aeeb6ecf45af330cd85839331cd/1791493314218/5fc89395-7615-4686-8c6b-c4ee18a339f3']
        added = ['refs/codex/turn-diffs/captures/1791494664091/8911643a-75e4-4ea9-af3d-5a855add4b94/base', 'refs/codex/turn-diffs/checkpoints/0ffbdcd6f70c87bce378fa4d5cce3525/e7c6e4bf4cf109eaf570fa86e1b97f25/1791494399976/22a64bf6-cad3-4cd6-8055-a42dfc0f3530', 'refs/codex/turn-diffs/checkpoints/efb6bd169b50b2e8afe1c6b736722b5d/947ef825d7a9f31c7b375220ce14b4c4/1791494576454/8a083cad-8672-438e-90c3-ab89013d756e']
        self.t.abandon('Recreate disposable preparation with observed refs')
        tree = self.t.git('rev-parse', 'HEAD^{tree}').decode().strip()
        for name in removed:
            self.t.git('update-ref', name, tree)
        self.p = self.t.prepare(['a.txt'], self.f.f.message, 'test', self.base,
                               'refs/heads/tx/' + self.owner, 'Fixture scope', validate_only=True)
        raw = (self.t.store / c.TRANSACTION).read_bytes()
        for name in removed:
            self.t.git('update-ref', '-d', name)
        for name in added:
            self.t.git('update-ref', name, tree)
        result = self.reconcile(self.advance())
        packet = self.t.load()
        plan = self.t.load_path(self.t.store / result['audit'])
        self.assertEqual((self.t.store / plan['archive']).read_bytes(), raw)
        self.assertTrue(packet['transient_ref_audits'])
        self.assertEqual(packet['state'], 'validated')
        self.assertNotIn('approval', packet)
        self.assertEqual((self.path / 'a.txt').read_bytes(), self.owned)
        self.assertEqual(self.t.git('show', ':a.txt'), self.blob)

    def test_protected_ref_additions_stop_reconciliation_without_mutation(self):
        new = self.advance()
        for name in ('refs/heads/protected', 'refs/tags/protected', 'refs/unknown/protected',
                     'refs/replace/' + '1' * 40, 'refs/notes/protected', 'refs/heads/feature/protected'):
            with self.subTest(name=name):
                self.t.git('update-ref', name, self.base)
                with self.assertRaisesRegex(ValueError, 'Unexpected refs/worktrees'):
                    self.reconcile(new)
                self.unchanged()
                self.t.git('update-ref', '-d', name)

    def test_unrelated_branches_remotes_codex_stash_and_worktrees_do_not_stop_reconciliation(self):
        other = Path(w.layout(self.f.root, 'other')['worktree'])
        self.flow.create(None, 'other', self.base)  # created and registered after the preparation
        new = self.advance()
        for name in ('refs/heads/tx/other-mission', 'refs/heads/claude/session', 'refs/remotes/origin/protected',
                     'refs/stash', 'refs/codex/unknown/protected', 'refs/codex/turn-diffs/captures-near/protected',
                     'refs/codex/turn-diffs/captures/non-tree'):
            self.t.git('update-ref', name, self.base)
        self.assertTrue(other.exists())
        result = self.reconcile(new)
        self.assertEqual(result['state'], 'reconciled')
        packet = self.t.load()
        self.assertEqual(packet['ref_policy'], c.REF_POLICY_V2)
        self.assertEqual((self.path / 'a.txt').read_bytes(), self.owned)
        self.assertEqual(c.Checkpoint(other).head(), self.base)
        self.assertTrue(self.reconcile(new)['idempotent'])
        # The owner's own branch, moved by the journaled step, is the one branch compared exactly.
        self.assertEqual(self.t.head(), new)

    def test_legacy_preparation_keeps_exact_ref_names(self):
        name = 'refs/codex/turn-diffs/captures/old/base'
        tree = self.t.git('rev-parse', 'HEAD^{tree}').decode().strip()
        self.t.git('update-ref', name, tree)
        packet = self.t.load()
        context = self.t.reconciliation_context()
        packet.pop('ref_policy')
        packet['reconciliation_context'] = {k: v for k, v in context.items()
                                            if k not in ('ref_policy', 'transient_refs')}
        self.t.save(packet)  # Deliberate legacy fixture, never a production packet migration.
        raw = (self.t.store / c.TRANSACTION).read_bytes()
        self.t.git('update-ref', '-d', name)
        self.t.git('update-ref', 'refs/codex/turn-diffs/captures/new/base', tree)
        with self.assertRaisesRegex(ValueError, 'Unexpected refs/worktrees'):
            self.reconcile(self.advance())
        self.assertEqual((self.t.store / c.TRANSACTION).read_bytes(), raw)
        self.assertEqual(self.t.head(), self.base)

    def advance(self, name='b.txt', content=b'upstream\n'):
        (self.f.root / name).write_bytes(content)
        self.f.f.git('add', name)
        self.f.f.git('commit', '-m', 'Disposable upstream fixture')
        return self.flow.main.head()

    def reconcile(self, new):
        return self.flow.reconcile(self.path, self.owner, self.base, new)

    def unchanged(self):
        self.assertEqual(self.t.head(), self.base)
        self.assertEqual((self.path / 'a.txt').read_bytes(), self.owned)
        self.assertEqual(self.t.git('show', ':a.txt'), self.blob)
        self.assertEqual((self.t.store / c.TRANSACTION).read_bytes(), self.raw)

    def test_nonoverlap_preserves_owned_bytes_and_blobs_and_final_g018(self):
        new = self.advance()
        result = self.reconcile(new)
        self.assertEqual(result['state'], 'reconciled')
        self.assertEqual(self.t.head(), new)
        self.assertEqual((self.path / 'a.txt').read_bytes(), self.owned)
        self.assertEqual(self.t.git('show', ':a.txt'), self.blob)
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.t.commit()
        sealed = self.t.seal()
        self.assertEqual(sealed['parent'], new)
        with redirect_stdout(io.StringIO()):
            self.t.approve(lambda _: 'APPROVE ' + sealed['id'] + ' ' + c.digest(c.canonical(sealed)))
        p = self.t.commit()
        self.assertEqual(p['parent'], new)
        self.assertEqual(p['tree'], sealed['tree'])
        self.assertEqual(self.t.git('cat-file', 'commit', p['result_sha']).split(b'\n\n', 1)[1],
                         self.f.f.message.read_bytes())
        self.t.finish()
        self.assertEqual({p.name for p in self.t.store.iterdir()}, {c.RECEIPT_KEY, ac.LANDING})

    def test_multiple_commits_bound_and_retry_is_idempotent(self):
        one = self.advance()
        two = self.advance('runtime.txt')
        result = self.reconcile(two)
        self.assertEqual(result['commits'], [one, two])
        raw = (self.t.store / c.TRANSACTION).read_bytes()
        self.assertTrue(self.reconcile(two)['idempotent'])
        self.assertEqual((self.t.store / c.TRANSACTION).read_bytes(), raw)

    def test_overlap_rejects_without_mutation_even_if_reverted(self):
        self.advance('a.txt')
        new = self.advance('a.txt', b'old\n')
        with self.assertRaisesRegex(ValueError, 'overlaps'):
            self.reconcile(new)
        self.unchanged()

    def test_non_descendant_rejected(self):
        # Independent root commit, moved only inside this disposable fixture.
        tree = self.flow.main.git('write-tree').decode().strip()
        unrelated = self.flow.main.git('commit-tree', tree, data=b'Independent fixture\n').decode().strip()
        self.flow.main.git('update-ref', 'refs/heads/main', unrelated, self.base)
        with self.assertRaisesRegex(ValueError, 'merge-base'):
            self.reconcile(unrelated)
        self.unchanged()

    def test_dirty_main_rejected(self):
        new = self.advance()
        (self.f.root / 'b.txt').write_text('dirty main\n')
        with self.assertRaisesRegex(ValueError, 'must be clean'):
            self.reconcile(new)
        self.unchanged()

    def test_working_and_staged_drift_rejected(self):
        new = self.advance()
        (self.path / 'a.txt').write_text('drift\n')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.reconcile(new)
        self.t.git('add', 'a.txt')
        with self.assertRaisesRegex(ValueError, 'Stale staged tree'):
            self.reconcile(new)
        self.assertEqual(self.t.head(), self.base)

    def test_original_failed_packet_is_preserved_with_authenticated_range(self):
        p = self.t.load()
        p.update(state='prepare-failed', error='stale base fixture')
        self.t.save(p)
        raw = (self.t.store / c.TRANSACTION).read_bytes()
        new = self.advance()
        r = self.reconcile(new)
        plan = self.t.load_path(self.t.store / r['audit'])
        self.assertEqual((self.t.store / plan['archive']).read_bytes(), raw)
        self.assertEqual(plan['paths'], ['b.txt'])
        self.assertEqual(plan['delta_sha256'], c.digest(self.t.git('diff', '--binary', '--no-ext-diff',
                                                                 '--no-textconv', self.base, new)))

    def test_approval_does_not_carry_across_parent(self):
        p = self.t.seal()
        with redirect_stdout(io.StringIO()):
            self.t.approve(lambda _: 'APPROVE ' + p['id'] + ' ' + c.digest(c.canonical(p)))
        self.reconcile(self.advance())
        self.assertNotIn('approval', self.t.load())
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.t.commit()

    def test_conversation_consent_reconcile_reseal_requires_fresh_user_reply(self):
        self.t.seal()
        review = self.t.present()
        consent = {'source': 'active-task-user', 'conversation_id': 'fixture-task',
                   'user_message_id': 'user-turn-2', 'channel': 'text-token', 'response': 'APPROVE',
                   'decision': 'approve', 'transaction_id': review['transaction_id'],
                   'prepared_sha256': review['prepared_sha256']}
        self.t.approve_consent(consent)
        self.reconcile(self.advance())
        self.assertNotIn('approval', self.t.load())
        with self.assertRaisesRegex(ValueError, 'sealed prepared'):
            self.t.approve_consent(consent)
        self.t.seal()
        with self.assertRaisesRegex(ValueError, 'currently presented'):
            self.t.approve_consent(consent)
        review = self.t.present()
        fresh = dict(consent, prepared_sha256=review['prepared_sha256'], user_message_id='user-turn-4')
        self.t.approve_consent(fresh)
        self.assertEqual(self.t.commit()['state'], 'verified')

    def test_interruption_after_index_or_ref_update_resumes_without_reapplication(self):
        new = self.advance()
        git = self.t.git
        for boundary in ('read-tree', 'update-ref'):
            with self.subTest(boundary=boundary):
                # First retry reaches the next boundary; source construction returns this tool.
                def interrupted(*args, **kwargs):
                    result = git(*args, **kwargs)
                    if args[0] == boundary:
                        raise KeyboardInterrupt('fixture after ' + boundary)
                    return result
                with patch.object(self.flow, 'source', return_value=self.t), patch.object(
                        self.t, 'git', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
                    self.reconcile(new)
        self.assertEqual(self.reconcile(new)['state'], 'reconciled')
        self.assertEqual((self.path / 'a.txt').read_bytes(), self.owned)
        self.assertTrue(self.reconcile(new)['idempotent'])

    def test_unrelated_refs_and_worktrees_preserved_and_new_drift_rejected(self):
        # Preparation context deliberately records a second owned checkout.
        other = Path(w.layout(self.f.root, 'other')['worktree'])
        self.flow.create(None, 'other', self.base)
        self.flow.main.git('branch', 'preserved-fixture', self.base)
        p = self.t.load()
        p['reconciliation_context'] = self.t.reconciliation_context()
        self.t.save(p)
        context = p['reconciliation_context']
        new = self.advance()
        self.reconcile(new)
        self.assertEqual(self.flow.main.git('rev-parse', 'preserved-fixture').decode().strip(), self.base)
        self.assertEqual(c.Checkpoint(other).head(), self.base)
        self.assertEqual(context['worktrees'].split('worktree ')[-1],
                         self.t.reconciliation_context()['worktrees'].split('worktree ')[-1])

    def test_unexpected_refs_configuration_and_ownership_fail_closed(self):
        new = self.advance()
        self.flow.main.git('branch', 'unexpected-fixture', new)
        with self.assertRaisesRegex(ValueError, 'Unexpected refs'):
            self.reconcile(new)
        self.flow.main.git('branch', '-d', 'unexpected-fixture')
        self.flow.main.git('config', 'fixture.drift', 'true')
        with self.assertRaisesRegex(ValueError, 'Stale Git configuration'):
            self.reconcile(new)
        self.flow.main.git('config', '--unset', 'fixture.drift')
        self.flow.main.git('worktree', 'unlock', str(self.path))
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.reconcile(new)
        self.assertEqual(self.t.head(), self.base)

    def test_same_main_returns_current_without_packet_changes(self):
        self.assertEqual(self.reconcile(self.base)['state'], 'current')
        self.unchanged()
        self.assertEqual(self.t.seal()['state'], 'prepared')

    def test_new_upstream_path_cannot_overwrite_ignored_content(self):
        # Ignored content is outside the checkpoint snapshot but still must survive.
        (self.path / 'ignored').mkdir()
        probe = self.path / 'ignored/probe.txt'
        probe.write_bytes(b'precious ignored bytes\n')
        (self.f.root / 'ignored').mkdir()
        (self.f.root / 'ignored/probe.txt').write_bytes(b'upstream bytes\n')
        self.flow.main.git('add', '-f', 'ignored/probe.txt')
        self.flow.main.git('commit', '-m', 'Disposable ignored collision')
        with self.assertRaisesRegex(ValueError, 'untracked/ignored'):
            self.reconcile(self.flow.main.head())
        self.assertEqual(probe.read_bytes(), b'precious ignored bytes\n')
        self.unchanged()

    def test_ambiguous_partial_working_update_stops_on_retry(self):
        new = self.advance()
        git = self.t.git
        def interrupted(*args, **kwargs):
            result = git(*args, **kwargs)
            if args[0] == 'read-tree':
                raise KeyboardInterrupt('fixture completed index update')
            return result
        with patch.object(self.flow, 'source', return_value=self.t), patch.object(
                self.t, 'git', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.reconcile(new)
        (self.path / 'a.txt').write_bytes(b'intermediate drift\n')
        with self.assertRaisesRegex(ValueError, 'Intermediate index/working'):
            self.reconcile(new)
        self.assertEqual(self.t.head(), self.base)

    def test_changed_validation_inputs_invalidate_content_evidence(self):
        p = self.t.load()
        check = {'id': 'governance-regressions', 'argv': ['{python}', '-B', rp.RUN_TESTS,
                 '-s', 'governance/tests', '-s', 'governance/config', '-p', 'test_checkpoint*.py', '-v']}
        baseline = self.t.validation_binding(p, check, content=True)
        (self.path / 'governance/tools/new-test.py').write_text('changed validation input\n')
        self.assertNotEqual(self.t.validation_binding(p, check, content=True), baseline)

    def test_tool_policy_classifies_reconciliation_tests(self):
        root = rp.ROOT
        policy = json.loads((root / c.POLICY).read_bytes())
        profiles = json.loads((root / c.PROFILES).read_bytes())
        shipped = object.__new__(c.Checkpoint)
        shipped.root = root
        _, profile = shipped.select_profile(['governance/tests/lifecycle/test_checkpoint_reconcile.py'],
                                            policy, profiles, 'auto')
        self.assertEqual(profile['change_classification'], 'tooling')

    def test_content_evidence_reuses_but_base_evidence_executes(self):
        # Add reviewed fixture suites before validation, retaining authenticated evidence.
        p = self.t.load()
        checks = []
        for id, (starts, pattern) in c.REPLAYABLE.items():
            argv = ['{python}', '-B', rp.RUN_TESTS] + [w for s in starts for w in ('-s', s)] + ['-p', pattern, '-v']
            p['policy']['commands'][id] = argv
            checks.append({'id': id, 'argv': argv})
        p['profile_data']['checks'] += checks
        calls = []
        def runner(argv, root):
            calls.append(argv)
            return {'argv': argv, 'exit_code': 0, 'stdout': 'PASS\n', 'stderr': '', 'duration_seconds': 5.0}
        self.t.runner = runner
        # This packet is built by hand to exercise evidence binding, so its selection is not the derived one.
        with patch.object(self.t, 'assert_selection_current'):
            self.t.validate_packet(p, False)
            before = len(calls)
            new = self.advance()
            self.reconcile(new)
            sealed = self.t.seal()
        last = {r['id']: r['action'] for r in sealed['validation_runs'][-5:]}
        self.assertEqual(last['governance-regressions'], 'reused')
        self.assertEqual(last['continuity-regressions'], 'executed')
        self.assertEqual(len(calls) - before, 4)  # whitespace, continuity, regressions, base suite


SELECTION_ROWS = [  # (path, class, profile, boundary, invariants), sorted by path
    ('a.txt', 'tooling', 'test', '-', '-'),
    ('b.txt', 'tooling', 'test', '-', '-'),
    ('devlog/*.md', 'record', 'test', '-', '-'),
    ('docs/*.md', 'governance', 'test', '-', '-'),
    ('frozen/', 'governance', 'test', 'frozen', '-'),
    ('mod.py', 'tooling', 'test', '-', 'symbol-map'),
    ('new-component/*.txt', 'tooling', 'test', '-', '-'),
    ('runtime.txt', 'runtime', 'test', 'runtime', '-'),
    ('governance/tools/*.py', 'tooling', 'test', '-', '-'),
]
SELECTION_ROWS.sort()  # the table parser requires ASCII ordinal order
CONTEXT = {'identity': 'Human', 'isAI': False, 'configuration': None}
MESSAGE = 'Subject\n\n- Detail.\n\nClass: tooling\nWorker: Human\n'
# The checkout rules and transaction authority files that worktree reconcile refuses to carry across.
AUTHORITY = [c.POLICY, c.PROFILES, c.PROJECT_POLICY, c.PROJECT_PROFILES, 'governance/tools/checkpoint.py', 'governance/tools/worktree.py', rp.AGENTS,
             'governance/CONSTRAINTS.md', 'governance/DECISIONS.md', 'governance/WORKTREE-WORKFLOW.md',
             'governance/CHECKPOINT-WORKFLOW.md', 'governance/CHECKPOINT-TOOL.md']
CHECKOUT_RULES = ['.gitattributes', '.gitignore', 'sub/.gitignore', 'sub/deep/.gitattributes']
STALE = 'classification or selection rules changed since preparation'


def selection_table(change=None):
    """The fixture artifact classification table; `change` maps a path to its replacement row fields."""
    lines = ['# Fixture classification', '', ac.HEADER, ac.SEPARATOR]
    for path, *cells in SELECTION_ROWS:
        cls, profile, boundary, invariants = (change or {}).get(path, cells)
        lines.append('| `%s` | %s | %s | %s | - | %s | - | - | - |' % (path, cls, profile, boundary, invariants))
    return '\n'.join(lines) + '\n'


class ReconcileSelectionTest(unittest.TestCase):
    """A reconciled transaction must derive the same selection from the successor base, or it is not sealed."""

    def setUp(self):
        self.f = fixtures.WorktreeTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.fx = self.f.f
        self.flow = self.f.flow
        self.owner = self.f.owner
        self.branch = 'refs/heads/tx/' + self.owner
        replay = lambda pattern: ['{python}', '-B', rp.RUN_TESTS, '-s', 'governance/tests', '-s', 'governance/config', '-p', pattern, '-v']
        policy = self.fx.policy
        policy['commit_metadata_required'] = True
        policy['commands'].update({'governance-regressions': replay('test_checkpoint*.py'),
                                   'continuity-regressions': replay('test_continuity.py'),
                                   'extra': ['{python}', '-B', '-c', 'print("extra PASS")']})
        normal = [{'id': n, 'argv': a} for n, a in policy['commands'].items() if n != 'extra']
        profiles = self.fx.profiles
        profiles['profiles']['test']['checks'] = normal
        profiles['profiles']['strict'] = dict(profiles['profiles']['test'], checks=normal + [
            {'id': 'extra', 'argv': policy['commands']['extra']}])
        profiles['impact'] = {'version': 1, 'invariants': {'inv': {'groups': ['regressions']}},
                              'symbol_maps': {'mod.py': {'f': ['inv']}}}
        self.fx.write_json(c.POLICY, policy)
        self.fx.write_json(c.PROFILES, profiles)
        (self.f.root / c.TABLE).write_text(selection_table(), encoding='utf-8', newline='\n')
        (self.f.root / 'mod.py').write_text('def f():\n    return 1\n\n\ndef g():\n    return 2\n')
        self.fx.git('add', '--', c.POLICY, c.PROFILES, c.TABLE, 'mod.py')
        self.fx.git('commit', '-m', 'Selection fixture rules')
        self.base = self.f.base = self.fx.parent = self.fx.git('rev-parse', 'HEAD').strip()
        self.path = self.f.path
        self.t = self.f.create()
        self.calls = []

        def runner(argv, root):
            self.calls.append(argv)
            return {'argv': argv, 'exit_code': 0, 'stdout': 'PASS\n', 'stderr': '', 'duration_seconds': 5.0}
        self.t.runner = runner
        self.fx.message.write_text(MESSAGE, encoding='utf-8')

    def prepare(self, manifest=('a.txt',), head=None):
        return self.t.prepare(list(manifest), self.fx.message, 'auto', head or self.base, self.branch,
                              'Fixture scope', validate_only=True, worker_context=CONTEXT)

    def prepare_owned(self, name='a.txt', data=b'owned edit\r\n'):
        (self.path / name).write_bytes(data)
        self.owned = {name: data}
        return self.prepare([name])

    def upstream(self, changes):
        for name, data in changes.items():
            path = self.f.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            self.fx.git('add', '--', name)
        self.fx.git('commit', '-m', 'Disposable upstream fixture')
        return self.flow.main.head()

    def upstream_table(self, change=None):
        return self.upstream({c.TABLE: selection_table(change).encode()})

    def reconcile(self, new):
        return self.flow.reconcile(self.path, self.owner, self.base, new)

    def expensive(self):
        return sum('test_checkpoint*.py' in argv for argv in self.calls)

    def rewind_main(self):
        # Only inside this disposable fixture: put main back so the next case starts from the prepared base.
        self.flow.main.git('reset', '--hard', self.base)

    def test_ordinary_preparation_seals_exactly_what_the_shared_derivation_returns(self):
        p = self.prepare_owned()
        derived = self.t.derive_selection(['a.txt'], MESSAGE, p['policy'], self.t.data()[1], CONTEXT, 'auto', self.branch)
        self.assertEqual(c.canonical(list(derived)), c.canonical([p['profile'], p['profile_data'], p['runtime']]))
        self.assertEqual(p['profile'], 'test')
        self.assertEqual(self.t.seal()['state'], 'prepared')

    def test_owned_path_reclassified_to_need_an_extra_check_refuses_seal_and_abandon_recovers(self):
        self.prepare_owned()
        new = self.upstream_table({'a.txt': ('tooling', 'strict', '-', '-')})
        self.assertEqual(self.reconcile(new)['state'], 'reconciled')
        with self.assertRaisesRegex(ValueError, STALE):
            self.t.seal()
        self.assertEqual(self.t.load()['state'], 'validated')
        self.assertEqual(self.t.abandon()['state'], 'idle')
        again = self.prepare(head=new)
        self.assertEqual(again['profile'], 'strict')
        self.assertIn('extra', [check['id'] for check in again['profile_data']['checks']])
        self.assertEqual(self.t.seal()['state'], 'prepared')

    def test_owned_path_that_becomes_blocked_refuses_seal(self):
        self.prepare_owned()
        self.reconcile(self.upstream_table({'a.txt': ('tooling', 'blocked', '-', '-')}))
        with self.assertRaisesRegex(ValueError, STALE + '.*No reviewed validation scope'):
            self.t.seal()
        self.assertEqual(self.t.load()['state'], 'validated')

    def test_owned_path_that_becomes_frozen_refuses_seal(self):
        self.prepare_owned()
        self.reconcile(self.upstream_table({'a.txt': ('tooling', 'test', 'frozen', '-')}))
        with self.assertRaisesRegex(ValueError, STALE + '.*Frozen path'):
            self.t.seal()

    def test_class_trailer_that_no_longer_matches_refuses_seal(self):
        self.prepare_owned()
        self.reconcile(self.upstream_table({'a.txt': ('governance', 'test', '-', '-')}))
        with self.assertRaisesRegex(ValueError, STALE + '.*Class must match'):
            self.t.seal()

    def test_a_sealed_selection_that_differs_from_the_live_derivation_refuses_seal(self):
        self.prepare_owned()
        packet = self.t.load()
        packet['profile_data']['limitations'] = 'tampered'
        self.t.save(packet)
        with self.assertRaisesRegex(ValueError, STALE):
            self.t.seal()

    def seal_and_actions(self):
        sealed = self.t.seal()
        self.assertEqual(sealed['state'], 'prepared')
        return {r['id']: r['action'] for r in sealed['validation_runs'][-4:]}

    def assert_reuse_unaffected(self, new):
        before = self.expensive()
        self.assertEqual(self.reconcile(new)['state'], 'reconciled')
        actions = self.seal_and_actions()
        self.assertEqual(actions['governance-regressions'], 'reused')
        self.assertEqual(self.expensive(), before)  # the expensive suite did not run again
        self.assertEqual(actions['continuity-regressions'], 'executed')  # base-sensitive evidence refreshes
        self.assertEqual(actions['continuity'], 'executed')

    def test_unrelated_table_row_change_seals_with_equal_selection_and_reusable_evidence(self):
        p = self.prepare_owned()
        self.assertEqual(self.expensive(), 1)
        # The fixture table does not mark itself Bound, so only the selection can differ here and it does not.
        self.assert_reuse_unaffected(self.upstream_table({'b.txt': ('governance', 'test', '-', '-')}))
        sealed = self.t.load()
        self.assertEqual(c.canonical([sealed['profile'], sealed['profile_data'], sealed['runtime']]),
                         c.canonical([p['profile'], p['profile_data'], p['runtime']]))

    def test_unrelated_non_bound_file_change_seals_and_a_replayable_suite_is_reused(self):
        self.prepare_owned()
        self.assert_reuse_unaffected(self.upstream({'b.txt': b'upstream\n'}))

    def test_table_edit_touched_and_reverted_upstream_is_accepted_without_staleness(self):
        self.prepare_owned()
        self.upstream_table({'a.txt': ('tooling', 'strict', '-', '-')})
        new = self.upstream_table()
        self.assertEqual(self.reconcile(new)['state'], 'reconciled')
        self.assertEqual(self.t.seal()['state'], 'prepared')

    def test_moving_head_does_not_change_the_symbol_impact_of_a_valid_transaction(self):
        # changed_symbols compares the working file with HEAD, which reconcile moves; the manifest is untouched.
        p = self.prepare_owned('mod.py', b'def f():\r\n    return 10\r\n\r\n\r\ndef g():\r\n    return 2\r\n')
        impact = p['profile_data']['impact']['mod.py']
        self.assertEqual((impact['mode'], impact['symbols']), ('mapped', ['f']))
        self.reconcile(self.upstream({'b.txt': b'upstream\n', 'new-component/x.txt': b'more\n'}))
        sealed = self.t.seal()
        self.assertEqual(sealed['state'], 'prepared')
        self.assertEqual(sealed['profile_data']['impact'], p['profile_data']['impact'])

    def unchanged(self, raw):
        self.assertEqual(self.t.head(), self.base)
        self.assertEqual((self.path / 'a.txt').read_bytes(), self.owned['a.txt'])
        self.assertEqual((self.t.store / c.TRANSACTION).read_bytes(), raw)

    def test_upstream_change_to_each_transaction_authority_path_is_rejected_without_mutation(self):
        self.prepare_owned()
        raw = (self.t.store / c.TRANSACTION).read_bytes()
        for name in AUTHORITY:
            with self.subTest(path=name):
                current = self.f.root / name
                new = self.upstream({name: (current.read_bytes() if current.exists() else b'') + b'\n'})
                try:
                    with self.assertRaisesRegex(ValueError, 'Upstream transaction authority requires human review'):
                        self.reconcile(new)
                    self.unchanged(raw)
                finally:
                    self.rewind_main()

    def test_upstream_change_to_a_checkout_rule_file_is_rejected_without_mutation(self):
        self.prepare_owned()
        raw = (self.t.store / c.TRANSACTION).read_bytes()
        for name in CHECKOUT_RULES:
            with self.subTest(path=name):
                current = self.f.root / name
                new = self.upstream({name: (current.read_bytes() if current.exists() else b'') + b'# rule\n'})
                try:
                    with self.assertRaisesRegex(ValueError, 'Upstream checkout rules require human review'):
                        self.reconcile(new)
                    self.unchanged(raw)
                finally:
                    self.rewind_main()

    def test_authority_path_touched_and_reverted_upstream_is_still_rejected(self):
        self.prepare_owned()
        self.upstream({rp.AGENTS: b'changed\n'})
        self.fx.git('rm', '-q', '--', rp.AGENTS)
        self.fx.git('commit', '-m', 'Disposable revert')
        with self.assertRaisesRegex(ValueError, 'Upstream transaction authority requires human review'):
            self.reconcile(self.flow.main.head())


class ReconcileNewFolderTest(unittest.TestCase):
    """A transaction that ADDS A FOLDER reconciles like any other: Git collapses the new directory in the pre-staging status."""

    def setUp(self):
        self.f = fixtures.WorktreeTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.t = self.f.create()
        (self.f.path / 'new-component').mkdir()
        (self.f.path / 'new-component/x.txt').write_bytes(b'new file\n')
        (self.f.path / 'new-component/y.txt').write_bytes(b'another new file\n')
        self.manifest = ['new-component/x.txt', 'new-component/y.txt']
        self.t.prepare(self.manifest, self.f.f.message, 'test', self.f.base, 'refs/heads/tx/' + self.f.owner,
                       'Fixture scope', validate_only=True)

    def advance(self, name='b.txt'):
        (self.f.root / name).write_bytes(b'upstream\n')
        self.f.f.git('add', name)
        self.f.f.git('commit', '-m', 'Disposable upstream fixture')
        return self.f.flow.main.head()

    def test_the_collapsed_new_directory_is_not_unrelated_dirty_state(self):
        self.assertEqual(self.t.load()['residual'], ['?? new-component/'], 'the premise: Git collapsed the new directory')
        new = self.advance()
        result = self.f.flow.reconcile(self.f.path, self.f.owner, self.f.base, new)
        self.assertEqual((result['state'], self.t.head()), ('reconciled', new))
        packet = self.t.load()
        self.assertEqual((packet['state'], packet['manifest']), ('validated', self.manifest))
        self.assertEqual((self.f.path / 'new-component/x.txt').read_bytes(), b'new file\n')
        sealed = self.t.seal()
        self.assertEqual((sealed['state'], sealed['parent']), ('prepared', new))

    def test_a_real_unrelated_dirty_path_still_stops_the_reconcile(self):
        (self.f.path / 'stray.txt').write_text('unrelated\n')
        self.t.abandon('Prepare again beside an unrelated file')
        self.t.prepare(self.manifest, self.f.f.message, 'test', self.f.base, 'refs/heads/tx/' + self.f.owner,
                       'Fixture scope', validate_only=True)
        with self.assertRaisesRegex(ValueError, 'Unrelated dirty state requires human review'):
            self.f.flow.reconcile(self.f.path, self.f.owner, self.f.base, self.advance())
        self.assertEqual(self.t.head(), self.f.base)


if __name__ == '__main__':
    unittest.main()
