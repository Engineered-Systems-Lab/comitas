"""The adoption helper, in disposable repositories: it makes a new project pass its own checks before the first commit.

Each test builds a repository of its own with `git init`, a few files and `git add -A`, as README step 1 describes, and
calls `adoption.adopt` on it. No test writes a Git configuration (a commit takes its identity from `-c`)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from governance.tools.continuity import artifact_classification as ac
from governance.tools.support import adoption
import repo_paths as rp

CITATIONS = ac.CITATIONS
IDENTITY = ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid')


def run(root, *args):
    done = adoption.git(root, *args)
    return done.stdout.decode('utf-8')


def modes(root):
    return {path: mode for mode, path in adoption.indexed(root)}


class AdoptionFixture(unittest.TestCase):
    """A new repository that is initialized and staged, with no commit; it has no tests of its own."""

    FILES = {'README.md': 'x\n', 'run.sh': '#!/bin/sh\necho a\n', 'scripts/nested.sh': '#!/bin/sh\necho b\n',
             'tool.py': 'print(1)\n', 'project-governance/config/devlog.json': '{}\n'}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'new-project'
        self.root.mkdir()
        run(self.root, 'init', '-b', 'main')
        for name, text in self.FILES.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode('utf-8'))
        run(self.root, 'add', '-A')

    def staged(self):
        return run(self.root, 'ls-files', '-s')


class RepairsTest(AdoptionFixture):
    def test_every_staged_script_becomes_100755_and_nothing_else_changes_mode(self):
        before = modes(self.root)
        self.assertEqual({before['run.sh'], before['scripts/nested.sh']}, {'100644'})
        result = adoption.adopt(self.root)
        after = modes(self.root)
        self.assertEqual({after['run.sh'], after['scripts/nested.sh']}, {'100755'})
        self.assertEqual(sorted(result['executable']), ['run.sh', 'scripts/nested.sh'])
        for path in ('README.md', 'tool.py', 'project-governance/config/devlog.json'):
            self.assertEqual(after[path], before[path], path)

    def test_the_label_is_declared_in_a_new_settings_file_with_lf_line_ends_and_staged(self):
        result = adoption.adopt(self.root)
        self.assertTrue(result['label_written'])
        raw = (self.root / CITATIONS).read_bytes()
        self.assertNotIn(b'\r', raw)
        self.assertTrue(raw.endswith(b'\n'))
        self.assertEqual(json.loads(raw), {'version': 1, 'external_repositories': ['template']})
        self.assertEqual(ac.citation_labels(self.root), ('template',))
        self.assertIn(CITATIONS, run(self.root, 'diff', '--cached', '--name-only'))
        self.assertEqual(run(self.root, 'status', '--porcelain', '--', CITATIONS).strip(), 'A  ' + CITATIONS)

    def test_a_second_run_before_the_first_commit_changes_nothing(self):
        adoption.adopt(self.root)
        index, settings = self.staged(), (self.root / CITATIONS).read_bytes()
        again = adoption.adopt(self.root)
        self.assertEqual((again['executable'], again['label_written']), ([], False))
        self.assertEqual(self.staged(), index)
        self.assertEqual((self.root / CITATIONS).read_bytes(), settings)
        self.assertIn('Nothing needed to change', adoption.report(again))

    def test_it_writes_no_git_configuration(self):
        config = self.root / '.git' / 'config'
        before = config.read_bytes()
        adoption.adopt(self.root)
        adoption.adopt(self.root)
        self.assertEqual(config.read_bytes(), before)

    def test_the_first_commit_then_holds_the_modes_and_the_label(self):
        adoption.adopt(self.root)
        run(self.root, *IDENTITY, 'commit', '-m', 'First commit')
        self.assertEqual({mode for mode, path in adoption.indexed(self.root) if path.endswith('.sh')}, {'100755'})
        self.assertIn('template', ac.citation_labels(self.root))


class SettingsTest(AdoptionFixture):
    def write_settings(self, text):
        path = self.root / CITATIONS
        path.write_bytes(text.encode('utf-8'))
        run(self.root, 'add', '--', CITATIONS)
        return path

    def test_an_existing_label_list_keeps_its_labels_and_gains_template(self):
        path = self.write_settings('{"version": 1, "external_repositories": ["lab"]}\n')
        result = adoption.adopt(self.root)
        self.assertTrue(result['label_written'])
        self.assertEqual(json.loads(path.read_bytes()), {'version': 1, 'external_repositories': ['lab', 'template']})
        self.assertEqual(ac.citation_labels(self.root), ('lab', 'template'))

    def test_a_file_without_the_key_gets_the_key_and_keeps_the_rest(self):
        path = self.write_settings('{"version": 1}\n')
        adoption.adopt(self.root)
        self.assertEqual(json.loads(path.read_bytes()), {'version': 1, 'external_repositories': ['template']})

    def test_a_file_that_declares_template_is_left_byte_for_byte(self):
        text = '{ "external_repositories": ["template", "lab"] }'
        path = self.write_settings(text)
        result = adoption.adopt(self.root)
        self.assertFalse(result['label_written'])
        self.assertEqual(path.read_bytes(), text.encode('utf-8'))

    def test_a_file_it_cannot_read_is_refused_and_left_alone(self):
        for text in ('not json', '[]', '{"external_repositories": "template"}', '{"external_repositories": [1]}',
                     '{"version": 2}', '{"labels": []}'):
            with self.subTest(text=text):
                path = self.write_settings(text)
                with self.assertRaises(ValueError):
                    adoption.adopt(self.root)
                self.assertEqual(path.read_bytes(), text.encode('utf-8'))


class RefusalTest(AdoptionFixture):
    def test_it_refuses_where_head_exists_and_changes_nothing(self):
        run(self.root, *IDENTITY, 'commit', '-m', 'First commit')
        index = self.staged()
        with self.assertRaisesRegex(ValueError, 'HEAD exists'):
            adoption.adopt(self.root)
        self.assertEqual(self.staged(), index)
        self.assertEqual({mode for mode, path in adoption.indexed(self.root) if path.endswith('.sh')}, {'100644'})
        self.assertFalse((self.root / CITATIONS).exists())

    def test_it_refuses_when_nothing_is_staged(self):
        run(self.root, 'rm', '-r', '-q', '--cached', '.')
        with self.assertRaisesRegex(ValueError, 'Nothing is staged'):
            adoption.adopt(self.root)
        self.assertFalse((self.root / CITATIONS).exists())

    def test_it_refuses_in_a_folder_that_is_not_the_top_of_a_repository(self):
        inner = self.root / 'inner'
        inner.mkdir()
        with self.assertRaisesRegex(ValueError, 'not the top folder'):
            adoption.adopt(inner)
        with tempfile.TemporaryDirectory() as bare:
            with self.assertRaisesRegex(ValueError, 'not the top folder'):
                adoption.adopt(Path(bare).resolve())


class CommandTest(unittest.TestCase):
    def test_the_command_refuses_in_this_repository_because_it_has_a_commit(self):
        done = subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.TOOLS / 'adopt.py')], capture_output=True, text=True,
                              timeout=120, cwd=tempfile.gettempdir(),
                              env={key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')})
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn('FAIL:', done.stderr)
        self.assertEqual(done.stdout, '')

    def test_the_report_names_what_was_done_and_the_next_step(self):
        text = adoption.report({'executable': ['a.sh'], 'scripts': 1, 'citations': CITATIONS, 'label_written': True})
        self.assertIn('a.sh', text)
        self.assertIn('Declared the label', text)
        self.assertIn('first commit', text)


if __name__ == '__main__':
    unittest.main()
