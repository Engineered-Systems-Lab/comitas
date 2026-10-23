"""The overlay of the project's optional governance files on the framework's: the union and the clash rules."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from governance.tools.continuity import artifact_classification as ac
from governance.tools.continuity import show_classification as sc
from governance.tools.continuity import validate_continuity as v
from governance.tools.support import timing_record
import repo_paths as rp

PROJECT_ROW = '| `{path}` | record | record-only | - | - | - | - | - | - |\n'
PROJECT_PROFILE = {'limitations': 'Project docs only.',
                   'checks': [{'id': 'continuity', 'argv': ['{python}', '-B', 'governance/tools/validate_continuity.py']}]}


def project_table(*rows, profile='record-only'):
    lines = [ac.HEADER, ac.SEPARATOR]
    for path in rows:
        lines.append(PROJECT_ROW.format(path=path).replace('record-only', profile).rstrip('\n'))
    return '# Project table\n\n' + '\n'.join(lines) + '\n'


class OverlayTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for relative in (ac.TABLE, ac.PROFILES, ac.POLICY):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rp.ROOT / relative, target)
        self.framework_profiles = json.loads((self.root / ac.PROFILES).read_text(encoding='utf-8'))
        self.framework_policy = json.loads((self.root / ac.POLICY).read_text(encoding='utf-8'))

    def write(self, relative, text):
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding='utf-8', newline='\n')

    def write_json(self, relative, data):
        self.write(relative, json.dumps(data))

    def load(self):
        return ac.load_table(self.root, self.framework_profiles)

    def test_no_project_files_gives_exactly_the_framework(self):
        text = (self.root / ac.TABLE).read_text(encoding='utf-8')
        alone = ac.parse_table(text, self.framework_profiles)
        loaded = self.load()
        self.assertEqual([(r.path, r.cells) for r in loaded.rows], [(r.path, r.cells) for r in alone.rows])
        self.assertEqual(loaded.historical, alone.historical)
        self.assertEqual(ac.load_profiles(self.root), self.framework_profiles)
        self.assertEqual(ac.load_policy(self.root), self.framework_policy)

    def test_the_framework_classifies_the_project_folder_as_high(self):
        resolved = self.load().resolve(ac.PROJECT_TABLE)
        self.assertEqual(resolved.approval_sensitivity, 'high')
        self.assertEqual(self.load().resolve(ac.PROJECT_POLICY).approval_sensitivity, 'high')

    def test_a_project_row_for_an_ordinary_path_is_added_to_the_union(self):
        self.write(ac.PROJECT_TABLE, project_table('notes/', 'notes/a.md'))
        loaded = self.load()
        framework_paths = {r.path for r in ac.parse_table((self.root / ac.TABLE).read_text(encoding='utf-8'),
                                                          self.framework_profiles).rows}
        self.assertEqual({r.path for r in loaded.rows}, framework_paths | {'notes/', 'notes/a.md'})
        self.assertEqual(loaded.resolve('notes/a.md').profile, 'record-only')
        self.assertEqual(loaded.by_path['notes/a.md'].source, ac.PROJECT_TABLE)
        self.assertIsNone(loaded.by_path['AGENTS.md'].source)
        self.assertEqual(loaded.resolve('AGENTS.md').approval_sensitivity, 'medium')
        paths = [r.path for r in loaded.rows]
        self.assertEqual(paths, sorted(paths))

    def test_a_project_row_under_the_framework_folder_is_refused(self):
        for path in ('governance/extra.md', 'governance/tools/', 'governance'):
            with self.subTest(path=path):
                self.write(ac.PROJECT_TABLE, project_table(path))
                with self.assertRaisesRegex(ValueError, 'may not classify'):
                    self.load()

    def test_a_project_row_under_the_project_folder_is_refused(self):
        for path in ('project-governance/extra.md', 'project-governance/config/', 'project-governance'):
            with self.subTest(path=path):
                self.write(ac.PROJECT_TABLE, project_table(path))
                with self.assertRaisesRegex(ValueError, 'may not classify'):
                    self.load()

    def test_a_project_row_that_repeats_a_framework_row_is_refused(self):
        self.write(ac.PROJECT_TABLE, project_table('AGENTS.md'))
        with self.assertRaisesRegex(ValueError, 'Duplicate classification path'):
            self.load()

    def test_a_project_table_needs_the_high_row_in_the_framework_table(self):
        text = (self.root / ac.TABLE).read_text(encoding='utf-8')
        lines = [line for line in text.split('\n') if not line.startswith('| `project-governance/`')]
        self.write(ac.TABLE, '\n'.join(lines))
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md'))
        with self.assertRaisesRegex(ValueError, 'approval-sensitivity high'):
            self.load()

    def test_a_project_profile_with_a_framework_profile_name_is_refused(self):
        self.write_json(ac.PROJECT_PROFILES, {'version': self.framework_profiles['version'],
                                              'profiles': {'tooling': PROJECT_PROFILE}})
        with self.assertRaisesRegex(ValueError, 'clash with the framework: tooling'):
            ac.load_profiles(self.root)
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md'))
        with self.assertRaisesRegex(ValueError, 'clash with the framework: tooling'):
            self.load()

    def test_a_project_profile_with_a_new_name_is_added(self):
        self.write_json(ac.PROJECT_PROFILES, {'version': self.framework_profiles['version'],
                                              'profiles': {'project-docs': PROJECT_PROFILE}})
        merged = ac.load_profiles(self.root)
        self.assertEqual(set(merged['profiles']), set(self.framework_profiles['profiles']) | {'project-docs'})
        self.assertEqual(merged['profiles']['project-docs'], PROJECT_PROFILE)
        self.assertEqual(merged['impact'], self.framework_profiles['impact'])
        # The loader returns a new object and leaves the framework's untouched.
        self.assertNotIn('project-docs', self.framework_profiles['profiles'])
        # The project's table may name it, and the framework's table may not.
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md', profile='project-docs'))
        self.assertEqual(self.load().resolve('notes/a.md').profile, 'project-docs')
        with self.assertRaisesRegex(ValueError, "unknown profile 'project-docs'"):
            ac.parse_table((self.root / ac.TABLE).read_text(encoding='utf-8').replace(
                '| `.gitattributes` | tooling | tooling', '| `.gitattributes` | tooling | project-docs'), self.framework_profiles)

    def test_a_project_profiles_file_may_add_nothing_but_profiles_and_impact_invariants(self):
        version = self.framework_profiles['version']
        self.write_json(ac.PROJECT_PROFILES, {'version': version, 'commit_metadata': {}})
        with self.assertRaisesRegex(ValueError, 'may not set commit_metadata'):
            ac.load_profiles(self.root)
        for key in ('version', 'symbol_maps'):
            self.write_json(ac.PROJECT_PROFILES, {'version': version, 'impact': {key: 1}})
            with self.assertRaisesRegex(ValueError, 'may add only impact.invariants'):
                ac.load_profiles(self.root)

    def test_a_project_impact_invariant_is_added_and_a_framework_one_is_never_shadowed(self):
        version = self.framework_profiles['version']
        self.write_json(ac.PROJECT_PROFILES, {'version': version, 'impact': {'invariants': {
            'project-only': {'groups': [], 'requires': ['continuity']}}}})
        merged = ac.load_profiles(self.root)
        self.assertEqual(set(merged['impact']['invariants']),
                         set(self.framework_profiles['impact']['invariants']) | {'project-only'})
        self.assertEqual(merged['impact']['symbol_maps'], self.framework_profiles['impact']['symbol_maps'])
        self.assertNotIn('project-only', self.framework_profiles['impact']['invariants'])
        self.write_json(ac.PROJECT_PROFILES, {'version': version, 'impact': {'invariants': {'continuity': {'groups': []}}}})
        with self.assertRaisesRegex(ValueError, 'impact invariants clash with the framework: continuity'):
            ac.load_profiles(self.root)

    def test_the_project_policy_adds_commands_and_never_replaces_one(self):
        version = self.framework_policy['version']
        self.write_json(ac.PROJECT_POLICY, {'version': version, 'commands': {'project-docs': ['{python}', '-B', 'x.py']},
                                            'required_checks': ['project-docs']})
        merged = ac.load_policy(self.root)
        self.assertEqual(merged['commands']['project-docs'], ['{python}', '-B', 'x.py'])
        self.assertEqual(merged['required_checks'], self.framework_policy['required_checks'] + ['project-docs'])
        self.assertEqual(merged['prohibited_components'], self.framework_policy['prohibited_components'])
        self.write_json(ac.PROJECT_POLICY, {'version': version, 'commands': {'continuity': ['x']}})
        with self.assertRaisesRegex(ValueError, 'clash with the framework: continuity'):
            ac.load_policy(self.root)
        self.write_json(ac.PROJECT_POLICY, {'version': version, 'timing_record': {}})
        with self.assertRaisesRegex(ValueError, 'may not set timing_record'):
            ac.load_policy(self.root)

    def test_the_merged_profiles_are_accepted_by_the_loader_and_the_framework_table_stays_strict(self):
        self.write_json(ac.PROJECT_PROFILES, {'version': self.framework_profiles['version'],
                                              'profiles': {'project-docs': PROJECT_PROFILE}})
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md', profile='project-docs'))
        merged = ac.load_profiles(self.root)
        self.assertEqual(ac.load_table(self.root, merged).resolve('notes/a.md').profile, 'project-docs')
        self.assertEqual([r.path for r in ac.load_table(self.root, merged).rows],
                         [r.path for r in ac.load_table(self.root, self.framework_profiles).rows])
        self.write(ac.TABLE, (self.root / ac.TABLE).read_text(encoding='utf-8').replace(
            '| `.gitattributes` | tooling | tooling', '| `.gitattributes` | tooling | project-docs'))
        with self.assertRaisesRegex(ValueError, "unknown profile 'project-docs'"):
            ac.load_table(self.root, merged)

    def test_the_tables_read_through_a_reader_are_the_tables_read_from_the_tree(self):
        self.write_json(ac.PROJECT_PROFILES, {'version': self.framework_profiles['version'],
                                              'profiles': {'project-docs': PROJECT_PROFILE}})
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md', profile='project-docs'))
        merged = ac.load_profiles(self.root)
        files = {name: (self.root / name).read_text(encoding='utf-8')
                 for name in (ac.TABLE, ac.PROJECT_TABLE, ac.POLICY, ac.PROFILES, ac.PROJECT_PROFILES)}
        files.pop(ac.POLICY)
        read = files.get
        self.assertEqual([(r.path, r.cells, r.source) for r in ac.table_from(read, merged).rows],
                         [(r.path, r.cells, r.source) for r in ac.load_table(self.root, merged).rows])
        self.assertEqual(ac.profiles_from(read), merged)
        files.pop(ac.PROJECT_TABLE)
        self.assertNotIn('notes/a.md', ac.table_from(read, merged).by_path)
        files.pop(ac.TABLE)
        with self.assertRaisesRegex(ValueError, 'table is missing'):
            ac.table_from(read, merged)

    def test_the_overlay_files_are_the_three_the_approvals_bind(self):
        self.assertEqual(ac.OVERLAY_FILES, (ac.PROJECT_TABLE, ac.PROJECT_POLICY, ac.PROJECT_PROFILES))
        for name in ac.OVERLAY_FILES:
            self.assertEqual(self.load().resolve(name).approval_sensitivity, 'high')

    def test_the_continuity_check_reads_the_union(self):
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md'))
        self.assertEqual(v.classification(self.root).resolve('notes/a.md').profile, 'record-only')
        self.assertIn('notes/a.md', v.classification(self.root).exact_paths())
        (self.root / ac.PROJECT_TABLE).unlink()
        self.assertIsNone(v.classification(self.root).resolve('notes/a.md'))

    def test_the_explainer_reads_the_union(self):
        self.write(ac.PROJECT_TABLE, project_table('notes/a.md'))
        shown = sc.show(self.root, ['notes/a.md', 'project-governance/config/devlog.json'], checks=True)
        self.assertIn('record-only', shown)
        self.assertNotIn('no classification row covers this path', shown)
        self.assertIn('high', shown)

    def test_the_timing_settings_come_from_the_merged_policy_and_a_project_cannot_set_them(self):
        framework = timing_record.load_settings(self.root)
        self.write_json(ac.PROJECT_POLICY, {'version': self.framework_policy['version'],
                                            'commands': {'project-docs': ['{python}', '-B', 'x.py']}})
        self.assertEqual(timing_record.load_settings(self.root), framework)
        self.write_json(ac.PROJECT_POLICY, {'version': self.framework_policy['version'], 'timing_record': {'window': 1}})
        with self.assertRaisesRegex(ValueError, 'may not set timing_record'):
            timing_record.load_settings(self.root)

    def test_the_loaders_do_not_change_what_they_were_given(self):
        before = copy.deepcopy(self.framework_profiles)
        self.write_json(ac.PROJECT_PROFILES, {'version': before['version'], 'profiles': {'project-docs': PROJECT_PROFILE}})
        ac.load_profiles(self.root)
        self.assertEqual(self.framework_profiles, before)


if __name__ == '__main__':
    unittest.main()
