"""The review step: the candidate, the bundle, the CLI discovery, the read-only command lines and the checked publish."""
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

    def answer(self, **changes):
        values = dict(handle=NAME, preview=self.preview, parent=self.parent, tree=self.tree,
                      total='Total: 1 finding, 0 blocker, 0 major, 1 minor, 0 note')
        values.update(changes)
        return ('Review of %(handle)s, preview %(preview)s\nCandidate: parent %(parent)s, tree %(tree)s\nRound: 1\n\n'
                'F1 [minor] file.txt:2\nWhat: x\nWhy: y\n\n%(total)s\n' % values)


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
        for flag in ('-s', 'read-only', '--ephemeral', '--ignore-user-config', 'mcp_servers={}', '-o', '-C'):
            self.assertIn(flag, argv)
        self.assertEqual(argv[-1], '-')
        self.assertNotIn('workspace-write', argv)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', argv)

    def test_the_claude_command_has_read_tools_only_and_no_settings_servers_or_session(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        argv = rs.command(self.cli('claude'), chosen, Path('answer.txt'))
        self.assertEqual(argv[argv.index('--tools') + 1], 'Read,Grep,Glob')
        self.assertEqual(argv[argv.index('--permission-mode') + 1], 'plan')
        self.assertEqual(argv[argv.index('--setting-sources') + 1], '')
        for flag in ('--strict-mcp-config', '--no-session-persistence'):
            self.assertIn(flag, argv)
        for danger in ('--dangerously-skip-permissions', 'bypassPermissions', 'Bash', 'Edit', 'Write'):
            self.assertNotIn(danger, ' '.join(argv))

    def test_an_answer_that_names_the_captured_candidate_passes_and_anything_else_is_refused(self):
        chosen = rs.candidate(self.root, NAME, self.lookup)
        rs.check_answer(self.answer(), NAME, chosen)
        rs.check_answer(self.answer(total='Total: 0 findings'), NAME, chosen)
        for broken in (self.answer(preview='c' * 32), self.answer(tree='d' * 40), self.answer(handle='other_job'),
                       self.answer(total='nothing'), 'free text only'):
            with self.subTest(broken=broken[:40]), self.assertRaises(rs.ReviewUnavailable):
                rs.check_answer(broken, NAME, chosen)

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

    def reply(self, text, code=0):
        def fake(argv, stdin=None, timeout=60, cwd=None):
            self.runs.append((argv, stdin, cwd))
            if argv[0].endswith('codex'):
                Path(argv[argv.index('-o') + 1]).write_text(text, encoding='utf-8')
                return code, b'', b'diagnostics'
            return code, json.dumps({'is_error': False, 'result': text}).encode(), b''
        rs.execute = fake

    def test_a_codex_run_binds_checks_and_saves_the_findings_with_the_prompt_on_standard_input(self):
        self.reply(self.answer())
        done = rs.run(self.root, NAME, 'codex', self.preview, lookup=self.lookup)
        saved = Path(done['saved'])
        self.assertEqual(saved.name, 'review-%s.md' % self.preview)
        self.assertEqual(saved.read_text(encoding='utf-8'), self.answer())
        argv, stdin, cwd = self.runs[0]
        self.assertIn(b'Preview id: ' + self.preview.encode(), stdin)
        self.assertEqual(cwd, str(self.root))
        self.assertEqual((done['version'], done['found_by']), ('9.9', 'PATH'))

    def test_a_claude_run_takes_the_result_field_not_the_diagnostics(self):
        self.reply(self.answer())
        done = rs.run(self.root, NAME, 'claude', self.preview, lookup=self.lookup)
        self.assertEqual(Path(done['saved']).read_text(encoding='utf-8'), self.answer())

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
                       'COMITAS_CODEX', 'REVIEW NOT SAVED', 'A review never approves'):
            self.assertIn(needle, text)
        for command in (rs.CODEX_FLAGS, rs.CLAUDE_FLAGS):
            self.assertTrue(command)

    def test_the_reviewer_role_stays_short_and_points_to_the_review_step(self):
        text = (rp.ROOT / 'governance/ROLE-REVIEWER.md').read_text(encoding='utf-8')
        self.assertLessEqual(len(text.split()), 700)
        self.assertIn('REVIEW-STEP.md', text)

    def test_the_guard_registers_tools_as_read_and_run_as_gated(self):
        sys.path.insert(0, str(rp.ROOT / 'governance' / 'tools'))
        import runtime_guard
        modes = runtime_guard.REGISTRY['review_step']['modes']
        self.assertEqual((modes['tools'], modes['run']), (runtime_guard.READ, runtime_guard.GATED))


if __name__ == '__main__':
    unittest.main()
