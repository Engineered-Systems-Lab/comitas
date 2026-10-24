"""A project's expectations of which checks its paths select, held to the real selection (G056).

`project-governance/config/selector-expectations.json` is optional. Each expectation names a git pathspec and the checks
that every tracked file it matches must select (`selects`) and must not select (`never`). The check is made here, against
the selection the checkpoint tool makes for one file as its whole manifest: the project's rows, profiles, narrowable checks
and impact invariants are all in it. A failing expectation names each file and what its selection lacks or must not hold.
A project edits only its declaration; this test and the reader stay the framework's.

The fixtures below are generic: a disposable repository with a project table, policy and profiles of its own."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import checkpoint as c
from governance.tools.continuity import artifact_classification as ac
import repo_paths as rp

SHOWN = 20  # files named in one failure before "and N more"


def tracked(root, pathspec):
    """The tracked files a git pathspec matches under `root`, sorted. Magic such as `:(glob)` is the pathspec's own."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    done = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--', pathspec], env=env, capture_output=True,
                          timeout=60)
    if done.returncode != 0:
        raise ValueError('git does not accept the pathspec %r: %s' % (pathspec, done.stderr.decode('utf-8', 'replace').strip()))
    return sorted(name for name in done.stdout.decode('utf-8').split(chr(0)) if name)


def failures(tool, policy, profiles, expectations, files=tracked):
    """One message per failing expectation; empty when every tracked file of every pathspec selects as declared.

    A file is judged by `select_profile` with itself as the manifest, so its row, its profile, the narrowing by its
    invariants (the project's included) and the always-run checks are exactly those of a change to that file. A file
    whose row is a symbol-map row is always judged with the whole envelope of its profile, as for a change whose symbols
    are unknown: the comparison with HEAD that picks mapped symbols is switched off, so an edit in the working tree never
    changes the verdict."""
    with patch.object(tool, 'changed_symbols', return_value=None):
        return judge(tool, policy, profiles, expectations, files)


def judge(tool, policy, profiles, expectations, files):
    found = []
    for number, expectation in enumerate(expectations, 1):
        head = 'Expectation %d (paths %r)' % (number, expectation['paths'])
        matched = files(tool.root, expectation['paths'])
        if not matched:
            found.append(head + ' matches no tracked file, so it checks nothing; correct the pathspec or remove it')
            continue
        lines = []
        for path in matched:
            try:
                name, profile = tool.select_profile([path], policy, profiles, 'auto')
            except ValueError as error:
                lines.append('  %s: no selection (%s)' % (path, error))
                continue
            ids = [check['id'] for check in profile['checks']]
            lacks = [n for n in expectation['selects'] if n not in ids]
            holds = [n for n in expectation['never'] if n in ids]
            if lacks or holds:
                lines.append('  %s (profile %s) %s' % (path, name, '; '.join(
                    ([('does not select ' + ', '.join(lacks))] if lacks else []) +
                    ([('selects ' + ', '.join(holds) + ', which it must never')] if holds else [])) +
                    '; it selects ' + ', '.join(ids)))
        if lines:
            shown = lines[:SHOWN] + (['  and %d more' % (len(lines) - SHOWN)] if len(lines) > SHOWN else [])
            found.append('%s: %d of %d tracked files select otherwise than declared (selects %s; never %s). Fix the row '
                         'of the file in the project table, or the expectation if the selection is the intended one:\n%s'
                         % (head, len(lines), len(matched), expectation['selects'], expectation['never'], '\n'.join(shown)))
    return found


def real_tool():
    tool = object.__new__(c.Checkpoint)
    tool.root = rp.ROOT
    return tool


class RealProjectTest(unittest.TestCase):
    def test_the_projects_expectations_hold_for_every_tracked_file_they_match(self):
        tool = real_tool()
        policy, profiles = tool.data()
        expectations = ac.selector_expectations(rp.ROOT, policy['commands'])
        self.assertEqual(failures(tool, policy, profiles, expectations), [])


class FixtureTest(unittest.TestCase):
    """A disposable project: its own policy command, a profile that may narrow it, an invariant, three table rows."""
    FILES = ('notes/a.md', 'notes/draft.md', 'notes/api/b.md', 'other/c.txt')
    ROWS = ('| `notes/*.md` | record | docs | - | - | - | - | - | - |',
            '| `notes/api/*.md` | record | docs | - | - | docs-lint-rule | - | - | - |',
            '| `notes/draft.md` | record | record-only | - | - | - | - | - | - |',
            '| `other/*.txt` | record | record-only | - | - | - | - | - | - |')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for relative in (ac.TABLE, ac.PROFILES, ac.POLICY):
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rp.ROOT / relative, self.root / relative)
        self.write(ac.PROJECT_TABLE, '\n'.join([ac.HEADER, ac.SEPARATOR, *self.ROWS]) + '\n')
        self.write(ac.PROJECT_POLICY, json.dumps({'version': 4, 'commands': {
            'docs-check': ['{python}', '-B', '-c', 'print(1)'], 'docs-lint': ['{python}', '-B', '-c', 'print(2)']}}))
        continuity = [x for x in json.loads((rp.ROOT / ac.PROFILES).read_text(encoding='utf-8'))['profiles']['record-only']['checks']
                      if x['id'] == 'continuity']
        self.write(ac.PROJECT_PROFILES, json.dumps({'version': 4, 'profiles': {'docs': {
            'limitations': 'Fixture only.', 'narrowable': ['docs-check'],
            'checks': continuity + [{'id': 'docs-check', 'argv': ['{python}', '-B', '-c', 'print(1)']}]}},
            'impact': {'invariants': {'docs-lint-rule': {'groups': ['docs-lint']}}}}))
        for name in self.FILES:
            self.write(name, 'x\n')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        for args in (['init', '-q'], ['add', '-A']):
            subprocess.run(['git', '-C', str(self.root), *args], env=env, check=True, capture_output=True)
        self.tool = object.__new__(c.Checkpoint)
        self.tool.root = self.root
        self.policy, self.profiles = self.tool.data()

    def write(self, relative, text):
        (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
        (self.root / relative).write_text(text, encoding='utf-8', newline='\n')

    def declare(self, *expectations):
        self.write(ac.SELECTOR_EXPECTATIONS, json.dumps({'version': 1, 'expectations': list(expectations)}))
        return ac.selector_expectations(self.root, self.policy['commands'])

    def run_check(self, *expectations):
        return failures(self.tool, self.policy, self.profiles, self.declare(*expectations))

    def test_no_declaration_means_no_expectation(self):
        self.assertEqual(ac.selector_expectations(self.root, self.policy['commands']), [])

    def test_a_right_selection_passes_and_the_narrowing_by_a_project_invariant_is_the_real_one(self):
        self.assertEqual(self.run_check({'paths': 'notes/a.md', 'selects': ['docs-check'], 'never': ['docs-lint']}), [])
        # The row of notes/api/ names the invariant, so docs-lint replaces docs-check for it; continuity stays.
        self.assertEqual(self.run_check({'paths': 'notes/api/*.md', 'selects': ['continuity', 'docs-lint'],
                                         'never': ['docs-check']}), [])

    def test_a_wrong_profile_fails_with_one_message_naming_the_file_and_what_differs(self):
        found = self.run_check({'paths': 'notes/*.md', 'selects': ['docs-check']})
        self.assertEqual(len(found), 1)
        message = found[0]
        self.assertIn("Expectation 1 (paths 'notes/*.md')", message)
        self.assertIn('2 of 3 tracked files', message)  # the pathspec crosses folders: a, draft and api/b
        # api/b is narrowed by the project invariant of its row: docs-lint replaces docs-check.
        self.assertIn('notes/api/b.md (profile docs) does not select docs-check; it selects continuity, docs-lint', message)
        self.assertIn('notes/draft.md (profile record-only) does not select docs-check', message)
        self.assertNotIn('notes/a.md', message)

    def test_a_forbidden_check_is_named_with_the_whole_selection(self):
        found = self.run_check({'paths': 'notes/a.md', 'never': ['docs-check']})
        self.assertEqual(len(found), 1)
        self.assertIn('selects docs-check, which it must never; it selects continuity, docs-check', found[0])

    def test_the_pathspec_is_git_syntax_and_must_match_something(self):
        found = self.run_check({'paths': ':(glob)notes/*.md', 'selects': ['docs-check']})  # glob magic: not api/b.md
        self.assertIn('1 of 2 tracked files', found[0])
        self.assertIn('matches no tracked file', self.run_check({'paths': 'nowhere/*', 'selects': ['continuity']})[0])

    def test_a_file_with_no_row_is_reported_not_skipped(self):
        self.write('loose/d.md', 'x\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '-A'], check=True, capture_output=True)
        found = self.run_check({'paths': 'loose/*', 'selects': ['continuity']})
        self.assertEqual(len(found), 1)
        self.assertIn('loose/d.md: no selection', found[0])

    def test_many_failures_are_listed_up_to_a_limit(self):
        for number in range(SHOWN + 3):
            self.write('notes/n%02d.md' % number, 'x\n')
        self.write('notes/zz.md', 'x\n')
        subprocess.run(['git', '-C', str(self.root), 'add', '-A'], check=True, capture_output=True)
        found = self.run_check({'paths': 'notes/n*.md', 'never': ['continuity']})
        self.assertEqual(len(found), 1)
        self.assertIn('and 3 more', found[0])

    def test_a_symbol_map_row_is_judged_with_the_whole_envelope_whatever_the_working_tree_holds(self):
        tool = real_tool()
        policy, profiles = tool.data()
        path = 'governance/tools/checkpoint.py'  # a symbol-map row of the framework; `main` is a mapped symbol
        expectation = [{'paths': path, 'selects': ['governance-regressions'], 'never': []}]
        with patch.object(tool, 'changed_symbols', return_value=['main']):
            # The real selection of such an edit is narrow, which is the case the verdict must not depend on.
            narrow = {x['id'] for x in tool.select_profile([path], policy, profiles, 'auto')[1]['checks']}
            self.assertNotIn('governance-regressions', narrow)
            self.assertEqual(failures(tool, policy, profiles, expectation), [])
        self.assertEqual(failures(tool, policy, profiles, expectation), [])

    def test_only_an_absent_declaration_means_none(self):
        target = self.root / ac.SELECTOR_EXPECTATIONS
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()  # a directory at the name
        with self.assertRaisesRegex(ValueError, 'exists but is not a readable file'):
            ac.selector_expectations(self.root, self.policy['commands'])
        target.rmdir()
        target.write_bytes(bytes([255, 254]) + b' not text')  # unreadable as text
        with self.assertRaisesRegex(ValueError, 'cannot be read'):
            ac.selector_expectations(self.root, self.policy['commands'])
        target.write_text('', encoding='utf-8')  # empty is not "none" either
        with self.assertRaisesRegex(ValueError, 'cannot be read'):
            ac.selector_expectations(self.root, self.policy['commands'])
        target.unlink()
        self.assertEqual(ac.selector_expectations(self.root, self.policy['commands']), [])

    def test_selects_is_a_floor_and_never_a_ban_for_each_file_alone(self):
        # notes/api/b.md selects continuity and docs-lint: more than the floor asks is fine.
        self.assertEqual(self.run_check({'paths': 'notes/api/b.md', 'selects': ['continuity']}), [])
        # One file in the match is enough to fail it, even when the others hold.
        found = self.run_check({'paths': 'notes/*.md', 'never': ['docs-lint']})
        self.assertEqual(len(found), 1)
        self.assertIn('1 of 3 tracked files', found[0])
        self.assertIn('notes/api/b.md', found[0])

    def test_the_reader_refuses_what_would_switch_the_guard_off(self):
        good = {'paths': 'notes/*', 'selects': ['continuity']}
        for bad, text in ((dict(good, select=['continuity']), 'only "paths"'),
                          (dict(good, selects=['no-such-check']), 'names no check of the policy: no-such-check'),
                          (dict(good, never=['continuity']), 'same check'),
                          ({'paths': ''}, 'one git pathspec'),
                          ({'paths': 'notes/*'}, 'at least one check'),
                          (dict(good, selects=['continuity', 'continuity']), 'distinct')):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, text):
                self.declare(bad)
        for raw, text in (('{', 'cannot be read'), ('[]', 'must be an object'),
                          (json.dumps({'version': 2, 'expectations': [good]}), 'must be an object'),
                          (json.dumps({'expectations': []}), 'nonempty list'),
                          (json.dumps({'expectations': [good], 'extra': 1}), 'must be an object')):
            self.write(ac.SELECTOR_EXPECTATIONS, raw)
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, text):
                ac.selector_expectations(self.root, self.policy['commands'])


if __name__ == '__main__':
    unittest.main()
