"""repo_paths: the one definition of the repository root and of the paths the tests and tools repeat."""
import json
from pathlib import Path
import unittest

from governance.tools.continuity import artifact_classification as ac
import repo_paths as rp

DOCUMENTS = ('AGENTS', 'README', 'ARCHITECTURE', 'PLAN', 'PROGRESS', 'CONSTRAINTS', 'DECISIONS', 'RATIONALE',
             'DEVLOG_INDEX')
FILES = ('TABLE', 'POLICY', 'PROFILES', 'CONTRACT', 'FEATURES', 'GOLDEN', 'CHECKPOINT',
         'CHECKPOINT_STATE', 'CHECKPOINT_SELECTION', 'WORKTREE', 'VALIDATE_CONTINUITY', 'CLAIMS', 'RUN_TESTS', 'LOCAL_STATE')


class RepoPathsTest(unittest.TestCase):
    def test_root_is_the_repository_root(self):
        self.assertTrue(rp.ROOT.is_absolute())
        self.assertEqual(rp.ROOT, Path(rp.__file__).resolve().parents[2])
        self.assertTrue((rp.ROOT / rp.TOOLS / 'repo_paths.py').is_file())
        self.assertTrue((rp.ROOT / rp.TABLE).is_file())

    def test_every_named_file_exists(self):
        # The devlog index and the documents a project replaces still exist as placeholders in the template.
        for name in DOCUMENTS + FILES:
            with self.subTest(name=name):
                value = getattr(rp, name)
                self.assertIsInstance(value, str)
                self.assertNotIn(chr(92), value)
                self.assertTrue((rp.ROOT / value).is_file(), value)

    def test_the_folders_are_the_anchors_artifact_classification_owns(self):
        self.assertIs(rp.TOOLS, ac.TOOLS)
        self.assertIs(rp.TESTS, ac.TESTS)
        self.assertIs(rp.CONFIG, ac.CONFIG)
        for name in ('TABLE', 'POLICY', 'PROFILES', 'CONTRACT', 'FEATURES'):
            self.assertIs(getattr(rp, name), getattr(ac, name), name)
        self.assertIs(rp.DEVLOG_SETTINGS, ac.DEVLOG)
        self.assertIs(rp.VALIDATE_CONTINUITY, ac.CONTINUITY_CHECKER)
        self.assertIn(rp.CHECKPOINT, ac.CHECKPOINT_DRIVER)
        self.assertIn(rp.CHECKPOINT_STATE, ac.CHECKPOINT_DRIVER)
        self.assertIn(rp.CHECKPOINT_SELECTION, ac.CHECKPOINT_DRIVER)
        self.assertIn(rp.WORKTREE, ac.WORKTREE_GUARD)

    def test_the_devlog_index_follows_the_default_folder(self):
        self.assertEqual(rp.DEVLOG_INDEX, ac.DEVLOG_DEFAULTS['folder'] + '/README.md')
        # The real folder is the setting, read through the one function that reads it.
        self.assertEqual(ac.devlog_settings(rp.ROOT)['folder'], json.loads((rp.ROOT / rp.DEVLOG_SETTINGS).read_text())['folder'])

    def test_the_project_paths_are_relative_posix_paths_in_the_project_folder(self):
        names = ('PROJECT_GOVERNANCE', 'PROJECT_CONFIG', 'PROJECT_TABLE', 'PROJECT_POLICY', 'PROJECT_PROFILES',
                 'PROJECT_CONSTRAINTS', 'PROJECT_DECISIONS', 'PROJECT_RATIONALE', 'PROJECT_CONTRACT',
                 'PROJECT_GOLDEN', 'FEATURES', 'DEVLOG_SETTINGS')
        self.assertEqual(rp.PROJECT_GOVERNANCE, 'project-governance')
        for name in names:
            with self.subTest(name=name):
                value = getattr(rp, name)
                self.assertIsInstance(value, str)
                self.assertNotIn(chr(92), value)
                self.assertFalse(value.startswith('/'))
                self.assertEqual(Path(value).as_posix(), value)
                self.assertTrue(value == rp.PROJECT_GOVERNANCE or value.startswith(rp.PROJECT_GOVERNANCE + '/'), value)
        # The files mirror the framework's, name for name.
        for project, framework in ((rp.PROJECT_POLICY, rp.POLICY), (rp.PROJECT_PROFILES, rp.PROFILES),
                                   (rp.PROJECT_GOLDEN, rp.GOLDEN)):
            self.assertEqual(project.split('/')[-1], framework.split('/')[-1])
            self.assertEqual(project, rp.PROJECT_CONFIG + '/' + framework.split('/')[-1])
        self.assertEqual(rp.PROJECT_TABLE.split('/')[-1], rp.TABLE.split('/')[-1])
        for name in ('PROJECT_GOVERNANCE', 'PROJECT_CONFIG', 'PROJECT_TABLE', 'PROJECT_POLICY', 'PROJECT_PROFILES'):
            self.assertIs(getattr(rp, name), getattr(ac, name), name)
        # The switches and the log settings are one file each, at the project's path, with no framework copy.
        self.assertEqual(rp.FEATURES, rp.PROJECT_CONFIG + '/checkpoint-features.toml')
        self.assertEqual(rp.DEVLOG_SETTINGS, rp.PROJECT_CONFIG + '/devlog.json')
        self.assertFalse((rp.ROOT / rp.CONFIG / 'checkpoint-features.toml').exists())
        self.assertFalse((rp.ROOT / rp.CONFIG / 'devlog.json').exists())

    def test_the_driver_files_do_not_import_it(self):
        for name in ac.CHECKPOINT_DRIVER + ac.WORKTREE_GUARD:
            with self.subTest(name=name):
                self.assertNotIn('repo_paths', (rp.ROOT / name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
