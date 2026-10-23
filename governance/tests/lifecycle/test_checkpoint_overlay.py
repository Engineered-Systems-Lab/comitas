"""The project's optional governance files at the checkpoint tool's readers: selection, the sealed packet and the table at HEAD.

Every mutation happens in a disposable Git repository; the real repository is only read. The fixture is a tiny framework
(one policy, one profile file, one table) with a project folder beside it, so the union is exercised end to end.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
import checkpoint as c

FRAMEWORK_ROWS = [
    ('a.txt', 'tooling', 'test', '-', '-'),
    ('governance/tools/x.py', 'tooling', 'test', '-', 'high'),
    ('project-governance/', 'governance', 'test', '-', 'high'),
]
PROJECT_ROWS = [
    ('notes/', 'record', 'project-docs', '-', '-'),
    ('notes/guarded.md', 'record', 'project-docs', '-', 'high'),
]


def table_text(rows):
    lines = ['# Fixture classification', '', ac.HEADER, ac.SEPARATOR]
    lines += ['| `%s` | %s | %s | %s | - | - | - | - | %s |' % row for row in sorted(rows)]
    return '\n'.join(lines) + '\n'


POLICY = {'version': 4, 'prohibited_components': ['.git', 'tmp', '__pycache__'], 'required_checks': ['continuity'],
          'commands': {'continuity': ['{python}', '-B', '-c', 'print("PASS")'],
                       'regressions': ['{python}', '-B', '-c', 'print("tests PASS")']}}
PROFILES = {'version': 4, 'profiles': {'test': {
    'checks': [{'id': name, 'argv': argv} for name, argv in POLICY['commands'].items()],
    'limitations': 'Disposable fixture commands only.'}},
    'impact': {'version': 1, 'invariants': {}, 'symbol_maps': {}},
    'commit_metadata': {'version': 2, 'classes': ['record', 'governance', 'tooling', 'runtime'], 'subclasses': []}}
PROJECT_POLICY = {'version': 4, 'commands': {'project-docs': ['{python}', '-B', '-c', 'print("project docs PASS")']}}
PROJECT_PROFILES = {'version': 4, 'profiles': {'project-docs': {
    'checks': [{'id': 'continuity', 'argv': POLICY['commands']['continuity']},
               {'id': 'project-docs', 'argv': PROJECT_POLICY['commands']['project-docs']}],
    'limitations': 'Project documents only.'}}}


class OverlayCheckpointTest(unittest.TestCase):
    PROJECT_FILES = True

    def setUp(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='checkpoint-overlay-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        for args in (('init', '-b', 'main'), ('config', 'user.name', 'Isolated Test'),
                     ('config', 'user.email', 'test@example.invalid'), ('config', 'core.autocrlf', 'false')):
            self.git(*args)
        self.write(c.TABLE, table_text(FRAMEWORK_ROWS))
        self.write(c.POLICY, json.dumps(POLICY))
        self.write(c.PROFILES, json.dumps(PROFILES))
        if self.PROJECT_FILES:
            self.write(c.PROJECT_TABLE, table_text(PROJECT_ROWS))
            self.write(c.PROJECT_POLICY, json.dumps(PROJECT_POLICY))
            self.write(c.PROJECT_PROFILES, json.dumps(PROJECT_PROFILES))
        self.write('a.txt', 'old\n')
        self.write('notes/n.md', 'old\n')
        self.write('notes/guarded.md', 'old\n')
        self.write('.gitignore', 'tmp/\n__pycache__/\n')
        self.git('add', '--', '.')
        self.git('commit', '-m', 'Initial fixture')
        self.parent = self.git('rev-parse', 'HEAD').strip()
        self.message = Path(self.temp.name) / 'message.txt'
        self.message.write_bytes(b'Exact subject\n\n## Topic\n- Make an edit.\n')
        self.tool = c.Checkpoint(self.root)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode()

    def write(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def prepare(self, manifest):
        return self.tool.prepare(manifest, self.message, 'auto', self.parent, 'refs/heads/main',
                                 'Lead developer delegated this exact fixture scope')

    def selection(self, manifest):
        policy, profiles = self.tool.data()
        return self.tool.derive_selection(manifest, 'subject', policy, profiles, None, 'auto', 'refs/heads/main')


class WithProjectFilesTest(OverlayCheckpointTest):
    def test_the_data_is_the_framework_plus_the_project(self):
        policy, profiles = self.tool.data()
        self.assertIn('project-docs', policy['commands'])
        self.assertIn('project-docs', profiles['profiles'])
        self.assertIn('test', profiles['profiles'])
        self.assertEqual(set(self.tool.table(policy, profiles).exact_paths()), {'a.txt', 'governance/tools/x.py', 'notes/guarded.md'})

    def test_selection_sees_the_project_row_the_project_profile_and_the_project_command(self):
        self.write('notes/n.md', 'new\n')
        name, profile, runtime = self.selection(['notes/n.md'])
        self.assertEqual(name, 'project-docs')
        self.assertEqual(sorted(check['id'] for check in profile['checks']), ['continuity', 'project-docs'])
        self.assertEqual(profile['classification'], {'notes/n.md': 'record'})
        self.assertFalse(runtime)
        view = profile['artifact_classification']['notes/n.md']
        self.assertEqual((view['profile'], view['row']), ('project-docs', 'notes/'))

    def test_a_prepare_runs_the_project_check_and_seals_the_merged_policy(self):
        self.write('notes/n.md', 'new\n')
        packet = self.prepare(['notes/n.md'])
        self.assertEqual(packet['profile'], 'project-docs')
        self.assertEqual(sorted(e['id'] for e in packet['evidence'] if e['id'] != 'staged-whitespace'),
                         ['continuity', 'project-docs'])
        self.assertTrue(all(e['exit_code'] == 0 for e in packet['evidence']))
        self.assertEqual(packet['policy']['commands']['project-docs'], PROJECT_POLICY['commands']['project-docs'])

    def test_the_packet_binds_each_project_file_by_its_hash(self):
        self.write('a.txt', 'new\n')
        packet = self.prepare(['a.txt'])
        for name in ac.OVERLAY_FILES:
            with self.subTest(name=name):
                self.assertEqual(packet['working'][name], c.digest((self.root / name).read_bytes()))

    def test_a_change_to_a_bound_project_file_after_the_seal_is_stale(self):
        self.write('a.txt', 'new\n')
        packet = self.prepare(['a.txt'])
        self.tool.assert_prepared(packet)
        for name, text in ((c.PROJECT_TABLE, table_text(PROJECT_ROWS) + '\n'),
                           (c.PROJECT_POLICY, json.dumps(PROJECT_POLICY) + '\n'),
                           (c.PROJECT_PROFILES, json.dumps(PROJECT_PROFILES) + '\n')):
            with self.subTest(name=name):
                before = (self.root / name).read_bytes()
                self.write(name, text)
                with self.assertRaises(ValueError):
                    self.tool.assert_prepared(packet)
                (self.root / name).write_bytes(before)
                self.tool.assert_prepared(packet)

    def test_a_project_command_changed_after_the_seal_makes_the_selection_stale(self):
        self.write('notes/n.md', 'new\n')
        packet = self.prepare(['notes/n.md'])
        self.tool.assert_selection_current(packet)
        changed = {'version': 4, 'commands': {'project-docs': ['{python}', '-B', '-c', 'print("changed")']}}
        self.write(c.PROJECT_POLICY, json.dumps(changed))
        profiles = json.loads(json.dumps(PROJECT_PROFILES))
        profiles['profiles']['project-docs']['checks'][1]['argv'] = changed['commands']['project-docs']
        self.write(c.PROJECT_PROFILES, json.dumps(profiles))
        with self.assertRaisesRegex(ValueError, 'rules changed since preparation'):
            self.tool.assert_selection_current(packet)

    def test_the_table_at_head_is_the_union_read_at_head(self):
        policy, profiles = self.tool.data()
        head = self.tool.head_table(profiles)
        self.assertEqual(head.resolve('notes/n.md').profile, 'project-docs')
        self.assertEqual(head.resolve('a.txt').profile, 'test')
        self.assertEqual({r.path for r in head.rows}, {r.path for r in self.tool.table(policy, profiles).rows})

    def test_a_project_row_lowered_in_the_candidates_own_table_still_counts_at_its_head_level(self):
        self.write(c.PROJECT_TABLE, table_text([row for row in PROJECT_ROWS if row[0] != 'notes/guarded.md'] +
                                               [('notes/guarded.md', 'record', 'project-docs', '-', '-')]))
        policy, profiles = self.tool.data()
        table, head = self.tool.table(policy, profiles), self.tool.head_table(profiles)
        grades = table.sensitivity('notes/guarded.md', head)
        self.assertEqual((grades['head'], grades['candidate'], grades['level']), ('high', 'low', 'high'))
        self.assertEqual(table.ineligible_paths(['notes/guarded.md'], 'medium', head), ['notes/guarded.md'])

    def test_a_project_row_added_by_the_candidate_has_no_row_at_head_and_counts_as_high(self):
        self.write(c.PROJECT_TABLE, table_text(PROJECT_ROWS + [('other/', 'record', 'project-docs', '-', '-')]))
        self.write('other/o.md', 'new\n')
        policy, profiles = self.tool.data()
        table, head = self.tool.table(policy, profiles), self.tool.head_table(profiles)
        self.assertEqual(table.resolve('other/o.md').approval_sensitivity, 'low')
        self.assertEqual(table.sensitivity('other/o.md', head)['level'], 'high')

    def test_the_project_table_cannot_lower_or_replace_a_framework_path(self):
        for row in (('governance/tools/x.py', 'tooling', 'test', '-', '-'), ('a.txt', 'tooling', 'test', '-', 'high'),
                    ('project-governance/config/', 'tooling', 'test', '-', '-')):
            with self.subTest(path=row[0]):
                self.write(c.PROJECT_TABLE, table_text(PROJECT_ROWS + [row]))
                policy, profiles = self.tool.data()
                with self.assertRaises(ValueError):
                    self.tool.table(policy, profiles)
        self.write(c.PROJECT_TABLE, table_text(PROJECT_ROWS))

    def test_a_project_file_cannot_set_what_the_framework_alone_decides(self):
        self.write(c.PROJECT_POLICY, json.dumps(dict(PROJECT_POLICY, commit_metadata_required=False, timing_record={})))
        with self.assertRaisesRegex(ValueError, 'may not set'):
            self.tool.data()
        self.write(c.PROJECT_POLICY, json.dumps(dict(PROJECT_POLICY, commands={'continuity': ['x']})))
        with self.assertRaisesRegex(ValueError, 'clash with the framework'):
            self.tool.data()
        self.write(c.PROJECT_POLICY, json.dumps(PROJECT_POLICY))
        self.write(c.PROJECT_PROFILES, json.dumps({'version': 4, 'profiles': {'test': PROFILES['profiles']['test']}}))
        with self.assertRaisesRegex(ValueError, 'clash with the framework'):
            self.tool.data()

    def test_the_head_reader_says_none_for_a_file_head_does_not_hold(self):
        self.assertIsNone(self.tool.head_text('project-governance/nothing.json'))
        self.assertEqual(self.tool.head_text('a.txt'), 'old\n')

    def test_the_framework_table_cannot_name_a_project_profile(self):
        self.write(c.TABLE, table_text(FRAMEWORK_ROWS + [('b.txt', 'tooling', 'project-docs', '-', '-')]))
        policy, profiles = self.tool.data()
        with self.assertRaisesRegex(ValueError, "unknown profile 'project-docs'"):
            self.tool.table(policy, profiles)


class WithoutProjectFilesTest(OverlayCheckpointTest):
    """Absent project files are exactly the behaviour before the overlay existed."""
    PROJECT_FILES = False

    def test_the_data_is_the_framework_files_exactly(self):
        policy, profiles = self.tool.data()
        self.assertEqual((policy, profiles), (POLICY, PROFILES))

    def test_the_table_is_the_framework_table_and_head_agrees(self):
        policy, profiles = self.tool.data()
        table, head = self.tool.table(policy, profiles), self.tool.head_table(profiles)
        self.assertEqual([r.path for r in table.rows], [r.path for r in head.rows])
        self.assertEqual([r.path for r in table.rows], sorted(r[0] for r in FRAMEWORK_ROWS))
        self.assertTrue(all(r.source is None for r in table.rows))

    def test_the_packet_binds_the_absent_project_files_as_absent(self):
        self.write('a.txt', 'new\n')
        packet = self.prepare(['a.txt'])
        for name in ac.OVERLAY_FILES:
            self.assertIn(name, packet['working'])
            self.assertIsNone(packet['working'][name])
        self.assertEqual(packet['profile'], 'test')
        self.assertEqual(packet['policy'], POLICY)
        self.tool.assert_prepared(packet)

    def test_a_project_file_that_appears_after_the_seal_is_stale(self):
        self.write('a.txt', 'new\n')
        packet = self.prepare(['a.txt'])
        self.write(c.PROJECT_POLICY, json.dumps(PROJECT_POLICY))
        with self.assertRaises(ValueError):
            self.tool.assert_prepared(packet)


if __name__ == '__main__':
    unittest.main()
