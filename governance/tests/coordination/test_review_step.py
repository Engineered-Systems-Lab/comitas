"""The review step: the candidate, the bundle, the CLI discovery, the read-only command lines and the checked publish."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from governance.tools.coordination import local_jobs as j
from governance.tools.coordination import review_step as rs
import repo_paths as rp

NAME = 'sample_job'
# Shaped like the logs of the installed CLIs: Codex's standard error and Claude Code's stream-json events.
CODEX_LOG = ('OpenAI Codex v0.0.0\n--------\nmodel: some-model\nsandbox: read-only\nreasoning effort: high\n--------\n'
             'exec\n"powershell.exe" -Command "Get-Content file.txt" in WORKDIR\n succeeded in 12ms:\none\n'
             'tokens used\n31,500\n')
CODEX_BLOCKED = ('OpenAI Codex v0.0.0\n--------\nmodel: some-model\nsandbox: read-only\nreasoning effort: none\n--------\n'
                 '2026-01-01T00:00:00Z ERROR codex_core::tools::router: error=exec_command failed: CreateProcess { message: '
                 '"Rejected(type file.txt) rejected: blocked by policy" }\ntokens used\n15,825\n')


def claude_stream(text, reads=1, denials=(), where=None, thinking=7, tool='Read'):
    events = [{'type': 'system', 'subtype': 'init', 'model': 'some-model', 'permissionMode': 'plan',
               'tools': ['Glob', 'Grep', 'Read']}]
    for number in range(reads):
        events.append({'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'id': 't%d' % number, 'name': tool,
             'input': {'file_path': str(Path(where or tempfile.gettempdir()) / 'file.txt')}}]}})
        events.append({'type': 'user', 'message': {'content': [
            {'type': 'tool_result', 'tool_use_id': 't%d' % number, 'content': 'one'}]}})
    events.append({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': text,
                   'permission_denials': list(denials),
                   'usage': {'input_tokens': 10, 'output_tokens': 5, 'output_tokens_details': {'thinking_tokens': thinking}}})
    return '\n'.join(json.dumps(event) for event in events).encode()


def git(repo, *args):
    done = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True)
    return done.stdout.decode().strip()


class ReviewFixture(unittest.TestCase):
    """A disposable primary with the role documents, a mission worktree with a candidate, and a fake lookup naming it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / 'primary'
        self.root.mkdir()
        git(self.root, 'init', '-b', 'main')
        git(self.root, 'config', 'user.name', 'Fixture')
        git(self.root, 'config', 'user.email', 'fixture@example.invalid')
        (self.root / '.gitignore').write_text('/.local/\n')
        for relative in (j.REVIEWER_DOCUMENT, j.READING_DOCUMENT):
            (self.root / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rp.ROOT / relative, self.root / relative)
        (self.root / 'file.txt').write_text('one\n')
        git(self.root, 'add', '.')
        git(self.root, 'commit', '-m', 'base')
        self.parent = git(self.root, 'rev-parse', 'HEAD')
        (self.root / 'file.txt').write_text('one\ntwo\n')
        git(self.root, 'add', 'file.txt')
        self.tree = git(self.root, 'write-tree')
        git(self.root, 'reset', '--hard', self.parent)
        self.folder = base / '_review' / 'mission'
        self.folder.mkdir(parents=True)
        self.preview = 'a' * 32
        self.found = {'handle': NAME, 'read_at': 'now', 'cannot_find': [], 'missions': [self.mission(self.preview)]}
        self.lookup = lambda primary, name: self.found
        j.safe_directory(self.root, create=True)

    def mission(self, preview):
        return {'owner': 'mission', 'state': 'live', 'worktree': {'path': str(self.root)},
                'review_folder': {'path': str(self.folder)},
                'preview': {'found': True, 'id': preview, 'parent': self.parent, 'tree': self.tree}}

    def log(self, folder=None):
        return CODEX_LOG.replace('WORKDIR', str(folder or self.root))

    def answer(self, **changes):
        values = dict(handle=NAME, preview=self.preview, parent=self.parent, tree=self.tree,
                      coverage='Coverage: complete', total='Total: 1 finding, 0 blocker, 0 major, 1 minor, 0 note')
        values.update(changes)
        return ('Review of %(handle)s, preview %(preview)s\nCandidate: parent %(parent)s, tree %(tree)s\nRound: 1\n'
                '%(coverage)s\n\nF1 [minor] file.txt:2\nWhat: x\nWhy: y\n\n%(total)s\n' % values)


class CandidateTest(ReviewFixture):
    def test_the_one_live_mission_with_a_preview_is_captured(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        self.assertEqual((chosen['preview_id'], chosen['parent'], chosen['tree']), (self.preview, self.parent, self.tree))

    def test_no_mission_or_two_missions_or_no_preview_refuse_and_guess_nothing(self):
        self.found['missions'] = []
        with self.assertRaises(rs.ReviewUnavailable):
            rs.candidate(self.root, NAME, self.lookup)
        self.found['missions'] = [self.mission(self.preview), dict(self.mission('b' * 32), owner='other')]
        with self.assertRaises(rs.ReviewUnavailable):
            rs.candidate(self.root, NAME, self.lookup)
        self.found['missions'] = [dict(self.mission(self.preview), preview={'found': False, 'reason': 'none'})]
        with self.assertRaises(rs.ReviewUnavailable):
            rs.candidate(self.root, NAME, self.lookup)

    def test_a_preview_that_names_a_short_or_invalid_id_is_refused(self):
        self.found['missions'][0]['preview']['tree'] = self.tree[:12]
        with self.assertRaises(rs.ReviewUnavailable):
            rs.candidate(self.root, NAME, self.lookup)


class BundleTest(ReviewFixture):
    def test_the_bundle_holds_the_candidate_the_diff_the_earlier_rounds_and_what_is_missing(self):
        (self.folder / ('review-' + 'b' * 32 + '.md')).write_text('EARLIER FINDING TEXT\n')
        (self.folder / ('response-' + 'b' * 32 + '.md')).write_text('EARLIER RESPONSE TEXT\n')
        text = rs.bundle_text(self.root, NAME, None, rs.candidate(self.root, NAME, self.lookup))
        for needle in ('Preview id: ' + self.preview, 'Parent: ' + self.parent, 'Tree: ' + self.tree, '+two',
                       'EARLIER FINDING TEXT', 'EARLIER RESPONSE TEXT', 'WHAT IS NOT HERE', 'no record exists for ' + NAME,
                       'file.txt'):
            self.assertIn(needle, text)
        self.assertNotIn('read_at', text)

    def test_round_one_reviews_the_whole_change_and_a_later_round_gets_the_diff_since_the_last_reviewed_preview(self):
        first = rs.bundle_text(self.root, NAME, None, rs.candidate(self.root, NAME, self.lookup))
        self.assertIn('Round: 1 (review the whole change)', first)
        self.assertNotIn('CHANGED SINCE THE LAST ROUND', first)
        (self.root / 'file.txt').write_text('one\nzero\n')
        git(self.root, 'add', 'file.txt')
        earlier = git(self.root, 'write-tree')
        git(self.root, 'reset', '--hard', self.parent)
        (self.folder / ('review-' + 'b' * 32 + '.md')).write_text('EARLIER FINDING TEXT\n')
        self.found['missions'][0]['history'] = {'found': True, 'entries': [
            {'seq': 1, 'preview_id': 'b' * 32, 'tree': earlier}, {'seq': 2, 'preview_id': self.preview, 'tree': self.tree}]}
        later = rs.bundle_text(self.root, NAME, None, rs.candidate(self.root, NAME, self.lookup))
        self.assertIn('Round: 2 (judge the earlier findings and only the lines changed since the round before)', later)
        since = later.split('CHANGED SINCE THE LAST ROUND', 1)[1]
        self.assertIn('git diff %s %s' % (earlier, self.tree), since)
        self.assertIn('-zero', since)
        (self.folder / ('review-' + 'c' * 32 + '.md')).write_text('LATER FINDING TEXT\n')  # reviewed, its history record refused
        refused = rs.bundle_text(self.root, NAME, None, rs.candidate(self.root, NAME, self.lookup))
        self.assertIn('names no tree for an earlier reviewed preview', refused)  # never the older baseline, which could hide a revert
        self.assertNotIn('git diff %s' % earlier, refused)
        self.found['missions'][0]['history'] = {'found': False, 'reason': 'none'}
        unknown = rs.bundle_text(self.root, NAME, None, rs.candidate(self.root, NAME, self.lookup))
        self.assertIn('names no tree for an earlier reviewed preview', unknown)

    def test_a_bundle_over_the_limit_is_refused_with_sizes_and_never_cut(self):
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.bundle_text(self.root, NAME, 200, rs.candidate(self.root, NAME, self.lookup))
        self.assertIn('nothing is cut', str(caught.exception))

    def test_the_brief_prints_the_supplied_material_whole_and_drops_the_lookup_commands(self):
        supplied = rs.bundle_text(self.root, NAME, None, rs.candidate(self.root, NAME, self.lookup))
        brief = j.review_brief(self.root, NAME, supplied)
        self.assertIn('THE WORK IS SUPPLIED BELOW', brief)
        self.assertIn(supplied.strip('\n'), brief)
        self.assertNotIn("where '%s'" % NAME, brief)
        self.assertTrue(brief.index('ANSWER IN THIS FORMAT') < brief.index('SUPPLIED MATERIAL') < brief.index('This brief reads only'))
        self.assertNotIn('THE WORK IS SUPPLIED', j.review_brief(self.root, NAME))
        role = (rp.ROOT / 'governance/ROLE-REVIEWER.md').read_text(encoding='utf-8')
        self.assertIn("git -C '<primary-checkout>' diff <parent> <tree>", role)
        self.assertNotIn('safe.directory', role + j.review_brief(self.root, NAME))


class DiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.calls = []
        self.saved = rs.execute
        self.addCleanup(setattr, rs, 'execute', self.saved)
        rs.execute = self.fake

    def fake(self, argv, stdin=None, timeout=60, cwd=None):
        self.calls.append(argv)
        path = Path(argv[0])
        text = path.read_text() if path.is_file() else ''
        if argv[1:] == ['--version']:
            return 0, ('codex-cli ' + text.strip()).encode(), b''
        return 0, (' '.join(rs.CODEX_FLAGS + rs.CLAUDE_FLAGS) if 'lacks' not in path.name else '--sandbox').encode(), b''

    def executable(self, folder, name, version):
        target = self.base / folder
        target.mkdir(parents=True, exist_ok=True)
        path = target / name
        path.write_text(version)
        return path

    def test_a_configured_executable_comes_first_and_a_broken_one_is_refused_not_replaced(self):
        good = self.executable('cfg', 'codex.exe', '0.1.0')
        found = rs.find_cli('codex', {'COMITAS_CODEX': str(good), 'PATH': str(self.base)})
        self.assertEqual((found['source'], found['path']), ('COMITAS_CODEX', str(good)))
        with self.assertRaises(rs.ReviewUnavailable):
            rs.find_cli('codex', {'COMITAS_CODEX': str(self.base / 'missing.exe')})

    def test_the_bundled_copy_is_the_fallback_and_the_highest_version_wins(self):
        self.executable('app/OpenAI/Codex/bin/aaa', 'codex.exe', '0.9.0')
        self.executable('app/OpenAI/Codex/bin/zzz', 'codex.exe', '0.167.0-alpha.3')
        found = rs.find_cli('codex', {'LOCALAPPDATA': str(self.base / 'app'), 'PATH': ''})
        self.assertIn('zzz', found['path'])
        self.assertEqual(found['source'], 'bundled with the Codex app')

    def test_no_cli_or_a_cli_without_a_needed_flag_is_unavailable(self):
        with self.assertRaises(rs.ReviewUnavailable):
            rs.find_cli('codex', {'PATH': '', 'LOCALAPPDATA': str(self.base / 'nowhere')})
        lacking = self.executable('cfg', 'codex-lacks.exe', '0.1.0')
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.find_cli('codex', {'COMITAS_CODEX': str(lacking)})
        self.assertIn('lacks the flags', str(caught.exception))

    def test_version_keys_order_by_number(self):
        self.assertGreater(rs.version_key('codex-cli 0.171.0-alpha.13'), rs.version_key('codex-cli 0.167.0-alpha.3.2'))


class CommandAndPublishTest(ReviewFixture):
    def cli(self, kind):
        return {'kind': kind, 'path': '/x/' + kind, 'version': '1.0', 'source': 'PATH'}

    def test_the_codex_command_is_read_only_isolated_and_reads_the_prompt_from_standard_input(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        argv = rs.command(self.cli('codex'), chosen, Path('answer.txt'))
        for flag in ('-s', 'read-only', '--ephemeral', '--ignore-user-config', 'mcp_servers={}', '-o', '-C',
                     'model_reasoning_effort="high"'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-1], '-')
        self.assertNotIn('workspace-write', argv)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)

    def test_the_codex_command_names_a_windows_sandbox_mode_on_windows_only(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        windows = rs.command(self.cli('codex'), chosen, Path('answer.txt'), True)
        self.assertEqual(windows[windows.index('windows.sandbox="unelevated"') - 1], '-c')
        self.assertNotIn('windows.sandbox', ' '.join(rs.command(self.cli('codex'), chosen, Path('answer.txt'), False)))

    def test_the_claude_command_has_read_tools_only_and_no_settings_servers_or_session(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        argv = rs.command(self.cli('claude'), chosen, Path('answer.txt'))
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob')
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'plan')
        self.assertEqual(argv[argv.index('--setting-sources') + 1], '')
        self.assertEqual(argv[argv.index('--effort') + 1], 'high')
        self.assertEqual(argv[argv.index('--output-format') + 1], 'stream-json')
        self.assertIn('--verbose', argv)
        for flag in ('--strict-mcp-config', '--no-session-persistence'):
            self.assertIn(flag, argv)
        for danger in ('--dangerously-skip-permissions', 'bypassPermissions', 'Bash', 'Edit', 'Write'):
            self.assertNotIn(danger, ' '.join(argv))

    def test_an_answer_that_names_the_captured_candidate_passes_and_anything_else_is_refused(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        rs.check_answer(self.answer(), NAME, chosen)
        rs.check_answer(self.answer(total='Total: 0 findings'), NAME, chosen)
        for broken in (self.answer(preview='c' * 32), self.answer(tree='d' * 40), self.answer(handle='other_job'),
                       self.answer(total='nothing'), 'free text only', self.answer(coverage=''),
                       self.answer(coverage='Coverage: maybe'),
                       self.answer(coverage='Coverage: complete\nCoverage: complete')):
            with self.subTest(broken=broken[:40]), self.assertRaises(rs.ReviewUnavailable):
                rs.check_answer(broken, NAME, chosen)

    def test_text_before_the_header_is_left_out_and_a_wrong_candidate_after_it_is_still_refused(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        clean = self.answer().strip('\n')
        for lead, body in (('FINDINGS FORMAT\n', self.answer()), ('Here is my review.\n\n```text\n', self.answer() + '```\n'),
                           ('', self.answer())):
            with self.subTest(lead=lead):
                self.assertEqual(rs.check_answer(lead + body, NAME, chosen), (clean, [x for x in lead.split('\n') if x.strip()]))
        for broken in ('FINDINGS FORMAT\n' + self.answer(tree='d' * 40), 'FINDINGS FORMAT\n' + self.answer(preview='c' * 32),
                       'Review of %s, preview %s\n' % (NAME, self.preview),
                       'Coverage: partial: the owning documents\n' + self.answer(),
                       'Review of %s, preview %s\nCandidate: draft\n' % (NAME, 'c' * 32) + self.answer()):
            with self.subTest(broken=broken[:60]), self.assertRaises(rs.ReviewUnavailable):
                rs.check_answer(broken, NAME, chosen)

    def test_the_total_line_takes_singular_plural_the_copied_template_and_trailing_spaces(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        for total in ('Total: 2 findings, 1 blocker, 0 major, 0 minor, 1 note',
                      'Total: 3 findings, 2 blockers, 0 major, 0 minor, 1 note',
                      'Total: 1 finding, 0 blocker, 0 major, 0 minor, 1 note',
                      'Total: 4 findings, 0 blockers, 1 major, 1 minor, 2 notes',
                      'Total: 0 finding(s), 0 blocker(s), 0 major, 0 minor, 0 note(s)',
                      'Total: 0 finding(s), 0 blocker, 0 major, 0 minor, 0 note',
                      'Total: 0 finding', 'Total: 2 findings, 0 blocker, 1 major, 1 minor, 0 note   \t'):
            with self.subTest(total=total):
                rs.check_answer(self.answer(total=total), NAME, chosen)

    def test_the_format_block_and_the_check_are_one_definition(self):
        # The Total and Coverage lines of the reviewer role's format, filled in as a reviewer copies them, must pass.
        text = (rp.ROOT / 'governance/ROLE-REVIEWER.md').read_text(encoding='utf-8')
        block = text.split('FINDINGS FORMAT\n', 1)[1].split('```', 1)[0].split('\n')
        total = next(line for line in block if line.startswith('Total: '))
        filled = (total.replace('<n>', '2').replace('<b>', '0').replace('<m>', '1').replace('<i>', '1')
                  .replace('<o>', '0'))
        self.assertTrue(rs.total_pattern('\n'.join(block)).fullmatch(filled), filled)
        coverage = next(line for line in block if line.startswith('Coverage: '))
        self.assertEqual(coverage, 'Coverage: complete | partial: <what you could not read>')
        self.assertTrue(rs.COVERAGE.fullmatch('Coverage: complete'))
        self.assertTrue(rs.COVERAGE.fullmatch('Coverage: partial: the owning documents'))

    def test_a_partial_review_is_not_saved(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.check_answer(self.answer(coverage='Coverage: partial: the sandbox blocked reads',
                                        total='Total: 0 findings'), NAME, chosen)
        self.assertIn('partial', str(caught.exception))

    def test_the_codex_log_yields_the_evidence_and_a_blocked_run_is_no_review(self):
        good = rs.codex_evidence(self.log().encode(), '', (str(self.root),))
        self.assertEqual((good['model'], good['sandbox'], good['effort'], good['reads'], good['refused'], good['tokens']),
                         ('some-model', 'read-only', 'high', 1, 0, 31500))
        self.assertEqual(rs.evidence_problems(good), [])
        bad = rs.codex_evidence(CODEX_BLOCKED.encode())
        problems = ' | '.join(rs.evidence_problems(bad))
        for needle in ('reasoning effort none', '1 command(s) refused', 'no file of the worktree read'):
            self.assertIn(needle, problems)
        self.assertEqual(bad['tokens'], 15825)

    def test_the_prompt_echoed_in_the_codex_log_is_not_evidence(self):
        # The first real run counted "blocked by policy" in a Job record the log echoed with the prompt.
        prompt = ('THE JOB RECORD\n"text": "every command was refused: rejected: blocked by policy }"\n'
                  'ERROR codex_core: rejected: blocked by policy\nreasoning effort: none\n')
        log = self.log().replace('--------\nexec', '--------\nuser\n' + prompt + '\ncodex\nexec')
        found = rs.codex_evidence(log.encode(), prompt, (str(self.root),))
        self.assertEqual((found['refused'], found['effort'], found['reads']), (0, 'high', 1))
        self.assertEqual(rs.evidence_problems(found), [])

    def test_the_claude_stream_yields_the_evidence_and_a_denied_or_readless_run_is_no_review(self):
        roots = (str(self.root),)
        good = rs.claude_evidence(claude_stream('x', reads=2, where=self.root), roots)
        self.assertEqual((good['model'], good['reads'], good['refused'], good['tokens'], good['effort']),
                         ('some-model', 2, 0, 15, 'high (passed), 7 thinking tokens'))
        self.assertEqual(rs.evidence_problems(good), [])
        for stream, needle in ((claude_stream('x', reads=0), 'no file'),
                               (claude_stream('x', where=Path(self.temp.name) / 'elsewhere'), 'no file'),
                               (claude_stream('x', where=self.root, tool='Glob'), 'no file'),
                               (claude_stream('x', where=self.root, thinking=0), 'reasoning effort'),
                               (claude_stream('x', where=self.root, denials=[{'tool_name': 'Bash'}]), '1 command(s) refused')):
            with self.subTest(needle=needle):
                self.assertIn(needle, ' '.join(rs.evidence_problems(rs.claude_evidence(stream, roots))))

    def test_a_codex_read_counts_only_in_the_worktree_and_in_milliseconds_or_seconds(self):
        roots = (str(self.root),)
        slow = self.log().replace('succeeded in 12ms:', 'succeeded in 1.20s:')
        self.assertEqual(rs.codex_evidence(slow.encode(), '', roots)['reads'], 1)
        elsewhere = self.log(Path(self.temp.name) / 'elsewhere')
        self.assertEqual(rs.codex_evidence(elsewhere.encode(), '', roots)['reads'], 0)

    def test_the_folder_of_a_codex_command_is_its_last_in_and_must_be_absolute(self):
        roots = (str(self.root),)
        looped = self.log().replace('-Command "Get-Content file.txt" in ', '-Command "foreach ($f in ls) { cat $f }" in ')
        self.assertEqual(rs.codex_evidence(looped.encode(), '', roots)['reads'], 1)
        spaced = Path(self.temp.name) / 'work in progress'
        spaced.mkdir()
        self.assertEqual(rs.codex_evidence(self.log(spaced).encode(), '', (str(spaced),))['reads'], 1)
        relative = self.log('relative/folder')
        self.assertEqual(rs.codex_evidence(relative.encode(), '', roots)['reads'], 0)

    def test_a_claude_log_without_thinking_tokens_shows_no_reasoning(self):
        events = [json.loads(line) for line in claude_stream('x', where=self.root).decode().split('\n')]
        del events[-1]['usage']['output_tokens_details']
        stream = '\n'.join(json.dumps(event) for event in events).encode()
        self.assertIn('reasoning effort', ' '.join(rs.evidence_problems(rs.claude_evidence(stream, (str(self.root),)))))

    def test_a_log_that_no_longer_states_a_fact_is_refused(self):
        silent = rs.codex_evidence(('some other output in %s\n succeeded in 3ms:\n' % self.root).encode(), '',
                                   (str(self.root),))
        self.assertEqual(silent['reads'], 1)
        problems = ' | '.join(rs.evidence_problems(silent))
        for needle in ('reasoning effort not stated', 'the sandbox', 'the tokens used'):
            self.assertIn(needle, problems)
        events = [json.loads(line) for line in claude_stream('x', where=self.root).decode().split('\n')]
        del events[-1]['permission_denials']
        stream = '\n'.join(json.dumps(event) for event in events).encode()
        self.assertIn('does not state whether', ' '.join(rs.evidence_problems(rs.claude_evidence(stream, (str(self.root),)))))

    def test_a_run_that_loses_the_race_keeps_the_first_file(self):
        target = self.folder / ('review-%s.md' % self.preview)
        name = 'rename' if os.name == 'nt' else 'link'
        saved = getattr(os, name)

        def late(source, path):  # the other run publishes after this one checked and before it moves its file
            Path(path).write_text('first\n')
            return saved(source, path)
        setattr(os, name, late)
        self.addCleanup(setattr, os, name, saved)
        with self.assertRaises(rs.ReviewUnavailable):
            rs.publish(self.folder, self.preview, 'second', False)
        self.assertEqual(target.read_text(), 'first\n')
        self.assertEqual([p.name for p in self.folder.iterdir()], [target.name])

    def test_a_save_the_file_system_refuses_is_a_refusal_not_a_crash(self):
        name = 'rename' if os.name == 'nt' else 'link'
        saved = getattr(os, name)

        def denied(source, path):
            raise PermissionError(5, 'Access is denied')
        setattr(os, name, denied)
        self.addCleanup(setattr, os, name, saved)
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.publish(self.folder, self.preview, 'text', False)
        self.assertIn('nothing was saved', str(caught.exception))
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_publish_keeps_an_existing_file_unless_replace_is_given_and_writes_lf(self):
        target = rs.publish(self.folder, self.preview, 'line one\nline two', False)
        self.assertEqual(target.read_bytes(), b'line one\nline two\n')
        with self.assertRaises(rs.ReviewUnavailable):
            rs.publish(self.folder, self.preview, 'other', False)
        self.assertEqual(target.read_bytes(), b'line one\nline two\n')
        rs.publish(self.folder, self.preview, 'newer', True)
        self.assertEqual(target.read_bytes(), b'newer\n')
        self.assertEqual([p.name for p in self.folder.iterdir()], [target.name])


class RunTest(ReviewFixture):
    def setUp(self):
        super().setUp()
        self.runs = []
        self.saved = rs.execute, rs.find_cli
        self.addCleanup(lambda: (setattr(rs, 'execute', self.saved[0]), setattr(rs, 'find_cli', self.saved[1])))
        rs.find_cli = lambda kind, environ=None: {'kind': kind, 'path': '/x/' + kind, 'version': '9.9', 'source': 'PATH'}

    def reply(self, text, code=0, log=None, stream=None):
        def fake(argv, stdin=None, timeout=60, cwd=None):
            self.runs.append((argv, stdin, cwd))
            if argv[0].endswith('codex'):
                Path(argv[argv.index('-o') + 1]).write_bytes(text.encode('utf-8'))
                return code, b'', (log if log is not None else self.log()).encode()
            return code, stream if stream is not None else claude_stream(text, where=self.root), b'diagnostics'
        rs.execute = fake

    def test_a_refused_answer_is_kept_with_the_reason_and_the_evidence_and_nothing_is_saved(self):
        partial = self.answer(coverage='Coverage: partial: the sandbox blocked reads', total='Total: 0 findings')
        for text, log, needle in ((self.answer(total='Total: nothing'), None, 'Total line'),
                                  (partial, None, 'partial'),
                                  (self.answer(), CODEX_BLOCKED, 'refused by the sandbox')):
            self.reply(text, log=log)
            with self.subTest(needle=needle), self.assertRaises(rs.ReviewRefused) as caught:
                rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
            self.assertIn(needle, str(caught.exception))
            self.assertEqual(caught.exception.answer, text)
            self.assertIn('seconds', caught.exception.evidence)
            self.assertEqual(list(self.folder.iterdir()), [])

    def test_a_refused_answer_is_the_raw_text_and_survives_a_failed_exit_and_a_failed_save(self):
        fenced = '```text\n' + self.answer(total='Total: nothing') + '```\n'
        self.reply(fenced)
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertEqual(caught.exception.answer, fenced)
        self.reply(self.answer(), code=1)
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertIn('exit 1', str(caught.exception))
        self.assertEqual(caught.exception.answer, self.answer())
        self.assertEqual(caught.exception.evidence['reads'], 1)
        calls = {'n': 0}

        def moving(primary, name):
            calls['n'] += 1
            if calls['n'] > 1:
                self.found['missions'] = [self.mission('f' * 32)]
            return self.found
        self.reply(self.answer())
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=moving)
        self.assertIn('changed during the run', str(caught.exception))
        self.assertEqual(caught.exception.answer, self.answer())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_an_os_error_while_saving_and_a_timeout_keep_the_answer(self):
        saved = rs.publish

        def broken(*args, **kwargs):
            raise OSError(28, 'No space left on device')
        self.addCleanup(setattr, rs, 'publish', saved)
        rs.publish = broken
        self.reply(self.answer())
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertIn('No space left', str(caught.exception))
        self.assertEqual(caught.exception.answer, self.answer())
        rs.publish = saved

        real = rs.processes.run

        def slow(argv, **kwargs):  # the CLI printed its result, then the run timed out; Git runs as usual
            if argv[0] != '/x/claude':
                return real(argv, **kwargs)
            raise subprocess.TimeoutExpired(argv, 1, output=claude_stream(self.answer(), where=self.root))
        self.addCleanup(setattr, rs.processes, 'run', real)
        rs.processes.run = slow
        rs.execute = self.saved[0]  # the real execute, over the fake processes.run
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'claude', self.preview, lookup=self.lookup)
        self.assertIn('timed out', str(caught.exception))
        self.assertEqual(caught.exception.answer, self.answer())

    def test_the_reviewer_is_given_the_format_of_the_code_that_checks_it(self):
        # The primary may still hold an older reviewer role (a run of a candidate before it lands): the brief takes
        # the format from the checkout of the running code, so format and check cannot disagree.
        role = self.root / j.REVIEWER_DOCUMENT
        role.write_text(role.read_text(encoding='utf-8').replace('Coverage: complete | partial: <what you could not read>\n', ''),
                        encoding='utf-8')
        self.reply(self.answer())
        rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertIn(b'Coverage: complete | partial: <what you could not read>', self.runs[0][1])
        self.assertNotIn('Coverage:', j.review_brief(self.root, NAME))

    def test_a_log_whose_counts_are_not_numbers_is_no_evidence_and_keeps_the_answer(self):
        events = [json.loads(line) for line in claude_stream(self.answer(), where=self.root).decode().split('\n')]
        events[-1]['usage']['output_tokens'] = '5'
        self.reply(self.answer(), stream='\n'.join(json.dumps(event) for event in events).encode())
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'claude', self.preview, lookup=self.lookup)
        self.assertIn('the tokens used', str(caught.exception))
        self.assertEqual(caught.exception.answer, self.answer())

    def test_a_claude_run_that_read_nothing_is_refused(self):
        self.reply(self.answer(), stream=claude_stream(self.answer(), reads=0))
        with self.assertRaises(rs.ReviewRefused) as caught:
            rs.run(self.root, NAME, 'claude', self.preview, lookup=self.lookup)
        self.assertIn('no file of the worktree', str(caught.exception))

    def test_the_command_prints_a_refused_answer_whole_after_the_reason(self):
        answer = 'Partial review. The sandbox blocked local read commands.\nTotal: 0 finding(s)'

        def refused(*args, **kwargs):
            raise rs.ReviewRefused('the answer does not begin with "Review of"; it is not saved', answer,
                                   {'effort': 'none', 'reads': 0, 'refused': 3, 'seconds': 4})
        self.addCleanup(setattr, rs, 'run', rs.run)
        rs.run = refused
        out, err = io.TextIOWrapper(io.BytesIO(), encoding='utf-8'), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rs.main(['--repo', str(self.root), 'run', NAME, '--preview', self.preview, '--reviewer', 'codex'])
        out.flush()
        printed = out.buffer.getvalue().decode('utf-8')
        self.assertEqual(code, 2)
        self.assertIn('REVIEW NOT SAVED', err.getvalue())
        self.assertIn('NOT SAVED: the answer does not begin', printed)
        self.assertIn('reasoning effort none, 0 read(s) succeeded, 0 failed, 3 refused', printed)
        self.assertIn('Do not save this answer by hand', printed)
        self.assertTrue(printed.rstrip('\n').endswith(answer))

    def test_a_codex_run_binds_checks_and_saves_the_findings_with_the_prompt_on_standard_input(self):
        self.reply(self.answer())
        done = rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        saved = Path(done['saved'])
        self.assertEqual(saved.name, 'review-%s.md' % self.preview)
        text = saved.read_text(encoding='utf-8')
        self.assertTrue(text.startswith(self.answer() + '\n'))
        self.assertTrue(text.rstrip('\n').split('\n')[-1].startswith(rs.SAVED_BY + 'codex 9.9; Run: model some-model'))
        argv, stdin, cwd = self.runs[0]
        self.assertIn(b'Preview id: ' + self.preview.encode(), stdin)
        self.assertEqual(cwd, str(self.root))
        self.assertEqual((done['version'], done['found_by']), ('9.9', 'PATH'))

    def test_a_claude_run_takes_the_result_field_not_the_diagnostics(self):
        self.reply(self.answer())
        done = rs.run(self.root, NAME, 'claude', self.preview, lookup=self.lookup)
        self.assertTrue(Path(done['saved']).read_text(encoding='utf-8').startswith(self.answer() + '\n' + rs.SAVED_BY))

    def test_a_failed_wrong_or_late_run_saves_nothing_and_keeps_an_existing_file(self):
        target = self.folder / ('review-%s.md' % self.preview)
        for text, code in ((self.answer(), 1), (self.answer(preview='e' * 32), 0), ('', 0)):
            self.reply(text, code)
            with self.assertRaises(rs.ReviewUnavailable):
                rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
            self.assertFalse(target.exists())
        target.write_text('kept\n')
        self.reply(self.answer())
        with self.assertRaises(rs.ReviewUnavailable):
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertEqual(target.read_text(), 'kept\n')

    def test_a_preview_that_changes_during_the_run_is_not_saved(self):
        answer = self.answer()
        calls = {'n': 0}

        def moving(primary, name):
            calls['n'] += 1
            if calls['n'] > 1:
                self.found['missions'] = [self.mission('f' * 32)]
            return self.found
        self.reply(answer)
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=moving)
        self.assertIn('changed during the run', str(caught.exception))
        self.assertEqual([p.name for p in self.folder.iterdir()], [])

    def test_a_newer_preview_than_the_one_shown_is_refused_before_anything_runs(self):
        self.reply(self.answer())
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.run(self.root, NAME, 'codex', 'b' * 32, lookup=self.lookup)
        self.assertIn('not ' + 'b' * 32, str(caught.exception))
        self.assertEqual(self.runs, [])
        with self.assertRaises(rs.ReviewUnavailable):
            rs.run(self.root, NAME, 'codex', 'short', lookup=self.lookup)

    def test_a_held_lifecycle_lock_refuses_the_start_and_the_save(self):
        self.reply(self.answer())
        lock = self.root / '.local' / 'locks' / 'worktree-workflow.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('')
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertIn('nothing was started', str(caught.exception))
        self.assertEqual(self.runs, [])

        lock.unlink()
        reviewer = rs.execute

        def taken(*args, **kwargs):  # another operation takes the lock while the reviewer runs
            lock.write_text('')
            return reviewer(*args, **kwargs)
        rs.execute = taken
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertTrue(lock.exists(), 'the other operation keeps its lock')
        self.assertIn('not saved', str(caught.exception))
        self.assertEqual([p.name for p in self.folder.iterdir()], [])

    def test_the_save_holds_the_lifecycle_lock_and_releases_it(self):
        lock = self.root / '.local' / 'locks' / 'worktree-workflow.lock'
        seen = []

        def watching(primary, name):
            seen.append(lock.exists())
            return self.found
        self.reply(self.answer())
        rs.run(self.root, NAME, 'codex', self.preview, lookup=watching)
        self.assertEqual(seen, [False, True])  # the start, then the check under the lock just before the save
        self.assertFalse(lock.exists())

    def test_a_preview_racing_the_run_cannot_write_its_records_between_the_last_check_and_the_save(self):
        from governance.tools.lifecycle import preview_record
        attempts = []

        def racing(primary, name):  # a preview of the mission tries to record itself at the moment of each look
            try:
                with preview_record.records_lock(self.folder):
                    attempts.append('written')
            except ValueError:
                attempts.append('refused')
            return self.found
        saving = rs.publish

        def publish(*args, **kwargs):  # and once more at the moment of the save itself
            racing(None, None)
            return saving(*args, **kwargs)
        self.addCleanup(setattr, rs, 'publish', saving)
        rs.publish = publish
        self.reply(self.answer())
        done = rs.run(self.root, NAME, 'codex', self.preview, lookup=racing)
        self.assertEqual(attempts, ['written', 'refused', 'refused'])  # free at the start, held from the last check through the save
        self.assertTrue(Path(done['saved']).is_file())
        self.assertFalse((self.folder / preview_record.LOCK).exists())

    def test_a_records_lock_held_at_the_save_refuses_it_and_is_left_alone(self):
        from governance.tools.lifecycle import preview_record
        lock = self.folder / preview_record.LOCK
        self.reply(self.answer())
        reviewer = rs.execute

        def taken(*args, **kwargs):  # a preview of the mission is writing its records when the run ends
            lock.write_bytes(b'')
            return reviewer(*args, **kwargs)
        rs.execute = taken
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertIn('records lock', str(caught.exception))
        self.assertIn('not saved', str(caught.exception))
        self.assertEqual([p.name for p in self.folder.iterdir()], [preview_record.LOCK])
        self.assertFalse((self.root / '.local' / 'locks' / 'worktree-workflow.lock').exists())

    def test_the_last_preview_is_read_under_the_lock_immediately_before_the_save_and_nothing_after(self):
        lock = self.root / '.local' / 'locks' / 'worktree-workflow.lock'
        reads = []

        def counting(primary, name):
            reads.append(lock.exists())
            return self.found
        self.reply(self.answer())
        done = rs.run(self.root, NAME, 'codex', self.preview, lookup=counting)
        self.assertEqual(reads, [False, True])
        self.assertTrue(Path(done['saved']).is_file())
        self.assertFalse(hasattr(rs, 'withdraw'))

    def test_a_third_round_is_refused_before_anything_runs_unless_the_lead_developer_asked(self):
        self.reply(self.answer())
        (self.folder / ('review-' + 'b' * 32 + '.md')).write_text('round one\n')
        rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)  # round 2 runs
        self.runs.clear()
        with self.assertRaises(rs.ReviewUnavailable) as again:  # another run of the same preview is round 3
            rs.run(self.root, NAME, 'codex', self.preview, True, lookup=self.lookup)
        self.assertIn('round 3', str(again.exception))
        self.assertEqual(self.runs, [])
        self.found['missions'] = [self.mission('c' * 32)]
        (self.folder / ('response-' + 'c' * 32 + '.md')).write_text('not a round\n')
        with self.assertRaises(rs.ReviewUnavailable) as caught:
            rs.run(self.root, NAME, 'codex', 'c' * 32, lookup=self.lookup)
        self.assertIn('round 3', str(caught.exception))
        self.assertIn('--extra-round', str(caught.exception))
        self.assertEqual(self.runs, [])
        self.assertFalse((self.folder / ('review-' + 'c' * 32 + '.md')).exists())
        self.reply(self.answer(preview='c' * 32))
        done = rs.run(self.root, NAME, 'codex', 'c' * 32, lookup=self.lookup, extra_round=True)
        self.assertEqual(done['round'], 3)

    def test_a_run_leaves_out_leading_text_and_says_so(self):
        self.reply('FINDINGS FORMAT\n' + self.answer())
        done = rs.run(self.root, NAME, 'claude', self.preview, lookup=self.lookup)
        self.assertEqual(done['left_out'], ['FINDINGS FORMAT'])
        self.assertTrue(Path(done['saved']).read_text(encoding='utf-8').startswith('Review of '))

    def test_no_cli_falls_back_by_refusing_and_the_command_names_the_paste_path(self):
        def missing(kind, environ=None):
            raise rs.ReviewUnavailable('no usable codex CLI')
        rs.find_cli = missing
        with self.assertRaises(rs.ReviewUnavailable):
            rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        self.assertIn('review-brief', rs.FALLBACK)
        self.assertIn('--with-diff', rs.FALLBACK)
        self.assertIn("--preview '%s'", rs.FALLBACK)
        self.assertEqual(rs.FALLBACK.count('%s'), 3)


class DocumentTest(unittest.TestCase):
    def test_the_review_step_document_names_the_offer_the_paths_and_the_command_lines(self):
        text = ' '.join((rp.ROOT / 'governance/REVIEW-STEP.md').read_text(encoding='utf-8').split())
        for needle in ('`Review first`', 'a message of its own', '--with-diff', 'never cuts the diff', '--reviewer codex',
                       '--reviewer claude', 'codex exec -s read-only', 'claude -p --permission-mode plan',
                       'COMITAS_CODEX', 'REVIEW NOT SAVED', 'A review never approves', '## When a review stops',
                       '--extra-round', 'postponed by default', 'CHANGED SINCE THE LAST ROUND', rs.SAVED_BY.strip(),
                       'never saves a refused answer by hand', "a missed item of the Job's scope"):
            self.assertIn(needle, text)
        for command in (rs.CODEX_FLAGS, rs.CLAUDE_FLAGS):
            self.assertTrue(command)

    def test_the_reviewer_role_stays_short_and_points_to_the_review_step(self):
        text = (rp.ROOT / 'governance/ROLE-REVIEWER.md').read_text(encoding='utf-8')
        self.assertLessEqual(len(text.split()), 800)  # the severity definitions live in the format the reviewer copies
        self.assertIn('REVIEW-STEP.md', text)
        block = text.split('FINDINGS FORMAT\n', 1)[1].split('```', 1)[0]
        for severity in ('blocker: the change trusts what it should refuse', "a missed item of the Job's scope",
                         'a broken frozen specification or contract', 'minor: a false refusal'):
            self.assertIn(severity, block)  # the definitions are in the format the reviewer copies

    def test_the_guard_registers_tools_as_read_and_run_as_gated(self):
        sys.path.insert(0, str(rp.ROOT / 'governance' / 'tools'))
        import runtime_guard
        modes = runtime_guard.REGISTRY['review_step']['modes']
        self.assertEqual((modes['tools'], modes['run']), (runtime_guard.READ, runtime_guard.GATED))


if __name__ == '__main__':
    unittest.main()
