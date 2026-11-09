"""Committed executable paths (the policy's `executable_paths`, `*.sh` by default) are executable in the index (mode 100755).

The check reads the index of the repository that holds this template with `git ls-files -s`, so it needs no working-tree
permission bits (they do not exist on Windows, where core.filemode is false). It never touches the disposable repositories
that other tests build: those hold their own fixture files, and this module looks only at the real repository.
It is skipped, not failed, when the template is not inside a Git repository (for example an unpacked archive).
"""
from pathlib import Path
import subprocess
import unittest
from governance.tools.continuity import artifact_classification as ac
import checkpoint as c
import repo_paths as rp


REPO = rp.ROOT


def indexed_scripts():
    try:
        out = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-s', '-z'],
                             capture_output=True, check=True).stdout.decode('utf-8')
    except (OSError, subprocess.CalledProcessError):
        return None
    patterns = ac.staged_executable_patterns(
        lambda *args: subprocess.run(['git', '-C', str(REPO), *args], capture_output=True, check=True).stdout)
    rows = []
    for entry in filter(None, out.split('\0')):
        meta, path = entry.split('\t', 1)
        if ac.is_script(path, patterns) and meta.split()[0] in ('100644', '100755'):  # regular files only
            rows.append((meta.split()[0], path))
    return rows


class ScriptModesTest(unittest.TestCase):
    def test_every_committed_executable_path_is_100755(self):
        rows = indexed_scripts()
        if rows is None:
            self.skipTest('not a Git repository')
        self.assertTrue(rows, 'expected the template to ship shell scripts')
        wrong = [f'{mode} {path}' for mode, path in rows if mode != '100755']
        self.assertEqual(wrong, [], 'set the bit with: git update-index --chmod=+x <path>')


if __name__ == '__main__':
    unittest.main()
