"""The commands are the public interface: every documented entry point answers, and every tool path the documents name exists."""
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import repo_paths as rp
from governance.tools.worktrees import guard

# The scripts that documented command lines run. The implementation lives in packages; each of these stays at its path.
ENTRY_POINTS = ('checkpoint', 'worktree', 'local_jobs', 'run_tests', 'runtime_guard', 'validate_continuity',
                'show_classification', 'timing_record', 'commit_metadata', 'approval_words', 'adopt')
TOOL_PATH = re.compile(r'governance/tools/[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)*\.py')
DOCUMENTS = ('*.md', 'governance/**/*.md', 'devlog/*.md', 'governance/scripts/*.sh')


class EntryPointTest(unittest.TestCase):
    def test_every_documented_script_answers_help(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            for name in ENTRY_POINTS:
                with self.subTest(script=name):
                    script = rp.ROOT / rp.TOOLS / (name + '.py')
                    self.assertTrue(script.is_file(), name)
                    result = subprocess.run([sys.executable, '-B', str(script), '--help'], cwd=elsewhere,
                                            capture_output=True, text=True, timeout=120)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('usage', (result.stdout + result.stderr).lower())

    def test_every_tool_path_the_documents_name_exists(self):
        named = {}
        for pattern in DOCUMENTS:
            for document in rp.ROOT.glob(pattern):
                for path in TOOL_PATH.findall(document.read_text(encoding='utf-8')):
                    named.setdefault(path, document.relative_to(rp.ROOT).as_posix())
        self.assertIn(rp.CHECKPOINT, named)
        for path, document in sorted(named.items()):
            with self.subTest(path=path, document=document):
                self.assertTrue((rp.ROOT / path).is_file(), path + ' is named in ' + document)

    def test_the_isolated_validator_runs_the_committed_checker_without_package_files(self):
        # The guard loads the checker alone, with only its parser, under stand-in packages: no package file runs.
        with tempfile.TemporaryDirectory() as folder:
            clone = Path(folder) / 'clone'
            subprocess.run(['git', 'clone', '-q', '--no-hardlinks', str(rp.ROOT), str(clone)], check=True, capture_output=True)
            for package in ('governance/__init__.py', 'governance/tools/__init__.py', 'governance/tools/continuity/__init__.py'):
                (clone / package).write_text('raise RuntimeError("a package file ran in the isolated validator")' + chr(10))
            result = subprocess.run([sys.executable, '-I', '-B', '-c', guard.CANDIDATE_CONTINUITY,
                                     str(clone / 'governance/tools/continuity/validate_continuity.py'), str(clone)],
                                    capture_output=True, text=True, timeout=300, cwd=folder)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS', result.stdout)


if __name__ == '__main__':
    unittest.main()
