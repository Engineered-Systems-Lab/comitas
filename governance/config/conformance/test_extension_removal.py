"""The core never depends on an extension: remove every extension from a scratch copy and run the core's own tests on it.

The copy is the tracked tree in a temporary directory (a file that is new and not yet added to Git is not in it). In it the
removal script of `governance/extensions/` deletes the extensions and the extension layer, with their rows, golden entries,
policy commands, profile entries and links; then no file other than a document may still name an extension; then the copy
becomes a Git repository (the identity comes from the environment, no Git configuration is read or written) and the tests
of the areas most likely to depend on an extension run in it, each through the repository's own runner:

- the classification table, its golden pin and the real rows (every tracked file needs a row, the names in a row exist);
- the continuity checker on the copy (links, reachability, devlog settings);
- the checkpoint scope selector, which selects checks from the real table, policy and profiles;
- the shared repository paths and the commit-metadata vocabulary read from the real profiles;
- the modes of the shipped scripts, link retargeting and the agent pointer.

A full-suite run would be far too slow for a test; these are the modules that read the real configuration, which is where
a hidden dependency shows. The test skips when there is no usable Git checkout or no extension layer to remove. It never
runs itself in the copy, so it cannot recurse."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

import repo_paths as rp

REPO = rp.ROOT
LAYER = 'governance/extensions'
SCRIPT = LAYER + '/remove_extension.py'
RUNNER = 'governance/tools/run_tests.py'
BOTH = ('governance/tests', 'governance/config')
# (test module, start folders): the areas that read the real table, policy, profiles or documents.
AREAS = (
    ('test_artifact_classification.py', BOTH),
    ('test_continuity.py', BOTH),
    ('test_checkpoint_scope.py', ('governance/config',)),
    ('test_repo_paths.py', ('governance/tests',)),
    ('test_checkpoint_metadata.py', ('governance/tests',)),
    ('test_script_modes.py', ('governance/config',)),
    ('test_link_retarget.py', ('governance/tests',)),
    ('test_agent_pointer.py', ('governance/config',)),
)
IDENTITY = {'GIT_AUTHOR_NAME': 'Scratch', 'GIT_COMMITTER_NAME': 'Scratch',
            'GIT_AUTHOR_EMAIL': 'noreply@example.invalid', 'GIT_COMMITTER_EMAIL': 'noreply@example.invalid'}
TIMEOUT = 300  # seconds per command, so a hang fails the test instead of the suite


def environment():
    return {**{k: v for k, v in os.environ.items() if not k.startswith('GIT_')}, **IDENTITY, 'PYTHONDONTWRITEBYTECODE': '1'}


def run(command, cwd, check=True):
    done = subprocess.run(command, cwd=cwd, env=environment(), capture_output=True, timeout=TIMEOUT)
    text = (done.stdout + done.stderr).decode('utf-8', 'replace')
    if check and done.returncode:
        raise AssertionError(' '.join(map(str, command)) + ' exited ' + str(done.returncode) + ':\n' + text[-3000:])
    return done.returncode, text


def indexed():
    """[(mode, path)] of the repository's index, or None when this is not a usable Git checkout."""
    try:
        out = subprocess.run(['git', '-C', str(REPO), 'ls-files', '-s', '-z'], capture_output=True, check=True,
                             env=environment()).stdout.decode('utf-8')
    except (OSError, subprocess.CalledProcessError):
        return None
    return [(entry.split('\t', 1)[0].split()[0], entry.split('\t', 1)[1]) for entry in out.split('\0') if entry]


def forget_read_only(function, path, error):
    os.chmod(path, stat.S_IWRITE)
    function(path)


class RemovalTest(unittest.TestCase):
    def setUp(self):
        self.entries = indexed()
        if self.entries is None:
            self.skipTest('not a Git checkout')
        if not (REPO / SCRIPT).is_file():
            self.skipTest('no extension layer to remove')
        self.manifests = [json.loads(path.read_text(encoding='utf-8')) for path in sorted((REPO / LAYER).glob('*/extension.json'))]
        if not self.manifests:
            self.skipTest('no extension installed')
        # One parent holds the copy and the runner's `tmp` folder (which the runner puts next to the checkout).
        self.parent = Path(tempfile.mkdtemp(prefix='extension-removal-'))
        self.addCleanup(shutil.rmtree, self.parent, onexc=forget_read_only)
        self.root = self.parent / 'copy'
        for mode, path in self.entries:
            if (REPO / path).is_file():
                (self.root / path).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / path, self.root / path)

    def repository(self):
        """Turn the copy into a Git repository on main with one commit, keeping the executable bits of the index."""
        run(['git', 'init', '-q', '-b', 'main'], self.root)
        run(['git', 'add', '-A'], self.root)
        executable = [path for mode, path in self.entries if mode == '100755' and (self.root / path).is_file()]
        for start in range(0, len(executable), 50):
            run(['git', 'update-index', '--chmod=+x', '--', *executable[start:start + 50]], self.root)
        run(['git', 'commit', '-q', '-m', 'Scratch copy'], self.root)

    def core_tests(self, patterns):
        for pattern, starts in patterns:
            command = [sys.executable, '-B', RUNNER] + [part for start in starts for part in ('-s', start)] + ['-p', pattern]
            started = time.monotonic()
            code, text = run(command, self.root, check=False)
            self.assertEqual(code, 0, pattern + ' fails once the extensions are removed (' +
                             str(round(time.monotonic() - started)) + ' s):\n' + text[-3000:])

    def names_in_files(self, markers):
        """Tracked files of the copy, other than documents, that still carry a marker of a removed extension."""
        found = []
        for path in sorted(self.root.rglob('*')):
            relative = path.relative_to(self.root).as_posix()
            if not path.is_file() or relative.startswith('.git/') or relative.endswith('.md'):
                continue
            try:
                text = path.read_text(encoding='utf-8')
            except (UnicodeDecodeError, OSError):
                continue
            hits = sorted({marker for marker in markers if marker in text})
            if hits:
                found.append(relative + ': ' + ', '.join(hits))
        return found

    def test_the_core_passes_its_tests_with_every_extension_and_the_layer_removed(self):
        markers = [marker for manifest in self.manifests for marker in manifest['markers']]
        run([sys.executable, '-B', SCRIPT, '--all'], self.root)
        self.assertFalse((self.root / LAYER).exists())
        self.assertFalse((self.root / 'governance/tests/extensions').exists())
        self.assertEqual(self.names_in_files(markers), [], 'files of the core name an extension')
        self.repository()
        self.core_tests(AREAS)

    def test_removing_one_extension_keeps_the_layer_and_the_core_consistent(self):
        manifest = self.manifests[0]
        run([sys.executable, '-B', SCRIPT, manifest['name']], self.root)
        self.assertFalse((self.root / manifest['folder']).exists())
        self.assertFalse((self.root / manifest['tests']).exists())
        self.assertTrue((self.root / LAYER / 'README.md').is_file())
        self.repository()
        run([sys.executable, '-B', 'governance/tools/validate_continuity.py'], self.root)
        self.core_tests(AREAS[:1])


if __name__ == '__main__':
    unittest.main()
