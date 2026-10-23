"""local_state: the one place that names the machine-local state of a repository, found from any checkout or worktree."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.support import local_state as ls
import repo_paths as rp

CLEAN = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
IDENTITY = dict(CLEAN, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='noreply@example.invalid',
                GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='noreply@example.invalid')


def git(root, *args, env=CLEAN):
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True, env=env).stdout


class Case(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, CLEAN, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        temp = tempfile.TemporaryDirectory(prefix='local-state-test-')
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def repository(self, name='main', ignore=True):
        root = self.base / name
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(root)], check=True, capture_output=True, env=CLEAN)
        (root / '.gitignore').write_text('/.local/\n' if ignore else 'tmp/\n', newline='\n')
        git(root, 'add', '.gitignore')
        git(root, 'commit', '-q', '-m', 'start', env=IDENTITY)
        return root


class LocationsTest(Case):
    def test_the_names_are_plain_relative_single_segments(self):
        for name in (ls.LOCAL, ls.TIMINGS, ls.LOCKS, ls.TIMINGS_FILE, ls.LIFECYCLE_LOCK):
            self.assertTrue(name and '/' not in name and chr(92) not in name, name)
        self.assertEqual((ls.LOCAL, ls.TIMINGS, ls.LOCKS), ('.local', 'timings', 'locks'))

    def test_the_primary_checkout_is_found_from_the_main_checkout(self):
        root = self.repository()
        common = ls.common_directory(root)
        self.assertEqual(common, root / '.git')
        self.assertEqual(ls.primary_root(common), root)
        self.assertEqual(ls.local_folder(common), root / '.local')
        self.assertEqual(ls.timings_file(common), root / '.local' / 'timings' / 'checkpoint-timings.jsonl')
        self.assertEqual(ls.lifecycle_lock(common), root / '.local' / 'locks' / 'worktree-workflow.lock')

    def test_a_linked_worktree_finds_the_primary_of_its_repository(self):
        root = self.repository()
        linked = self.base / 'linked'
        git(root, 'worktree', 'add', '-q', '--detach', str(linked))
        self.assertEqual(ls.common_directory(linked), root / '.git')
        self.assertEqual(ls.primary_root(ls.common_directory(linked)), root)

    def test_a_clone_has_its_own_primary(self):
        root = self.repository()
        clone = self.base / 'clone'
        subprocess.run(['git', 'clone', '-q', str(root), str(clone)], check=True, capture_output=True, env=CLEAN)
        self.assertEqual(ls.primary_root(ls.common_directory(clone)), clone)
        self.assertNotEqual(ls.local_folder(ls.common_directory(clone)), ls.local_folder(ls.common_directory(root)))

    def test_a_bare_repository_has_no_primary(self):
        bare = self.base / 'bare.git'
        subprocess.run(['git', 'init', '-q', '--bare', str(bare)], check=True, capture_output=True, env=CLEAN)
        with self.assertRaisesRegex(ls.LocalStateError, 'no primary checkout'):
            ls.primary_root(ls.common_directory(bare))

    def test_a_common_directory_not_named_dot_git_has_no_primary(self):
        separate = self.base / 'separate'
        separate.mkdir()
        subprocess.run(['git', 'init', '-q', '--separate-git-dir', str(self.base / 'elsewhere'), str(separate)],
                       check=True, capture_output=True, env=CLEAN)
        with self.assertRaisesRegex(ls.LocalStateError, 'no primary checkout'):
            ls.primary_root(ls.common_directory(separate))

    def test_a_folder_that_is_not_a_repository_is_refused(self):
        with self.assertRaisesRegex(ls.LocalStateError, 'not a Git repository'):
            ls.common_directory(self.base)


class IgnoreTest(Case):
    def test_an_ignored_local_folder_is_created_on_demand(self):
        root = self.repository()
        common = ls.common_directory(root)
        self.assertFalse((root / '.local').exists())
        folder = ls.prepare(common, ls.LOCKS)
        self.assertEqual(folder, root / '.local' / 'locks')
        self.assertTrue(folder.is_dir())
        self.assertEqual(ls.prepare(common, ls.LOCKS), folder)
        self.assertEqual(git(root, 'status', '--porcelain'), '')

    def test_a_local_folder_that_is_not_ignored_is_refused_and_nothing_is_created(self):
        root = self.repository(ignore=False)
        with self.assertRaisesRegex(ls.LocalStateError, r'not git-ignored.*\.gitignore'):
            ls.prepare(ls.common_directory(root), ls.TIMINGS)
        self.assertFalse((root / '.local').exists())

    def test_a_linked_worktree_prepares_the_folder_of_the_primary(self):
        root = self.repository()
        linked = self.base / 'linked'
        git(root, 'worktree', 'add', '-q', '--detach', str(linked))
        folder = ls.prepare(ls.common_directory(linked), ls.TIMINGS)
        self.assertEqual(folder, root / '.local' / 'timings')
        self.assertFalse((linked / '.local').exists())

    def test_a_file_in_place_of_the_folder_is_refused(self):
        root = self.repository()
        (root / '.local').mkdir()
        (root / '.local' / 'locks').write_text('a file in the way')
        with self.assertRaisesRegex(ls.LocalStateError, 'indirect local storage'):
            ls.prepare(ls.common_directory(root), ls.LOCKS)


class StoreTest(Case):
    def test_the_store_of_the_primary_checkout_is_in_its_git_directory(self):
        root = self.repository()
        self.assertEqual(ls.checkout_store(root), root / '.git' / 'governance')
        self.assertEqual(ls.store_folder(root / '.git'), root / '.git' / 'governance')
        self.assertEqual(ls.checkout_git_directory(root), root / '.git')

    def test_the_store_of_a_linked_worktree_is_in_its_own_git_directory(self):
        root = self.repository()
        linked = self.base / 'linked'
        git(root, 'worktree', 'add', '-q', '--detach', str(linked))
        own = Path(git(linked, 'rev-parse', '--absolute-git-dir').strip()).resolve()
        self.assertEqual(own.parent, root / '.git' / 'worktrees')
        self.assertEqual(ls.checkout_store(linked), own / 'governance')
        self.assertNotEqual(ls.checkout_store(linked), ls.checkout_store(root))

    def test_the_store_dies_with_its_worktree(self):
        root = self.repository()
        linked = self.base / 'linked'
        git(root, 'worktree', 'add', '-q', '--detach', str(linked))
        store = ls.checkout_store(linked)
        store.mkdir()
        (store / 'receipt.key').write_bytes(b'k' * 32)
        git(root, 'worktree', 'remove', '--force', str(linked))
        self.assertFalse(store.exists())
        self.assertFalse(store.parent.exists())

    def test_a_clean_that_removes_ignored_files_leaves_the_store_alone(self):
        root = self.repository()
        store = ls.checkout_store(root)
        store.mkdir()
        (store / 'receipt.key').write_bytes(b'k' * 32)
        (root / '.local').mkdir()
        (root / '.local' / 'note.txt').write_text('ignored')
        git(root, 'clean', '-fdx')
        self.assertTrue((store / 'receipt.key').is_file())
        self.assertFalse((root / '.local').exists())

    def test_the_name_collides_with_nothing_git_keeps_in_a_git_directory(self):
        root = self.repository()
        linked = self.base / 'linked'
        git(root, 'worktree', 'add', '-q', '--detach', str(linked))
        for directory in (root / '.git', ls.checkout_git_directory(linked)):
            self.assertFalse((directory / ls.STORE).exists(), directory)
        # The marker names the checkpoint tool reads in a Git directory, and the other names Git itself keeps.
        taken = {'MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'index.lock', 'HEAD',
                 'index', 'config', 'hooks', 'info', 'logs', 'objects', 'refs', 'worktrees', 'locked', 'gitdir',
                 'commondir', 'ORIG_HEAD', 'FETCH_HEAD', 'packed-refs', 'shallow', 'modules', 'lfs', 'description'}
        self.assertNotIn(ls.STORE, taken)

    def test_an_entry_that_is_not_a_git_directory_gives_no_store(self):
        plain = self.base / 'plain'
        plain.mkdir()
        self.assertIsNone(ls.checkout_store(plain))
        (plain / '.git').write_text('not a pointer')
        self.assertIsNone(ls.checkout_store(plain))


class RepoPathsTest(unittest.TestCase):
    def test_repo_paths_names_the_module_and_re_exports_the_names(self):
        self.assertTrue((rp.ROOT / rp.LOCAL_STATE).is_file())
        self.assertEqual((rp.LOCAL, rp.TIMINGS_FILE, rp.LIFECYCLE_LOCK), (ls.LOCAL, ls.TIMINGS_FILE, ls.LIFECYCLE_LOCK))

    def test_this_repository_ignores_the_local_folder(self):
        ls.require_ignored(ls.common_directory(rp.ROOT), ls.LOCKS)


if __name__ == '__main__':
    unittest.main()
