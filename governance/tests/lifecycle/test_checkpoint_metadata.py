"""Commit metadata syntax, pre-staging gates and additive impact selection."""
import copy
import json
from pathlib import Path
import unittest
import tempfile
from unittest.mock import Mock, patch

from governance.tools.continuity import artifact_classification
import checkpoint as c
from governance.tools.lifecycle import commit_metadata as m
from governance.tests.lifecycle import test_checkpoint
import repo_paths as rp


def shipped_tool():
    tool = object.__new__(c.Checkpoint)
    tool.root = rp.ROOT
    return tool


class MetadataTest(unittest.TestCase):
    def setUp(self):
        root = rp.ROOT
        self.profiles = json.loads((root / c.PROFILES).read_text())
        self.policy = json.loads((root / c.POLICY).read_text())

    def message(self, trailers='Class: governance\nSubclass: agent-guidance, checkpoint\nWorker: OpenAI Codex [User-visible configuration]'):
        return 'Keep pleasant titles\n\n- Detail.\n\n' + trailers + '\n'

    def parse(self, trailers):
        return m.parse_metadata(self.message(trailers), self.profiles)

    def test_explicit_canonical_array_and_optional_subclass(self):
        value = m.parse_metadata(self.message(), self.profiles)
        self.assertEqual(value, {'classes': ['governance'], 'subclasses': ['checkpoint', 'agent-guidance'],
                                 'worker': 'OpenAI Codex [User-visible configuration]', 'inferredFields': []})
        self.assertEqual(self.parse('Class: record\nWorker: OpenAI ChatGPT')['subclasses'], [])
        self.assertEqual(self.parse('Class: record\nSubclass: devlog\nWorker: Human')['subclasses'], ['devlog'])

    def test_syntax_fail_closed(self):
        for trailers in (
            'Worker: Human', 'Class: tooling', 'Class: unknown\nWorker: Human',
            'Class: tooling\nClass: tooling\nWorker: Human',
            'Class: tooling\nWorker: Human\nWorker: Human',
            'Class: governance\nSubclass: checkpoint\nSubclass: agent-guidance\nWorker: Human',
            'class: tooling\nWorker: Human', 'Class:  tooling\nWorker: Human',
            'Class: governance\nSubclass: checkpoint,agent-guidance\nWorker: Human',
            'Class: governance\nSubclass: checkpoint,  agent-guidance\nWorker: Human',
            'Class: tooling\nSubclass: checkpoint, checkpoint\nWorker: Human',
            'Class: tooling\nSubclass: checkpoint,\nWorker: Human',
            'Class: tooling\nSubclass: Checkpoint\nWorker: Human',
            'Class: tooling\nSubclass: unknown\nWorker: Human',
            'Class: record\nSubclass: checkpoint\nWorker: Human',
            'Class: tooling\nSubclass: \nWorker: Human',
            'Class: tooling\nWorker: Tool []', 'Class: tooling\nWorker: Tool [ ]',
            'Class: tooling\nWorker: Tool [model', 'Class: tooling\nWorker: Tool [a] [b]',
            'Class: tooling\nWorker: Tool\tName', 'Class: tooling\nWorker: Human\n\nLater body'):
            with self.subTest(trailers=trailers), self.assertRaises(ValueError):
                self.parse(trailers)

    def test_trailer_placement(self):
        for text in ('Title\nClass: tooling\nWorker: Human\n',
                     'Title\n\nClass: tooling\nWorker: Human\nBody',
                     'Title\n\nClass: tooling\n\nWorker: Human',
                     'Title\n\nClass: tooling\nWorker: Human\n\n- Last bullet'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.parse_metadata(text, self.profiles)

    def test_a_trailer_outside_the_allowlist_is_refused_by_name(self):
        for key in ('Co-Authored-By', 'Signed-off-by', 'Status'):
            with self.subTest(key=key), self.assertRaisesRegex(
                    ValueError, '^Trailer ' + key + ' is not allowed in the final trailer block; '
                    'the allowed trailers are Class, Subclass, Worker$'):
                self.parse('Class: tooling\nWorker: Human\n' + key + ': Someone <someone@example.com>')
        # History is read, never refused: reconstruction and the history check tolerate the old blocks.
        self.assertEqual(m.parse_metadata(self.message('Class: tooling\nWorker: Human\nCo-Authored-By: Someone'),
                                          self.profiles, other_trailers=True)['worker'], 'Human')

    def test_a_block_of_only_the_allowed_trailers_is_accepted(self):
        value = self.parse('Class: tooling\nSubclass: checkpoint\nWorker: Anthropic Claude Code [Claude Opus 5.5]')
        self.assertEqual((value['classes'], value['subclasses'], value['worker']),
                         (['tooling'], ['checkpoint'], 'Anthropic Claude Code [Claude Opus 5.5]'))

    def test_class_matches_path_evidence_and_subclass_is_a_label_never_a_lever(self):
        tool = shipped_tool()
        profile = tool.select_profile([rp.AGENTS], self.policy, self.profiles, 'auto')[1]
        labels = profile['artifact_classification'] if 'artifact_classification' in profile else None
        self.assertIsNone(labels)  # sealed by attach_classification during prepare, not by selection
        profile = tool.attach_classification([rp.AGENTS], self.policy, self.profiles, profile)
        declared = m.parse_metadata(self.message('Class: governance\nSubclass: agent-guidance\nWorker: Human'), self.profiles)
        selected = m.refine_selection(declared, profile, self.policy, self.profiles)
        # A label selects nothing: the checks are exactly the path evidence's.
        self.assertEqual(selected['checks'], profile['checks'])
        self.assertEqual(selected['metadata_impact'], {'invariants': [], 'groups': []})
        self.assertFalse(any(check['id'].startswith(('runtime-', 'component-')) for check in selected['checks']))
        # The label must describe the manifest, in either direction.
        for subclasses in (['checkpoint', 'agent-guidance'], ['checkpoint'], []):
            with self.subTest(subclasses=subclasses), self.assertRaisesRegex(ValueError, 'Subclass must match'):
                m.refine_selection(dict(declared, subclasses=subclasses), profile, self.policy, self.profiles)
        wrong = dict(declared, **{'classes': ['record'], 'subclasses': []})
        with self.assertRaisesRegex(ValueError, 'manifest change classification'):
            m.refine_selection(wrong, profile, self.policy, self.profiles)
        paths = ['governance/DECISIONS.md', 'governance/tools/support/timing_record.py']
        mixed = tool.attach_classification(paths, self.policy, self.profiles,
                                           tool.select_profile(paths, self.policy, self.profiles, 'auto')[1])
        self.assertEqual(mixed['change_classification'], 'mixed')
        m.refine_selection(dict(declared, classes=['governance', 'tooling'], subclasses=['checkpoint', 'decisions']),
                           mixed, self.policy, self.profiles)

    def test_base_lists_and_global_subclasses(self):
        value = self.parse('Class: governance, record\nSubclass: agent-guidance, progress\nWorker: Human')
        self.assertEqual(value['classes'], ['record', 'governance'])
        self.assertEqual(value['subclasses'], ['progress', 'agent-guidance'])
        tool = shipped_tool()
        paths = [rp.PROGRESS, rp.AGENTS]
        profile = tool.attach_classification(paths, self.policy, self.profiles,
                                             tool.select_profile(paths, self.policy, self.profiles, 'auto')[1])
        self.assertEqual(profile['change_classification'], 'mixed')
        selected = m.refine_selection(value, profile, self.policy, self.profiles)
        self.assertEqual(selected['commit_metadata']['classes'], ['record', 'governance'])
        self.assertEqual(selected['checks'], profile['checks'])
        for classes in (['record'], ['governance'], ['record', 'governance', 'tooling']):
            with self.subTest(classes=classes), self.assertRaisesRegex(ValueError, 'manifest change classification'):
                m.refine_selection(dict(value, classes=classes, subclasses=[]), profile, self.policy, self.profiles)
        record = tool.attach_classification([rp.PROGRESS], self.policy, self.profiles,
                                            tool.select_profile([rp.PROGRESS], self.policy, self.profiles, 'auto')[1])
        m.refine_selection(self.parse('Class: record\nSubclass: progress\nWorker: Human'), record, self.policy, self.profiles)
        with self.assertRaisesRegex(ValueError, 'invalid under selected Class'):
            self.parse('Class: tooling, runtime\nSubclass: progress, agent-guidance\nWorker: Human')

    def test_documentation_area_subclasses(self):
        registry = {e['value']: e for e in self.profiles['commit_metadata']['subclasses']}
        expected = {'decisions': ['governance'], 'plan': ['record'], 'constraints': ['governance'],
                    'rationale': ['record'], 'architecture': ['governance']}
        for name, classes in expected.items():
            with self.subTest(name=name):
                self.assertEqual(registry[name]['classes'], classes)
                self.assertNotIn('invariants', registry[name])
                self.assertFalse({'paths', 'prefixes'} & set(registry[name]))
        value = self.parse('Class: governance, record\nSubclass: architecture, plan, decisions\nWorker: Human')
        self.assertEqual(value['subclasses'], ['decisions', 'plan', 'architecture'])
        with self.assertRaisesRegex(ValueError, 'invalid under selected Class'):
            self.parse('Class: governance\nSubclass: plan\nWorker: Human')
        with self.assertRaisesRegex(ValueError, 'invalid under selected Class'):
            self.parse('Class: record\nSubclass: decisions\nWorker: Human')
        tool = shipped_tool()
        profile = tool.attach_classification(['docs/PLAN.md'], self.policy, self.profiles,
                                             tool.select_profile(['docs/PLAN.md'], self.policy, self.profiles, 'auto')[1])
        self.assertEqual(profile['change_classification'], 'record')
        selected = m.refine_selection(self.parse('Class: record\nSubclass: plan\nWorker: Human'), profile, self.policy, self.profiles)
        self.assertEqual(selected['checks'], profile['checks'])

    def test_class_lists_fail_closed(self):
        for value in ('mixed', 'record, mixed', 'record, record', 'record, unknown',
                      'record,governance', 'record,  governance', 'record , governance',
                      'record,\tgovernance', 'record,', 'Record'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse('Class: ' + value + '\nWorker: Human')
        with self.assertRaises(ValueError):
            m.parse_metadata(self.message('Class: mixed\nWorker: Human'), self.profiles,
                             legacy_aggregate=True)

    def test_historical_aggregate_reconstructs_base_classes_only(self):
        text = self.message('Class: mixed\nSubclass: checkpoint\nWorker: OpenAI Codex')
        table = shipped_tool().table(self.policy, self.profiles)
        value = m.reconstruct(text, [rp.AGENTS, 'governance/tools/checkpoint.py'], self.policy, self.profiles, table)
        self.assertEqual(value['classes'], ['governance', 'tooling'])
        self.assertEqual(value['subclasses'], ['checkpoint'])
        self.assertEqual(value['inferredFields'], ['classes'])
        self.assertEqual(value['worker'], 'OpenAI Codex')
        unknown = m.reconstruct(text, ['unknown.txt'], self.policy, self.profiles, table)
        self.assertEqual(unknown['classes'], [])
        self.assertEqual(unknown['subclasses'], [])
        self.assertEqual(unknown['inferredFields'], [])
        with self.assertRaises(ValueError):
            m.reconstruct(self.message('Class: mixed, record\nWorker: Human'),
                          [rp.PROGRESS], self.policy, self.profiles, table)

    def test_reconstruction_uses_historical_paths_for_deleted_files(self):
        historical = (chr(10) + '## Historical paths table' + chr(10) + chr(10) + '| Path | Class | Subclass |' + chr(10) +
                      '| --- | --- | --- |' + chr(10) + '| `docs/WORKTREE-WORKFLOW.md` | governance | - |' + chr(10))
        root = rp.ROOT
        table = artifact_classification.parse_table((root / c.TABLE).read_text(encoding='utf-8') + historical,
                                                    self.profiles)
        value = m.reconstruct('Old change\n\nWorker: Human', ['docs/WORKTREE-WORKFLOW.md'],
                              self.policy, self.profiles, table)
        self.assertEqual(value['classes'], ['governance'])
        self.assertEqual(value['inferredFields'], ['classes'])
        with self.assertRaisesRegex(ValueError, 'classifies from the table'):
            m.classify_paths([rp.AGENTS], self.policy, self.profiles)

    def test_new_prepare_requires_metadata_before_any_staging(self):
        fixture = test_checkpoint.CheckpointTest('test_active_hook_rejected')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        policy = copy.deepcopy(fixture.policy)
        policy.update(commit_metadata_required=True)
        profiles = copy.deepcopy(fixture.profiles)
        profiles['profiles']['test']['limitations'] = 'Disposable metadata gate fixture.'
        fixture.write_json(c.POLICY, policy)
        fixture.write_json(c.PROFILES, profiles)
        with self.assertRaisesRegex(ValueError, 'Class trailer required'):
            fixture.prepare()
        self.assertEqual(fixture.tool.inventory(), [])
        self.assertFalse((fixture.tool.store / c.TRANSACTION).exists())
        fixture.message.write_text(self.message('Class: tooling\nWorker: Human'), encoding='utf-8')
        prepared = fixture.tool.prepare(['a.txt'], fixture.message, 'test', fixture.parent,
                                        'refs/heads/main', 'Disposable metadata preparation',
                                        worker_context={'identity': 'Human', 'isAI': False, 'configuration': None})
        self.assertEqual(prepared['profile_data']['commit_metadata']['worker'], 'Human')

    def test_ai_known_configuration_is_required_and_matches_exactly(self):
        context = {'identity': 'OpenAI Codex', 'isAI': True, 'configuration': 'GPT-6.1 Sol Medium'}
        for worker in ('OpenAI Codex', 'OpenAI Codex [GPT-6.1 Sol High]', 'OpenAI Codex [invented-version]'):
            with self.subTest(worker=worker), self.assertRaisesRegex(ValueError, 'known'):
                m.validate_worker_context(self.parse('Class: tooling\nWorker: ' + worker), context)
        metadata = self.parse('Class: tooling\nWorker: OpenAI Codex [GPT-6.1 Sol Medium]')
        self.assertEqual(m.validate_worker_context(metadata, context), context)

    def test_unknown_ai_configuration_and_non_ai_allow_omission(self):
        context = {'identity': 'OpenAI Codex', 'isAI': True, 'configuration': None}
        metadata = self.parse('Class: tooling\nWorker: OpenAI Codex')
        self.assertEqual(m.validate_worker_context(metadata, context), context)
        human = {'identity': 'Human', 'isAI': False, 'configuration': None}
        self.assertEqual(m.validate_worker_context(self.parse('Class: tooling\nWorker: Human'), human), human)
        self.assertEqual(m.reconstruct('Historic\n\nWorker: OpenAI Codex', [], self.policy, self.profiles,
                                       shipped_tool().table(self.policy, self.profiles))['worker'],
                         'OpenAI Codex')

    def test_context_must_be_explicit_and_valid(self):
        metadata = self.parse('Class: tooling\nWorker: OpenAI Codex')
        for context in (None, {}, {'identity': 'OpenAI Codex', 'isAI': True, 'configuration': ''},
                        {'identity': 'OpenAI Codex', 'isAI': 'true', 'configuration': None},
                        {'identity': 'Different tool', 'isAI': True, 'configuration': None},
                        {'identity': 'OpenAI Codex', 'isAI': True, 'configuration': '[invented]'}):
            with self.subTest(context=context), self.assertRaises(ValueError):
                m.validate_worker_context(metadata, context)

    def test_prepare_rejects_omitted_known_ai_configuration_before_staging(self):
        fixture = test_checkpoint.CheckpointTest('test_active_hook_rejected')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        policy = copy.deepcopy(fixture.policy)
        policy.update(commit_metadata_required=True)
        profiles = copy.deepcopy(fixture.profiles)
        profiles['profiles']['test']['limitations'] = 'Disposable known configuration fixture.'
        fixture.write_json(c.POLICY, policy)
        fixture.write_json(c.PROFILES, profiles)
        context = {'identity': 'OpenAI Codex', 'isAI': True, 'configuration': 'GPT-6.1 Sol Medium'}
        fixture.message.write_text(self.message('Class: tooling\nWorker: OpenAI Codex'), encoding='utf-8')
        args = (['a.txt'], fixture.message, 'test', fixture.parent, 'refs/heads/main', 'Disposable model gate')
        with self.assertRaisesRegex(ValueError, 'known'):
            fixture.tool.prepare(*args, worker_context=context)
        self.assertEqual(fixture.tool.inventory(), [])
        self.assertFalse((fixture.tool.store / c.TRANSACTION).exists())
        fixture.message.write_text(self.message('Class: tooling\nWorker: OpenAI Codex [GPT-6.1 Sol Medium]'),
                                   encoding='utf-8')
        prepared = fixture.tool.prepare(*args, worker_context=context)
        self.assertEqual(prepared['profile_data']['worker_context'], context)
        self.assertEqual(prepared['profile_data']['commit_metadata']['worker'], 'OpenAI Codex [GPT-6.1 Sol Medium]')

    def test_registry_rejects_duplicate_and_competing_impacts(self):
        profiles = copy.deepcopy(self.profiles)
        profiles['commit_metadata']['subclasses'].append(profiles['commit_metadata']['subclasses'][0])
        with self.assertRaisesRegex(ValueError, 'Duplicate registry'):
            m.registry(profiles)
        profiles = copy.deepcopy(self.profiles)
        profiles['commit_metadata']['subclasses'][0]['invariants'] = ['independent-policy']
        with self.assertRaisesRegex(ValueError, 'existing impact'):
            m.registry(profiles)



class WorkerBootstrapTest(unittest.TestCase):
    branch = 'refs/heads/tx/test-worker'

    def test_runtime_wins_and_no_probe_when_complete(self):
        runtime = dict(provider='OpenAI', product='Codex', model='Exposed model',
                       configuration='Exposed setting', isAI=True)
        probe = Mock(side_effect=AssertionError('unnecessary probe'))
        value = m.WorkerBootstrap(self.branch).resolve(runtime, dict(model='Lower model'),
            {'WORKER_CONTEXT': '{"model":"Lower environment"}'}, probe)
        self.assertEqual(m.worker_projection(value), 'OpenAI Codex [Exposed model Exposed setting]')
        self.assertEqual(set(value['sources'].values()), {'runtime/session'})
        probe.assert_not_called()

    def test_launcher_environment_and_one_probe_fill_only_missing(self):
        probe = Mock(return_value={'model': 'Ignored model', 'configuration': 'Provider setting'})
        resolver = m.WorkerBootstrap(self.branch)
        value = resolver.resolve({'provider': 'Anthropic'}, {'product': 'Claude Code', 'isAI': True},
            {'WORKER_CONTEXT': '{"model":"Exact model"}'}, probe)
        self.assertEqual(m.worker_projection(value), 'Anthropic Claude Code [Exact model Provider setting]')
        self.assertEqual(value['sources']['product'], 'launcher/config')
        self.assertEqual(value['sources']['model'], 'environment')
        self.assertEqual(value['sources']['configuration'], 'provider-probe')
        probe.assert_called_once_with('Anthropic')
        value['model'] = 'external mutation'
        again = resolver.resolve({'model': 'new'}, probe=probe)
        self.assertEqual(again['model'], 'Exact model')
        probe.assert_called_once()

    def test_unavailable_probe_is_not_retried_and_unknowns_are_explicit(self):
        probe = Mock(return_value=None)
        resolver = m.WorkerBootstrap(self.branch)
        value = resolver.resolve({'provider': 'OpenAI', 'product': 'Codex', 'isAI': True},
                                 environment={}, probe=probe)
        self.assertIsNone(value['configuration'])
        self.assertIsNone(value['model'])
        self.assertEqual(value['sources']['configuration'], 'unknown')
        self.assertEqual(m.worker_projection(value), 'OpenAI Codex')
        resolver.resolve(probe=probe)
        probe.assert_called_once()
        unknown = m.WorkerBootstrap(self.branch).resolve(environment={})
        self.assertEqual(m.worker_projection(unknown), 'Unknown worker')
        self.assertIsNone(unknown['isAI'])
        no_provider = Mock()
        m.WorkerBootstrap(self.branch).resolve(environment={}, probe=no_provider)
        no_provider.assert_not_called()

    def test_claude_products_and_provider_configuration_remain_distinct(self):
        for product in ('Claude Desktop', 'Claude Code'):
            value = m.WorkerBootstrap(self.branch).resolve(dict(provider='Anthropic', product=product,
                model='Exposed model', configuration='Provider mode', isAI=True), environment={})
            self.assertEqual(m.worker_projection(value), 'Anthropic ' + product + ' [Exposed model Provider mode]')
            self.assertEqual(m.validate_worker_context({'worker': m.worker_projection(value)}, value), value)
        with self.assertRaisesRegex(ValueError, 'Conflicting worker'):
            m.WorkerBootstrap(self.branch).resolve({'provider': 'OpenAI'}, {'provider': 'Anthropic'})
        with self.assertRaisesRegex(ValueError, 'Conflicting worker'):
            m.WorkerBootstrap(self.branch).resolve({'product': 'Claude Desktop'}, {'product': 'Claude Code'})

    def test_bootstrap_file_reuses_without_reading_sources_or_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            output, runtime = Path(folder) / 'frozen.json', Path(folder) / 'session.json'
            runtime.write_text(json.dumps(dict(provider='OpenAI', product='Codex', isAI=True)))
            with patch.dict(m.os.environ, {}, clear=True):
                value = m.bootstrap_worker(output, self.branch, runtime)
            runtime.unlink()
            with patch.dict(m.os.environ, {'WORKER_CONTEXT': 'invalid changed data'}):
                self.assertEqual(m.bootstrap_worker(output, self.branch, runtime), value)
            complete = Path(folder) / 'complete.json'
            runtime.write_text(json.dumps(dict(provider='OpenAI', product='Codex', model='Known',
                                              configuration='Known mode', isAI=True)))
            with patch.dict(m.os.environ, {'WORKER_CONTEXT': 'must not read'}):
                m.bootstrap_worker(complete, self.branch, runtime, Path(folder) / 'absent-launcher.json')
            with self.assertRaisesRegex(ValueError, 'another transaction'):
                m.bootstrap_worker(output, 'refs/heads/tx/other')
            broken = dict(value, model='fabricated')
            with self.assertRaisesRegex(ValueError, 'provenance'):
                m.validate_worker_context({'worker': 'OpenAI Codex [fabricated]'}, broken)
            with self.assertRaisesRegex(ValueError, 'projection'):
                m.validate_worker_context({'worker': 'OpenAI Codex [fabricated]'}, value)

    def test_prepare_seals_frozen_context_and_later_stages_never_resolve(self):
        fixture = test_checkpoint.CheckpointTest('test_active_hook_rejected')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        policy = copy.deepcopy(fixture.policy)
        policy.update(commit_metadata_required=True)
        profiles = copy.deepcopy(fixture.profiles)
        profiles['profiles']['test']['limitations'] = 'Disposable frozen worker fixture.'
        fixture.write_json(c.POLICY, policy)
        fixture.write_json(c.PROFILES, profiles)
        context = m.WorkerBootstrap('refs/heads/main').resolve(
            {'provider': 'OpenAI', 'product': 'Codex', 'isAI': True}, environment={})
        fixture.message.write_text('Bootstrap once\n\nClass: tooling\nWorker: OpenAI Codex\n')
        args = (['a.txt'], fixture.message, 'test', fixture.parent, 'refs/heads/main', 'Bootstrap fixture')
        with self.assertRaisesRegex(ValueError, 'transaction mismatch'):
            fixture.tool.prepare(*args, worker_context=dict(context, transaction=self.branch))
        self.assertEqual(fixture.tool.inventory(), [])
        with patch.object(m.WorkerBootstrap, 'resolve', side_effect=AssertionError('rediscovery')):
            packet = fixture.tool.prepare(*args, worker_context=context, validate_only=True)
            self.assertEqual(packet['profile_data']['worker_context'], context)
            fixture.tool.seal()
            self.assertEqual(fixture.tool.inspect()['profile_data']['worker_context'], context)
            review = fixture.tool.present()
            fixture.tool.approve_consent(fixture.consent(review))
            result = fixture.tool.commit()
            self.assertEqual(result['profile_data']['worker_context'], context)
            self.assertEqual(fixture.tool.verify()['profile_data']['worker_context'], context)

if __name__ == '__main__':
    unittest.main()
