"""The shared Git fake answers the reads the checkpoint tool makes, with defaults for the ones a test never named."""
from pathlib import Path
import unittest

from ..fake_git import FakeGit, HEAD_ID, bare_tool


class FakeGitTest(unittest.TestCase):
    def test_show_and_cat_file_read_the_tree_and_fail_like_the_tool_for_a_missing_path(self):
        fake = FakeGit({'a.md': 'text', 'b.bin': b'\xff'})
        self.assertEqual(fake('show', 'HEAD:a.md'), b'text')
        self.assertEqual(fake('show', 'refs/heads/main:b.bin'), b'\xff')
        self.assertEqual(fake('cat-file', '-e', 'HEAD:a.md'), b'')
        for args in (('show', 'HEAD:missing.md'), ('cat-file', '-e', 'HEAD:missing.md')):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'Git failed'):
                fake(*args)

    def test_another_revision_can_hold_its_own_files(self):
        fake = FakeGit({'f.toml': 'head'}, others={'refs/heads/main': {'f.toml': 'main'}})
        self.assertEqual(fake('show', 'HEAD:f.toml'), b'head')
        self.assertEqual(fake('show', 'refs/heads/main:f.toml'), b'main')

    def test_a_read_nobody_named_answers_what_a_read_that_found_nothing_does(self):
        fake = FakeGit({'a.md': 'x'})
        self.assertEqual(fake('rev-parse', 'HEAD'), (HEAD_ID + '\n').encode())
        self.assertEqual(fake('symbolic-ref', '--quiet', 'HEAD'), b'refs/heads/main\n')
        self.assertEqual(fake('ls-files', '-z'), b'a.md\0')
        self.assertEqual(fake('ls-files'), b'a.md\n')
        self.assertEqual(fake('a-read-added-next-year', '--whatever'), b'')
        self.assertEqual(fake.reads('ls-files'), [('ls-files', '-z'), ('ls-files',)])

    def test_an_unavailable_repository_fails_every_call(self):
        with self.assertRaisesRegex(ValueError, 'not a git repository'):
            FakeGit({'a.md': 'x'}, unavailable=True)('show', 'HEAD:a.md')

    def test_the_rooted_form_serves_a_function_that_takes_the_root_first(self):
        self.assertEqual(FakeGit({'a.md': 'x'}).rooted(Path('.'), 'show', 'HEAD:a.md'), b'x')

    def test_a_bare_tool_reads_through_the_fake_and_nothing_else(self):
        fake = FakeGit({'a.py': 'x = 1\n'})
        tool = bare_tool(Path('.'), fake)
        self.assertEqual(tool.git('show', 'HEAD:a.py'), b'x = 1\n')
        self.assertEqual(fake.calls, [('show', 'HEAD:a.py')])


if __name__ == '__main__':
    unittest.main()
