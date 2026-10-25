"""Deterministic contract acceptance using independent disposable Git inputs."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import generate as g
import data_launcher as launcher
import declared_inputs as declared
import repo_paths as rp
from governance.tools.continuity import artifact_classification as ac

MC_DATA = 'governance/extensions/mission_control/mission-control-data.sh'


def project_letter(text):
    """The framework's G-prefixed text with the project's D prefix: the digits stay, so no D id is spelled out in this file."""
    return text.replace('G', 'D')


def working_proposal(root):
    """Every path whose working bytes differ from HEAD, so the generator reads the candidate as it will land:
    a changed or new file by its working bytes, a deleted one as a deletion, and every other file from HEAD."""
    def listed(*args):
        return [p.decode('utf-8') for p in subprocess.check_output(['git', '-C', str(root), *args]).split(b'\0') if p]
    changed = listed('diff', '--name-status', '--no-renames', '-z', 'HEAD')
    proposal = {name: None if status == 'D' else g.digest((root / name).read_bytes())
                for status, name in zip(changed[::2], changed[1::2])}
    proposal.update({name: g.digest((root / name).read_bytes())
                     for name in listed('ls-files', '--others', '--exclude-standard', '-z')})
    return proposal


class GeneratorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.output = Path(self.temp.name) / 'normal'
        self.inventory = {'version': 1, 'commitHistory': 'reachable-from-declared-revision',
                          'linkPolicy': 'explicit-markdown-and-checkpoint', 'datasets': {
            'decisions': {'paths': [rp.DECISIONS], 'markdownDirectories': []},
            'rationale': {'paths': [rp.RATIONALE], 'markdownDirectories': []},
            'documents': {'paths': ['docs/guide.md', 'docs/target.md'], 'markdownDirectories': []},
            'devlog': {'paths': [], 'markdownDirectories': ['devlog']}}}
        self.write(rp.DECISIONS, '# Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n| G001 | 2026-10-03 | Accepted | [Guide](../docs/guide.md#guide) |\n\n## G001 - First\n\nComplete detailed decision.\n')
        self.write('docs/guide.md', '# Guide\n\nLiteral Unicode: café — 🖖.\n\n[Target](target.md#target)\n')
        self.write('docs/target.md', '# Target\n\nComplete target content.\n')
        self.write(rp.RATIONALE, '# Rationale\n\nStatus: Explanatory / non-normative\n\n[Guide](../docs/guide.md#guide)\n')
        self.write('devlog/2026-10-02T0843Z-event.md', '# Operational log\n\nStatus: Historical / non-normative\nDate: 2026-10-02T08:43Z\n\nContemporary uncertainty preserved.\n')
        self.write('devlog/2026-10-03T0001Z-thought.md', '# Second entry\n\nStatus: Historical / non-normative\nDate: 2026-10-03T00:01Z\n\nAn entry in the other date format.\n')
        repository = rp.ROOT
        for name in g.COMMIT_INPUTS:
            if (repository / name).is_file():  # the project's files are optional
                self.write(name, (repository / name).read_text(encoding='utf-8'))
        self.run_git('init', '-b', 'fixture')
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()

    def write(self, name, content):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content, encoding='utf-8', newline='\n')

    def run_git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode('utf-8')

    def commit(self):
        self.run_git('add', '--all')
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                     'commit', '-m', 'Declared fixture', '--allow-empty')

    def generate(self, mode='full', output=None, changed=(), proposal=None, revision=None):
        return g.generate(self.root, revision or self.revision, self.inventory,
                          output or self.output, mode, changed, proposal)

    def bytes(self, output=None):
        return {kind: ((output or self.output) / (kind + '.json')).read_bytes() for kind in g.TYPES}

    def items(self, kind):
        return json.loads(self.bytes()[kind])['items']

    def metadata_commit(self, path, message):
        existing = (self.root / path).read_text(encoding='utf-8') if (self.root / path).exists() else ''
        self.write(path, existing + '\nChanged fixture\n')
        self.run_git('add', '--', path)
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                     'commit', '-m', message)
        self.revision = self.run_git('rev-parse', 'HEAD').strip()
        self.generate()
        return next(item for item in self.items('commits') if item['sha'] == self.revision)

    def test_the_working_candidate_lets_a_decision_link_to_a_heading_it_adds(self):
        decisions = (self.root / rp.DECISIONS).read_text(encoding='utf-8')
        self.write('docs/target.md', '# Target\n\nComplete target content.\n\n## Added\n\nA section of this candidate.\n')
        self.write(rp.DECISIONS, decisions.replace('Complete detailed decision.',
                                                   'Complete detailed decision, see [Added](../docs/target.md#added).'))
        pair = g.Generator(g.Selection(self.root, self.revision, working_proposal(self.root)),
                           self.inventory).decision_rationale()
        self.assertTrue(pair['decisions'])
        self.write(rp.DECISIONS, decisions.replace('Complete detailed decision.',
                                                   'Complete detailed decision, see [Nowhere](../docs/target.md#nowhere).'))
        with self.assertRaisesRegex(ValueError, 'Broken fragment: docs/target.md#nowhere'):
            g.Generator(g.Selection(self.root, self.revision, working_proposal(self.root)),
                        self.inventory).decision_rationale()

    def test_conventions_are_documents_not_historical_logs(self):
        path = 'devlog/CONVENTIONS.md'
        published = declared.load(rp.ROOT)
        self.assertIn(path, published['datasets']['documents']['paths'])
        self.assertIn(path, published['datasets']['devlog']['excludePaths'])
        self.write(path, '# devlog conventions\n\nAuthoring rules, not an event.\n')
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()
        self.inventory['datasets']['documents']['paths'].append(path)
        self.inventory['datasets']['devlog']['excludePaths'] = [path]
        self.generate()
        self.assertIn(path, [item['source']['path'] for item in self.items('documents')])
        self.assertNotIn(path, [item['source']['path'] for item in self.items('devlog')])
        self.assertEqual(len(self.items('devlog')), 2)

    def test_a_disabled_devlog_gives_an_empty_dataset_that_says_so(self):
        self.inventory['datasets']['devlog'] = {'paths': [], 'markdownDirectories': [], 'excludePaths': [], 'enabled': False}
        self.generate()
        dataset = json.loads(self.bytes()['devlog'])
        self.assertEqual((dataset['items'], dataset['enabled']), ([], False))
        self.assertNotIn('enabled', json.loads(self.bytes()['decisions']))
        self.inventory['datasets']['devlog']['markdownDirectories'] = ['devlog']
        with self.assertRaisesRegex(ValueError, 'disabled dataset'):
            self.generate()
        self.inventory['datasets']['decisions']['enabled'] = False
        self.inventory['datasets']['devlog'] = {'paths': [], 'markdownDirectories': ['devlog']}
        with self.assertRaisesRegex(ValueError, 'Unknown input selector'):
            self.generate()

    def test_explicit_commit_metadata_arrays_and_canonical_order(self):
        item = self.metadata_commit(rp.CHECKPOINT,
                                   'Keep title\n\n- Detail\n\nClass: tooling\n'
                                   'Subclass: mission-control, checkpoint\nWorker: OpenAI Codex [Visible config]')
        self.assertEqual(item['classes'], ['tooling'])
        self.assertEqual(item['subclasses'], ['checkpoint', 'mission-control'])
        self.assertEqual(item['worker'], 'OpenAI Codex [Visible config]')
        self.assertEqual(item['inferredFields'], [])
        self.assertEqual(item['topics'], [])
        item = self.metadata_commit(rp.CHECKPOINT,
                                   'One subclass\n\nClass: tooling\nSubclass: checkpoint\nWorker: Human')
        self.assertEqual(item['subclasses'], ['checkpoint'])
        self.assertEqual(item['inferredFields'], [])
        item = self.metadata_commit(rp.CHECKPOINT, 'No subclass\n\nClass: tooling\nWorker: Human')
        self.assertEqual(item['subclasses'], [])
        self.assertEqual(item['inferredFields'], [])

    def test_multiclass_metadata_json_and_retired_aggregate(self):
        item = self.metadata_commit(rp.CHECKPOINT,
                                   'Plural\n\nClass: tooling, governance\nSubclass: checkpoint\nWorker: Human')
        self.assertEqual(item['classes'], ['governance', 'tooling'])
        self.assertNotIn('class', item)
        self.assertEqual(item['inferredFields'], [])
        item = self.metadata_commit('AGENTS.md', 'Historical\n\nClass: mixed\nWorker: OpenAI Codex')
        self.assertEqual(item['classes'], ['governance'])
        self.assertEqual(item['inferredFields'], ['classes', 'subclasses'])
        self.assertEqual(item['worker'], 'OpenAI Codex')
        self.assertIn('Class: mixed', item['contentText'])
        self.assertEqual(item['topics'], [])

    def test_historical_multiple_path_classes_are_deterministic(self):
        self.write('docs/PROGRESS.md', 'Recorded progress\n')
        self.write('AGENTS.md', 'Agent governance\n')
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()
        self.generate()
        item = next(item for item in self.items('commits') if item['sha'] == self.revision)
        self.assertEqual(item['classes'], ['record', 'governance'])
        self.assertEqual(item['subclasses'], ['progress', 'agent-guidance'])
        self.assertEqual(item['inferredFields'], ['classes', 'subclasses'])
        self.assertNotIn('class', item)
        first = self.bytes()['commits']
        self.generate()
        self.assertEqual(first, self.bytes()['commits'])

    def test_commit_classes_come_from_the_project_table_too(self):
        # The reconstruction reads the framework's table and the project's, as the revision holds them.
        from governance.tools.continuity import artifact_classification as ac
        rows = [ac.HEADER, ac.SEPARATOR, '| `notes/` | record | record-only | - | - | - | plan | - | - |']
        self.write(ac.PROJECT_TABLE, '\n'.join(rows) + '\n')
        self.commit()
        item = self.metadata_commit('notes/a.md', 'A project note without metadata')
        self.assertEqual((item['classes'], item['subclasses']), (['record'], ['plan']))
        self.assertEqual(item['inferredFields'], ['classes', 'subclasses'])
        (self.root / ac.PROJECT_TABLE).unlink()
        self.commit()
        item = self.metadata_commit('notes/b.md', 'The same without the project table')
        self.assertEqual((item['classes'], item['subclasses']), ([], []))

    def test_historical_path_inference_and_unknown_worker(self):
        item = self.metadata_commit('devlog/2026-10-03T0001Z-thought.md',
                                   'Historical worker not recorded')
        self.assertEqual(item['classes'], ['record'])
        self.assertEqual(item['subclasses'], ['devlog'])
        self.assertIsNone(item['worker'])
        self.assertEqual(item['inferredFields'], ['classes', 'subclasses'])
        item = self.metadata_commit('unknown.txt', 'Unsupported historical evidence')
        self.assertEqual(item['classes'], [])
        self.assertEqual(item['subclasses'], [])
        self.assertIsNone(item['worker'])
        self.assertEqual(item['inferredFields'], [])

    def test_partial_historical_worker_is_preserved_without_model_invention(self):
        item = self.metadata_commit(rp.CHECKPOINT, 'Partial metadata\n\nWorker: OpenAI ChatGPT')
        self.assertEqual(item['worker'], 'OpenAI ChatGPT')
        self.assertEqual(item['classes'], ['tooling'])
        self.assertEqual(item['subclasses'], ['checkpoint'])
        self.assertEqual(item['inferredFields'], ['classes', 'subclasses'])

    def test_metadata_policy_dependency_rebuilds_commits_in_normal_mode(self):
        self.generate()
        name = g.artifact_classification.PROFILES
        profiles = json.loads((self.root / name).read_text())
        profiles['commit_metadata']['subclasses'].append({'value': 'unused-label', 'classes': ['record']})
        proposal = self.proposal(name, json.dumps(profiles))
        result = self.generate('normal', changed=[name], proposal=proposal)
        self.assertEqual(result['updated'], ['commits'])

    def test_malformed_explicit_metadata_is_not_inferred(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate subclass'):
            self.metadata_commit(rp.CHECKPOINT,
                                 'Malformed\n\nClass: tooling\nSubclass: checkpoint, checkpoint\nWorker: Human')

    def proposal(self, name, text):
        self.write(name, text)
        return {name: g.digest((self.root / name).read_bytes())}

    def test_independent_full_equal_inputs_byte_identical_across_checkouts(self):
        self.generate()
        original = self.bytes()
        clone = Path(self.temp.name) / 'another-checkout'
        subprocess.run(['git', 'clone', '--no-hardlinks', str(self.root), str(clone)],
                       check=True, capture_output=True)
        other = Path(self.temp.name) / 'independent'
        with patch.dict(os.environ, {'TZ': 'Asia/Tokyo', 'LANG': 'xx_XX', 'HOSTNAME': 'other'}):
            g.generate(clone, self.revision, self.inventory, other)
        self.assertEqual(original, self.bytes(other))

    def test_normal_full_equivalence_and_unaffected_bytes_and_mtimes(self):
        self.generate()
        before = self.bytes()
        times = {kind: (self.output / (kind + '.json')).stat().st_mtime_ns for kind in g.TYPES}
        name = 'devlog/2026-10-02T0843Z-event.md'
        proposal = self.proposal(name, '# Changed log\n\nStatus: Historical / non-normative\n\nComplete changed historical content.\n')
        result = self.generate('normal', changed=[name], proposal=proposal)
        self.assertEqual(result['updated'], ['devlog'])
        after = self.bytes()
        for kind in set(g.TYPES) - {'devlog'}:
            self.assertEqual(before[kind], after[kind])
            self.assertEqual(times[kind], (self.output / (kind + '.json')).stat().st_mtime_ns)
        full = Path(self.temp.name) / 'full'
        self.generate(output=full, proposal=proposal)
        self.assertEqual(after, self.bytes(full))
        self.generate('verify', proposal=proposal)

    def test_changed_link_target_updates_declared_dependents_only(self):
        self.generate()
        proposal = self.proposal('docs/guide.md', '# Guide\n\nNew authoritative content.\n[Target](target.md#target)\n')
        result = self.generate('normal', changed=['docs/guide.md'], proposal=proposal)
        self.assertEqual(result['updated'], ['decisions', 'documents', 'rationale'])
        full = Path(self.temp.name) / 'full'
        self.generate(output=full, proposal=proposal)
        self.assertEqual(self.bytes(), self.bytes(full))

    def test_addition_and_deletion_in_declared_directory(self):
        self.generate()
        name = 'devlog/new.md'
        proposal = self.proposal(name, '# Added\n\nStatus: Historical / non-normative\n')
        self.assertEqual(self.generate('normal', changed=[name], proposal=proposal)['updated'], ['devlog'])
        self.assertEqual(len(self.items('devlog')), 3)
        # Omission from the next proposal removes the explicitly proposed addition.
        self.assertEqual(self.generate('normal', changed=[name])['updated'], ['devlog'])
        self.assertEqual(len(self.items('devlog')), 2)
        deleted = 'devlog/2026-10-03T0001Z-thought.md'
        self.generate('normal', changed=[deleted], proposal={deleted: None})
        self.assertEqual(len(self.items('devlog')), 1)

    def test_deleting_link_target_fails_before_any_writes(self):
        self.generate()
        before = self.bytes()
        with self.assertRaises(ValueError):
            self.generate('normal', changed=['docs/target.md'], proposal={'docs/target.md': None})
        self.assertEqual(before, self.bytes())

    def test_broken_explicit_path_fragment_and_reference_fail(self):
        for href in ('missing.md', 'target.md#missing', 'Target.md#target', '../../outside.md'):
            with self.subTest(href=href), self.assertRaises(ValueError):
                self.generate(proposal=self.proposal('docs/guide.md', '# Guide\n\n[Broken](' + href + ')\n'))
        with self.assertRaisesRegex(ValueError, 'Undefined'):
            self.generate(proposal=self.proposal('docs/guide.md', '# Guide\n\n[Broken][unknown]\n'))

    def test_changed_link_removes_old_dependency_and_adds_new(self):
        self.generate()
        name = rp.RATIONALE
        proposal = self.proposal(name, '# Rationale\n\n[Target](../docs/target.md#target)\n')
        result = self.generate('normal', changed=[name], proposal=proposal)
        self.assertEqual(result['updated'], ['decisions', 'rationale'])
        state = json.loads((self.output / 'state.json').read_bytes())
        self.assertEqual(state['datasets']['rationale']['dependencies'],
                         ['docs/target.md', rp.DECISIONS, name])

    def rationale_fixture(self):
        registry = '# Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
        for label in ('G001', 'G002', 'G003', 'G016', 'G017', 'G016/G017 extension'):
            registry += f'| {label} | 2026-10-03 | Accepted | Recorded {label}. |\n'
        self.write(rp.DECISIONS, registry)
        self.write(rp.RATIONALE,
            '# Rationale\n\nStatus: Explanatory / non-normative\n\nHistorical context, not approval.\n\n'
            '## Evidence audit\n\nMissing means not documented.\n\n'
            '| Decision | Rationale status | Finding / reading location |\n| --- | --- | --- |\n'
            '| G001 | explicit | See First. |\n| G002 | recoverable | See Shared. |\n'
            '| G003 | missing | No reason recorded. |\n\n'
            '## First: G001\n\nComplete reason. [Target](../docs/target.md#target)\n\n'
            '## Shared: G016/G017 extension\n\nOne joint explanation, never split.\n\n'
            '## Shared range: G001-G002\n\nOne shared section. G003 is incidental evidence.\n\n'
            '## Unnumbered context\n\nNo decision ID is assigned here.\n')
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()

    def project_homes(self):
        """The framework's rationale fixture, with the letter swapped, as the project's own registry and rationale. The
        expected values are the framework's too, so that no D-prefixed id is written out in this file."""
        self.rationale_fixture()
        for name, home in ((rp.DECISIONS, ac.PROJECT_DECISIONS), (rp.RATIONALE, ac.PROJECT_RATIONALE)):
            self.write(home, project_letter((self.root / name).read_text(encoding='utf-8')))
        self.write(rp.DECISIONS, '# Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
                                 '| G001 | 2026-10-03 | Accepted | The framework decision. |\n')
        self.write(rp.RATIONALE, '# Rationale\n\nStatus: Explanatory / non-normative\n')
        self.inventory['datasets']['decisions']['paths'].append(ac.PROJECT_DECISIONS)
        self.inventory['datasets']['rationale']['paths'].append(ac.PROJECT_RATIONALE)
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()

    def test_a_project_registry_keeps_the_d_prefix_beside_the_framework_g_prefix(self):
        # The framework numbers its decisions with G in its own registry and a project numbers its own with D in the
        # project's, same three digits, and Mission Control lists both.
        self.project_homes()
        self.generate()
        self.assertEqual(sorted(item['id'] for item in self.items('decisions')),
                         sorted(['decision:G001'] + [project_letter(label) for label in (
                             'decision:G001', 'decision:G002', 'decision:G003', 'decision:G016',
                             'decision:G016/G017 extension', 'decision:G017')]))
        joint = next(item for item in self.items('rationale') if item['title'].startswith('Shared:'))
        self.assertEqual(joint['decisionIds'], [project_letter(label) for label in ('G016', 'G016/G017 extension', 'G017')])
        shared = next(item for item in self.items('rationale') if item['title'].startswith('Shared range:'))
        self.assertEqual(shared['decisionIds'], [project_letter('G001'), project_letter('G002')])
        self.assertEqual({item['source']['path'] for item in self.items('decisions')}, {rp.DECISIONS, ac.PROJECT_DECISIONS})

    def test_a_registry_with_the_other_prefix_is_refused_with_one_message(self):
        self.project_homes()
        project = (self.root / ac.PROJECT_DECISIONS).read_text(encoding='utf-8')
        self.write(ac.PROJECT_DECISIONS, project + '| G050 | 2026-10-03 | Accepted | A framework id in the project registry. |\n')
        self.commit()
        with self.assertRaisesRegex(ValueError, r'project-governance/DECISIONS.md takes only decisions with the prefix D.*G050'):
            self.generate(revision=self.run_git('rev-parse', 'HEAD').strip())
        self.write(ac.PROJECT_DECISIONS, project)
        self.write(rp.DECISIONS, (self.root / rp.DECISIONS).read_text(encoding='utf-8') +
                   project_letter('| G002 | 2026-10-03 | Accepted | A project id in the framework registry. |\n'))
        self.commit()
        with self.assertRaisesRegex(ValueError, r'governance/DECISIONS.md takes only decisions with the prefix G.*'
                                    + project_letter('G002')):
            self.generate(revision=self.run_git('rev-parse', 'HEAD').strip())

    def empty_project_homes(self):
        """The shape the starter ships for the project's registry and rationale, written here as fixture text: the test
        never reads the project's real files, which a project fills with its own decisions."""
        self.write(ac.PROJECT_DECISIONS, '# Project Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n')
        self.write(ac.PROJECT_RATIONALE, '# Why the project\'s decisions exist\n\nStatus: Explanatory / non-normative\n\n'
                                         '## Evidence audit\n\n| Decision | Rationale status | Finding / reading location |\n'
                                         '| --- | --- | --- |\n')

    def check_a_missing_project_home_means_none_and_an_empty_registry_is_valid(self):
        # The inventory names the project's files; a revision without them reads the framework's alone.
        self.inventory['datasets']['decisions']['paths'].append(ac.PROJECT_DECISIONS)
        self.inventory['datasets']['rationale']['paths'].append(ac.PROJECT_RATIONALE)
        self.generate()
        self.assertEqual([item['id'] for item in self.items('decisions')], ['decision:G001'])
        self.empty_project_homes()
        self.commit()
        self.generate(revision=self.run_git('rev-parse', 'HEAD').strip())
        self.assertEqual([item['id'] for item in self.items('decisions')], ['decision:G001'])

    def test_a_missing_project_home_means_none_and_an_empty_registry_is_valid(self):
        self.check_a_missing_project_home_means_none_and_an_empty_registry_is_valid()

    def test_the_empty_registry_check_does_not_read_the_projects_real_registry(self):
        # A project that records decisions must still pass the check above: the repository root the test could read holds
        # a decision of its own, and the test must neither read nor assert on it.
        with tempfile.TemporaryDirectory() as temp:
            project_root = Path(temp)
            real = project_letter('| G001 | 2026-10-03 | Accepted | The project\'s own decision. |\n')
            for name, text in ((ac.PROJECT_DECISIONS, '# Project Decisions\n\n| ID | Date | Status | Decision |\n'
                                                      '| --- | --- | --- | --- |\n' + real + '\n## ' + project_letter('G001') + ' - Own\n\nBody.\n'),
                               (ac.PROJECT_RATIONALE, '# Rationale\n\nStatus: Explanatory / non-normative\n')):
                (project_root / name).parent.mkdir(parents=True, exist_ok=True)
                (project_root / name).write_text(text, encoding='utf-8', newline='\n')
            with patch.object(rp, 'ROOT', project_root):
                self.check_a_missing_project_home_means_none_and_an_empty_registry_is_valid()

    def test_rationale_records_preserve_joint_sections_and_exact_bidirectional_links(self):
        self.rationale_fixture()
        self.generate()
        rationales = self.items('rationale')
        self.assertEqual(len(rationales), 5)
        joint = next(item for item in rationales if item['title'].startswith('Shared:'))
        self.assertEqual(joint['decisionIds'], ['G016', 'G016/G017 extension', 'G017'])
        self.assertNotIn('decisionId', joint)
        self.assertIn('One joint explanation, never split.', joint['contentMarkdown'])
        shared = next(item for item in rationales if item['title'].startswith('Shared range:'))
        self.assertEqual(shared['decisionIds'], ['G001', 'G002'])
        self.assertNotIn('G003', shared['rationaleStatuses'])
        missing = next(item for item in rationales if item.get('decisionId') == 'G003')
        self.assertEqual(missing['rationaleStatuses'], {'G003': 'missing'})
        self.assertIn('Historical context, not approval.', missing['contentMarkdown'])
        self.assertIn('No reason recorded.', missing['contentMarkdown'])
        self.assertGreater(missing['source']['lineStart'], 1)
        decisions = {item['id']: item for item in self.items('decisions')}
        for item in rationales:
            edges = [link for link in item['links'] if link['relation'] == 'explains']
            self.assertEqual(len(edges), len(item['decisionIds']))
            for edge in edges:
                self.assertEqual(edge['evidence'], item['source'])
                inverse = [link for link in decisions[edge['target']['recordId']]['links']
                           if link['relation'] == 'rationale' and link['target']['recordId'] == item['id']]
                self.assertEqual(len(inverse), 1)
                self.assertEqual(inverse[0]['evidence'], item['source'])
        self.generate('verify')

    def test_rationale_association_changes_update_both_families_and_preserve_others(self):
        self.rationale_fixture()
        self.generate()
        before = self.bytes()
        times = {kind: (self.output / (kind + '.json')).stat().st_mtime_ns for kind in g.TYPES}
        name = rp.RATIONALE
        text = (self.root / name).read_text().replace('## First: G001', '## First: G002')
        proposal = self.proposal(name, text)
        self.assertEqual(self.generate('normal', changed=[name], proposal=proposal)['updated'],
                         ['decisions', 'rationale'])
        for kind in set(g.TYPES) - {'decisions', 'rationale'}:
            self.assertEqual(before[kind], self.bytes()[kind])
            self.assertEqual(times[kind], (self.output / (kind + '.json')).stat().st_mtime_ns)
        full = Path(self.temp.name) / 'rationale-full'
        self.generate(output=full, proposal=proposal)
        self.assertEqual(self.bytes(), self.bytes(full))

    def test_unknown_rationale_id_fails_before_writes_and_code_is_not_coverage(self):
        self.rationale_fixture()
        self.generate()
        before = self.bytes()
        name = rp.RATIONALE
        text = (self.root / name).read_text()
        with self.assertRaisesRegex(ValueError, 'Unknown rationale decision'):
            self.generate(proposal=self.proposal(name, text + '\n## Bad: G999\n\nUnknown.\n'))
        self.assertEqual(before, self.bytes())
        self.generate(proposal=self.proposal(name, text + '\n```md\n## Example: G999\n```\n'))

    def extension_fixture(self):
        header = '# Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
        registry = (header + '| G001 | 2026-10-03 | Accepted | Recorded G001. |\n'
                    '| G002 | 2026-10-03 | Accepted | Recorded G002. See [the entry](#g002-renamed-entry-2026-10-08). |\n'
                    '| G003 | 2026-10-03 | Accepted | Recorded G003. |\n')
        self.extension_sections = (
            '\n## G001 - First\n\nDetail.\n\n'
            '### G001 own extension (2026-10-08)\n\nThe extension only exists as a heading. It has a second sentence.\n\n'
            '### G001 dated status (2026-10-09, Superseded by G003)\n\nThe heading states its own status.\n\n'
            '## G002 - Second\n\n### G002 renamed entry (2026-10-08)\n\nThe registry row stands for this heading.\n\n'
            '## G003 - Third\n\n### G003 (2026-10-08)\n\nSame label as its registry row.\n')
        self.extension_registry = registry
        self.write(rp.DECISIONS, registry + self.extension_sections)
        self.write(rp.RATIONALE,
            '# Rationale\n\nStatus: Explanatory / non-normative\n\n'
            '## Evidence audit\n\nDefinitions.\n\n'
            '| Decision | Rationale status | Finding / reading location |\n| --- | --- | --- |\n'
            '| G001 | explicit | The registry row. |\n| G001 own extension | explicit | Only a heading. |\n'
            '| G001 dated status | recoverable | A stated status. |\n'
            '| G002 renamed entry | explicit | Spelled as the heading. |\n'
            '| G003 | missing | Registry label equals the heading. |\n\n'
            '## First: G001\n\nReason one.\n\n## Renamed: G002 renamed entry\n\nExplained under the heading wording.\n')
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()

    def extension_proposal(self, decisions=None, rationale=None):
        proposal = {}
        if decisions is not None:
            proposal.update(self.proposal(rp.DECISIONS, decisions))
        if rationale is not None:
            proposal.update(self.proposal(rp.RATIONALE, rationale))
        return proposal

    def test_extension_headings_are_decision_identities_and_registry_rows_win(self):
        self.extension_fixture()
        self.generate()
        decisions = {item['id']: item for item in self.items('decisions')}
        self.assertEqual(sorted(decisions), ['decision:G001', 'decision:G001 dated status',
                                             'decision:G001 own extension', 'decision:G002', 'decision:G003'])
        own = decisions['decision:G001 own extension']
        text = (self.root / rp.DECISIONS).read_text().split('\n')
        self.assertEqual((own['title'], own['decisionId'], own['date'], own['status']),
                         ('G001 own extension', 'G001 own extension', '2026-10-08', 'Accepted'))
        self.assertEqual(own['summary'], 'The extension only exists as a heading.')
        self.assertEqual(own['source']['fragment'], 'g001-own-extension-2026-10-08')
        self.assertEqual(text[own['source']['lineStart'] - 1], '### G001 own extension (2026-10-08)')
        self.assertTrue(own['contentMarkdown'].startswith('### G001 own extension (2026-10-08)'))
        self.assertNotIn('Detail.', own['contentMarkdown'])
        stated = decisions['decision:G001 dated status']
        self.assertEqual((stated['date'], stated['status']), ('2026-10-09', 'Superseded by G003'))
        # The registry row keeps its record, summary and identity, and still embeds the extension text.
        self.assertEqual(decisions['decision:G002']['summary'], 'Recorded G002. See [the entry](#g002-renamed-entry-2026-10-08).')
        self.assertIn('The registry row stands for this heading.', decisions['decision:G002']['contentMarkdown'])
        self.assertIn('The extension only exists as a heading.', decisions['decision:G001']['contentMarkdown'])
        self.assertEqual(decisions['decision:G003']['summary'], 'Recorded G003.')

    def test_extension_identities_associate_with_rationale_in_both_directions(self):
        self.extension_fixture()
        self.generate()
        decisions = {item['id']: item for item in self.items('decisions')}
        rationales = {item['id']: item for item in self.items('rationale')}
        # Audit rows spelled with a heading wording resolve to the registry identity that stands for it.
        self.assertEqual(sorted(rationales), ['rationale:G001', 'rationale:G001 dated status',
                                              'rationale:G001 own extension', 'rationale:G002', 'rationale:G003'])
        self.assertEqual(rationales['rationale:G002']['decisionIds'], ['G002'])
        self.assertEqual(rationales['rationale:G002']['rationaleStatuses'], {'G002': 'explicit'})
        self.assertEqual(rationales['rationale:G001 own extension']['decisionIds'], ['G001 own extension'])
        self.assertEqual(rationales['rationale:G001 own extension']['rationaleStatuses'], {'G001 own extension': 'explicit'})
        for identity, item in rationales.items():
            edges = [link for link in item['links'] if link['relation'] == 'explains']
            self.assertEqual([edge['target']['recordId'] for edge in edges],
                             ['decision:' + label for label in item['decisionIds']])
            for edge in edges:
                inverse = [link for link in decisions[edge['target']['recordId']]['links']
                           if link['relation'] == 'rationale' and link['target']['recordId'] == identity]
                self.assertEqual(len(inverse), 1)
        self.assertEqual(sum(1 for item in decisions.values()
                             if not any(link['relation'] == 'rationale' for link in item['links'])), 0)
        self.generate('verify')

    def test_a_heading_wording_in_a_rationale_annotation_resolves_like_the_audit_row(self):
        self.extension_fixture()
        text = (self.root / rp.RATIONALE).read_text()
        self.generate(proposal=self.extension_proposal(rationale=text.replace(
            '## Renamed: G002 renamed entry', '## Renamed: G001 own extension, G002 renamed entry')))
        shared = next(item for item in self.items('rationale') if item['title'].startswith('Renamed:'))
        self.assertEqual(shared['decisionIds'], ['G001 own extension', 'G002'])

    def test_unknown_duplicate_and_ambiguous_extension_labels_fail_clearly(self):
        self.extension_fixture()
        decisions = (self.root / rp.DECISIONS).read_text()
        rationale = (self.root / rp.RATIONALE).read_text()
        row = '| G001 | explicit | The registry row. |\n'
        with self.assertRaisesRegex(ValueError, 'Unknown rationale audit decision: G001 not a heading '
                                                r'\(neither a registry row nor a dated extension heading'):
            self.generate(proposal=self.extension_proposal(rationale=rationale.replace(
                row, row + '| G001 not a heading | explicit | Neither. |\n')))
        with self.assertRaisesRegex(ValueError, 'Duplicate rationale audit decision: G002 renamed entry'):
            self.generate(proposal=self.extension_proposal(rationale=rationale.replace(
                row, row + '| G002 | explicit | Registry spelling of the same thing. |\n')))
        with self.assertRaisesRegex(ValueError, 'linked by several registry rows: G002 renamed entry'):
            self.generate(proposal=self.extension_proposal(decisions=decisions.replace(
                '| G003 | 2026-10-03 | Accepted | Recorded G003. |',
                '| G003 | 2026-10-03 | Accepted | Recorded G003. [Also](#g002-renamed-entry-2026-10-08). |')))
        with self.assertRaisesRegex(ValueError, 'Duplicate extension heading: G001 own extension'):
            self.generate(proposal=self.extension_proposal(decisions=decisions + (
                '\n### G001 own extension (2026-10-10)\n\nA second heading with the same label.\n')))
        with self.assertRaisesRegex(ValueError, 'Extension heading without text: G001 own extension'):
            self.generate(proposal=self.extension_proposal(decisions=decisions.replace(
                'The extension only exists as a heading. It has a second sentence.\n\n', '')))

    def test_extension_derivation_is_deterministic_and_normal_mode_follows_it(self):
        self.extension_fixture()
        self.generate()
        first = self.bytes()
        other = Path(self.temp.name) / 'again'
        self.generate(output=other)
        self.assertEqual(first, self.bytes(other))
        name = rp.DECISIONS
        text = (self.root / name).read_text()
        proposal = self.proposal(name, text.replace('It has a second sentence.', 'It has another sentence.'))
        self.assertEqual(self.generate('normal', changed=[name], proposal=proposal)['updated'],
                         ['decisions', 'rationale'])
        full = Path(self.temp.name) / 'extension-full'
        self.generate(output=full, proposal=proposal)
        self.assertEqual(self.bytes(), self.bytes(full))

    def test_record_links_reject_missing_wrong_identity_and_stale_evidence_before_writes(self):
        self.rationale_fixture()
        self.generate()
        before = self.bytes()
        build = g.Generator.build
        for mutation in ('missing', 'wrong', 'evidence'):
            def tampered(generator, kind):
                value = json.loads(build(generator, kind))
                if kind == 'decisions':
                    link = next(link for item in value['items'] for link in item['links']
                                if link['relation'] == 'rationale')
                    if mutation == 'missing':
                        link['target']['recordId'] = 'rationale:missing'
                    elif mutation == 'wrong':
                        link['target']['recordId'] = 'rationale:G003'
                    else:
                        link['evidence']['fragment'] = 'missing-fragment'
                return g.canonical(value)
            with self.subTest(mutation=mutation), patch.object(g.Generator, 'build', tampered):
                with self.assertRaises(ValueError):
                    self.generate()
            self.assertEqual(before, self.bytes())

    def test_unknown_or_incomplete_dependency_impact_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'Missing dependency state'):
            self.generate('normal')
        self.generate()
        proposal = self.proposal('docs/target.md', '# Target\n\nChanged.\n')
        with self.assertRaisesRegex(ValueError, 'Incomplete change set'):
            self.generate('normal', proposal=proposal)
        proposal = self.proposal('unknown.md', '# Unknown\n')
        with self.assertRaisesRegex(ValueError, 'Unknown dependency'):
            self.generate('normal', changed=['unknown.md'], proposal=proposal)

    def test_changed_inventory_or_behavior_requires_explicit_full(self):
        self.generate()
        self.inventory['datasets']['documents']['paths'].remove('docs/target.md')
        with self.assertRaisesRegex(ValueError, 'Unknown generator/inventory'):
            self.generate('normal')
        self.generate()
        state_file = self.output / 'state.json'
        state = json.loads(state_file.read_bytes())
        state['behavior'] = '0' * 64
        state_file.write_bytes(g.canonical(state))
        with self.assertRaisesRegex(ValueError, 'Unknown generator/inventory'):
            self.generate('normal')

    def test_corrupt_output_and_dependency_state_fail(self):
        self.generate()
        (self.output / 'documents.json').write_bytes(b'{}\n')
        with self.assertRaisesRegex(ValueError, 'Dataset/state mismatch'):
            self.generate('normal')
        self.generate()
        state_file = self.output / 'state.json'
        state = json.loads(state_file.read_bytes())
        state['datasets']['rationale']['dependencies'] = [rp.RATIONALE]
        state_file.write_bytes(g.canonical(state))
        with self.assertRaisesRegex(ValueError, 'Incomplete dependencies'):
            self.generate('normal')

    def test_source_full_sha_posix_identity_no_ambient_fields(self):
        self.generate()
        for kind in g.TYPES:
            for item in self.items(kind):
                source = item['source']
                for key in ('revision', 'blob', 'commit'):
                    if key in source:
                        self.assertRegex(source[key], r'^[0-9a-f]{40}$')
                if 'path' in source:
                    self.assertNotIn('\\', source['path'])
                    self.assertEqual(g.Selection(self.root, self.revision).source(source['path']),
                                     {k: v for k, v in source.items() if k != 'lineStart'})
                self.assertNotIn('generatedAt', item)
                self.assertNotIn('hostname', item)
        self.assertFalse(any(str(self.root).encode() in raw for raw in self.bytes().values()))
        commit = self.items('commits')[0]
        self.assertEqual(commit['sha'], self.revision)
        self.assertEqual(commit['id'], 'commit:' + self.revision)

    def test_canonical_serialization_stable_total_order_unicode_finite(self):
        self.generate()
        for raw in self.bytes().values():
            value = json.loads(raw)
            self.assertEqual(raw, g.canonical(value))
            self.assertTrue(raw.endswith(b'\n'))
            self.assertNotIn(b'\r', raw)
            self.assertFalse(raw.startswith(b'\xef\xbb\xbf'))
            ids = [i['id'] for i in value['items']]
            self.assertEqual(ids, sorted(set(ids)))
        self.assertIn('café — 🖖'.encode(), self.bytes()['documents'])
        with self.assertRaises(ValueError):
            g.canonical(float('nan'))

    def test_self_contained_content_preserves_facts(self):
        self.generate()
        logs = self.items('devlog')
        self.assertEqual({v.get('date') for v in logs}, {'2026-10-02T08:43Z', '2026-10-03T00:01Z'})
        self.assertIn('Contemporary uncertainty preserved.', logs[0]['contentMarkdown'])
        self.assertIn('Complete detailed decision.', self.items('decisions')[0]['contentMarkdown'])
        for kind in g.TYPES:
            for item in self.items(kind):
                self.assertTrue(item.get('contentMarkdown') or item.get('contentText'))

    def test_log_status_mismatch_fails(self):
        proposal = self.proposal('devlog/2026-10-03T0001Z-thought.md', '# Entry\n\nStatus: Accepted\n')
        with self.assertRaisesRegex(ValueError, 'Log status mismatch'):
            self.generate(proposal=proposal)

    def test_proposal_identity_explicit_revision_case_and_ambient_head(self):
        with self.assertRaisesRegex(ValueError, 'full Git commit SHA'):
            self.generate(revision='HEAD')
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.generate(proposal={'docs/guide.md': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'case'):
            self.generate(proposal={'docs/Guide.md': g.digest((self.root / 'docs/guide.md').read_bytes())})
        self.generate()
        before = self.bytes()
        self.write('docs/guide.md', '# Ambient dirty files are ignored\n')
        self.commit()
        self.generate()
        self.assertEqual(before, self.bytes())

    def test_revision_advance_changes_commits_only_and_keeps_source_identity(self):
        self.generate()
        before = self.bytes()
        self.write('unselected-component.txt', 'Unrelated repository content.\n')
        self.commit()  # Unselected content never becomes a generator input.
        revision = self.run_git('rev-parse', 'HEAD').strip()
        result = self.generate('normal', revision=revision)
        self.assertEqual(result['updated'], ['commits'])
        for kind in set(g.TYPES) - {'commits'}:
            self.assertEqual(before[kind], self.bytes()[kind])
        full = Path(self.temp.name) / 'full'
        self.generate(output=full, revision=revision)
        self.assertEqual(self.bytes(), self.bytes(full))

    def test_checkpoint_identity_and_broken_checkpoint_fail(self):
        name = 'devlog/2026-10-02T0843Z-event.md'
        proposal = self.proposal(name, '# Log\n\nStatus: Historical / non-normative\nRepository checkpoint: `' + self.revision + '`\n')
        self.generate(proposal=proposal)
        link = self.items('devlog')[0]['links'][0]
        self.assertEqual(link['relation'], 'checkpoint')
        self.assertEqual(link['target'], {'commit': self.revision})
        with self.assertRaises(ValueError):
            self.generate(proposal=self.proposal(name, '# Log\n\nStatus: Historical / non-normative\nRepository checkpoint: `' + '0' * 40 + '`\n'))

    def test_a_labeled_commit_on_a_repository_checkpoint_line_is_refused(self):
        name = 'devlog/2026-10-02T0843Z-event.md'
        head = '# Log\n\nStatus: Historical / non-normative\nRepository checkpoint: '
        self.write(ac.CITATIONS, json.dumps({'version': 1, 'external_repositories': ['upstream']}))
        for line in ('upstream commit `' + self.revision + '`', 'upstream commit `abc1234`', 'upstream commit `nothex`'):
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, 'not a labeled one'):
                    self.generate(proposal=self.proposal(name, head + line + '\n'))
        # An undeclared label is no exception: the line holds one backticked full id and nothing else.
        with self.assertRaisesRegex(ValueError, 'not a labeled one'):
            self.generate(proposal=self.proposal(name, head + 'other commit `' + self.revision + '`\n'))

    def test_a_repository_checkpoint_line_that_is_not_one_backticked_full_id_is_refused(self):
        name = 'devlog/2026-10-02T0843Z-event.md'
        head = '# Log\n\nStatus: Historical / non-normative\nRepository checkpoint: '
        self.write(ac.CITATIONS, json.dumps({'version': 1, 'external_repositories': ['upstream']}))
        for line in ('upstream commit ' + self.revision, self.revision, '`' + self.revision + '` and more', '`' + self.revision[:7] + '`'):
            with self.subTest(line=line):
                with self.assertRaisesRegex(ValueError, 'not a labeled one'):
                    self.generate(proposal=self.proposal(name, head + line + '\n'))

    def test_unsupported_link_syntax_fails_instead_of_silent_drop(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported inline'):
            self.generate(proposal=self.proposal('docs/guide.md', '# Guide\n\n[Broken](target (bad).md)\n'))

    def test_a_link_to_an_example_environment_file_resolves(self):
        examples = ('docs/.env.example', 'docs/.env.sample', 'docs/.env.template',
                    'config/.env.prod.example', 'config/.env.prod.sample', 'config/.env.prod.template')
        for name in examples:
            self.write(name, 'KEY=\n')
        links = ''.join('[{0}](../{0})\n\n'.format(n) if n.startswith('config/') else
                        '[{0}]({1})\n\n'.format(n, n[len('docs/'):]) for n in examples)
        self.write('docs/guide.md', '# Guide\n\n' + links)
        self.commit()
        self.revision = self.run_git('rev-parse', 'HEAD').strip()
        self.generate()
        guide = next(i for i in self.items('documents') if i['source']['path'] == 'docs/guide.md')
        self.assertEqual(sorted(l['target']['path'] for l in guide['links']), sorted(examples))

    def test_private_environment_files_are_still_refused(self):
        for name in ('.env', '.envrc', '.env.local', '.env.production', 'docs/.env.example.local', 'x/.env.example.bak',
                     'x/.env.', '.env.localexample'):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ValueError, 'Private/transient input'):
                    g.path_name(name)
        for name in ('.env.example', 'a/.env.sample', '.env.template', 'a/.env.prod.example',
                     '.env.prod.sample', '.env.prod.template'):
            with self.subTest(name=name):
                self.assertEqual(g.path_name(name), name)

    def test_verify_does_not_mutate_outputs_or_state(self):
        self.generate()
        state = (self.output / 'state.json').read_bytes()
        before = self.bytes()
        self.generate('verify')
        self.assertEqual(state, (self.output / 'state.json').read_bytes())
        self.assertEqual(before, self.bytes())

    def test_cli_explicit_selection_reconstructs_and_verifies(self):
        inventory = Path(self.temp.name) / 'inputs.json'
        selection = Path(self.temp.name) / 'selection.json'
        inventory.write_bytes(g.canonical(self.inventory))
        selection.write_bytes(g.canonical({'revision': self.revision, 'proposal': {}}))
        argv = [os.sys.executable, '-B', str(Path(g.__file__).resolve()), 'full',
                '--repo', str(self.root), '--selection', str(selection),
                '--inventory', str(inventory), '--output', str(self.output)]
        subprocess.run(argv, check=True, capture_output=True)
        argv[3] = 'verify'
        subprocess.run(argv, check=True, capture_output=True)

    def test_only_evidence_backed_directed_links_and_no_reverse_links(self):
        self.generate()
        guide = next(i for i in self.items('documents') if i['source']['path'] == 'docs/guide.md')
        target = next(i for i in self.items('documents') if i['source']['path'] == 'docs/target.md')
        self.assertEqual(len(guide['links']), 1)
        self.assertEqual(guide['links'][0]['target']['fragment'], 'target')
        self.assertEqual(guide['links'][0]['evidence'], guide['source'])
        self.assertEqual(target['links'], [])
        text = '# Guide\n\n[One][ref]\n\n[ref]: target.md#target\n\n`[Not a link](missing.md)`\n\n```md\n[Example](missing.md)\n```\n'
        self.generate(proposal=self.proposal('docs/guide.md', text))
        guide = next(i for i in self.items('documents') if i['source']['path'] == 'docs/guide.md')
        self.assertEqual(len(guide['links']), 1)


class DeclaredInputsTest(unittest.TestCase):
    """The inventory is the framework part, the project's optional part and the devlog setting."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, value):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8', newline='\n')

    def framework(self, **datasets):
        base = {'version': 1, 'commitHistory': 'reachable-from-declared-revision',
                'linkPolicy': 'explicit-markdown-and-checkpoint', 'datasets': {
                    'decisions': {'paths': ['governance/DECISIONS.md'], 'markdownDirectories': []},
                    'rationale': {'paths': ['governance/RATIONALE.md'], 'markdownDirectories': []},
                    'documents': {'paths': ['governance/README.md'], 'markdownDirectories': []},
                    'devlog': {'paths': [], 'markdownDirectories': []}}}
        base['datasets'].update(datasets)
        self.write(declared.FRAMEWORK, base)

    def test_the_framework_part_alone_is_the_inventory_without_a_project_part(self):
        self.framework()
        value = declared.load(self.root)
        self.assertEqual(value['datasets']['documents']['paths'], ['governance/README.md'])
        self.assertEqual(value['datasets']['devlog'], {'paths': [], 'excludePaths': ['devlog/CONVENTIONS.md', 'devlog/README.md'],
                                                       'markdownDirectories': ['devlog']})
        self.assertNotIn('excludePaths', value['datasets']['documents'])

    def test_the_project_part_is_unioned_and_sorted(self):
        self.framework()
        self.write(declared.PROJECT, {'version': 1, 'datasets': {'documents': {'paths': ['docs/PLAN.md', 'AGENTS.md', 'governance/README.md']}}})
        self.assertEqual(declared.load(self.root)['datasets']['documents']['paths'],
                         ['AGENTS.md', 'docs/PLAN.md', 'governance/README.md'])

    def test_the_folder_comes_from_the_devlog_setting(self):
        self.framework()
        self.write(rp.DEVLOG_SETTINGS, {'version': 1, 'folder': 'journal', 'date_format': '%Y%m%d'})
        log = declared.load(self.root)['datasets']['devlog']
        self.assertEqual(log['markdownDirectories'], ['journal'])
        self.assertEqual(log['excludePaths'], ['journal/CONVENTIONS.md', 'journal/README.md'])

    def test_a_disabled_devlog_is_empty_and_says_so_whatever_is_declared(self):
        self.framework(devlog={'paths': [], 'markdownDirectories': ['devlog']})
        self.write(rp.DEVLOG_SETTINGS, {'version': 1, 'folder': 'devlog', 'date_format': '%Y%m%d', 'enabled': False})
        self.assertEqual(declared.load(self.root)['datasets']['devlog'],
                         {'paths': [], 'markdownDirectories': [], 'excludePaths': [], 'enabled': False})

    def test_malformed_parts_stop_the_load(self):
        self.framework()
        for part in ({'version': 2, 'datasets': {}}, {'version': 1, 'datasets': {'commits': {}}},
                     {'version': 1, 'datasets': {'documents': {'files': []}}},
                     {'version': 1, 'datasets': {'documents': {'paths': 'docs/PLAN.md'}}}, {'version': 1, 'extra': 1}, []):
            with self.subTest(part=part):
                self.write(declared.PROJECT, part)
                with self.assertRaises(ValueError):
                    declared.load(self.root)

    def test_the_shipped_parts_declare_the_framework_and_the_project_documents(self):
        value = declared.load(rp.ROOT)
        paths = value['datasets']['documents']['paths']
        for name in ('governance/AGENT-ORIENTATION.md', 'governance/LOCAL-COORDINATION.md', 'governance/ROLE-COORDINATOR.md',
                     'governance/ROLE-WORKER.md', 'AGENTS.md', 'docs/PROGRESS.md', 'devlog/CONVENTIONS.md'):
            self.assertIn(name, paths)
        framework = json.loads((rp.ROOT / declared.FRAMEWORK).read_text(encoding='utf-8'))
        self.assertTrue(all(path.startswith('governance/') for spec in framework['datasets'].values() for path in spec['paths']))


    def extension(self, name, group=None, **more):
        manifest = dict({'version': 1, 'name': name}, **more)
        if group is not None:
            manifest['group'] = group
        self.write('governance/extensions/%s/extension.json' % name, manifest)

    def project(self, **part):
        self.write(declared.PROJECT, dict({'version': 1}, **part))

    def test_an_extension_declares_its_group_and_its_documents_become_inputs(self):
        self.framework()
        self.extension('mission_control', {'label': 'Own', 'paths': ['governance/README.md']})
        self.extension('beta', {'label': 'Beta', 'paths': ['governance/extensions/beta/README.md']})
        self.extension('alpha', {'label': 'Alpha', 'paths': ['governance/extensions/alpha/README.md', 'governance/extensions/alpha/DESIGN.md']})
        self.extension('plain')
        self.assertEqual([(g['label'], g['source']) for g in declared.groups(self.root)],
                         [('Own', 'the extension mission_control'), ('Alpha', 'the extension alpha'), ('Beta', 'the extension beta')])
        self.assertEqual(declared.load(self.root)['datasets']['documents']['paths'],
                         ['governance/README.md', 'governance/extensions/alpha/DESIGN.md', 'governance/extensions/alpha/README.md',
                          'governance/extensions/beta/README.md'])

    def test_removing_an_extension_removes_its_group_and_its_inputs(self):
        self.framework()
        self.extension('alpha', {'label': 'Alpha', 'paths': ['governance/extensions/alpha/README.md']})
        self.project(groups=[{'label': 'Mine', 'paths': ['governance/README.md']}])
        self.assertEqual([g['label'] for g in declared.groups(self.root)], ['Alpha', 'Mine'])
        shutil.rmtree(self.root / 'governance/extensions/alpha')
        self.assertEqual([g['label'] for g in declared.groups(self.root)], ['Mine'])
        self.assertEqual(declared.load(self.root)['datasets']['documents']['paths'], ['governance/README.md'])

    def test_the_project_groups_follow_the_extensions_in_declared_order(self):
        self.framework()
        self.extension('alpha', {'label': 'Alpha', 'paths': ['governance/extensions/alpha/README.md']})
        self.project(datasets={'documents': {'paths': ['docs/A.md', 'docs/B.md'], 'markdownDirectories': ['docs/plans']}},
                     groups=[{'label': 'Zed', 'paths': ['docs/B.md']}, {'label': 'Bee', 'paths': ['docs/A.md', 'docs/plans/one.md']}])
        found = declared.groups(self.root, declared.load(self.root))
        self.assertEqual([(g['label'], g['paths']) for g in found],
                         [('Alpha', ['governance/extensions/alpha/README.md']), ('Zed', ['docs/B.md']),
                          ('Bee', ['docs/A.md', 'docs/plans/one.md'])])

    def test_without_any_declaration_there_are_no_groups(self):
        self.framework()
        self.assertEqual(declared.groups(self.root), [])
        self.project(datasets={})
        self.assertEqual(declared.groups(self.root), [])

    def test_a_label_declared_twice_is_refused_across_sources(self):
        self.framework()
        self.extension('alpha', {'label': 'Same', 'paths': ['governance/extensions/alpha/README.md']})
        self.project(groups=[{'label': 'Same', 'paths': ['governance/README.md']}])
        with self.assertRaisesRegex(ValueError, 'label "Same" is declared by the extension alpha and by the project'):
            declared.load(self.root)
        shutil.rmtree(self.root / 'governance/extensions/alpha')
        self.project(groups=[{'label': 'Twice', 'paths': ['governance/README.md']}, {'label': 'Twice', 'paths': ['governance/RATIONALE.md']}])
        with self.assertRaisesRegex(ValueError, 'label "Twice" is declared by the project and by the project'):
            declared.load(self.root)

    def test_a_path_in_two_groups_is_refused_across_sources(self):
        self.framework()
        self.extension('alpha', {'label': 'Alpha', 'paths': ['governance/extensions/alpha/README.md']})
        self.project(groups=[{'label': 'Mine', 'paths': ['governance/extensions/alpha/README.md']}])
        with self.assertRaisesRegex(ValueError, 'path "governance/extensions/alpha/README.md" is in the group "Alpha" and in the group "Mine"'):
            declared.load(self.root)

    def test_a_project_path_that_is_not_an_input_is_refused(self):
        self.framework()
        self.project(groups=[{'label': 'Mine', 'paths': ['docs/missing.md']}])
        with self.assertRaisesRegex(ValueError, 'path "docs/missing.md" of the group "Mine" is not among the inputs'):
            declared.load(self.root)
        self.project(datasets={'documents': {'markdownDirectories': ['docs'], 'excludePaths': ['docs/skip.md']}},
                     groups=[{'label': 'Mine', 'paths': ['docs/skip.md']}])
        with self.assertRaisesRegex(ValueError, 'not among the inputs'):
            declared.load(self.root)

    def test_malformed_groups_are_refused(self):
        self.framework()
        ok = ['governance/README.md']
        for groups in ({'label': 'Mine', 'paths': ok}, [[]], [{'label': 'Mine'}], [{'label': '', 'paths': ok}],
                       [{'label': ' Mine', 'paths': ok}], [{'label': 'Mine', 'paths': []}],
                       [{'label': 'Mine', 'paths': 'governance/README.md'}], [{'label': 'Mine', 'paths': [3]}],
                       [{'label': 'Mine', 'paths': ['/governance/README.md']}], [{'label': 'Mine', 'paths': ['governance\\README.md']}],
                       [{'label': 'Mine', 'paths': ['governance/../governance/README.md']}], [{'label': 'Mine', 'paths': ['./governance/README.md']}],
                       [{'label': 'Mine', 'paths': ok, 'color': 'red'}]):
            with self.subTest(groups=groups):
                self.project(groups=groups)
                with self.assertRaises(ValueError):
                    declared.load(self.root)

    def test_a_malformed_extension_group_is_refused(self):
        self.framework()
        for group in ({'label': 'A'}, {'label': 'A', 'paths': []}, 'A', {'label': 'A', 'paths': ['../x.md']}):
            with self.subTest(group=group):
                self.extension('alpha', group)
                with self.assertRaises(ValueError):
                    declared.load(self.root)

    def test_the_shipped_groups_are_valid_and_their_documents_are_inputs(self):
        inventory = declared.load(rp.ROOT)
        found = declared.groups(rp.ROOT, inventory)
        self.assertEqual(found[0]['label'], 'Mission Control')
        for group in found:
            for path in group['paths']:
                self.assertTrue(declared.is_input(path, inventory), path)


class RealRepositoryTest(unittest.TestCase):
    """The generation step that reads the decisions and the rationale, run on this repository's own files."""

    def test_decision_and_rationale_reading_accepts_the_real_files(self):
        root = rp.ROOT
        head = subprocess.check_output(['git', '-C', str(root), 'rev-parse', '--verify', 'HEAD']).decode('ascii').strip()
        inventory = declared.load(root)
        # The whole working candidate stands over HEAD, link targets included, so an edit under test is read before
        # it is committed and a decision may link to a heading added in the same candidate.
        pair = g.Generator(g.Selection(root, head, working_proposal(root)), inventory).decision_rationale()
        self.assertTrue(pair['decisions'])
        self.assertTrue(pair['rationale'])


class DataLauncherTest(unittest.TestCase):
    """Portable Python data launcher against disposable admitted Git inputs."""
    fixture, write, run_git, commit = GeneratorTest.setUp, GeneratorTest.write, GeneratorTest.run_git, GeneratorTest.commit

    def setUp(self):
        self.fixture()
        repository = rp.ROOT
        packages = ('lifecycle', 'continuity', 'coordination', 'support')
        self.write('governance/__init__.py', '')
        self.write('governance/tools/__init__.py', '')
        for name in (MC_DATA, 'governance/tools/runtime_guard.py',
                     'governance/tools/commit_metadata.py', 'governance/tools/lifecycle/commit_metadata.py',
                     'governance/tools/continuity/artifact_classification.py',
                     'governance/extensions/mission_control/generate.py', 'governance/extensions/mission_control/data_launcher.py',
                     'governance/extensions/mission_control/declared_inputs.py',
                     'governance/tools/local_jobs.py', 'governance/tools/coordination/local_jobs.py',
                     *('governance/tools/%s/__init__.py' % package for package in packages)):
            self.write(name, (repository / name).read_text(encoding='utf-8'))
        self.write('governance/extensions/mission_control/inputs.json', g.canonical(self.inventory).decode('utf-8'))
        self.write('.gitignore', 'tmp/\n__pycache__/\n/.local/\n')
        self.commit()
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}

    def launch(self, *args):
        return subprocess.run([sys.executable, '-B', str(self.root / 'governance/extensions/mission_control/data_launcher.py'), *args],
                              cwd=self.temp.name, env=self.environment, capture_output=True, text=True, encoding='utf-8')

    def head(self):
        return self.run_git('rev-parse', 'HEAD').strip()

    def assertScratchClean(self):
        self.assertFalse((self.root / 'tmp').exists())
        self.assertEqual(list(self.root.rglob('*.publish')), [])

    def local_base(self):
        return launcher.local_base(self.root)

    def test_refresh_local_writes_only_the_one_ignored_copy(self):
        tracked = self.run_git('ls-files', '-s')
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Tracked files untouched', result.stdout)
        self.assertEqual(self.run_git('status', '--porcelain'), '')
        self.assertEqual(self.run_git('ls-files', '-s'), tracked)
        # No tracked dataset folder and no pinned selection appear: the datasets live only in the local copy.
        self.assertFalse((self.root / 'governance/extensions/mission_control/data').exists())
        self.assertFalse((self.root / 'governance/extensions/mission_control/snapshot-selection.json').exists())
        copy = launcher.local_snapshot(self.root)
        self.assertEqual(copy['revision'], self.head())
        self.assertRegex(copy['generator'], '^[0-9a-f]{12}$')
        expected = Path(self.temp.name) / 'expected'
        g.generate(self.root, self.head(), declared.load(self.root), expected)
        for kind in g.TYPES:
            self.assertEqual((copy['folder'] / (kind + '.json')).read_bytes(), (expected / (kind + '.json')).read_bytes())
        self.assertEqual(sorted(p.name for p in self.local_base().iterdir()), ['data', 'meta.json'])
        # The generator's dependency state stays beside the datasets: it is what lets the next refresh be incremental.
        self.assertEqual(sorted(p.name for p in (self.local_base() / 'data').iterdir()),
                         sorted([k + '.json' for k in g.TYPES] + ['state.json']))
        self.assertIn('Full rebuild at', result.stdout)
        self.assertIn('there is no whole local copy yet', result.stdout)
        self.assertScratchClean()

    def test_refresh_local_replaces_the_copy_and_a_failed_run_keeps_the_previous_one(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        first = launcher.local_snapshot(self.root)
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.commit()
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        second = launcher.local_snapshot(self.root)
        self.assertEqual(second['revision'], self.head())
        self.assertNotEqual(second['revision'], first['revision'])
        self.assertEqual(sorted(p.name for p in self.local_base().iterdir()), ['data', 'meta.json'])
        self.write('docs/guide.md', '# Guide\n\n[Broken](target (bad).md)\n')
        self.commit()
        before = {p.name: p.read_bytes() for p in (self.local_base() / 'data').iterdir()}
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unsupported inline', result.stderr)
        self.assertIn('live data untouched', result.stderr)
        self.assertEqual(launcher.local_snapshot(self.root), second)
        self.assertEqual({p.name: p.read_bytes() for p in (self.local_base() / 'data').iterdir()}, before)
        self.assertEqual(sorted(p.name for p in self.local_base().iterdir()), ['data', 'meta.json'])
        self.assertScratchClean()

    def test_local_copy_must_be_whole_to_count(self):
        self.assertIsNone(launcher.local_snapshot(self.root))
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        copy = launcher.local_snapshot(self.root)
        self.assertIsNotNone(copy)
        meta = self.local_base() / 'meta.json'
        good = meta.read_bytes()
        meta.write_bytes(good.replace(b'"generator"', b'"other"'))
        self.assertIsNone(launcher.local_snapshot(self.root))
        meta.write_bytes(good)
        (copy['folder'] / 'rationale.json').unlink()
        self.assertIsNone(launcher.local_snapshot(self.root))

    def local_datasets(self):
        return {kind: (self.local_base() / 'data' / (kind + '.json')).read_bytes() for kind in g.TYPES}

    def assertLocalIsFullRebuild(self):
        expected = Path(self.temp.name) / 'expected'
        shutil.rmtree(expected, ignore_errors=True)
        g.generate(self.root, self.head(), declared.load(self.root), expected)
        self.assertEqual(self.local_datasets(), {kind: (expected / (kind + '.json')).read_bytes() for kind in g.TYPES})
        # The state is the full rebuild's too, apart from the generator digest when a test altered the fixture's copy.
        state, reference = (json.loads(folder.joinpath('state.json').read_bytes())
                            for folder in (self.local_base() / 'data', expected))
        self.assertEqual({k: v for k, v in state.items() if k != 'behavior'},
                         {k: v for k, v in reference.items() if k != 'behavior'})
        self.assertEqual(launcher.local_snapshot(self.root)['revision'], self.head())

    def refresh_fallback(self, reason):
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Full rebuild at ' + self.head()[:7], result.stdout)
        self.assertNotIn('Incremental', result.stdout)
        self.assertIn(reason, result.stdout)
        self.assertIn('Verify skipped', result.stdout)
        self.assertLocalIsFullRebuild()
        self.assertEqual(sorted(p.name for p in self.local_base().iterdir()), ['data', 'meta.json'])
        self.assertScratchClean()

    def tamper_state(self, change):
        path = self.local_base() / 'data' / 'state.json'
        state = json.loads(path.read_bytes())
        change(state)
        path.write_bytes(g.canonical(state))

    def test_refresh_local_updates_incrementally_and_matches_a_full_rebuild(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        first = launcher.local_snapshot(self.root)
        unchanged = self.local_datasets()
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.write('devlog/2026-10-04T0000Z-new.md',
                   '# New log\n\nStatus: Historical / non-normative\nDate: 2026-10-04T00:00Z\n\nAdded later.\n')
        self.commit()
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Incremental update at ' + self.head()[:7], result.stdout)
        self.assertRegex(result.stdout, r'in \d+\.\d s: \d of 5 datasets rebuilt')
        self.assertIn('2 changed input paths', result.stdout)
        self.assertIn('Verify skipped on this local path', result.stdout)
        self.assertLocalIsFullRebuild()
        self.assertNotEqual(launcher.local_snapshot(self.root)['revision'], first['revision'])
        # The rationale dataset did not depend on either changed path.
        self.assertEqual(self.local_datasets()['rationale'], unchanged['rationale'])
        self.assertEqual(sorted(p.name for p in self.local_base().iterdir()), ['data', 'meta.json'])
        self.assertEqual(self.run_git('status', '--porcelain'), '')
        self.assertScratchClean()
        # A deletion is an input change too.
        self.run_git('rm', '-q', '--', 'devlog/2026-10-04T0000Z-new.md')
        self.commit()
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Incremental update', result.stdout)
        self.assertLocalIsFullRebuild()

    def test_refresh_local_does_nothing_when_already_current(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        meta = (self.local_base() / 'meta.json').read_bytes()
        before = self.local_datasets()
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Already current at ' + self.head()[:7], result.stdout)
        self.assertEqual((self.local_base() / 'meta.json').read_bytes(), meta)
        self.assertEqual(self.local_datasets(), before)
        self.assertScratchClean()

    def test_a_commit_that_changes_no_tracked_input_still_updates_the_commit_dataset(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        self.write('unrelated.txt', 'not an input\n')
        self.commit()
        result = self.launch('refresh-local')
        self.assertIn('Incremental update', result.stdout)
        self.assertIn('1 of 5 datasets rebuilt (commits), 0 changed input paths', result.stdout)
        self.assertLocalIsFullRebuild()

    def test_fallback_when_the_dependency_state_is_missing_or_damaged(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        state = self.local_base() / 'data' / 'state.json'
        state.unlink()
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.commit()
        self.refresh_fallback('the local dependency state is missing or damaged')
        for damage in (b'{not json', b'[]', b'{}'):
            with self.subTest(damage=damage):
                state.write_bytes(damage)
                self.refresh_fallback('the local dependency state is missing or damaged')
        # A dataset that no longer matches the hash its state recorded is damage too.
        dataset = self.local_base() / 'data' / 'rationale.json'
        dataset.write_bytes(dataset.read_bytes() + b' ')
        self.refresh_fallback('the local dependency state is missing or damaged')
        self.assertEqual(json.loads(state.read_bytes())['revision'], self.head())

    def test_fallback_when_the_generator_or_the_inventory_changed(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        generator = self.root / 'governance/extensions/mission_control/generate.py'
        generator.write_text(generator.read_text(encoding='utf-8') + '\n# changed behavior\n', encoding='utf-8', newline='\n')
        self.refresh_fallback('the generator or the inventory changed since the last refresh')
        # With the new generator recorded, the next refresh is incremental again.
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.commit()
        self.assertIn('Incremental update', self.launch('refresh-local').stdout)
        self.tamper_state(lambda state: state.update(inventory='0' * 64))
        self.refresh_fallback('the generator or the inventory changed since the last refresh')

    def test_fallback_when_the_recorded_revision_is_not_an_ancestor_of_head(self):
        base = self.head()
        self.write('docs/target.md', '# Target\n\nOn a branch that is then abandoned.\n')
        self.commit()
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        abandoned = self.head()
        self.run_git('reset', '--hard', base)
        self.assertNotEqual(self.head(), abandoned)
        self.refresh_fallback('the last refreshed revision ' + abandoned[:7] + ' is not an ancestor of HEAD')
        # A revision that no longer exists at all falls back the same way.
        self.tamper_state(lambda state: state.update(revision='1' * 40))
        meta = self.local_base() / 'meta.json'
        meta.write_bytes((json.dumps(dict(json.loads(meta.read_bytes()), revision='1' * 40), indent=2, sort_keys=True)
                          + '\n').encode('ascii'))
        self.refresh_fallback('the last refreshed revision 1111111 is not an ancestor of HEAD')

    def test_fallback_when_the_state_and_the_meta_file_disagree(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        self.tamper_state(lambda state: state.update(revision=self.revision))
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.commit()
        self.refresh_fallback('the dependency state and the meta file describe different revisions')

    def test_fallback_when_normal_mode_refuses(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        # The state still matches its datasets, but its recorded dependencies are incomplete.
        self.tamper_state(lambda state: state['datasets']['documents'].update(dependencies=[]))
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.commit()
        self.refresh_fallback('Incomplete dependencies')

    def test_a_failed_fallback_keeps_the_previous_copy(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        previous = launcher.local_snapshot(self.root)
        (self.local_base() / 'data' / 'state.json').unlink()
        before = {p.name: p.read_bytes() for p in (self.local_base() / 'data').iterdir()}
        self.write('docs/guide.md', '# Guide\n\n[Broken](target (bad).md)\n')
        self.commit()
        result = self.launch('refresh-local')
        self.assertEqual(result.returncode, 1)
        self.assertIn('live data untouched', result.stderr)
        self.assertEqual(launcher.local_snapshot(self.root), previous)
        self.assertEqual({p.name: p.read_bytes() for p in (self.local_base() / 'data').iterdir()}, before)
        self.assertScratchClean()

    def test_refresh_local_full_rebuilds_and_verifies_without_trying_the_incremental_update(self):
        self.assertEqual(self.launch('refresh-local').returncode, 0)
        self.write('docs/target.md', '# Target\n\nChanged target content.\n')
        self.commit()
        result = self.launch('refresh-local-full')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Full rebuild with verification at ' + self.head()[:7], result.stdout)
        self.assertNotIn('Verify skipped', result.stdout)
        self.assertLocalIsFullRebuild()
        self.assertEqual(self.run_git('status', '--porcelain'), '')
        self.assertScratchClean()

    def test_only_the_two_local_modes_exist(self):
        # The committed-snapshot modes are gone with the committed snapshots.
        for args in (('refresh',), ('check',), ('publish',), (), ('refresh-local', 'extra'), ('refresh-local-full', 'extra')):
            with self.subTest(args=args):
                result = self.launch(*args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('Usage: data_launcher.py refresh-local|refresh-local-full', result.stderr)
        for name in ('SELECTION', 'PROJECTION', 'backlog', 'plain_revision', 'REFRESH'):
            self.assertFalse(hasattr(launcher, name), name)

    def test_publication_retries_permission_errors_and_cleans_failed_stage(self):
        source, target = Path(self.temp.name) / 'source', Path(self.temp.name) / 'target'
        source.write_bytes(b'new')
        target.write_bytes(b'old')
        replace = launcher.os.replace
        with patch.object(launcher.os, 'replace') as mocked, \
                patch.object(launcher.time, 'sleep'):
            calls = 0
            def retry(staged, destination):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise PermissionError()
                replace(staged, destination)
            mocked.side_effect = retry
            launcher.publish(source, target)
        self.assertEqual(target.read_bytes(), b'new')
        target.write_bytes(b'old')
        with patch.object(launcher.os, 'replace', side_effect=PermissionError()), \
                patch.object(launcher.time, 'sleep'):
            with self.assertRaises(PermissionError):
                launcher.publish(source, target)
        self.assertEqual(target.read_bytes(), b'old')
        self.assertFalse(target.with_name('target.publish').exists())

    def test_verification_failure_prevents_publication(self):
        scratch = Path(self.temp.name) / 'verify-failure'
        scratch.mkdir()
        with patch.object(launcher, 'generate', side_effect=[None, ValueError('verification failed')]), \
                patch.object(launcher, 'publish_local') as publish:
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                launcher.run(self.root, scratch, 'refresh-local-full')
            publish.assert_not_called()
        self.assertIsNone(launcher.local_snapshot(self.root))

    @unittest.skipUnless(shutil.which('bash'), 'Bash required only for compatibility shim')
    def test_shell_shim_from_another_directory(self):
        result = subprocess.run([shutil.which('bash'), str(self.root / MC_DATA),
                                 'refresh'], cwd=self.temp.name, env=self.environment,
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(launcher.local_snapshot(self.root)['revision'], self.head())
        for args in ((), ('unknown',), ('check',), ('refresh', 'extra')):
            result = subprocess.run([shutil.which('bash'), str(self.root / MC_DATA),
                                     *args], cwd=self.temp.name, env=self.environment, capture_output=True)
            self.assertEqual(result.returncode, 2)
        self.assertScratchClean()


if __name__ == '__main__':
    unittest.main()
