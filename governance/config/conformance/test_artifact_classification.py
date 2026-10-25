"""The real classification table of this project: its rows, the independent golden pin, the approval-sensitivity lists
and how the explainer reads them. The parser's own behavior is tested on synthetic tables in
governance/tests/continuity/test_artifact_classification.py."""
import unittest
from governance.tools.continuity import artifact_classification as ac
from governance.tests.continuity.test_artifact_classification import full, parse, row, table
import repo_paths as rp

import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import patch

import checkpoint as c
from governance.tools.lifecycle import early_approval as ea
from governance.tools.lifecycle import review_render
from governance.tools.continuity import show_classification as sc

ROOT = rp.ROOT

# The rows of the framework's table outside governance/: the files the tools themselves own at the root (the agent entry
# floor and the checkout rules). Everything else is the project's.
FRAMEWORK_ROOT_ROWS = ('.gitattributes', '.gitignore', 'AGENTS.md')


def real_data(root=ROOT):
    """The policy, the profiles (the framework's plus the project's, as the tools read them) and the table of `root`."""
    policy = json.loads((root / ac.POLICY).read_bytes())
    profiles = ac.load_profiles(root)
    table = ac.load_table(root, profiles)
    return policy, profiles, table


def real_table_with(rows=(), historical=()):
    """The real table text with extra rows in sorted position and, when given, a Historical paths section."""
    profiles = ac.load_profiles(ROOT)
    lines = (ROOT / c.TABLE).read_text(encoding='ascii').split(chr(10))
    start = next(i for i, line in enumerate(lines) if line.startswith('| Path | Class | Profile |')) + 2
    end = start
    while lines[end].startswith('|'):
        end += 1
    merged = {line.split('`')[1]: line for line in lines[start:end] + list(rows)}
    text = chr(10).join(lines[:start] + [merged[k] for k in sorted(merged)] + lines[end:])
    if historical:
        text = text.rstrip(chr(10)) + chr(10) * 2 + '## Historical paths table' + chr(10) * 2 + chr(10).join(
            ['| Path | Class | Subclass |', '| --- | --- | --- |', *historical]) + chr(10)
    return ac.parse_table(text, profiles)


class RealTableTest(unittest.TestCase):
    def setUp(self):
        self.policy, self.profiles, self.table = real_data()

    def test_the_document_is_ascii_sorted_and_parses(self):
        text = (ROOT / c.TABLE).read_text(encoding='ascii')
        self.assertTrue(text.isascii())
        self.assertGreater(len(self.table.rows), 50)
        self.assertTrue((ROOT / ac.PROJECT_TABLE).read_text(encoding='ascii').isascii())

    def test_every_row_path_is_classified_by_its_own_row(self):
        for row in self.table.rows:
            if row.kind == 'file':
                self.assertEqual(self.table.resolve(row.path).row.path, row.path)

    def test_the_policy_and_profiles_name_no_paths(self):
        """The table is the only list of paths: nothing else may restate one."""
        for key in ('scope_rules', 'frozen_paths', 'runtime_paths', 'governance_paths'):
            self.assertNotIn(key, self.policy)
        self.assertNotIn('rules', self.profiles['impact'])
        for entry in self.profiles['commit_metadata']['subclasses']:
            self.assertFalse({'paths', 'prefixes', 'invariants'} & set(entry), entry['value'])

    def test_the_framework_table_holds_framework_paths_only(self):
        """The project-side rows live in the project's table: the framework's holds the folder it owns and the few
        tool-owned root files, so a project row cannot creep back into the file an upgrade replaces."""
        framework = ac.parse_table((ROOT / c.TABLE).read_text(encoding='ascii'), self.profiles)
        outside = sorted(row.path for row in framework.rows
                         if not row.path.startswith(('governance/', 'project-governance')) and row.path not in FRAMEWORK_ROOT_ROWS)
        self.assertEqual(outside, [])
        self.assertEqual(sorted(row.path for row in framework.rows if row.path in FRAMEWORK_ROOT_ROWS),
                         sorted(FRAMEWORK_ROOT_ROWS))
        self.assertTrue(all(row.source is None for row in framework.rows))

    def test_the_project_table_holds_project_paths_only_and_is_in_the_union(self):
        project = ac.parse_table((ROOT / ac.PROJECT_TABLE).read_text(encoding='ascii'), self.profiles)
        self.assertTrue(project.rows)
        for row in project.rows:
            self.assertFalse(ac.reserved(row.path.rstrip('/')), row.path)
            self.assertEqual(self.table.by_path[row.path].source, ac.PROJECT_TABLE, row.path)
        self.assertEqual(len(self.table.rows), len(project.rows) + len(
            ac.parse_table((ROOT / c.TABLE).read_text(encoding='ascii'), self.profiles).rows))

    def test_live_file_rows_exist_and_historical_paths_do_not(self):
        for row in self.table.rows:
            if row.kind == 'file' and not row.path.startswith(ac.PROJECT_GOVERNANCE + '/'):
                self.assertTrue((ROOT / row.path).is_file(), row.path)
        for path in self.table.historical:
            self.assertFalse((ROOT / path).exists(), path)

    def test_historical_paths_still_classify_old_commits(self):
        table = real_table_with(historical=['| `docs/WORKTREE-WORKFLOW.md` | governance | - |'])
        self.assertEqual(table.classify('docs/WORKTREE-WORKFLOW.md'), ('governance', []))
        self.assertIsNone(table.resolve('docs/WORKTREE-WORKFLOW.md'))
        self.assertIsNone(table.classify('docs/never-existed.md'))
        # Only the framework's own table is pinned: the Historical paths of the project's table are the project's, which
        # rule 8 requires it to extend whenever it deletes or renames a file.
        framework = ac.parse_table((ROOT / c.TABLE).read_text(encoding='ascii'), self.profiles)
        self.assertEqual(set(framework.historical), set())
        self.assertLessEqual(set(framework.historical), set(self.table.historical))

    def test_a_project_historical_row_is_read_and_classifies_old_commits(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (c.TABLE, ac.PROFILES, ac.POLICY):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes((ROOT / name).read_bytes())
            project = chr(10).join(['# Project table', '', ac.HEADER, ac.SEPARATOR,
                                    '| `notes.md` | record | record-only | - | - | - | - | - | - |', '',
                                    '## Historical paths', '', '| Path | Class | Subclass |', '| --- | --- | --- |',
                                    '| `old/removed.sh` | tooling | - |']) + chr(10)
            (root / ac.PROJECT_TABLE).parent.mkdir(parents=True, exist_ok=True)
            (root / ac.PROJECT_TABLE).write_text(project, encoding='ascii', newline=chr(10))
            _, _, table = real_data(root)
            self.assertEqual(set(table.historical), {'old/removed.sh'})
            self.assertEqual(table.classify('old/removed.sh'), ('tooling', []))
            self.assertIsNone(table.resolve('old/removed.sh'))
            self.assertEqual(table.resolve('notes.md').klass, 'record')


class ProjectProfileTest(unittest.TestCase):
    """A project row may name a profile the project added (G045): what the tools read, these tests read too."""
    PROJECT_PROFILE = {'limitations': 'Fixture only.',
                       'checks': [{'id': 'continuity', 'argv': ['{python}', '-B', 'governance/tools/validate_continuity.py']}]}

    def test_a_project_row_naming_a_project_profile_is_read_and_sealed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in (c.TABLE, ac.PROFILES, ac.POLICY):
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_bytes((ROOT / name).read_bytes())
            framework = json.loads((ROOT / ac.PROFILES).read_bytes())
            files = {ac.PROJECT_PROFILES: json.dumps({'version': framework['version'],
                                                      'profiles': {'sample-offline': self.PROJECT_PROFILE}}),
                     ac.PROJECT_TABLE: '\n'.join(['# Project table', '', ac.HEADER, ac.SEPARATOR,
                                                  '| `notes.md` | record | sample-offline | - | - | - | - | - | - |']) + '\n'}
            for name, text in files.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text, encoding='ascii', newline='\n')
            policy, profiles, table = real_data(root)
            self.assertEqual(table.resolve('notes.md').profile, 'sample-offline')
            tool = object.__new__(c.Checkpoint)
            tool.root = root
            profile = tool.select_profile(['notes.md'], policy, profiles, 'auto')[1]
            sealed = tool.attach_classification(['notes.md'], policy, profiles, profile)
            self.assertEqual(sealed['artifact_classification']['notes.md']['profile'], 'sample-offline')


class GoldenTest(unittest.TestCase):
    """The independent pins, one file per side: the framework's golden pins rows of the framework's table and the
    project's golden pins rows of the project's, and no path is pinned by both."""
    ENTRY = {'class', 'profile', 'boundary', 'bound', 'invariants', 'subclass', 'reachable', 'approval_sensitivity', 'why'}
    SIDES = ((rp.GOLDEN, None), (rp.PROJECT_GOLDEN, ac.PROJECT_TABLE))

    def test_critical_paths_resolve_as_pinned(self):
        policy, profiles, table = real_data()
        for golden_path, source in self.SIDES:
            golden = json.loads((ROOT / golden_path).read_text(encoding='ascii'))
            self.assertEqual(set(golden), {'version', 'paths'})
            self.assertEqual(golden['version'], 1)
            keys = list(golden['paths'])
            self.assertEqual(keys, sorted(keys), golden_path + ': golden keys must be sorted by ASCII ordinal')
            self.assertGreaterEqual(len(keys), 5 if source else 20)
            for path, entry in golden['paths'].items():
                with self.subTest(golden=golden_path, path=path):
                    resolved = table.resolve(path)
                    if entry.get('refused'):
                        # Refused: no row covers the path or its profile is blocked; the boundary is still pinned.
                        self.assertEqual(set(entry), {'refused', 'boundary', 'why'})
                        self.assertTrue(resolved is None or resolved.profile == 'blocked')
                        self.assertEqual([] if resolved is None else resolved.boundary, entry['boundary'])
                        continue
                    self.assertEqual(set(entry), self.ENTRY)
                    self.assertIsNotNone(resolved)
                    self.assertEqual(resolved.row.source, source, 'the row comes from the other side table')
                    self.assertEqual(
                        {'class': resolved.klass, 'profile': resolved.profile, 'boundary': resolved.boundary,
                         'bound': resolved.bound, 'invariants': resolved.invariants, 'subclass': resolved.subclasses,
                         'reachable': resolved.reachable, 'approval_sensitivity': resolved.approval_sensitivity},
                        {k: v for k, v in entry.items() if k != 'why'})

    def test_no_path_is_pinned_on_both_sides(self):
        framework, project = (json.loads((ROOT / path).read_text(encoding='ascii'))['paths'] for path, _ in self.SIDES)
        self.assertEqual(sorted(set(framework) & set(project)), [])

    def test_golden_is_independent_of_the_table_document(self):
        for golden_path, _ in self.SIDES:
            text = (ROOT / golden_path).read_text(encoding='ascii')
            self.assertNotIn('ARTIFACT-CLASSIFICATION.md`', text)


# Paths whose bytes decide what a go approves, how checks are derived or how an approval is recorded, bound or
# integrated. This list is independent of the table: a row that loses its mark fails here. The approval machinery
# itself is `high`; the rest are `medium`.
APPROVAL_HIGH = (
    'governance/tools/approval_words.py', 'governance/tools/lifecycle/approval_words.py', 'governance/tools/lifecycle/__init__.py',
    'governance/tools/worktrees/__init__.py', 'governance/__init__.py', 'governance/tools/__init__.py', 'governance/config/checkpoint-policy.json',
    'governance/tools/checkpoint.py', 'governance/tools/lifecycle/checkpoint_evidence.py', 'governance/tools/lifecycle/checkpoint_lifecycle.py',
    'governance/tools/lifecycle/checkpoint_selection.py', 'governance/tools/lifecycle/checkpoint_state.py', 'governance/tools/lifecycle/early_approval.py', 'governance/tools/worktree.py', 'governance/tools/worktrees/guard.py',
    'governance/tools/worktrees/worktree_carry.py', 'governance/tools/worktrees/worktree_support.py', 'governance/tools/runtime_guard.py',
    'project-governance/config/artifact-classification-golden.json', 'project-governance/config/checkpoint-features.toml',
    'project-governance/config/checkpoint-policy.json', 'project-governance/config/checkpoint-profiles.json',
    'project-governance/ARTIFACT-CLASSIFICATION.md', 'project-governance/config/citations.json',
    'project-governance/config/devlog.json', 'project-governance/config/frozen-manifest.json',
    'project-governance/config/reconstruction-contract.json', 'project-governance/config/selector-expectations.json',
)
APPROVAL_MEDIUM = (
    'project-governance/CONSTRAINTS.md', 'project-governance/DECISIONS.md',
    'AGENTS.md', '.gitattributes', '.gitignore',
    'governance/ARTIFACT-CLASSIFICATION.md', 'governance/CHECKPOINT-TOOL.md', 'governance/CHECKPOINT-WORKFLOW.md',
    'governance/CONSTRAINTS.md', 'governance/DECISIONS.md', 'governance/LOCAL-COORDINATION.md',
    'governance/AGENT-ORIENTATION.md', 'governance/ROLE-COORDINATOR.md', 'governance/ROLE-WORKER.md',
    'governance/WORKTREE-WORKFLOW.md',
    'governance/config/artifact-classification-golden.json',
    'governance/tools/continuity/artifact_classification.py', 'governance/config/checkpoint-profiles.json',
    'governance/tools/lifecycle/commit_metadata.py', 'governance/tools/commit_metadata.py',
    'governance/tools/support/link_retarget.py', 'governance/config/reconstruction-contract-v1.json', 'governance/tools/lifecycle/review_render.py',
    'governance/tools/run_tests.py', 'governance/tests/run_tests.py', 'governance/tests/support/test_run_tests.py',
    'governance/tools/continuity/validate_continuity.py', 'governance/tools/validate_continuity.py',
    'governance/tests/continuity/test_artifact_classification.py', 'governance/tests/continuity/test_continuity.py',
    'governance/tests/lifecycle/test_approval_words.py', 'governance/config/conformance/test_artifact_classification.py',
    'governance/tests/lifecycle/test_checkpoint.py', 'governance/tests/lifecycle/test_checkpoint_abandon.py', 'governance/tests/lifecycle/test_checkpoint_deletion.py', 'governance/tests/lifecycle/test_checkpoint_evidence.py',
    'governance/tests/lifecycle/test_checkpoint_metadata.py', 'governance/tests/lifecycle/test_checkpoint_reconcile.py',
    'governance/config/conformance/test_checkpoint_scope.py', 'governance/tests/worktrees/test_checkpoint_worktrees.py', 'governance/config/conformance/test_continuity.py',
    'governance/tests/lifecycle/test_early_approval.py', 'governance/tests/support/test_link_retarget.py',
    'governance/tests/support/test_runtime_guard.py', 'governance/tests/lifecycle/test_checkpoint_interpreter_pin.py',
    'governance/tests/lifecycle/test_review_render.py', 'governance/tests/__init__.py',
    'governance/config/conformance/__init__.py', 'governance/tests/__init__.py',
)


class ApprovalSensitivityTest(unittest.TestCase):
    def test_every_sensitive_row_resolves_to_its_value(self):
        _, _, table = real_data()
        for paths, value in ((APPROVAL_HIGH, 'high'), (APPROVAL_MEDIUM, 'medium')):
            for path in paths:
                with self.subTest(path=path):
                    resolved = table.resolve(path)
                    self.assertIsNotNone(resolved, path)
                    self.assertEqual(resolved.approval_sensitivity, value)
                    self.assertEqual(resolved.values['Approval-sensitivity'], value)
                    self.assertEqual(table.ineligible_paths([path], 'low', table), [path])
                    self.assertEqual(table.ineligible_paths([path], 'medium', table), [path] if value == 'high' else [])

    def test_the_eligible_set_at_reach_low_is_the_set_of_hyphen_rows(self):
        # Zero change of behaviour of the rename: what was eligible when the cell was a hyphen still is, and what
        # was `yes` is `medium` or `high`, so it still is not. Row cells are read raw, not through the property.
        _, _, table = real_data()
        paths = [row.path for row in table.rows if row.kind == 'file'] + ['new/unknown.py', 'devlog/evil.sh']
        hyphen = {p for p in paths if table.resolve(p) is not None and table.resolve(p).profile != 'blocked'
                  and table.resolve(p).values['Approval-sensitivity'] == '-'}
        eligible = {p for p in paths if table.ineligible_paths([p], 'low', table) == []}
        self.assertEqual(eligible, hyphen)
        sensitive = {p for p in paths if table.resolve(p) is not None
                     and table.resolve(p).values['Approval-sensitivity'] in ('medium', 'high')}
        self.assertEqual(sensitive, {p for p in paths if table.resolve(p) is not None} - eligible
                         - {p for p in paths if table.resolve(p) is not None and table.resolve(p).profile == 'blocked'})
        self.assertGreaterEqual(len(sensitive), 50)
        for path in APPROVAL_HIGH + APPROVAL_MEDIUM:
            self.assertIn(path, sensitive)

    def test_the_high_list_is_exactly_the_approval_machinery(self):
        _, _, table = real_data()
        high = [row.path for row in table.rows
                if row.kind == 'file' and table.resolve(row.path).approval_sensitivity == 'high']
        self.assertEqual(sorted(high), sorted(APPROVAL_HIGH))

    def test_the_project_fallback_stays_high_and_keeps_its_scope(self):
        _, _, table = real_data()
        sentinel = 'project-governance/unclassified-sentinel'
        pin = json.loads((ROOT / rp.GOLDEN).read_text(encoding='ascii'))['paths'][sentinel]
        self.assertEqual({key: value for key, value in pin.items() if key != 'why'}, {
            'class': 'governance', 'profile': 'governance', 'boundary': [], 'bound': False,
            'invariants': ['classification', 'continuity', 'early-approval', 'selector'],
            'subclass': [], 'reachable': False, 'approval_sensitivity': 'high',
        })
        self.assertEqual(table.resolve(sentinel).row.path, 'project-governance/')
        resolved = table.resolve('project-governance/notes/anything.md')
        self.assertEqual(resolved.approval_sensitivity, 'high')
        self.assertEqual((resolved.klass, resolved.profile), ('governance', 'governance'))
        self.assertEqual(resolved.invariants, ['classification', 'continuity', 'early-approval', 'selector'])
        self.assertFalse(resolved.bound)
        self.assertFalse(resolved.reachable)
        self.assertEqual(resolved.boundary, [])
        self.assertEqual(resolved.subclasses, [])

    def test_ordinary_paths_are_low(self):
        _, _, table = real_data()
        for path in (rp.PROJECT_RATIONALE, rp.PROGRESS, 'docs/PLAN.md', 'governance/RATIONALE.md', rp.DEVLOG_INDEX,
                     'governance/tools/local_jobs.py', 'governance/tools/timing_record.py',
                     'governance/tools/coordination/local_jobs.py', 'governance/tools/support/timing_record.py'):
            with self.subTest(path=path):
                self.assertEqual(table.resolve(path).approval_sensitivity, 'low')
                self.assertEqual(table.ineligible_paths([path], 'low', table), [])
                self.assertEqual(table.ineligible_paths([path], 'medium', table), [])

    def test_a_mixed_manifest_is_refused_whole_at_reach_low(self):
        _, _, table = real_data()
        self.assertEqual(table.ineligible_paths([rp.PROGRESS, rp.CHECKPOINT], 'low', table),
                         [rp.CHECKPOINT])
        self.assertEqual(table.ineligible_paths([rp.PROGRESS, rp.CONSTRAINTS], 'low', table),
                         [rp.CONSTRAINTS])
        self.assertEqual(table.ineligible_paths([rp.PROGRESS, rp.CONSTRAINTS], 'medium', table), [])

    def test_an_unknown_reach_allows_nothing(self):
        _, _, table = real_data()
        for reach in ('off', 'high', 'LOW', '', None, True):
            with self.subTest(reach=reach):
                self.assertEqual(table.ineligible_paths([rp.PROGRESS], reach, table), [rp.PROGRESS])

    def test_a_path_with_no_row_and_a_blocked_path_fail_closed(self):
        _, _, table = real_data()
        for reach in ('low', 'medium'):
            self.assertEqual(table.ineligible_paths(['new.py', 'devlog/evil.sh'], reach, table),
                             ['devlog/evil.sh', 'new.py'])
        self.assertEqual(table.sensitive_paths(['new.py', rp.PROGRESS, rp.CONSTRAINTS], table),
                         {'new.py': 'high', rp.CONSTRAINTS: 'medium'})

    def test_the_cell_inherits_like_the_others_and_a_hyphen_clears_it(self):
        parsed = parse(table(full('d/', critical='medium'),
                             row('d/x.md', 'record', 'record-only', '', '', '', '', '', ''),
                             row('d/y.md', 'record', 'record-only', '', '', '', '', '', '-'),
                             row('d/z.md', 'record', 'record-only', '', '', '', '', '', 'high')))
        self.assertEqual(parsed.resolve('d/x.md').approval_sensitivity, 'medium')
        self.assertEqual(parsed.resolve('d/y.md').approval_sensitivity, 'low')
        self.assertEqual(parsed.resolve('d/z.md').approval_sensitivity, 'high')

    def test_directory_rows_must_fill_the_cell_and_no_other_value_parses(self):
        with self.assertRaisesRegex(ValueError, 'must fill every column'):
            parse(table(row('d/', 'record', 'record-only', '-', '-', '-', '-', '-', '')))
        for bad in ('no', 'yes', 'low', 'true', 'MEDIUM', 'High', 'maybe'):
            with self.subTest(bad), self.assertRaisesRegex(ValueError,
                                                           'Approval-sensitivity must be medium, high or a hyphen'):
                parse(table(full('d/', critical=bad)))

    def test_the_old_column_name_no_longer_parses(self):
        with self.assertRaisesRegex(ValueError, 'Exactly one classification table'):
            parse(table(full('a/'), header=ac.HEADER.replace('Approval-sensitivity', 'Approval-critical')))

    def test_an_unknown_value_reads_as_high(self):
        # The parser refuses an unknown cell, so this is the second line of defence for a value that bypassed it.
        resolved = parse(table(full('d/', critical='medium'))).resolve('d/x.md')
        for cell in ('yes', 'maybe', '', 'Medium', None):
            with self.subTest(cell=cell):
                resolved.values['Approval-sensitivity'] = cell
                self.assertEqual(resolved.approval_sensitivity, 'high')

    def test_the_sealed_view_carries_the_sensitivity(self):
        _, _, table = real_data()
        self.assertEqual(ac.view(table, rp.CHECKPOINT, table)['approval_sensitivity'], 'high')
        self.assertEqual(ac.view(table, rp.CONSTRAINTS, table)['approval_sensitivity'], 'medium')
        self.assertEqual(ac.view(table, rp.PROGRESS, table)['approval_sensitivity'], 'low')


class SensitivityRuleTest(unittest.TestCase):
    """A path counts as the higher of its row at HEAD and its row in the candidate's table, so no path is judged by a
    table its own transaction wrote."""
    ROWS = {
        'area/': full('area/', 'tooling', 'tooling', critical='medium'),
        'guarded/': full('guarded/', 'tooling', 'tooling', critical='high'),
        'open/': full('open/', 'tooling', 'tooling'),
        'open/bad.txt': full('open/bad.txt', 'tooling', 'blocked'),
        'open/low.txt': full('open/low.txt', 'tooling', 'tooling'),
        'open/mid.txt': full('open/mid.txt', 'tooling', 'tooling', critical='medium'),
        'open/top.txt': full('open/top.txt', 'tooling', 'tooling', critical='high'),
        'solo.txt': full('solo.txt', 'tooling', 'tooling', critical='medium'),
    }

    def build(self, replace=None, drop=()):
        rows = dict(self.ROWS)
        rows.update(replace or {})
        for path in drop:
            rows.pop(path, None)
        return parse(table(*(rows[path] for path in sorted(rows))))

    def setUp(self):
        self.head = self.build()

    def level(self, candidate, path):
        return candidate.sensitivity(path, self.head)

    def test_an_unchanged_table_grades_each_path_by_its_row(self):
        for path, expected in (('open/low.txt', 'low'), ('open/mid.txt', 'medium'), ('open/top.txt', 'high'),
                               ('area/new.txt', 'medium'), ('guarded/x.txt', 'high'), ('open/bad.txt', 'high'),
                               ('nowhere/x.txt', 'high')):
            with self.subTest(path=path):
                self.assertEqual(self.level(self.head, path)['level'], expected)

    def test_a_candidate_that_lowers_a_row_is_judged_by_the_head_row_at_every_reach(self):
        for path, was in (('open/top.txt', 'high'), ('open/mid.txt', 'medium')):
            candidate = self.build({path: full(path, 'tooling', 'tooling')})
            grades = self.level(candidate, path)
            self.assertEqual((grades['head'], grades['candidate'], grades['level']), (was, 'low', was))
            self.assertEqual(candidate.ineligible_paths([path], 'low', self.head), [path])
            self.assertEqual(candidate.ineligible_paths([path], 'medium', self.head), [path] if was == 'high' else [])

    def test_a_candidate_that_raises_a_row_is_judged_at_the_raised_level(self):
        medium = self.build({'open/low.txt': full('open/low.txt', 'tooling', 'tooling', critical='medium')})
        self.assertEqual(self.level(medium, 'open/low.txt')['level'], 'medium')
        self.assertEqual(medium.ineligible_paths(['open/low.txt'], 'low', self.head), ['open/low.txt'])
        self.assertEqual(medium.ineligible_paths(['open/low.txt'], 'medium', self.head), [])
        high = self.build({'open/low.txt': full('open/low.txt', 'tooling', 'tooling', critical='high')})
        for reach in ('low', 'medium'):
            self.assertEqual(high.ineligible_paths(['open/low.txt'], reach, self.head), ['open/low.txt'])

    def test_a_deleted_path_keeps_its_head_row(self):
        for path, expected in (('open/low.txt', 'low'), ('open/mid.txt', 'medium'), ('open/top.txt', 'high'),
                               ('solo.txt', 'medium')):
            with self.subTest(path=path):
                candidate = self.build(drop=(path,))
                self.assertEqual(self.level(candidate, path)['level'], expected)
                refused = {reach: candidate.ineligible_paths([path], reach, self.head) for reach in ('low', 'medium')}
                self.assertEqual(refused, {'low': [] if expected == 'low' else [path],
                                           'medium': [path] if expected == 'high' else []})
        self.assertEqual(self.level(self.build(drop=('solo.txt',)), 'solo.txt')['candidate'], 'none')

    def test_a_new_file_is_never_declared_lower_than_the_area_it_sits_in(self):
        for path, expected in (('area/new.txt', 'medium'), ('guarded/new.txt', 'high'), ('open/new.txt', 'low')):
            with self.subTest(path=path):
                candidate = self.build({path: full(path, 'tooling', 'tooling')})
                self.assertEqual(self.level(candidate, path)['level'], expected)
        raised = self.build({'open/new.txt': full('open/new.txt', 'tooling', 'tooling', critical='medium')})
        self.assertEqual(self.level(raised, 'open/new.txt')['level'], 'medium')

    def test_the_boundary_is_on_when_head_or_the_candidate_puts_the_path_on_it(self):
        head = self.build({'area/': full('area/', 'runtime', 'tooling', boundary='runtime', critical='medium')})
        without = self.build({'area/': full('area/', 'runtime', 'tooling', critical='medium')})
        for path in ('area/new.txt', 'area/x/deep.txt'):
            with self.subTest(path=path):
                self.assertTrue(c.Checkpoint.in_runtime(path, head, head))
                self.assertTrue(c.Checkpoint.in_runtime(path, without, head), 'a candidate cannot drop the cell')
                self.assertTrue(c.Checkpoint.in_runtime(path, head, without), 'a candidate can add the cell')
                self.assertFalse(c.Checkpoint.in_runtime(path, without, without))
        self.assertFalse(c.Checkpoint.in_runtime('open/low.txt', without, head))
        self.assertTrue(c.Checkpoint.in_runtime('area/new.txt', head), 'the candidate table alone still counts')
        self.assertFalse(c.Checkpoint.in_runtime('area/new.txt', without, None), 'no HEAD table: the candidate decides')

    def test_a_path_with_no_row_at_head_and_a_blocked_path_are_high(self):
        candidate = self.build({'fresh/x.txt': full('fresh/x.txt', 'tooling', 'tooling')})
        self.assertEqual(self.level(candidate, 'fresh/x.txt')['level'], 'high')
        unblocked = self.build({'open/bad.txt': full('open/bad.txt', 'tooling', 'tooling')})
        self.assertEqual(self.level(unblocked, 'open/bad.txt')['level'], 'high')
        blocked = self.build({'open/low.txt': full('open/low.txt', 'tooling', 'blocked')})
        self.assertEqual(self.level(blocked, 'open/low.txt')['level'], 'high')

    def test_an_unavailable_head_table_makes_every_path_high(self):
        for reach in ('low', 'medium'):
            self.assertEqual(self.head.ineligible_paths(['open/low.txt'], reach, None), ['open/low.txt'])
        self.assertEqual(self.head.sensitive_paths(['open/low.txt'], None), {'open/low.txt': 'high'})

    def test_the_sealed_view_records_both_rows_and_the_effective_level(self):
        candidate = self.build({'open/top.txt': full('open/top.txt', 'tooling', 'tooling')})
        sealed = ac.view(candidate, 'open/top.txt', self.head)
        self.assertEqual((sealed['approval_sensitivity'], sealed['head_sensitivity'], sealed['candidate_sensitivity']),
                         ('high', 'high', 'low'))
        self.assertIn('sensitivity-lowering-ignored', review_render.flags('open/top.txt', {
            'artifact_classification': {'open/top.txt': sealed}}))
        raised = ac.view(self.build({'open/low.txt': full('open/low.txt', 'tooling', 'tooling', critical='medium')}),
                         'open/low.txt', self.head)
        self.assertIn('sensitivity-raised', review_render.flags('open/low.txt', {
            'artifact_classification': {'open/low.txt': raised}}))

    def test_a_sealed_entry_without_the_head_sensitivity_is_refused(self):
        entry = {'resolved': True, 'approval_sensitivity': 'low', 'profile': 'tooling'}
        packet = {'manifest': ['open/low.txt'], 'runtime': False,
                  'profile_data': {'artifact_classification': {'open/low.txt': entry}}}
        self.assertTrue(ea.sealed_reasons(packet, 'medium'))
        entry['head_sensitivity'] = 'low'
        self.assertEqual(ea.sealed_reasons(packet, 'medium'), [])
        entry['head_sensitivity'] = 'high'
        self.assertTrue(ea.sealed_reasons(packet, 'medium'))

    def test_the_committed_reach_is_valid_and_the_real_table_grades_a_manifest_at_medium(self):
        data = tomllib.loads((ROOT / ea.FEATURES).read_text(encoding='utf-8'))
        self.assertIn(data[ea.KEY], (ea.OFF,) + ea.REACHES)
        _, _, real = real_data()
        ordinary = [rp.PROGRESS, rp.CONSTRAINTS, 'governance/tools/lifecycle/review_render.py']
        self.assertEqual(ea.reasons_not_eligible(real, real, ordinary, 'medium', 'fixture'), [])
        self.assertTrue(ea.reasons_not_eligible(real, real, ordinary, 'low', 'fixture'))
        for high in APPROVAL_HIGH:
            with self.subTest(high=high):
                self.assertTrue(ea.reasons_not_eligible(real, real, ordinary + [high], 'medium', 'fixture'))

    def test_the_committed_carry_switch_is_the_boolean_true_and_the_reader_accepts_only_that(self):
        data = tomllib.loads((ROOT / ea.FEATURES).read_text(encoding='utf-8'))
        self.assertIs(data[ea.CARRY_KEY], True)
        for value, on in ((True, True), (False, False), ('true', False), (1, False), (None, False)):
            with self.subTest(value=value), patch.object(ea, 'read_features', return_value=(
                    {} if value is None else {ea.CARRY_KEY: value}, 'read')):
                self.assertEqual(ea.carry_switch(None)[0], on)
        with patch.object(ea, 'read_features', return_value=(None, 'unreadable')):
            self.assertEqual(ea.carry_switch(None), (False, 'unreadable'))

    def test_a_runtime_boundary_manifest_is_graded_by_its_rows_like_any_other(self):
        real = real_table_with(rows=[
            '| `components/x/README.md` | runtime | tooling | runtime | - | - | - | - | - |',
            '| `docs/runtime-guide.md` | runtime | tooling | - | - | - | - | - | - |'])
        boundary = ['components/x/README.md', 'docs/runtime-guide.md']
        self.assertTrue(all(real.in_boundary(p, 'runtime') for p in boundary[:1]))
        for reach in ('low', 'medium'):
            self.assertEqual(ea.reasons_not_eligible(real, real, boundary, reach, 'fixture'), [], reach)
        for high in APPROVAL_HIGH:
            self.assertTrue(ea.reasons_not_eligible(real, real, boundary + [high], 'medium', 'fixture'))
        self.assertTrue(ea.reasons_not_eligible(real, real, boundary, 'off', 'fixture'))


class ShowClassificationTest(unittest.TestCase):
    def show(self, *paths, **flags):
        return sc.show(ROOT, list(paths), **flags)

    def test_the_effective_sensitivity_is_shown_beside_the_row(self):
        out = self.show(rp.CHECKPOINT, rp.PROGRESS, 'governance/tools/continuity/artifact_classification.py')
        lines = [l for l in out.splitlines() if l.startswith('  effective sensitivity')]
        self.assertEqual([l.split()[2] for l in lines], ['high', 'low', 'medium'])
        self.assertTrue(all('(HEAD and working rows agree)' in l for l in lines), lines)

    def test_the_effective_sensitivity_says_when_head_and_the_working_row_differ(self):
        probe = SensitivityRuleTest('test_an_unchanged_table_grades_each_path_by_its_row')
        head = probe.build()
        lowered = probe.build({'open/top.txt': full('open/top.txt', 'tooling', 'tooling')})
        self.assertEqual(sc.explain_sensitivity(lowered, head, 'open/top.txt'),
                         '  %-22s%-26s(%s)' % ('effective sensitivity', 'high', 'HEAD row high, working row low: the higher counts'))
        raised = probe.build({'open/low.txt': full('open/low.txt', 'tooling', 'tooling', critical='medium')})
        self.assertIn('medium', sc.explain_sensitivity(raised, head, 'open/low.txt'))
        self.assertIn('HEAD row low, working row medium', sc.explain_sensitivity(raised, head, 'open/low.txt'))
        self.assertIn('cannot be read', sc.explain_sensitivity(head, None, 'open/low.txt'))
        self.assertIn('high', sc.explain_sensitivity(head, None, 'open/low.txt').split('(')[0])
        fresh = probe.build({'fresh/x.txt': full('fresh/x.txt', 'tooling', 'tooling')})
        self.assertIn('no row covers it at HEAD', sc.explain_sensitivity(fresh, head, 'fresh/x.txt'))

    def test_an_unreadable_head_table_counts_every_path_as_high_in_the_listing(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(sc.head_table(folder, real_data()[1]))

    def test_resolves_and_labels_each_cell(self):
        out = self.show('devlog/new.md')
        self.assertIn('Effective classification of devlog/new.md', out)
        self.assertIn('record-only', out)
        self.assertIn('(row devlog/*.md)', out)
        self.assertNotIn('own row', out)

    def test_unknown_paths_fail_closed(self):
        out = self.show('devlog/evil.sh', 'new.py')
        self.assertEqual(out.count('no classification row covers this path'), 2)

    def test_verbose_prints_the_rows_as_written_with_line_numbers(self):
        out = self.show(rp.DEVLOG_INDEX, verbose=True)
        self.assertRegex(out, r'own row +line \d+: ')
        self.assertIn('parent row line ', out)
        self.assertIn('| `devlog/README.md` |', out)
        self.assertIn('| `devlog/*.md` |', out)

    def test_checks_explain_the_narrow_selection(self):
        record = self.show(rp.PROGRESS, checks=True)
        self.assertIn('from profile record-only: continuity', record)
        governance = self.show(rp.DECISIONS, checks=True)
        self.assertIn('from profile governance: continuity', governance)
        self.assertNotIn('governance-regressions', governance.split('checks')[1].split('from invariants')[0])
        table_edit = self.show(rp.TABLE, checks=True)
        self.assertIn('classification-tests', table_edit)
        self.assertIn('checkpoint-selector', table_edit)
        self.assertNotIn('checkpoint-invariants', table_edit)
        symbols = self.show(rp.CHECKPOINT, checks=True)
        self.assertIn('changed code symbols decide', symbols)
        blocked = self.show('devlog/evil.sh', checks=True)
        self.assertIn('blocked', blocked)

    def test_checks_agree_with_the_selector_for_mapped_paths(self):
        policy, profiles, table = real_data()
        tool = object.__new__(c.Checkpoint)
        tool.root = ROOT
        for path in (rp.PROGRESS, rp.DECISIONS, rp.TABLE,
                     'governance/config/conformance/test_continuity.py', 'governance/tools/worktree.py', rp.DEVLOG_INDEX):
            with self.subTest(path=path):
                # The path's own scope, whatever link-only retarget the policy admits for a document at the moment.
                with patch.object(tool, 'link_retargets', return_value={}):
                    selected = {x['id'] for x in tool.select_profile([path], policy, profiles, 'auto')[1]['checks']}
                lines = sc.explain_checks(table.resolve(path), policy, profiles)
                text = ' '.join(lines)
                explained = set()
                for line in lines:
                    if ': ' in line and ('from profile' in line or 'from invariants' in line):
                        explained.update(t.strip() for t in line.split(': ', 1)[1].split(',')
                                         if t.strip() not in ('none', 'nothing more', ''))
                self.assertEqual(explained, selected, text)

    def test_it_runs_standalone_from_any_directory_without_the_checkpoint_module(self):
        script = ROOT / 'governance/tools/show_classification.py'
        with tempfile.TemporaryDirectory() as elsewhere:
            result = subprocess.run([sys.executable, '-B', str(script), rp.PROGRESS, '--checks'],
                                    cwd=elsewhere, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Effective classification of docs/PROGRESS.md', result.stdout)
        self.assertIn('from profile record-only: continuity', result.stdout)
        probe = ('import sys; sys.path[:0] = [%r, %r]; from governance.tools.continuity import show_classification; '
                 'print(sorted(m for m in ("checkpoint", "governance.tools.lifecycle.commit_metadata") if m in sys.modules))') % (str(ROOT), str(ROOT / 'governance' / 'tools'))
        loaded = subprocess.run([sys.executable, '-B', '-c', probe], capture_output=True, text=True)
        self.assertEqual(loaded.stdout.strip(), '[]', 'show_classification must not depend on the checkpoint module')

    def test_paths_are_accepted_in_either_slash_style_and_absolute_inside_the_repository(self):
        expected = self.show(rp.CHECKPOINT)
        self.assertEqual(self.show('governance' + chr(92) + 'tools' + chr(92) + 'checkpoint.py'), expected)
        self.assertEqual(self.show('./governance/tools/checkpoint.py'), expected)
        self.assertEqual(self.show(str(ROOT / 'governance' / 'tools' / 'checkpoint.py')), expected)
        with self.assertRaisesRegex(ValueError, 'outside the repository'):
            self.show(str(Path(tempfile.gettempdir()) / 'elsewhere.txt'))

    def test_the_cli_reports_failure_without_a_traceback(self):
        script = ROOT / 'governance/tools/show_classification.py'
        clean = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}  # the guard would name a host's GIT_* first
        bad = subprocess.run([sys.executable, '-B', str(script), 'x', '--repo', str(Path(tempfile.gettempdir()))],
                             capture_output=True, text=True, env=clean)
        self.assertEqual(bad.returncode, 1)
        self.assertTrue(bad.stderr.startswith('FAIL: '))
        self.assertNotIn('Traceback', bad.stderr)

    def test_the_command_is_read_only_and_needs_no_transaction(self):
        before = subprocess.check_output(['git', '--no-optional-locks', '-C', str(ROOT), 'status', '--porcelain']).decode()
        self.assertIn('Effective classification', self.show('AGENTS.md'))
        after = subprocess.check_output(['git', '--no-optional-locks', '-C', str(ROOT), 'status', '--porcelain']).decode()
        self.assertEqual(before, after)


class SealedClassificationTest(unittest.TestCase):
    def setUp(self):
        self.policy, self.profiles, self.table = real_data()
        self.tool = object.__new__(c.Checkpoint)
        self.tool.root = ROOT

    def selected(self, manifest):
        return self.tool.select_profile(manifest, self.policy, self.profiles, 'auto')[1]

    def test_prepare_seals_the_resolved_values_of_every_manifest_path(self):
        manifest = [rp.PROGRESS, rp.DECISIONS, 'devlog/new.md']
        sealed = self.tool.attach_classification(manifest, self.policy, self.profiles, self.selected(manifest))
        view = sealed['artifact_classification']
        self.assertEqual(set(view), set(manifest))
        self.assertEqual(view[rp.PROGRESS]['class'], 'record')
        self.assertEqual(view[rp.PROGRESS]['subclass'], ['progress'])
        self.assertTrue(view[rp.DECISIONS]['bound'])
        self.assertEqual(view['devlog/new.md']['subclass'], ['devlog'])

    FIXTURE_PROFILE = 'fixture-offline'

    def refuse_a_tampered_project_table(self, project_profile=None, project_row=None, source=ROOT):
        """Run the tampered-table refusal in a temporary root; the optional arguments give the root a project profile
        and a project row, as an adopting project has them. The root starts as a copy of `source`, so every profile the
        copied table names is kept: the fixture profile is merged into the source's own project profiles file, under a
        name that file does not use, and `{profile}` in the row stands for that name."""
        manifest = [rp.PROGRESS]
        profile = self.selected(manifest)
        with tempfile.TemporaryDirectory() as temp:
            # the framework's table and profiles as they are, the project's table tampered with, and the project's profiles when it has any
            names = [c.TABLE, ac.PROFILES, ac.PROJECT_TABLE]
            if (source / ac.PROJECT_PROFILES).is_file():
                names.append(ac.PROJECT_PROFILES)
            for name in names:
                (Path(temp) / name).parent.mkdir(parents=True, exist_ok=True)
                (Path(temp) / name).write_text((source / name).read_text(encoding='ascii'), encoding='ascii', newline='\n')
            text = (source / ac.PROJECT_TABLE).read_text(encoding='ascii')
            tampered = text.replace('| `docs/PROGRESS.md` | record | record-only |', '| `docs/PROGRESS.md` | record | tooling |')
            self.assertNotEqual(text, tampered)
            if project_profile is not None:
                target = Path(temp) / ac.PROJECT_PROFILES
                real = json.loads(target.read_bytes()) if target.is_file() else {
                    'version': json.loads((source / ac.PROFILES).read_bytes())['version'], 'profiles': {}}
                name = self.FIXTURE_PROFILE
                while name in real['profiles']:
                    name += '-x'
                real['profiles'][name] = project_profile
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(real), encoding='ascii', newline='\n')
            if project_row:
                tampered = self.insert_row(tampered, project_row.replace('{profile}', name))
            (Path(temp) / ac.PROJECT_TABLE).write_text(tampered, encoding='ascii', newline='\n')
            self.tool.root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'disagrees with the checkpoint selection'):
                self.tool.attach_classification(manifest, self.policy, self.profiles, profile)

    @staticmethod
    def insert_row(text, row):
        """Put `row` into the first table of `text`, at its place in the ASCII order of the paths, whatever rows the table has."""
        lines = text.split('\n')
        start = lines.index(ac.SEPARATOR) + 1
        end = start
        while end < len(lines) and lines[end].startswith('| '):
            end += 1
        path = row.split('|')[1].strip()
        at = end
        for index in range(start, end):
            if lines[index].split('|')[1].strip() > path:
                at = index
                break
        return '\n'.join(lines[:at] + [row] + lines[at:])

    def test_disagreement_with_the_policy_refuses_preparation(self):
        self.refuse_a_tampered_project_table()

    def test_disagreement_is_refused_in_a_root_with_a_project_profile_and_a_row_naming_it(self):
        self.refuse_a_tampered_project_table(
            ProjectProfileTest.PROJECT_PROFILE, '| `notes.md` | record | {profile} | - | - | - | - | - | - |')

    def test_disagreement_is_refused_where_the_project_table_already_names_its_own_profile(self):
        """An adopting project's table names profiles of its own, and its other rows are not the template's."""
        framework = json.loads((ROOT / ac.PROFILES).read_bytes())
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp)
            for name in (c.TABLE, ac.PROFILES):
                (source / name).parent.mkdir(parents=True, exist_ok=True)
                (source / name).write_bytes((ROOT / name).read_bytes())
            rows = ['| `docs/PROGRESS.md` | record | record-only | - | yes | - | progress | yes | - |',
                    '| `zeta/*.txt` | record | other-offline | - | - | - | - | - | - |']
            files = {ac.PROJECT_PROFILES: json.dumps({'version': framework['version'],
                                                      'profiles': {'other-offline': ProjectProfileTest.PROJECT_PROFILE}}),
                     ac.PROJECT_TABLE: '\n'.join(['# Project table', '', ac.HEADER, ac.SEPARATOR] + rows) + '\n'}
            for name, text in files.items():
                (source / name).parent.mkdir(parents=True, exist_ok=True)
                (source / name).write_text(text, encoding='ascii', newline='\n')
            self.refuse_a_tampered_project_table(
                ProjectProfileTest.PROJECT_PROFILE, '| `notes.md` | record | {profile} | - | - | - | - | - | - |', source=source)

    def test_every_anchor_exists_and_the_table_must_exist_at_its_anchor(self):
        for anchor in (ac.POLICY, ac.PROFILES, ac.TABLE, ac.CONTRACT):
            self.assertTrue((ROOT / anchor).is_file(), anchor)
        self.assertEqual(c.TABLE, ac.TABLE)
        with tempfile.TemporaryDirectory() as temp:
            self.tool.root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'table is missing'):
                self.tool.attach_classification([rp.PROGRESS], self.policy, self.profiles, {})

    def test_the_table_is_sealed_as_bound_content(self):
        self.assertIn(rp.TABLE, self.table.bound_paths())
        self.assertIn(rp.TABLE, self.tool.bound_paths(self.policy))

    def test_the_resolved_view_reaches_the_presentation(self):
        manifest = [rp.DECISIONS]
        sealed = self.tool.attach_classification(manifest, self.policy, self.profiles, self.selected(manifest))
        self.assertEqual(sealed['artifact_classification'][rp.DECISIONS]['reachable'], True)
        self.assertEqual(sealed['artifact_classification'][rp.DECISIONS]['subclass'], ['decisions'])


if __name__ == '__main__':
    unittest.main()
