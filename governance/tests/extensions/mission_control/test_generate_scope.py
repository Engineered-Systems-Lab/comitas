"""The checks that a change to the Mission Control extension, or to the documents it reads, selects.

These cases read the real table, policy and profiles. They moved here from the core's selector tests because they name the
extension: the core's own tests no longer do, and they go with the extension when it is removed."""
import json
import unittest
import checkpoint as c
import repo_paths as rp

EXTENSION = 'governance/extensions/mission_control/'
TESTS = 'governance/tests/extensions/mission_control/'


class ExtensionScopeTest(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads((rp.ROOT / c.POLICY).read_bytes())
        self.profiles = json.loads((rp.ROOT / c.PROFILES).read_bytes())
        self.tool = object.__new__(c.Checkpoint)
        self.tool.root = rp.ROOT

    def select(self, paths):
        return self.tool.select_profile(paths, self.policy, self.profiles, 'auto')

    def ids(self, paths):
        return {v['id'] for v in self.select(paths)[1]['checks']}

    def test_the_ui_has_explicit_tooling_scope_and_focused_impact(self):
        paths = [EXTENSION + 'ui/app.mjs', TESTS + 'check.py', EXTENSION + 'mission-control.sh']
        name, profile = self.select(paths)
        self.assertEqual(name, 'tooling')
        self.assertEqual(self.ids([EXTENSION + 'mission-control.sh']), {'continuity', 'continuity-regressions', 'mission-control-ui'})
        self.assertTrue(all(value == 'tooling' for value in profile['classification'].values()))
        self.assertEqual(self.tool.table(self.policy).resolve(paths[0]).invariants, ['mission-control-ui'])
        self.assertEqual(self.profiles['impact']['invariants']['mission-control-ui']['groups'], ['mission-control-ui'])
        self.assertEqual(self.policy['commands']['mission-control-ui'], ['{python}', '-B', TESTS + 'check.py'])

    def test_the_generator_selects_only_generator_and_cheap_continuity(self):
        paths = [EXTENSION + name for name in ('generate.py', 'inputs.json', 'declared_inputs.py', 'extension.json',
                                               'README.md', '.gitignore')] + [TESTS + 'test_generate.py', TESTS + 'test_generate_scope.py']
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.ids([path]), {'continuity', 'continuity-regressions', 'mission-control-generator'})
                self.assertEqual(self.select([path])[1]['classification'][path], 'tooling')
        self.assertEqual(self.ids(paths + [c.POLICY, c.PROFILES]),
                         {'continuity', 'continuity-regressions', 'mission-control-generator', 'checkpoint-selector',
                          'checkpoint-invariants', 'commit-metadata'})
        # The generated datasets and the pinned selection are not committed, so no check verifies them.
        self.assertNotIn('mission-control-snapshots', self.policy['commands'])
        with self.assertRaises(ValueError):
            self.ids([EXTENSION + 'ui.js'])

    def test_the_documents_the_generator_reads_run_its_tests(self):
        self.assertEqual(self.ids([rp.RATIONALE]), {'continuity', 'continuity-regressions', 'mission-control-generator'})
        manifest = ['AGENTS.md', 'governance/CONSTRAINTS.md', rp.DECISIONS, rp.RATIONALE]
        self.assertEqual(self.ids(manifest), {'continuity', 'continuity-regressions', 'mission-control-generator'})
        self.assertEqual(self.ids(manifest[:2]), {'continuity', 'continuity-regressions'})

    def test_the_manifest_names_what_the_policy_and_the_profiles_hold(self):
        manifest = json.loads((rp.ROOT / EXTENSION / 'extension.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['folder'] + '/', EXTENSION)
        self.assertEqual(manifest['tests'] + '/', TESTS)
        for command in manifest['policy_commands']:
            self.assertIn(command, self.policy['commands'])
        for invariant in manifest['invariants']:
            self.assertIn(invariant, self.profiles['impact']['invariants'])
        known = {entry['value'] for entry in self.profiles['commit_metadata']['subclasses']}
        self.assertTrue(set(manifest['subclasses']) <= known)
        self.assertTrue((rp.ROOT / manifest['project_files'][0]).is_file())


if __name__ == '__main__':
    unittest.main()
