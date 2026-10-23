"""The project's homes for its rules and decisions, and its half of the reconstruction contract, beside the framework's.

Every file is optional (an absent one means none), a registry takes only its own prefix (G in the framework's, D in the
project's), a present home must be linked from AGENTS.md, and the contract is the union of the two parts with a clash refused.
No D-prefixed id is written out in this file: the project's ids are the framework's with the letter swapped."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from governance.tests.fake_git import FakeGit
from governance.tools.continuity import artifact_classification as ac
from governance.tools.continuity import validate_continuity as v
import repo_paths as rp

PROJECT_ROW = '| ' + 'D' + '010 | 2026-10-19 | Accepted | A project decision. |\n'
FRAMEWORK_CONTRACT = {'version': 1, 'bootstrap': 'governance/COMMAND-RECOVERY-PROMPT-v1.md',
                      'facts': {'framework_fact': {'source': 'docs/PROGRESS.md#facts', 'pattern': '^x$', 'type': 'string'}},
                      'governance_evidence': ['governance/CONSTRAINTS.md'], 'required_sections': ['current_state']}
PROJECT_CONTRACT = {'version': 1,
                    'facts': {'service': {'source': 'docs/PROGRESS.md#facts', 'pattern': '^y$', 'type': 'string'}},
                    'governance_evidence': ['AGENTS.md', 'governance/CONSTRAINTS.md'],
                    'completion_evidence_fact': 'service'}


class ProjectHomesTest(unittest.TestCase):
    """The continuity check on a small repository with no devlog, with and without the project's homes."""

    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='project-homes-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.write(rp.AGENTS, '# Agents\n')
        self.write(rp.README, '# Repository\n')
        self.write(ac.DEVLOG, json.dumps({'enabled': False}))
        self.write(v.CONTRACT, '{"bootstrap":"governance/COMMAND-RECOVERY-PROMPT-v1.md"}')
        self.write(rp.DECISIONS, '# Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
                                 '| G001 | 2026-10-19 | Accepted | A framework decision. |\n\n## G001 - First\n\nText.\n')

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def validate(self):
        with patch.object(v, 'scope', return_value=([], [], [])), patch.object(v, 'git', FakeGit().rooted):
            return v.validate_docs(self.root)

    def homes(self, decisions='# Project Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'):
        self.write(rp.AGENTS, '# Agents\n\n[Rules](project-governance/CONSTRAINTS.md) [Decisions](project-governance/DECISIONS.md)\n')
        self.write(ac.PROJECT_CONSTRAINTS, '# Project Constraints\n\n[Decisions](DECISIONS.md)\n')
        self.write(ac.PROJECT_DECISIONS, decisions + '\n[Why](RATIONALE.md)\n')
        self.write(ac.PROJECT_RATIONALE, '# Why\n\nStatus: Explanatory / non-normative\n')

    def test_absent_homes_mean_none(self):
        self.assertEqual(self.validate(), [])

    def test_present_homes_linked_from_agents_pass_and_an_empty_registry_is_valid(self):
        self.homes()
        self.assertEqual(self.validate(), [])
        self.write(ac.PROJECT_DECISIONS, '# Project Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
                   + PROJECT_ROW + '\n## ' + PROJECT_ROW[2:6] + ' - Second\n\nText.\n\n[Why](RATIONALE.md)\n')
        self.assertEqual(self.validate(), [])

    def test_a_present_home_must_be_reachable_from_agents(self):
        self.homes()
        self.write(rp.AGENTS, '# Agents\n')
        with self.assertRaisesRegex(ValueError, 'Not reachable from AGENTS.md: ' + ac.PROJECT_CONSTRAINTS):
            self.validate()
        # One link is enough: the rules link the registry and the registry links its rationale.
        self.write(rp.AGENTS, '# Agents\n\n[Rules](' + ac.PROJECT_CONSTRAINTS + ')\n')
        self.assertEqual(self.validate(), [])
        self.write(ac.PROJECT_DECISIONS, '# Project Decisions\n')
        with self.assertRaisesRegex(ValueError, 'Not reachable from AGENTS.md: ' + ac.PROJECT_RATIONALE):
            self.validate()

    def test_the_links_of_a_present_home_are_checked(self):
        self.homes()
        self.write(ac.PROJECT_CONSTRAINTS, '# Project Constraints\n\n[Missing](missing.md) [Decisions](DECISIONS.md)\n')
        with self.assertRaisesRegex(ValueError, 'Missing recovery evidence'):
            self.validate()

    def test_the_project_registry_takes_only_its_own_prefix(self):
        self.homes('# Project Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
                   '| G002 | 2026-10-19 | Accepted | A framework id in the project registry. |\n')
        with self.assertRaisesRegex(ValueError, r'project-governance/DECISIONS.md takes only decisions with the prefix D.*G002'):
            self.validate()

    def test_the_framework_registry_takes_only_its_own_prefix(self):
        self.write(rp.DECISIONS, (self.root / rp.DECISIONS).read_text(encoding='utf-8') + '\n## ' + PROJECT_ROW[2:6] + ' - Wrong\n')
        with self.assertRaisesRegex(ValueError, r'governance/DECISIONS.md takes only decisions with the prefix G.*' + PROJECT_ROW[2:6]):
            self.validate()

    def test_the_prefix_check_reads_rows_and_headings_only(self):
        ac.check_registry_prefix(ac.PROJECT_DECISIONS, '# Decisions\n\nProse may cite ' + 'G' + '001 or any other id.\n')
        for text in ('| G003 | x |', '## G003 - t', '### G003 extension (2026-10-19)'):
            with self.assertRaisesRegex(ValueError, 'prefix D'):
                ac.check_registry_prefix(ac.PROJECT_DECISIONS, text)
        self.assertEqual(ac.registry_prefix(ac.PROJECT_DECISIONS), 'D')
        self.assertEqual(ac.registry_prefix(ac.FRAMEWORK_DECISIONS), 'G')

    def test_the_shipped_starters_pass_the_check(self):
        for name in (ac.PROJECT_CONSTRAINTS, ac.PROJECT_DECISIONS, ac.PROJECT_RATIONALE):
            self.assertTrue((rp.ROOT / name).is_file(), name)
        ac.check_registry_prefix(ac.PROJECT_DECISIONS, (rp.ROOT / ac.PROJECT_DECISIONS).read_text(encoding='utf-8'))
        ac.check_registry_prefix(ac.FRAMEWORK_DECISIONS, (rp.ROOT / ac.FRAMEWORK_DECISIONS).read_text(encoding='utf-8'))


class ContractUnionTest(unittest.TestCase):
    """The reconstruction contract is the framework's part with the project's added; a clash is refused."""

    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='contract-union-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        self.write(ac.CONTRACT, FRAMEWORK_CONTRACT)

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8', newline='\n')

    def test_no_project_part_is_the_framework_part_alone(self):
        self.assertEqual(ac.load_contract(self.root), FRAMEWORK_CONTRACT)
        self.assertEqual(v.reconstruction_contract(self.root), FRAMEWORK_CONTRACT)

    def test_a_missing_framework_part_is_refused(self):
        (self.root / ac.CONTRACT).unlink()
        with self.assertRaisesRegex(ValueError, 'contract is missing'):
            ac.load_contract(self.root)

    def test_the_project_part_adds_facts_evidence_and_the_completion_fact(self):
        self.write(ac.PROJECT_CONTRACT, PROJECT_CONTRACT)
        merged = ac.load_contract(self.root)
        self.assertEqual(sorted(merged['facts']), ['framework_fact', 'service'])
        self.assertEqual(merged['governance_evidence'], ['governance/CONSTRAINTS.md', 'AGENTS.md'])
        self.assertEqual(merged['completion_evidence_fact'], 'service')
        self.assertEqual(merged['bootstrap'], FRAMEWORK_CONTRACT['bootstrap'])
        self.assertEqual(merged['required_sections'], ['current_state'])
        self.assertEqual(FRAMEWORK_CONTRACT['facts'].keys(), {'framework_fact'})  # the inputs are not changed

    def test_a_clash_or_a_framework_key_is_refused(self):
        framework = copy.deepcopy(FRAMEWORK_CONTRACT)
        for name, change, message in (
                ('fact', lambda p: p['facts'].update(framework_fact=FRAMEWORK_CONTRACT['facts']['framework_fact']), 'facts clash'),
                ('bootstrap', lambda p: p.update(bootstrap='x.md'), 'may not set bootstrap'),
                ('sections', lambda p: p.update(required_sections=['x']), 'may not set required_sections'),
                ('version', lambda p: p.update(version=2), 'version must equal'),
                ('evidence', lambda p: p.update(governance_evidence='AGENTS.md'), 'must be a list of strings')):
            with self.subTest(name=name):
                project = copy.deepcopy(PROJECT_CONTRACT)
                change(project)
                self.write(ac.PROJECT_CONTRACT, project)
                with self.assertRaisesRegex(ValueError, message):
                    ac.load_contract(self.root)
        framework['completion_evidence_fact'] = 'framework_fact'
        self.write(ac.CONTRACT, framework)
        self.write(ac.PROJECT_CONTRACT, PROJECT_CONTRACT)
        with self.assertRaisesRegex(ValueError, 'completion_evidence_fact clashes'):
            ac.load_contract(self.root)

    def test_the_shipped_parts_make_the_contract_the_repository_verifies(self):
        merged = ac.load_contract(rp.ROOT)
        framework = json.loads((rp.ROOT / ac.CONTRACT).read_text(encoding='utf-8'))
        project = json.loads((rp.ROOT / ac.PROJECT_CONTRACT).read_text(encoding='utf-8'))
        self.assertEqual(framework['facts'], {})  # the facts read from the progress record are the project's
        self.assertTrue(set(project['facts']) <= set(merged['facts']))
        self.assertTrue(all(name.startswith('governance/') for name in framework['governance_evidence']))
        self.assertEqual(merged['bootstrap'], framework['bootstrap'])
        self.assertEqual(sorted(merged['governance_evidence']),
                         sorted(framework['governance_evidence'] + project['governance_evidence']))


if __name__ == '__main__':
    unittest.main()
