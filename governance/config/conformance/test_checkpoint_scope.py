"""Manifest selection and unchanged approval boundaries, without runtime execution."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import checkpoint as c
from governance.tests.lifecycle import test_checkpoint as fixtures
from governance.tests.fake_git import FakeGit, bare_tool
import repo_paths as rp


REPO = rp.ROOT
# The one check that runs the whole conformance folder (and the continuity tests) for any change under governance/ or project-governance/.
CONFORMANCE = 'continuity-regressions'


def table_text(replace=None, add=()):
    """The real classification table with some rows replaced (by path) or added, kept sorted."""
    lines = (REPO / c.TABLE).read_text(encoding='utf-8').split(chr(10))
    start = lines.index(next(l for l in lines if l.startswith('| Path | Class | Profile |'))) + 2
    end = start
    while lines[end].startswith('|'):
        end += 1
    rows = {l.split('`')[1]: l for l in lines[start:end]}
    for path, line in (replace or {}).items():
        assert path in rows, path
        rows[path] = line
    for line in add:
        rows[line.split('`')[1]] = line
    return chr(10).join(lines[:start] + [rows[k] for k in sorted(rows)] + lines[end:])


def extension_checks():
    """The policy commands the installed extensions declare in their manifests; empty when none is installed."""
    names = set()
    for manifest in sorted((REPO / 'governance' / 'extensions').glob('*/extension.json')):
        names.update(json.loads(manifest.read_text(encoding='utf-8'))['policy_commands'])
    return names


class ScopeTest(unittest.TestCase):
    def variant(self, **kwargs):
        """A checkpoint whose root holds an edited copy of the table; policy and profiles stay in memory."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        (Path(temp.name) / 'governance').mkdir()
        (Path(temp.name) / c.TABLE).write_text(table_text(**kwargs), encoding='utf-8', newline=chr(10))
        tool = object.__new__(c.Checkpoint)
        tool.root = Path(temp.name)
        return tool

    def setUp(self):
        root = rp.ROOT
        self.policy = json.loads((root / c.POLICY).read_bytes())
        self.profiles = json.loads((root / c.PROFILES).read_bytes())
        self.tool = object.__new__(c.Checkpoint)
        self.tool.root = rp.ROOT

    def select(self, paths, requested='auto'):
        return self.tool.select_profile(paths, self.policy, self.profiles, requested)

    def ids(self, paths):
        return {v['id'] for v in self.select(paths)[1]['checks']}

    def core_ids(self, paths):
        """The checks of the core: those an installed extension owns (its manifest says which) are left out."""
        return self.ids(paths) - extension_checks()

    def test_record_only_including_index_and_progress(self):
        paths = ['devlog/new.md', rp.PROGRESS]
        name, profile = self.select(paths)
        self.assertEqual(name, 'record-only')
        self.assertEqual(self.ids(paths), {'continuity'})
        self.assertEqual(self.select([rp.RATIONALE])[0], 'record-only')
        self.assertEqual(self.core_ids([rp.RATIONALE]), {'continuity', CONFORMANCE})
        self.tool.root = Path('.')
        with patch.object(self.tool, 'runner', create=True, return_value={'exit_code': 0}) as runner:
            self.tool.checks(profile['checks'])
        self.assertEqual(runner.call_count, 1)
        self.assertIn('governance/tools/validate_continuity.py', runner.call_args.args[0])

    def test_a_governance_path_nobody_classified_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'No reviewed'):
            self.select(['governance/UNREVIEWED.md'])

    def test_governance_is_focused(self):
        for path in ['AGENTS.md', 'governance/README.md',
                     rp.DECISIONS, 'governance/CONSTRAINTS.md',
                     'governance/CHECKPOINT-WORKFLOW.md', 'governance/CHECKPOINT-TOOL.md',
                     'governance/COMMAND-CONTINUITY.md', 'governance/COMMAND-RECOVERY-PROMPT-v1.md',
                     'governance/WORKTREE-WORKFLOW.md', 'devlog/CONVENTIONS.md']:
            with self.subTest(path=path):
                name, profile = self.select([path])
                self.assertEqual(name, 'governance')
                self.assertEqual(profile['classification'], {path: 'governance'})
                self.assertEqual(profile['change_classification'], 'governance')
                self.assertIn('continuity', self.ids([path]))
                self.assertNotIn('governance-regressions', self.ids([path]))

    def test_deleted_documents_are_historical_not_live(self):
        for path in ('docs/CHECKPOINT-TOOL.md', 'docs/COMMAND-CONTINUITY.md',
                     'docs/COMMAND-RECOVERY-PROMPT-v1.md', 'docs/WORKTREE-WORKFLOW.md'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'No reviewed validation scope'):
                self.select([path])

    def test_tooling_category_is_separate_from_validation_scope(self):
        for path in [c.POLICY, c.PROFILES, rp.CHECKPOINT,
                     'governance/tools/validate_continuity.py', 'governance/tools/worktree.py',
                     'governance/config/conformance/test_checkpoint_scope.py']:
            with self.subTest(path=path):
                name, profile = self.select([path])
                self.assertEqual(name, 'tooling')
                self.assertEqual(profile['classification'], {path: 'tooling'})
                self.assertIn('continuity', self.ids([path]))

    def test_record_metadata_and_conservative_normative_docs(self):
        for path in ['devlog/new.md', 'devlog/README.md',
                     rp.RATIONALE, rp.PROGRESS, 'docs/PLAN.md', 'README.md']:
            with self.subTest(path=path):
                name, profile = self.select([path])
                self.assertEqual(name, 'record-only')
                self.assertEqual(profile['classification'][path], 'record')
        # Even a registry-only DECISIONS edit conservatively exercises governance checks.
        self.assertEqual(self.select([rp.DECISIONS])[0], 'governance')

    def test_cannot_override_or_omit_continuity_or_change_commands(self):
        for name, profile in self.profiles['profiles'].items():
            broken = copy.deepcopy(profile)
            broken['checks'] = [x for x in broken['checks'] if x['id'] != 'continuity']
            with self.subTest(profile=name), self.assertRaises(ValueError):
                self.tool.check_commands(broken, self.policy, False)
        for path in [rp.DECISIONS, rp.CHECKPOINT]:
            for check in self.select([path])[1]['checks']:
                # Only the conformance check discovers every test of its folders; none runs the whole suite.
                self.assertNotEqual('python-regressions', check['id'])
                if check['id'] != CONFORMANCE:
                    self.assertNotIn('test_*.py', check['argv'])
        with self.assertRaisesRegex(ValueError, 'must match'):
            self.select([rp.CHECKPOINT], 'record-only')
        broken = copy.deepcopy(self.profiles['profiles']['tooling'])
        broken['checks'][-1]['argv'] = ['echo', 'skip']
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.tool.check_commands(broken, self.policy, False)


class ImpactTest(unittest.TestCase):
    setUp = ScopeTest.setUp
    variant = ScopeTest.variant
    select = ScopeTest.select
    ids = ScopeTest.ids
    core_ids = ScopeTest.core_ids
    # Reuse policy setup/helpers, without duplicating inherited scope tests.
    def test_worktree_only_selects_affected_invariants(self):
        ids = self.ids(['governance/tools/worktree.py', 'governance/tests/lifecycle/test_checkpoint_reconcile.py'])
        self.assertEqual(ids, {'continuity', 'checkpoint-worktrees', 'checkpoint-reconcile',
                               'checkpoint-evidence', 'checkpoint-invariants', CONFORMANCE})
        self.assertNotIn('checkpoint-selector', ids)

    def test_evidence_selects_evidence_and_integrity(self):
        self.assertEqual(self.ids(['governance/tests/lifecycle/test_checkpoint_evidence.py']),
                         {'continuity', 'checkpoint-evidence', 'checkpoint-invariants', CONFORMANCE})

    def test_policy_selector_selects_selector_and_integrity(self):
        for path in (c.POLICY, c.PROFILES, 'governance/config/conformance/test_checkpoint_scope.py'):
            self.assertEqual(self.ids([path]), {'continuity', 'checkpoint-selector', 'checkpoint-invariants', 'commit-metadata',
                                                CONFORMANCE})

    def test_four_document_guidance_is_lightweight(self):
        manifest = ['AGENTS.md', 'governance/CONSTRAINTS.md', rp.DECISIONS,
                    rp.RATIONALE]
        name, profile = self.select(manifest)
        self.assertEqual(name, 'governance')
        self.assertEqual(profile['change_classification'], 'mixed')
        self.assertEqual(self.core_ids(manifest), {'continuity', CONFORMANCE})
        self.assertEqual(self.ids(manifest[:2]), {'continuity', CONFORMANCE})
        self.assertEqual(self.ids([rp.PROGRESS]), {'continuity'})

    def test_any_change_under_a_governance_folder_selects_the_whole_conformance_folder(self):
        command = self.policy['commands'][CONFORMANCE]
        self.assertEqual(command[command.index('-p') + 1], 'test_*.py')
        self.assertIn('governance/config', [command[i + 1] for i, word in enumerate(command) if word == '-s'])
        inside = [rp.DECISIONS, 'governance/CHECKPOINT-WORKFLOW.md', 'governance/README.md', c.POLICY, c.PROFILES,
                  rp.TABLE, 'governance/config/artifact-classification-golden.json',
                  'governance/config/conformance/test_decision_prefix.py',
                  'governance/config/conformance/test_go_asked_alone.py',
                  'governance/tests/run_tests.py', 'governance/tools/runtime_guard.py', 'governance/tools/worktree.py',
                  'governance/tools/local_jobs.py', 'governance/WORKER-HAZARDS.md',
                  'project-governance/CONSTRAINTS.md', 'project-governance/config/checkpoint-features.toml']
        for path in inside:
            with self.subTest(path=path):
                self.assertIn(CONFORMANCE, self.ids([path]))
        # Whatever else a change under governance/ or project-governance/ selects, it never loses the check.
        for symbols in (None, [], ['Unreviewed.symbol']):
            with self.subTest(symbols=symbols), patch.object(self.tool, 'changed_symbols', return_value=symbols):
                self.assertIn(CONFORMANCE, self.ids([rp.CHECKPOINT]))

    def test_no_tracked_path_under_a_governance_folder_can_miss_the_conformance_check(self):
        """The guard against a forgotten mapping: follow the real table and the real dependency edges for every file."""
        import subprocess
        listed = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-z', '--', 'governance', 'project-governance'],
                                capture_output=True)
        self.assertEqual(listed.returncode, 0)
        files = [f for f in listed.stdout.decode('utf-8').split(chr(0)) if f]
        self.assertGreater(len(files), 100)
        table = self.tool.table(self.policy, self.profiles)
        invariants = self.profiles['impact']['invariants']
        symbol_maps = self.profiles['impact']['symbol_maps']

        def reached(names):
            pending, seen = list(names), set(names)
            while pending:
                for dependency in invariants[pending.pop()].get('requires', []):
                    if dependency not in seen:
                        seen.add(dependency)
                        pending.append(dependency)
            return seen

        for path in files:
            resolved = table.resolve(path)
            with self.subTest(path=path):
                self.assertIsNotNone(resolved)
                if resolved.invariants is None:  # unmapped: the whole envelope of the profile runs
                    envelope = {check['id'] for check in self.profiles['profiles'][resolved.profile]['checks']}
                    self.assertIn(CONFORMANCE, envelope)
                    continue
                named = [i for i in resolved.invariants if i != 'symbol-map']
                if named:
                    self.assertIn('conformance', reached(named))
                if 'symbol-map' in resolved.invariants:  # every mapped symbol; an unmapped one runs the envelope
                    for symbol, mapped in symbol_maps.get(path, {}).items():
                        self.assertIn('conformance', reached(mapped), symbol)

    def test_agents_md_pins_rule_text_so_it_selects_the_conformance_folder_too(self):
        self.assertIn(CONFORMANCE, self.ids(['AGENTS.md']))

    def test_a_change_outside_the_governance_folders_does_not_select_the_conformance_folder(self):
        for path in ('docs/PLAN.md', 'README.md', 'devlog/new.md', rp.PROGRESS):
            with self.subTest(path=path):
                self.assertNotIn(CONFORMANCE, self.ids([path]))

    def test_the_runtime_guard_selects_only_its_focused_group_and_continuity(self):
        for path in ('governance/tools/runtime_guard.py', 'governance/tests/support/test_runtime_guard.py'):
            with self.subTest(path=path):
                self.assertEqual(self.ids([path]), {'continuity', 'governance-runtime', CONFORMANCE})

    def test_approval_mapping_selects_integrity_reconcile_and_evidence(self):
        expected = {'continuity', 'checkpoint-invariants', 'checkpoint-reconcile', 'checkpoint-evidence', CONFORMANCE}
        self.assertEqual(self.ids(['governance/tests/lifecycle/test_checkpoint.py']), expected)
        homes = {'assignment:DECISION_CONTROLS': ('lifecycle/checkpoint_lifecycle', 'assignment:DECISION_CONTROLS'),
                 'assignment:DECISION_TOKENS': ('lifecycle/checkpoint_lifecycle', 'assignment:DECISION_TOKENS'),
                 'assignment:DECISION_CHANNELS': ('lifecycle/checkpoint_lifecycle', 'assignment:DECISION_CHANNELS'),
                 'main': ('checkpoint', 'main')}
        for method in ('present', 'approve_consent', 'consent_receipt', 'assert_approval', 'commit', 'commit_identity'):
            homes['Lifecycle.' + method] = ('lifecycle/checkpoint_lifecycle', 'Lifecycle.' + method)
        for symbol, (module, key) in homes.items():
            # main also hosts the estimate and present --text commands, and present renders the review.
            wanted = expected | {'main': {'timing-tests', 'review-tests', 'early-approval-tests'},
                                 'Lifecycle.present': {'review-tests'},
                                 'Lifecycle.assert_approval': {'early-approval-tests'},
                                 'Lifecycle.approve_consent': {'interpreter-pin-tests'},
                                 'Lifecycle.commit': {'early-approval-tests', 'interpreter-pin-tests'},
                                 'Lifecycle.consent_receipt': {'approval-words-tests'}}.get(symbol, set())
            with self.subTest(symbol=symbol), patch.object(self.tool, 'changed_symbols', return_value=[key]):
                self.assertEqual(self.ids(['governance/tools/%s.py' % module]), wanted)

    def test_unknown_invariant_or_command_escalates(self):
        # An invariant the vocabulary does not know cannot even be written into the table.
        bad = self.variant(replace={c.POLICY: '| `' + c.POLICY + '` | tooling | tooling | - | - | unreviewed | checkpoint | - | - |'})
        with self.assertRaisesRegex(ValueError, 'unknown invariant'):
            bad.select_profile([c.POLICY], self.policy, self.profiles, 'auto')
        # A known invariant whose command group is unknown escalates to the whole envelope.
        profiles = copy.deepcopy(self.profiles)
        profiles['impact']['invariants']['selector']['groups'] = ['unreviewed']
        profile = self.tool.select_profile([c.POLICY], self.policy, profiles, 'auto')[1]
        self.assertEqual({c['id'] for c in profile['checks']},
                         {c['id'] for c in profiles['profiles']['tooling']['checks']})

    def test_symbol_mapping_unknown_or_missing_context_escalates(self):
        with patch.object(self.tool, 'changed_symbols', return_value=['Selection.select_profile']):
            self.assertEqual(self.ids([rp.CHECKPOINT_SELECTION]),
                             {'continuity', 'checkpoint-selector', 'checkpoint-invariants', 'commit-metadata',
                              'link-retarget-tests', CONFORMANCE})
        with patch.object(self.tool, 'changed_symbols', return_value=['Evidence.replayable']):
            self.assertEqual(self.ids([rp.CHECKPOINT_EVIDENCE]),
                             {'continuity', 'checkpoint-evidence', 'checkpoint-invariants', CONFORMANCE})
        # A symbol the reviewed map does not name, or no source context at all, keeps the whole envelope.
        for path, symbols in ((rp.CHECKPOINT_STATE, None), (rp.CHECKPOINT_STATE, []),
                              (rp.CHECKPOINT_STATE, ['RepoState.quiet_operation']),
                              (rp.CHECKPOINT_SELECTION, ['Selection.select_profile', 'Selection.unreviewed']),
                              (rp.CHECKPOINT, ['Checkpoint.driver_digest'])):
            with self.subTest(path=path, symbols=symbols), patch.object(self.tool, 'changed_symbols', return_value=symbols):
                self.assertIn('governance-regressions', self.ids([path]))

    def test_the_head_table_readers_are_mapped_and_select_narrow_checks(self):
        mapped = {'Selection.head_table': {'checkpoint-selector', 'classification-tests', 'early-approval-tests'},
                  'Selection.deleted_scope': {'checkpoint-selector', 'classification-tests'}}
        for symbol, groups in mapped.items():
            with self.subTest(symbol=symbol), patch.object(self.tool, 'changed_symbols', return_value=[symbol]):
                ids = self.ids([rp.CHECKPOINT_SELECTION])
                self.assertTrue(groups <= ids, ids)
                self.assertNotIn('governance-regressions', ids)
        with patch.object(self.tool, 'changed_symbols', return_value=list(mapped) + ['Selection.in_runtime']):
            self.assertNotIn('governance-regressions', self.ids([rp.CHECKPOINT_SELECTION]))

    def test_declared_component_dependency_selects_union_without_cross_leak(self):
        # Future component mappings are fixture policy only; no runtime execution.
        policy, profiles = copy.deepcopy(self.policy), copy.deepcopy(self.profiles)
        rows = {}
        for component in ('alpha', 'beta'):
            group = component + '-fixture'
            policy['commands'][group] = ['{python}', '-B', '-c', 'print("fixture")']
            profiles['profiles'][group] = {'checks': [
                {'id': 'continuity', 'argv': policy['commands']['continuity']},
                {'id': group, 'argv': policy['commands'][group]}], 'limitations': 'Fixture only'}
            rows[component + '.xml'] = f'| `{component}.xml` | runtime | {group} | - | - | {group} | - | - | - |'
            profiles['impact']['invariants'][group] = {'groups': [group]}
        profiles['impact']['invariants']['shared-fixture'] = {'groups': [],
            'requires': ['alpha-fixture', 'beta-fixture']}
        for shared in ('shared.xml', 'shared.py', 'shared-invariant.json'):
            rows[shared] = f'| `{shared}` | governance | governance | - | - | shared-fixture | - | - | - |'
        state = {'tool': self.variant(add=rows.values())}
        def ids(paths):
            return {c['id'] for c in state['tool'].select_profile(paths, policy, profiles, 'auto')[1]['checks']}
        self.assertEqual(ids(['alpha.xml']), {'continuity', 'alpha-fixture'})
        self.assertEqual(ids(['beta.xml']), {'continuity', 'beta-fixture'})
        self.assertEqual(ids(['shared.xml']), {'continuity', 'alpha-fixture', 'beta-fixture'})
        self.assertEqual(ids(['alpha.xml', 'beta.xml']), ids(['shared.xml']))
        for path in ('shared.py', 'shared-invariant.json'):
            self.assertEqual(ids([path]), ids(['shared.xml']))
        # A reviewed shared dependency is the only reason to cross the boundary.
        rows['alpha.xml'] = '| `alpha.xml` | runtime | alpha-fixture | - | - | alpha-fixture, shared-fixture | - | - | - |'
        state['tool'] = self.variant(add=rows.values())
        self.assertEqual(ids(['alpha.xml']), ids(['shared.xml']))
        self.assertEqual(ids(['beta.xml']), {'continuity', 'beta-fixture'})
        rows['beta.xml'] = '| `beta.xml` | runtime | beta-fixture | - | - | beta-fixture, shared-fixture | - | - | - |'
        state['tool'] = self.variant(add=rows.values())
        self.assertEqual(ids(['beta.xml']), ids(['shared.xml']))


class RuntimeComponentTest(unittest.TestCase):
    """The runtime-gate mechanism with two synthetic components; the template ships none, an adopter adds its own."""
    ROWS = {
        'components/alpha/*.src': '| `components/alpha/*.src` | runtime | alpha-runtime | runtime | - | - | - | - | - |',
        'components/alpha/contract.json': '| `components/alpha/contract.json` | runtime | alpha-contract | frozen, runtime | - | - | - | - | - |',
        'components/beta/*.src': '| `components/beta/*.src` | runtime | beta-runtime | runtime | - | - | - | - | - |',
    }
    GATES = {'alpha-runtime': ['alpha-offline', 'alpha-service'], 'alpha-contract': ['alpha-offline'],
             'beta-runtime': ['beta-offline']}

    def setUp(self):
        root = rp.ROOT
        self.policy = copy.deepcopy(json.loads((root / c.POLICY).read_bytes()))
        self.profiles = copy.deepcopy(json.loads((root / c.PROFILES).read_bytes()))
        for gate in {g for gates in self.GATES.values() for g in gates}:
            self.policy['commands'][gate] = ['{python}', '-B', '-c', 'print("' + gate + '")']
        for name, gates in self.GATES.items():
            checks = [{'id': i, 'argv': self.policy['commands'][i]} for i in ['continuity', *gates]]
            self.profiles['profiles'][name] = {'limitations': 'Synthetic component evidence only.',
                                               'runtime_checks': list(gates), 'checks': checks}
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        (Path(temp.name) / 'governance').mkdir()
        (Path(temp.name) / c.TABLE).write_text(table_text(add=self.ROWS.values()), encoding='utf-8', newline=chr(10))
        self.tool = object.__new__(c.Checkpoint)
        self.tool.root = Path(temp.name)

    def select(self, paths, profiles=None):
        return self.tool.select_profile(paths, self.policy, profiles or self.profiles, 'auto')

    def ids(self, paths):
        return {v['id'] for v in self.select(paths)[1]['checks']}

    def test_a_runtime_path_selects_its_own_gates_and_no_other_components(self):
        name, profile = self.select(['components/alpha/a.src'])
        self.assertEqual(name, 'alpha-runtime')
        self.assertEqual(profile['classification'], {'components/alpha/a.src': 'runtime'})
        self.assertEqual(profile['runtime_checks'], ['alpha-offline', 'alpha-service'])
        self.assertEqual(self.ids(['components/alpha/a.src']), {'continuity', 'alpha-offline', 'alpha-service'})
        self.assertEqual(self.ids(['components/beta/b.src']), {'continuity', 'beta-offline'})
        self.assertFalse({'beta-offline'} & self.ids(['components/alpha/a.src']))

    def test_runtime_gate_requirements_never_leak_between_components(self):
        alpha, beta = 'components/alpha/a.src', 'components/beta/b.src'
        profile = self.select([alpha, beta])[1]
        self.assertEqual(set(profile['runtime_checks']), {'alpha-offline', 'alpha-service', 'beta-offline'})
        self.tool.check_commands(profile, self.policy, True)
        for gate in profile['runtime_checks']:
            broken = dict(profile, checks=[x for x in profile['checks'] if x['id'] != gate])
            with self.subTest(gate=gate), self.assertRaisesRegex(ValueError, 'Profile missing required suite'):
                self.tool.check_commands(broken, self.policy, True)
        # A non-runtime path beside a component neither adds gates nor weakens the component's.
        profile = self.select([beta, rp.DECISIONS])[1]
        self.assertEqual(profile['runtime_checks'], ['beta-offline'])
        self.assertEqual(profile['change_classification'], 'mixed')

    def test_a_frozen_path_cannot_enter_a_checkpoint(self):
        with self.assertRaisesRegex(ValueError, 'Frozen'):
            self.tool.derive_selection(['components/alpha/contract.json'], 'm', self.policy, self.profiles, None, 'auto',
                                       'refs/heads/main')

    def test_unreviewed_paths_under_a_component_block(self):
        for path in ['components/alpha/unreviewed.sh', 'components/gamma/g.src']:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'No reviewed'):
                self.select([path])

    def test_there_is_no_shared_runtime_default_to_inherit(self):
        self.assertNotIn('runtime_checks', self.policy)
        path = ['components/beta/b.src']
        profile = self.select(path)[1]
        profiles = copy.deepcopy(self.profiles)
        del profiles['profiles']['beta-runtime']['runtime_checks']
        with self.assertRaisesRegex(ValueError, 'declares no runtime gates'):
            self.select(path, profiles)
        for bad in ('beta-offline', [], ['no-such-gate'], [7]):
            profiles['profiles']['beta-runtime']['runtime_checks'] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'Invalid runtime gate requirement'):
                self.select(path, profiles)
        bare = {k: v for k, v in profile.items() if k != 'runtime_checks'}
        with self.assertRaisesRegex(ValueError, 'declares no runtime gates'):
            self.tool.check_commands(bare, self.policy, True)
        legacy = dict(self.policy, runtime_checks=['beta-offline'])
        with self.assertRaisesRegex(ValueError, 'declares no runtime gates'):
            self.tool.check_commands(bare, legacy, True)
        self.assertNotIn('runtime_checks', self.select([rp.DECISIONS])[1])

    def test_the_runtime_boundary_is_read_from_the_table(self):
        table = self.tool.table(self.policy, self.profiles)
        self.assertTrue(self.tool.in_runtime('components/alpha/a.src', table))
        self.assertFalse(self.tool.in_runtime(rp.DECISIONS, table))
        self.assertEqual(table.boundary_paths('frozen'), ['components/alpha/contract.json'])
        self.assertEqual(sorted(table.boundary_paths('runtime')),
                         ['components/alpha/*.src', 'components/alpha/contract.json', 'components/beta/*.src'])


class SymbolDiffTest(unittest.TestCase):
    def test_authored_symbol_changes_additions_deletions_and_parse_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            before = 'import json\nclass X:\n    def a(self): return 1\n    def b(self): return 2\n'
            tool = bare_tool(Path(temp), FakeGit({'module.py': before}))
            path = tool.root / 'module.py'
            path.write_text(before.replace('return 1', 'return 3'))
            self.assertEqual(tool.changed_symbols('module.py'), ['X.a'])
            path.write_text(before + 'def added(): return 4\n')
            self.assertEqual(tool.changed_symbols('module.py'), ['added'])
            path.write_text(before.replace('    def b(self): return 2\n', ''))
            self.assertEqual(tool.changed_symbols('module.py'), ['X.b'])
            path.write_text(before + 'class X:\n    def a(self): return 5\n')
            self.assertIsNone(tool.changed_symbols('module.py'))
            path.write_text('syntax failure !')
            self.assertIsNone(tool.changed_symbols('module.py'))
            path.unlink()
            self.assertIsNone(tool.changed_symbols('module.py'))


class ScopeTransactionTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.CheckpointTest()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        f = self.fixture
        f.profiles['profiles']['test']['limitations'] = 'Disposable test commands only.'
        f.write_json(c.POLICY, f.policy)
        f.write_json(c.PROFILES, f.profiles)

    def test_sealed_selection_cannot_commit_without_approval(self):
        f = self.fixture
        packet = f.prepare()
        self.assertEqual(packet['profile_data']['classification'], {'a.txt': 'tooling'})
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            f.tool.commit()
        f.approve()
        f.policy['required_checks'] = ['continuity']
        f.write_json(c.POLICY, f.policy)
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            f.tool.commit()
        self.assertEqual(f.tool.head(), f.parent)

    def test_unknown_rejected_before_staging(self):
        f = self.fixture
        (f.root / 'unknown.dat').write_text('no row covers this\n')
        with self.assertRaisesRegex(ValueError, 'No reviewed'):
            f.prepare(['unknown.dat'])
        self.assertEqual(f.tool.inventory(), [])
        self.assertFalse((f.tool.store / c.TRANSACTION).exists())

    def test_deletion_is_classified_and_sealed(self):
        f = self.fixture
        (f.root / 'b.txt').unlink()
        packet = f.prepare(['b.txt'])
        self.assertEqual(packet['profile_data']['classification'], {'b.txt': 'tooling'})
        self.assertEqual(packet['manifest'], ['b.txt'])

    def test_failed_continuity_blocks_preparation(self):
        f = self.fixture
        original = f.tool.runner
        def fail(argv, root):
            if 'print("PASS")' in argv:
                return {'exit_code': 1, 'stdout': '', 'stderr': 'continuity failure'}
            return original(argv, root)
        with patch.object(f.tool, 'runner', side_effect=fail), self.assertRaisesRegex(ValueError, 'Validation failed'):
            f.prepare()
        self.assertEqual(f.tool.load()['state'], 'prepare-failed')


class CategoryTransactionTest(unittest.TestCase):
    def test_every_profile_keeps_exact_approval_integrity_and_continuity(self):
        # Actual G018 transactions in disposable repositories; component/runtime
        # commands are replaced with explicit fixture checks, never live suites.
        cases = [('record', 'record-only'), ('governance', 'governance'),
                 ('tooling', 'tooling'), ('runtime', 'component-a'),
                 ('runtime', 'component-b'), ('mixed', 'governance+tooling')]
        for category, profile_name in cases:
            with self.subTest(profile=profile_name):
                f = fixtures.CheckpointTest()
                try:
                    f.setUp()
                    names = profile_name.split('+')
                    f.profiles['profiles'] = {name: dict(f.profiles['profiles']['test'], limitations='Fixture only')
                                              for name in names}
                    rows = [('a.txt', category if category != 'mixed' else 'governance', names[0], '-')]
                    manifest = ['a.txt']
                    if category == 'mixed':
                        (f.root / 'b.txt').write_text('new b\n')
                        rows.append(('b.txt', 'tooling', names[1], '-'))
                        manifest.append('b.txt')
                    (f.root / c.TABLE).write_text(fixtures.fixture_table(rows), encoding='utf-8', newline='\n')
                    f.write_json(c.PROFILES, f.profiles)
                    packet = f.tool.prepare(manifest, f.message, 'auto', f.parent, f.tool.branch(), 'Fixture scope')
                    self.assertEqual(packet['profile_data']['change_classification'], category)
                    with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
                        f.tool.commit()
                    f.approve()
                    # Category/scope evidence is part of the integrity seal.
                    envelope_path = f.tool.store / c.TRANSACTION
                    envelope = json.loads(envelope_path.read_bytes())
                    envelope['packet']['profile_data']['impact']['a.txt']['mode'] = 'forged'
                    envelope_path.write_text(json.dumps(envelope))
                    with self.assertRaisesRegex(ValueError, 'seal mismatch'):
                        f.tool.commit()
                    self.assertEqual(f.tool.head(), f.parent)
                    broken = copy.deepcopy(packet['profile_data'])
                    broken['checks'] = [x for x in broken['checks'] if x['id'] != 'continuity']
                    with self.assertRaisesRegex(ValueError, 'missing required'):
                        f.tool.check_commands(broken, f.policy, False)
                finally:
                    f.doCleanups()


class ProjectArtifactScopeTest(unittest.TestCase):
    # Independent check expectations. Extension consumers assert their full selection in their own tests.
    BASE = {'continuity', 'continuity-regressions'}
    SELECTOR = {'checkpoint-selector', 'commit-metadata', 'checkpoint-invariants'}
    WORKTREES = {'checkpoint-worktrees', 'checkpoint-reconcile', 'checkpoint-evidence', 'checkpoint-invariants'}
    EXPECTED = {
        'ARTIFACT-CLASSIFICATION.md': ('governance', 'governance', {'classification-tests', 'checkpoint-selector'}),
        'CONSTRAINTS.md': ('governance', 'governance', set()),
        'DECISIONS.md': ('governance', 'governance', set()),
        'RATIONALE.md': ('record', 'record-only', set()),
        'config/artifact-classification-golden.json': ('tooling', 'tooling', {'classification-tests', 'checkpoint-selector'}),
        'config/checkpoint-policy.json': ('tooling', 'tooling', SELECTOR),
        'config/checkpoint-profiles.json': ('tooling', 'tooling', SELECTOR),
        'config/checkpoint-features.toml': ('tooling', 'tooling', WORKTREES | {'early-approval-tests', 'test-runner-tests'}),
        'config/citations.json': ('tooling', 'tooling', set()),
        'config/devlog.json': ('tooling', 'tooling', WORKTREES),
        'config/frozen-manifest.json': ('tooling', 'tooling', set()),
        'config/reconstruction-contract.json': ('tooling', 'tooling', WORKTREES),
        'config/selector-expectations.json': ('tooling', 'tooling', SELECTOR),
    }

    def test_core_artifact_fixture_is_independent_of_populated_project_dependencies(self):
        from governance.tools.continuity import artifact_classification as ac
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            contents = {path: (REPO / path).read_text(encoding='utf-8')
                        for path in (ac.TABLE, ac.POLICY, ac.PROFILES)}
            contents.update({ac.PROJECT_GOVERNANCE + '/' + name: '# Fixture\n'
                             for name in self.EXPECTED})
            command = ['{python}', '-B', 'fixture_check.py']
            contents[ac.PROJECT_POLICY] = json.dumps({'version': 4, 'commands': {'adopter-stack': command}})
            contents[ac.PROJECT_PROFILES] = json.dumps({
                'version': 4, 'profiles': {'adopter-checks': {
                    'limitations': 'Fixture only.',
                    'checks': [{'id': 'adopter-stack', 'argv': command}]}},
                'impact': {'invariants': {'adopter-stack': {'groups': ['adopter-stack'], 'requires': []}}}})
            contents[ac.PROJECT_TABLE] = '\n'.join([ac.HEADER, ac.SEPARATOR,
                '| `fixture.src` | tooling | adopter-checks | - | - | adopter-stack | - | - | - |', ''])
            for relative, text in contents.items():
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8', newline=chr(10))
            # Establish that the source is valid and contains both reported dependencies.
            tool = bare_tool(source, FakeGit(contents))
            policy, profiles = tool.data()
            self.assertEqual(tool.table(policy, profiles).resolve('fixture.src').profile, 'adopter-checks')
            self.assertEqual(profiles['impact']['invariants']['adopter-stack']['groups'], ['adopter-stack'])
            self.assertEqual(policy['commands']['adopter-stack'], command)
            installed = extension_checks()
            with patch.dict(globals(), REPO=source), patch.dict(globals(), extension_checks=lambda: installed):
                self.test_each_supported_core_artifact_selects_its_checks_present_or_absent()

    def test_each_supported_core_artifact_selects_its_checks_present_or_absent(self):
        from governance.tools.continuity import artifact_classification as ac
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            contents = {path: (REPO / path).read_text(encoding='utf-8')
                        for path in (ac.TABLE, ac.POLICY, ac.PROFILES)}
            # Each project artifact stands alone: deleting policy or profiles must
            # not leave dependencies borrowed from the adopting repository.
            project = {
                'ARTIFACT-CLASSIFICATION.md': fixtures.fixture_table([('fixture-only.md', 'record', 'record-only', '-')]),
                'CONSTRAINTS.md': '# Fixture constraints\n',
                'DECISIONS.md': '# Fixture decisions\n',
                'RATIONALE.md': '# Fixture rationale\n',
                'config/artifact-classification-golden.json': '{"version": 1, "paths": {}}',
                'config/checkpoint-policy.json': '{"version": 4, "commands": {}}',
                'config/checkpoint-profiles.json': '{"version": 4, "profiles": {}}',
                'config/checkpoint-features.toml': '',
                'config/citations.json': '{"version": 1, "external_repositories": []}',
                'config/devlog.json': '{"enabled": false}',
                'config/frozen-manifest.json': '{"version": 1, "files": {}}',
                'config/reconstruction-contract.json': '{"version": 1}',
                'config/selector-expectations.json': '{"version": 1, "expectations": []}',
            }
            self.assertEqual(set(project), set(self.EXPECTED))
            contents.update({ac.PROJECT_GOVERNANCE + '/' + name: text for name, text in project.items()})
            for path, text in contents.items():
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding='utf-8', newline=chr(10))
            tool = bare_tool(root, FakeGit(contents))
            for name, (klass, profile, extra) in self.EXPECTED.items():
                path = ac.PROJECT_GOVERNANCE + '/' + name
                for present in (False, True):
                    with self.subTest(path=path, present=present):
                        if present:
                            (root / path).write_text(contents[path], encoding='utf-8', newline=chr(10))
                        else:
                            (root / path).unlink(missing_ok=True)
                        policy, profiles = tool.data()
                        with patch.object(tool, 'link_retargets', return_value={}):
                            selected = tool.select_profile([path], policy, profiles, 'auto')[1]
                        actual = {check['id'] for check in selected['checks']}
                        self.assertEqual(actual - extension_checks(), self.BASE | extra)
                        self.assertEqual(selected['classification'][path], klass)
                        self.assertEqual(selected['path_profiles'][path], profile)
                        resolved = tool.table(policy, profiles).resolve(path)
                        self.assertEqual(resolved.row.path, path)
                        self.assertFalse(resolved.reachable)
                        self.assertEqual(resolved.boundary, [])
                (root / path).write_text(contents[path], encoding='utf-8', newline=chr(10))


if __name__ == '__main__':
    unittest.main()
