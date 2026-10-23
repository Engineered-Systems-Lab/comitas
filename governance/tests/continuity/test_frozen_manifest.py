"""The frozen files are checked against the committed checksum manifest, never against a commit (G054).

Each test builds a disposable repository with a real classification table whose rows freeze a folder, a filter and a file, so
the checker reads the table the way it does on a project. Nothing here touches this repository: the command `--freeze` is
run on the disposable root only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from governance.tests.continuity import test_continuity as fixtures
from governance.tools.continuity import artifact_classification as ac
from governance.tools.continuity import validate_continuity as v

PROFILES = {'version': 4, 'profiles': {'record-only': {'checks': [], 'limitations': 'Fixture.'}},
            'impact': {'version': 1, 'invariants': {}, 'symbol_maps': {}},
            'commit_metadata': {'version': 2, 'classes': ['record', 'governance', 'tooling', 'runtime'], 'subclasses': []}}
FROZEN_ROWS = ('api.txt', 'contracts/', 'schemas/*.json')
ENV = {**{k: val for k, val in os.environ.items() if not k.startswith('GIT_')},
       'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.test',
       'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.test'}


def table(frozen, unfrozen=()):
    lines = [ac.HEADER, ac.SEPARATOR]
    rows = [(path, 'frozen') for path in frozen] + [(path, '-') for path in unfrozen] + [('src/', 'runtime')]
    for path, boundary in sorted(rows):
        lines.append('| `' + path + '` | record | record-only | ' + boundary + ' | - | - | - | - | - |')
    return '# Fixture classification\n\n' + '\n'.join(lines) + '\n'


class Project:
    """A disposable repository that freezes `FROZEN_ROWS` (or the rows it is given)."""
    def __init__(self, directory, name='repo'):
        self.root = Path(directory) / name
        self.root.mkdir(parents=True)
        self.git('init', '-q', '-b', 'main')

    def git(self, *args, cwd=None):
        done = subprocess.run(['git', '-C', str(cwd or self.root), *args], capture_output=True, env=ENV)
        assert done.returncode == 0, done.stderr.decode()
        return done.stdout.decode().strip()

    def write(self, name, text, binary=False):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if binary:
            path.write_bytes(text)
        else:
            path.write_text(text, encoding='utf-8', newline='\n')

    def rows(self, frozen=FROZEN_ROWS, unfrozen=()):
        self.write(ac.TABLE, table(frozen, unfrozen))
        self.write(ac.POLICY, json.dumps({'version': 4}))
        self.write(ac.PROFILES, json.dumps(PROFILES))

    def commit(self, message='Commit'):
        self.git('add', '-A')
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD')

    def freeze(self, refreeze=()):
        return v.freeze(self.root, refreeze)

    def check(self):
        return v.frozen_invariants(self.root)

    def manifest(self):
        return v.read_frozen_manifest(self.root)

    def add_contract_files(self):
        self.write('api.txt', 'api v1\n')
        self.write('contracts/a.json', '{"v": 1}\n')
        self.write('contracts/deep/b.json', '{"b": 1}\n')
        self.write('schemas/s.json', '{"s": 1}\n')
        self.write('schemas/deep/t.json', '{"t": 1}\n')
        self.write('schemas/other.txt', 'not covered by the filter\n')


class FrozenCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='frozen-manifest-')
        self.addCleanup(self.directory.cleanup)
        self.project = Project(self.directory.name)
        self.root = self.project.root

    def frozen_project(self):
        """A committed project whose contract files are frozen, with their manifest."""
        p = self.project
        p.rows()
        p.add_contract_files()
        p.write('src/app.py', 'print("app")\n')
        p.freeze()
        p.commit('Start')
        return p


class FreezingIsAlwaysAllowedTest(FrozenCase):
    """Acceptance a, b and e: the manifest takes the content as it is, whatever the history of the paths."""

    def test_freezing_in_the_commit_that_added_the_paths_passes(self):
        p = self.project
        p.rows()
        p.add_contract_files()
        added, changed, removed, untouched = p.freeze()
        self.assertEqual(sorted(added), ['api.txt', 'contracts/a.json', 'contracts/deep/b.json', 'schemas/deep/t.json',
                                         'schemas/s.json'])
        self.assertEqual((changed, removed, untouched), ([], [], []))
        p.git('add', '-A')
        self.assertTrue(p.check())  # staged, not yet committed
        p.commit()
        self.assertTrue(p.check())

    def test_the_runtime_checkpoint_commit_may_add_and_freeze_the_paths(self):
        with tempfile.TemporaryDirectory(prefix='frozen-manifest-checkpoint-') as directory:
            root = Path(directory) / 'repo'
            checkpoint, _, _ = fixtures.build_fixture(root, frozen_with='checkpoint')
            scope = lambda r: (fixtures.FIXTURE_FROZEN, fixtures.FIXTURE_RUNTIME, fixtures.FIXTURE_REQUIRED)
            with patch.object(v, 'scope', scope):
                _, facts = v.verify_repository(root)
            self.assertEqual(facts['implementation_checkpoint'], checkpoint)
            self.assertIs(facts[v.FROZEN_FACT], True)

    def test_paths_changed_after_the_last_runtime_commit_can_be_frozen(self):
        p = self.project
        p.rows(frozen=(), unfrozen=('api.txt', 'contracts/', 'schemas/*.json'))
        p.add_contract_files()
        p.write('src/app.py', 'print("app")\n')
        p.commit('Import, not frozen')
        p.write('src/app.py', 'print("app 2")\n')
        runtime = p.commit('The last runtime commit')
        p.write('api.txt', 'api v2\n')
        p.write('contracts/a.json', '{"v": 2}\n')
        p.write('schemas/s.json', '{"s": 2}\n')
        p.commit('Revise the contracts in a record commit')
        p.rows()  # the rows now freeze the folder, the filter and the file
        p.freeze()
        p.commit('Freeze')
        self.assertTrue(p.check())
        self.assertNotEqual(p.git('log', '-1', '--format=%H', '--', 'src'), p.git('rev-parse', 'HEAD'))
        self.assertEqual(p.git('log', '-1', '--format=%H', '--', 'src'), runtime)
        self.assertEqual(sorted(p.manifest()), ['api.txt', 'contracts/a.json', 'contracts/deep/b.json',
                                                'schemas/deep/t.json', 'schemas/s.json'])
        self.assertEqual(p.manifest()['contracts/a.json'], v.digest(b'{"v": 2}\n'))

    def test_one_large_commit_then_the_rows_and_the_manifest(self):
        p = self.project
        p.rows(frozen=(), unfrozen=('contracts/',))
        for number in range(60):
            p.write(f'contracts/part{number}.json', '{"n": %d}\n' % number)
        p.write('src/app.py', 'print("app")\n')
        p.commit('One large import')
        p.rows(frozen=('contracts/',))
        p.freeze()
        p.commit('Freeze')
        self.assertTrue(p.check())
        self.assertEqual(len(p.manifest()), 60)

    def test_the_rows_and_the_paths_in_one_commit(self):
        p = self.project
        p.rows()
        p.add_contract_files()
        p.freeze()
        p.commit('Everything at once')
        self.assertTrue(p.check())

    def test_the_three_step_order_still_passes(self):
        p = self.project
        p.rows(frozen=(), unfrozen=('api.txt', 'contracts/', 'schemas/*.json'))
        p.add_contract_files()
        p.write('src/app.py', 'print("app")\n')
        p.commit('Import, not frozen')
        p.write('src/app.py', 'print("app 2")\n')
        p.commit('A small runtime commit')
        p.rows()
        p.freeze()
        p.commit('Freeze')
        self.assertTrue(p.check())

    def test_a_project_without_frozen_rows_needs_no_manifest(self):
        p = self.project
        p.rows(frozen=(), unfrozen=('contracts/',))
        p.write('contracts/a.json', '{}\n')
        p.commit()
        self.assertIsNone(p.manifest())
        self.assertTrue(p.check())
        added, *_ = p.freeze()  # nothing to record
        self.assertEqual(added, [])
        self.assertEqual(p.manifest(), {})


class AFrozenFileCannotChangeTest(FrozenCase):
    """Acceptance c: after a freeze, each difference is refused."""

    def setUp(self):
        super().setUp()
        self.frozen_project()
        self.assertTrue(self.project.check())

    def test_a_changed_byte(self):
        for name in ('api.txt', 'contracts/deep/b.json', 'schemas/s.json'):
            with self.subTest(name=name):
                original = (self.root / name).read_bytes()
                (self.root / name).write_bytes(original + b' ')
                with self.assertRaisesRegex(ValueError, 'Frozen content changed: ' + name):
                    self.project.check()
                (self.root / name).write_bytes(original)
        self.assertTrue(self.project.check())

    def test_an_added_file(self):
        for name in ('contracts/new.json', 'schemas/deep/new.json', 'contracts/deep/er/new.txt'):
            with self.subTest(name=name):
                self.project.write(name, 'new\n')
                with self.assertRaisesRegex(ValueError, 'Frozen file inventory changed.*' + name):
                    self.project.check()
                (self.root / name).unlink()
        self.assertTrue(self.project.check())

    def test_a_file_the_rows_do_not_cover_is_free(self):
        self.project.write('schemas/other.txt', 'changed freely\n')
        self.project.write('src/app.py', 'print("changed")\n')
        self.assertTrue(self.project.check())

    def test_a_removed_file(self):
        (self.root / 'contracts' / 'a.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Frozen file inventory changed.*contracts/a.json'):
            self.project.check()

    def test_a_staged_difference(self):
        original = (self.root / 'contracts' / 'a.json').read_bytes()
        self.project.write('contracts/a.json', '{"v": "staged"}\n')
        self.project.git('add', 'contracts/a.json')
        (self.root / 'contracts' / 'a.json').write_bytes(original)  # the working tree matches again; the index does not
        with self.assertRaisesRegex(ValueError, 'Frozen index differs from the manifest: contracts/a.json'):
            self.project.check()

    def test_a_staged_removal_and_a_staged_addition(self):
        self.project.git('rm', '-q', '--cached', 'api.txt')
        with self.assertRaisesRegex(ValueError, 'Frozen index differs from the manifest: api.txt'):
            self.project.check()
        self.project.git('add', 'api.txt')
        self.assertTrue(self.project.check())

    def test_line_endings_do_not_change_an_entry(self):
        self.project.write('contracts/a.json', b'{"v": 1}\r\n', binary=True)
        self.assertEqual(self.project.manifest()['contracts/a.json'], v.digest(b'{"v": 1}\n'))
        self.assertEqual(v.digest(b'{"v": 1}\r\n'), v.digest(b'{"v": 1}\n'))
        self.project.git('add', 'contracts/a.json')
        self.assertTrue(self.project.check())


class AnyHistoryGivesTheSameResultTest(FrozenCase):
    """Acceptance d: the check reads no history, so fresh history and a shallow clone give the same result."""

    def test_fresh_history_and_a_depth_one_clone(self):
        self.frozen_project()
        self.project.write('contracts/later.json', '{}\n')  # more history, then frozen as it is
        self.project.commit('Add a contract')
        self.project.freeze()
        self.project.commit('Freeze it')
        self.assertTrue(self.project.check())
        with tempfile.TemporaryDirectory(prefix='frozen-manifest-clones-') as directory:
            fresh = Project(directory, 'fresh')  # the same tree in a single commit
            for path in sorted(p for p in self.project.git('ls-files').splitlines()):
                fresh.write(path, (self.root / path).read_bytes(), binary=True)
            fresh.commit('The whole tree at once')
            shallow = Path(directory) / 'shallow'
            self.project.git('clone', '-q', '--depth', '1', self.root.as_uri(), str(shallow), cwd=directory)
            self.assertEqual(self.project.git('rev-list', '--count', 'HEAD', cwd=shallow), '1')
            for root in (fresh.root, shallow):
                with self.subTest(root=root.name):
                    self.assertTrue(v.frozen_invariants(root))
            (shallow / 'contracts' / 'a.json').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'Frozen content changed'):
                v.frozen_invariants(shallow)


class ChangingAFreezeIsRestrictedTest(FrozenCase):
    """Acceptance f, and the rule of the command: only a named path changes or leaves the manifest."""

    def test_a_stale_checkpoint_is_still_refused(self):
        with tempfile.TemporaryDirectory(prefix='frozen-manifest-stale-') as directory:
            root = Path(directory) / 'repo'
            fixtures.build_fixture(root, frozen_with='checkpoint')
            (root / 'src' / 'app.txt').write_text('runtime v3\n', encoding='utf-8', newline='\n')
            for args in (('add', '-A'), ('commit', '-q', '-m', 'Another runtime commit')):
                subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=ENV, check=True)
            scope = lambda r: (fixtures.FIXTURE_FROZEN, fixtures.FIXTURE_RUNTIME, fixtures.FIXTURE_REQUIRED)
            with patch.object(v, 'scope', scope):
                with self.assertRaisesRegex(ValueError, 'Stale implementation checkpoint'):
                    v.verify_repository(root)

    def test_freeze_never_changes_an_existing_entry(self):
        p = self.frozen_project()
        before = (self.root / ac.FROZEN_MANIFEST).read_bytes()
        p.write('contracts/a.json', '{"v": "edited"}\n')
        added, changed, removed, untouched = p.freeze()
        self.assertEqual((added, changed, removed, untouched), ([], [], [], ['contracts/a.json']))
        self.assertEqual((self.root / ac.FROZEN_MANIFEST).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, 'Frozen content changed: contracts/a.json'):
            p.check()

    def test_freeze_adds_the_new_entries_and_leaves_the_others(self):
        p = self.frozen_project()
        p.write('contracts/a.json', '{"v": "edited"}\n')
        p.write('contracts/new.json', '{"new": 1}\n')
        added, changed, removed, untouched = p.freeze()
        self.assertEqual((added, changed, removed, untouched), (['contracts/new.json'], [], [], ['contracts/a.json']))
        self.assertEqual(p.manifest()['contracts/a.json'], v.digest(b'{"v": 1}\n'))
        self.assertEqual(p.manifest()['contracts/new.json'], v.digest(b'{"new": 1}\n'))

    def test_a_refreeze_changes_only_the_named_entry(self):
        p = self.frozen_project()
        p.write('contracts/a.json', '{"v": "edited"}\n')
        p.write('api.txt', 'api edited\n')
        added, changed, removed, untouched = p.freeze(['contracts/a.json'])
        self.assertEqual((added, changed, removed, untouched), ([], ['contracts/a.json'], [], ['api.txt']))
        self.assertEqual(p.manifest()['api.txt'], v.digest(b'api v1\n'))
        with self.assertRaisesRegex(ValueError, 'Frozen content changed: api.txt'):
            p.check()
        p.freeze(['api.txt'])
        p.git('add', '-A')
        self.assertTrue(p.check())

    def test_a_refreeze_removes_the_entry_of_a_path_that_is_no_longer_frozen(self):
        p = self.frozen_project()
        (self.root / 'contracts' / 'a.json').unlink()
        with self.assertRaisesRegex(ValueError, 'Frozen file inventory changed'):
            p.check()
        added, changed, removed, untouched = p.freeze()
        self.assertEqual((added, changed, removed, untouched), ([], [], [], ['contracts/a.json']))
        self.assertIn('contracts/a.json', p.manifest())
        p.freeze(['contracts/a.json'])
        self.assertNotIn('contracts/a.json', p.manifest())
        p.git('add', '-A')
        self.assertTrue(p.check())

    def test_a_refreeze_of_an_unknown_path_is_refused(self):
        p = self.frozen_project()
        with self.assertRaisesRegex(ValueError, 'neither frozen nor in the manifest: nowhere.txt'):
            p.freeze(['nowhere.txt'])

    def test_frozen_files_without_a_manifest_fail_with_one_message(self):
        p = self.project
        p.rows()
        p.add_contract_files()
        p.commit()
        with self.assertRaisesRegex(ValueError, r'Frozen file inventory changed: no manifest entry for .*--freeze'):
            p.check()

    def test_a_manifest_that_does_not_parse_is_refused(self):
        p = self.frozen_project()
        for text in ('{', '[]', '{"version": 2, "files": {}}', '{"version": 1, "files": {}, "extra": 1}',
                     '{"version": 1, "files": {"api.txt": "abc"}}', '{"version": 1, "files": {"../x": "' + '0' * 64 + '"}}'):
            with self.subTest(text=text):
                p.write(ac.FROZEN_MANIFEST, text)
                with self.assertRaisesRegex(ValueError, 'Frozen manifest'):
                    p.check()


class FreezeCommandTest(FrozenCase):
    def run_main(self, *arguments):
        output = []
        with patch.object(v, 'ROOT', self.root), patch.object(sys, 'argv', ['validate_continuity.py', *arguments]), \
                patch('builtins.print', side_effect=lambda *a, **k: output.append(' '.join(map(str, a)))):
            code = v.main()
        return code, output

    def test_the_command_writes_a_sorted_manifest_with_lf_only(self):
        p = self.project
        p.rows()
        p.add_contract_files()
        code, output = self.run_main('--freeze')
        self.assertEqual(code, 0, output)
        raw = (self.root / ac.FROZEN_MANIFEST).read_bytes()
        self.assertNotIn(b'\r', raw)
        self.assertTrue(raw.endswith(b'}\n'))
        data = json.loads(raw)
        self.assertEqual(data['version'], 1)
        self.assertEqual(list(data['files']), sorted(data['files']))
        self.assertIn('added: api.txt', output)
        p.git('add', '-A')
        self.assertTrue(p.check())

    def test_the_command_is_idempotent_and_names_what_it_leaves(self):
        p = self.frozen_project()
        before = (self.root / ac.FROZEN_MANIFEST).read_bytes()
        code, _ = self.run_main('--freeze')
        self.assertEqual((code, (self.root / ac.FROZEN_MANIFEST).read_bytes()), (0, before))
        p.write('api.txt', 'edited\n')
        code, output = self.run_main('--freeze')
        self.assertEqual((code, (self.root / ac.FROZEN_MANIFEST).read_bytes()), (0, before))
        self.assertTrue(any(line.startswith('left as it was') and line.endswith('api.txt') for line in output), output)
        code, output = self.run_main('--freeze', '--refreeze', 'api.txt')
        self.assertIn('changed: api.txt', output)
        self.assertEqual(p.manifest()['api.txt'], v.digest(b'edited\n'))

    def test_refreeze_goes_with_freeze(self):
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            self.run_main('--refreeze', 'api.txt')


if __name__ == '__main__':
    unittest.main()
