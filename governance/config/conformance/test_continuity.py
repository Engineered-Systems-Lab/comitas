"""The continuity checker against this project's real repository: the governance layout, the devlog settings file and the
classification coverage of every tracked file. The checker's behavior on disposable fixtures is tested in
governance/tests/continuity/test_continuity.py."""
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
from governance.tools.continuity import validate_continuity as v
import repo_paths as rp



class GovernanceLayoutTest(unittest.TestCase):
    def test_current_governance_is_reachable_and_links_resolve(self):
        v.validate_docs(v.ROOT)
        self.assertTrue(v.governance_layout(v.ROOT))

    def test_missing_governance_cannot_fall_back_to_pointer(self):
        original = v.read
        def read(root, relative):
            if relative == rp.CONSTRAINTS:
                raise ValueError('Missing recovery evidence: ' + relative)
            return original(root, relative)
        with patch.object(v, 'read', side_effect=read), self.assertRaisesRegex(ValueError, 'Missing recovery evidence'):
            v.validate_docs(v.ROOT)


class DevlogSettingsTest(unittest.TestCase):
    """The folder, the date pattern and the switch are adopter settings: an override of the defaults in the code, with every
    key optional; a key that is set must hold a good value."""

    def settings(self, text=None):
        directory = tempfile.TemporaryDirectory(prefix='devlog-settings-')
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        if text is not None:
            (root / ac.DEVLOG).parent.mkdir(parents=True)
            (root / ac.DEVLOG).write_text(text, encoding='utf-8')
        return ac.devlog_settings(root)

    def test_a_missing_file_means_the_defaults(self):
        self.assertEqual(self.settings(), {'folder': 'devlog', 'date_format': '%Y-%m-%dT%H:%MZ', 'enabled': True})

    def test_the_shipped_file_names_the_defaults(self):
        self.assertEqual(ac.devlog_settings(v.ROOT), ac.DEVLOG_DEFAULTS)

    def test_another_folder_and_pattern_are_accepted(self):
        value = self.settings('{"version": 1, "folder": "journal", "date_format": "%Y%m%d"}')
        self.assertEqual(value, {'folder': 'journal', 'date_format': '%Y%m%d', 'enabled': True})

    def test_the_log_can_be_switched_off_with_a_boolean(self):
        value = self.settings('{"version": 1, "folder": "devlog", "date_format": "%Y%m%d", "enabled": false}')
        self.assertEqual(value, {'folder': 'devlog', 'date_format': '%Y%m%d', 'enabled': False})
        with self.assertRaises(ValueError):
            self.settings('{"version": 1, "folder": "devlog", "date_format": "%Y%m%d", "enabled": "no"}')

    def test_partial_settings_take_the_default_for_every_key_left_out(self):
        defaults = dict(ac.DEVLOG_DEFAULTS)
        for text, expected in (('{}', {}), ('{"version": 1}', {}),
                               ('{"folder": "journal"}', {'folder': 'journal'}),
                               ('{"version": 1, "folder": "devlog"}', {}),
                               ('{"date_format": "%Y%m%d"}', {'date_format': '%Y%m%d'}),
                               ('{"enabled": false}', {'enabled': False})):
            with self.subTest(text=text):
                self.assertEqual(self.settings(text), {**defaults, **expected})

    def test_a_file_that_does_not_parse_as_an_object_means_the_defaults(self):
        for text in ('', 'not json', '{"folder": ', '[]', '7', 'null', '"devlog"'):
            with self.subTest(text=text):
                self.assertEqual(self.settings(text), ac.DEVLOG_DEFAULTS)

    def test_malformed_settings_stop_the_tools(self):
        for text in ('{"version": 2, "folder": "devlog", "date_format": "%Y"}',
                     '{"enable": false}', '{"enabled": "no"}', '{"enabled": 0}', '{"folder": 7}', '{"folder": ""}',
                     '{"version": 1, "folder": "a/b", "date_format": "%Y"}',
                     '{"version": 1, "folder": "..", "date_format": "%Y"}',
                     '{"version": 1, "folder": "devlog", "date_format": "%d-%m"}',
                     '{"version": 1, "folder": "devlog", "date_format": "%Y-%a"}',
                     '{"version": 1, "folder": "devlog", "date_format": 7}',
                     '{"version": 1, "folder": "devlog", "date_format": "%Y", "extra": 1}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.settings(text)

    def test_the_filename_prefix_keeps_only_safe_characters(self):
        self.assertEqual(ac.date_prefix('2026-10-16T13:03Z'), '2026-10-16T1303Z-')
        self.assertEqual(ac.date_prefix('20261016'), '20261016-')
        self.assertEqual(ac.date_prefix('2026/10/16 13:03'), '202610161303-')


class TableCoverageTest(unittest.TestCase):
    """Every file Git tracks must resolve to a row of the classification table."""
    ROWS = ['| `README.md` | record | record-only | - | - | - | - | yes | - |',
            '| `docs/` | record | record-only | - | - | - | - | - | - |',
            '| `quarantine/` | runtime | blocked | runtime | - | - | - | - | - |']

    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='table-coverage-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        v.git(self.root, 'init', '-q')
        text = '\n'.join([v.artifact_classification.HEADER, v.artifact_classification.SEPARATOR, *self.ROWS]) + '\n'
        self.table = v.artifact_classification.Table.parse(
            text, {'profiles': {'record-only'}, 'invariants': set(), 'subclasses': set()})

    def track(self, *names, add=True):
        for name in names:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('x\n', encoding='utf-8')
        if add:
            v.git(self.root, 'add', '--', *names)

    def test_covered_files_pass(self):
        self.track(rp.README, 'docs/a.md', 'quarantine/b.bin')
        self.assertEqual(v.unclassified_paths(self.root, self.table), [])
        v.validate_coverage(self.root, self.table)

    def test_uncovered_files_are_listed_sorted_with_unusual_names(self):
        self.track(rp.README, 'governance/scripts/z.sh', 'src/y.py', 'docs/odd name é.md', 'extra é.txt')
        self.assertEqual(v.unclassified_paths(self.root, self.table),
                         ['extra é.txt', 'governance/scripts/z.sh', 'src/y.py'])

    def test_failure_names_every_uncovered_path(self):
        self.track(rp.README, 'governance/scripts/z.sh', 'src/y.py')
        with self.assertRaises(ValueError) as caught:
            v.validate_coverage(self.root, self.table)
        message = str(caught.exception)
        self.assertIn('2 tracked path(s)', message)
        self.assertIn('governance/scripts/z.sh', message)
        self.assertIn('src/y.py', message)
        self.assertNotIn(rp.README, message)

    def test_staged_but_uncommitted_files_count_and_untracked_files_do_not(self):
        self.track('staged.txt')
        self.track('untracked.txt', add=False)
        self.assertEqual(v.unclassified_paths(self.root, self.table), ['staged.txt'])

    def test_blocked_row_is_a_row(self):
        self.track('quarantine/c.java')
        self.assertEqual(self.table.scope('quarantine/c.java'), ('runtime', 'blocked'))
        self.assertEqual(v.unclassified_paths(self.root, self.table), [])

    def test_unavailable_git_fails_instead_of_passing(self):
        with self.assertRaisesRegex(ValueError, 'Git evidence unavailable'):
            v.unclassified_paths(self.root / 'missing', self.table)

    def test_validate_docs_runs_the_coverage_check(self):
        with patch.object(v, 'unclassified_paths', return_value=['governance/scripts/new.sh']):
            with self.assertRaisesRegex(ValueError, r'no row in governance/ARTIFACT-CLASSIFICATION.md[\s\S]*governance/scripts/new.sh'):
                v.validate_docs(v.ROOT)

    def test_every_tracked_file_of_this_repository_has_a_row(self):
        self.assertEqual(v.unclassified_paths(v.ROOT, v.classification(v.ROOT)), [])


class IntegrationCheckerTest(unittest.TestCase):
    def test_the_worktree_workflow_names_the_checker_the_guard_hashes(self):
        # integrate --candidate-validator-sha256 takes the hash of CONTINUITY_CHECKER, not of its launcher.
        text = (rp.ROOT / 'governance/WORKTREE-WORKFLOW.md').read_text(encoding='utf-8')
        named = re.findall(r"--candidate-validator-sha256 '<[^>]*-of-([^>]+)>'", text)
        named += re.findall(r'candidate checker `([^`]+)`', text)
        self.assertEqual(named, [ac.CONTINUITY_CHECKER] * 2)


if __name__ == '__main__':
    unittest.main()
