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

    def project_invariant_files(self, invariants=None, narrowable=('wide-gate',)):
        """A project that adds a command group, an impact invariant over it and a profile that opts in to narrowing."""
        gate = lambda name: ['{python}', '-B', '-c', 'print("%s PASS")' % name]
        self.write(c.PROJECT_POLICY, json.dumps({'version': 4, 'commands': {
            'project-docs': PROJECT_POLICY['commands']['project-docs'], 'wide-gate': gate('wide'), 'docs-gate': gate('docs')}}))
        checks = [{'id': name, 'argv': argv} for name, argv in (
            ('continuity', POLICY['commands']['continuity']), ('project-docs', PROJECT_POLICY['commands']['project-docs']),
            ('wide-gate', gate('wide')))]
        profile = {'checks': checks, 'limitations': 'Project documents only.', 'narrowable': list(narrowable)}
        self.write(c.PROJECT_PROFILES, json.dumps({'version': 4, 'profiles': {'project-docs': profile}, 'impact': {
            'invariants': {'docs-only': {'groups': ['docs-gate']}} if invariants is None else invariants}}))
        rows = table_text(PROJECT_ROWS).rstrip(chr(10)).split(chr(10))
        rows.append('| `notes/readme.md` | record | project-docs | - | - | docs-only | - | - | - |')
        self.write(c.PROJECT_TABLE, chr(10).join(rows) + chr(10))
        self.write('notes/readme.md', 'old' + chr(10))

    def ids(self, path):
        self.write(path, 'new' + chr(10))
        return sorted(check['id'] for check in self.selection([path])[1]['checks'])

    def test_a_project_invariant_narrows_a_project_row_and_an_unmapped_path_keeps_the_envelope(self):
        self.project_invariant_files()
        self.assertEqual(self.ids('notes/readme.md'), ['continuity', 'docs-gate', 'project-docs'])
        self.assertEqual(self.ids('notes/n.md'), ['continuity', 'project-docs', 'wide-gate'])

    def test_a_profile_that_does_not_opt_in_is_not_narrowed(self):
        self.project_invariant_files(narrowable=())
        self.assertEqual(self.ids('notes/readme.md'), ['continuity', 'docs-gate', 'project-docs', 'wide-gate'])

    def test_a_project_invariant_may_require_a_framework_or_project_invariant(self):
        self.project_invariant_files({'base': {'groups': ['docs-gate']},
                                      'docs-only': {'groups': [], 'requires': ['base']}})
        self.assertEqual(self.ids('notes/readme.md'), ['continuity', 'docs-gate', 'project-docs'])

    def test_a_project_invariant_that_clashes_with_a_framework_name_is_refused(self):
        framework = json.loads((self.root / c.PROFILES).read_text(encoding='utf-8'))
        framework['impact']['invariants']['shared'] = {'groups': []}
        self.write(c.PROFILES, json.dumps(framework))
        self.project_invariant_files({'shared': {'groups': ['docs-gate']}})
        with self.assertRaisesRegex(ValueError, 'impact invariants clash with the framework: shared'):
            self.tool.data()

    def test_a_row_naming_an_unknown_invariant_is_still_refused(self):
        self.project_invariant_files({})
        policy, profiles = self.tool.data()
        with self.assertRaisesRegex(ValueError, 'unknown invariant'):
            self.tool.table(policy, profiles)

    def test_a_project_invariant_with_a_bad_shape_group_or_requirement_is_refused(self):
        for invariants, message in (({'x': {'groups': ['no-such-command']}}, 'unknown command group no-such-command'),
                                    ({'x': {'groups': [], 'requires': ['no-such']}}, 'requires unknown invariant no-such'),
                                    ({'x': {'groups': [], 'extra': 1}}, 'must be an object with groups'),
                                    ({'x': {'requires': []}}, 'must be an object with groups'),
                                    ({'x': {'groups': 'docs-gate'}}, 'groups must be a list of strings')):
            with self.subTest(invariants=invariants):
                self.project_invariant_files(invariants)
                with self.assertRaisesRegex(ValueError, message):
                    self.tool.data()

    def test_the_project_may_set_nothing_else_inside_impact(self):
        for key in ('version', 'symbol_maps'):
            with self.subTest(key=key):
                self.write(c.PROJECT_PROFILES, json.dumps(dict(PROJECT_PROFILES, impact={key: 1})))
                with self.assertRaisesRegex(ValueError, 'may add only impact.invariants'):
                    self.tool.data()

    def test_narrowable_names_only_checks_the_profile_holds_that_are_not_required(self):
        for narrowable, message in ((['ghost'], 'does not hold: ghost'), (['continuity'], 'required check or runtime gate')):
            with self.subTest(narrowable=narrowable):
                self.project_invariant_files(narrowable=narrowable)
                with self.assertRaisesRegex(ValueError, message):
                    self.tool.data()

    def scenario(self, framework=None, project=None, rows=(), required=None, runtime=None, narrowable=('wide-gate',)):
        """A fuller project: framework invariants `framework`, project invariants `project`, extra table `rows` (path,
        invariants), project `required` checks and `runtime` gates; the command groups are named like the invariants."""
        names = ['wide-gate', 'docs-gate', 'ext-gate', 'other-gate', 'req-gate', 'rt-gate']
        gate = lambda name: ['{python}', '-B', '-c', 'print("%s PASS")' % name]
        commands = {name: gate(name) for name in names}
        commands['project-docs'] = PROJECT_POLICY['commands']['project-docs']
        policy = {'version': 4, 'commands': commands}
        if required:
            policy['required_checks'] = list(required)
        self.write(c.PROJECT_POLICY, json.dumps(policy))
        framework_profiles = json.loads(json.dumps(PROFILES))
        framework_profiles['impact']['invariants'] = framework or {}
        self.write(c.PROFILES, json.dumps(framework_profiles))
        held = ['project-docs', 'wide-gate'] + list(required or []) + list(runtime or [])
        checks = [{'id': 'continuity', 'argv': POLICY['commands']['continuity']}]
        checks += [{'id': name, 'argv': commands[name]} for name in held]
        profile = {'checks': checks, 'limitations': 'Project documents only.', 'narrowable': list(narrowable)}
        if runtime:
            profile['runtime_checks'] = list(runtime)
        self.write(c.PROJECT_PROFILES, json.dumps({'version': 4, 'profiles': {'project-docs': profile},
                                                   'impact': {'invariants': project or {}}}))
        lines = ['| `%s` | %s | %s | %s | - | - | - | - | %s |' % row for row in sorted(PROJECT_ROWS)]
        for path, invariants in rows:
            lines.append('| `%s` | record | project-docs | - | - | %s | - | - | - |' % (path, invariants))
            self.write(path, 'old' + chr(10))
        head = table_text([]).rstrip(chr(10)).split(chr(10))
        self.write(c.PROJECT_TABLE, chr(10).join(head + sorted(lines)) + chr(10))

    def manifest_ids(self, *paths):
        for path in paths:
            self.write(path, 'new' + chr(10))
        return sorted(check['id'] for check in self.selection(list(paths))[1]['checks'])

    def test_a_project_invariant_requiring_a_framework_invariant_selects_its_groups_too(self):
        self.scenario(framework={'fw': {'groups': ['ext-gate']}},
                      project={'docs-only': {'groups': ['docs-gate'], 'requires': ['fw']}},
                      rows=[('notes/readme.md', 'docs-only')])
        self.assertEqual(self.manifest_ids('notes/readme.md'), ['continuity', 'docs-gate', 'ext-gate', 'project-docs'])

    def test_two_narrowed_project_paths_select_the_union_of_their_checks(self):
        self.scenario(project={'docs-only': {'groups': ['docs-gate']}, 'other': {'groups': ['other-gate']}},
                      rows=[('notes/a.md', 'docs-only'), ('notes/b.md', 'other')])
        self.assertEqual(self.manifest_ids('notes/a.md'), ['continuity', 'docs-gate', 'project-docs'])
        self.assertEqual(self.manifest_ids('notes/a.md', 'notes/b.md'),
                         ['continuity', 'docs-gate', 'other-gate', 'project-docs'])
        # One unmapped path in the manifest brings back the whole envelope.
        self.assertEqual(self.manifest_ids('notes/a.md', 'notes/n.md'),
                         ['continuity', 'docs-gate', 'project-docs', 'wide-gate'])

    def test_a_project_required_check_stays_selected_for_a_narrowed_row(self):
        self.scenario(project={'docs-only': {'groups': ['docs-gate']}}, rows=[('notes/readme.md', 'docs-only')],
                      required=['req-gate'])
        self.assertEqual(self.manifest_ids('notes/readme.md'), ['continuity', 'docs-gate', 'project-docs', 'req-gate'])

    def test_runtime_gates_stay_selected_and_cannot_be_narrowed(self):
        self.scenario(project={'docs-only': {'groups': ['docs-gate']}}, rows=[('notes/readme.md', 'docs-only')],
                      runtime=['rt-gate'])
        self.assertEqual(self.manifest_ids('notes/readme.md'), ['continuity', 'docs-gate', 'project-docs', 'rt-gate'])
        self.scenario(project={'docs-only': {'groups': ['docs-gate']}}, rows=[('notes/readme.md', 'docs-only')],
                      runtime=['rt-gate'], narrowable=('wide-gate', 'rt-gate'))
        with self.assertRaisesRegex(ValueError, 'required check or runtime gate: rt-gate'):
            self.tool.data()

    def test_an_unmapped_row_keeps_the_whole_envelope(self):
        self.scenario(project={'docs-only': {'groups': ['docs-gate']}}, rows=[('notes/readme.md', 'docs-only')])
        self.assertEqual(self.manifest_ids('notes/n.md'), ['continuity', 'project-docs', 'wide-gate'])

    def test_a_framework_invariant_with_an_unknown_group_still_makes_the_selection_broad(self):
        self.scenario(framework={'fw': {'groups': ['no-such-command']}},
                      project={'docs-only': {'groups': ['docs-gate'], 'requires': ['fw']}},
                      rows=[('notes/readme.md', 'docs-only'), ('notes/fw.md', 'fw')])
        self.assertEqual(self.manifest_ids('notes/fw.md'), ['continuity', 'project-docs', 'wide-gate'])
        self.assertEqual(self.manifest_ids('notes/readme.md'), ['continuity', 'project-docs', 'wide-gate'])

    def test_the_explainer_shows_the_narrowed_project_checks(self):
        from governance.tools.continuity import show_classification as show
        self.project_invariant_files()
        policy, profiles = self.tool.data()
        resolved = self.tool.table(policy, profiles).resolve('notes/readme.md')
        text = chr(10).join(show.explain_checks(resolved, policy, profiles))
        self.assertIn('from profile project-docs: continuity, project-docs', text)
        self.assertIn('from invariants docs-only: docs-gate', text)

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
