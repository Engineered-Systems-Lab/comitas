"""The documents show one command form for the governance tools: `python -B governance/tools/<tool>.py ...`.

The retired admission launcher (`governance_python.py`, with its `--` separator) is gone, and a second spelling would be a
permanent inconsistency again: `python3`, `py -3`, a path to an interpreter, a missing `-B`, a quoted or relative path to
the script. The test reads every Markdown file of the checkout and fails with the file, the line and the reason."""
import os
from pathlib import Path
import re
import unittest

import repo_paths as rp

# An interpreter word: python, python3, python3.13, py, with an optional .exe and an optional folder before it.
INTERPRETER = r'''(?<![\w.-])(?:[^\s`"'(]*[\\/])?(?:python[0-9.]*(?:\.exe)?|py(?:\.exe)?)'''
# A command line that runs a governance tool: an interpreter word, its options, then a path that names a tool script.
RUN = re.compile('(?P<interpreter>' + INTERPRETER + r')(?P<options>(?:\s+-\S+)*)\s+(?P<script>\S*governance/tools/[A-Za-z0-9_/]+\.py)')
RETIRED = re.compile(INTERPRETER + r'(?:\s+-\S+)*\s+\S*governance_python\.py')
CANONICAL = re.compile(r'python -B governance/tools/[a-z_]+\.py(?![A-Za-z0-9_./])')
SKIPPED_FOLDERS = ('.git', 'tmp', '_worktrees', '_review', 'node_modules')


def markdown_files(root):
    for folder, names, files in os.walk(root):
        names[:] = [n for n in names if n not in SKIPPED_FOLDERS]
        for file in files:
            if file.endswith('.md'):
                yield Path(folder) / file


def problems(line):
    """The reasons a line breaks the one command form; an empty list when it does not."""
    found = []
    if RETIRED.search(line):
        found.append('uses the retired admission launcher governance_python.py')
    for match in RUN.finditer(line):
        text = match.group(0)
        if match.group('interpreter') != 'python' or match.group('options') != ' -B' \
                or not re.fullmatch(r'governance/tools/[a-z_]+\.py', match.group('script')):
            found.append('is not the form python -B governance/tools/<tool>.py: ' + text)
    return found


class CommandFormTest(unittest.TestCase):
    def test_every_governance_tool_command_in_the_documents_is_the_short_form(self):
        found, canonical = [], 0
        for path in sorted(markdown_files(rp.ROOT)):
            for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                for reason in problems(line):
                    found.append('%s:%d %s' % (path.relative_to(rp.ROOT).as_posix(), number, reason))
                canonical += len(CANONICAL.findall(line))
        self.assertEqual(found, [])
        self.assertGreaterEqual(canonical, 30, 'the detector no longer finds the documented commands')

    def test_the_detector_accepts_the_short_form_and_names_every_other_form(self):
        good = ('python -B governance/tools/checkpoint.py --repo . inspect',
                '`python -B governance/tools/worktree.py --main \'<main-checkout>\' carry --path x`',
                'python -B governance/tools/run_tests.py -s governance/tests -p \'test_x.py\' -v',
                'python -m pip install -r governance/tools/requirements.txt',
                'python -B -m unittest -v governance.tests.lifecycle.test_checkpoint_abandon',
                'python -B governance/extensions/some_extension/run.py refresh-local',
                'the launcher governance/tools/worktree.py answers --help')
        for line in good:
            with self.subTest(line=line):
                self.assertEqual(problems(line), [])
        bad = ('python -B governance/tools/governance_python.py -- governance/tools/checkpoint.py --repo . inspect',
               'python -B governance/tools/governance_python.py --check',
               'python governance/tools/checkpoint.py --repo . inspect',
               'python3 -B governance/tools/checkpoint.py --repo . inspect',
               'py -3 -B governance/tools/checkpoint.py --repo . inspect',
               'python -B ./governance/tools/checkpoint.py --repo . inspect',
               'python -B "$ROOT/governance/tools/checkpoint.py" --repo . inspect',
               'python -B -I governance/tools/checkpoint.py --repo . inspect',
               'C:/Python313/python.exe -B governance/tools/checkpoint.py --repo . inspect',
               'python -B governance/tools/lifecycle/checkpoint_state.py')
        for line in bad:
            with self.subTest(line=line):
                self.assertTrue(problems(line), line)


if __name__ == '__main__':
    unittest.main()
