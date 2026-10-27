"""Repository-declared exclusive history boundaries; synthetic fixtures never use framework history."""
import json
import os
from pathlib import Path
import re
import subprocess
import stat
from contextlib import redirect_stdout
from io import StringIO
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
from governance.tools.lifecycle import commit_metadata as m
import repo_paths as rp


class HistoryUnavailable(Exception):
    """Expected absence of complete history, never a configuration or command failure."""


def history_settings(root):
    """Validate a present declaration before inspecting history; only absence returns None."""
    path = Path(root) / ac.COMMIT_HISTORY
    for part in (path, *list(path.parents)[:2]):
        if part.is_symlink() or part.is_junction():
            raise ValueError(ac.COMMIT_HISTORY + ': indirect configuration path')
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(ac.COMMIT_HISTORY + ': unreadable configuration: ' + str(error)) from error
    if not stat.S_ISREG(mode):
        raise ValueError(ac.COMMIT_HISTORY + ': nonregular configuration file')
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as error:
        raise ValueError(ac.COMMIT_HISTORY + ': unreadable or invalid JSON: ' + str(error)) from error
    required = {'version', 'convention_after'}
    allowed = required | {'trailer_allowlist_after'}
    if (not isinstance(data, dict) or not required <= data.keys() <= allowed
            or type(data['version']) is not int or data['version'] != 1):
        raise ValueError(ac.COMMIT_HISTORY + ': expected version 1, convention_after and optional trailer_allowlist_after')
    for key in allowed - {'version'}:
        if key in data and (not isinstance(data[key], str) or not re.fullmatch(r'[0-9a-f]{40}', data[key])):
            raise ValueError(ac.COMMIT_HISTORY + ': ' + key + ' must be a full lowercase 40-hex commit ID')
    return data['convention_after'], data.get('trailer_allowlist_after', data['convention_after'])


def git(root, *args, accepted=(0,)):
    """Preserve exit codes; callers name only expected Git predicate outcomes."""
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
    env['LC_ALL'] = 'C'
    try:
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env)
    except OSError as error:
        raise ValueError('Git executable unavailable: ' + str(error)) from error
    if result.returncode not in accepted:
        raise ValueError('git ' + ' '.join(args) + ' failed (' + str(result.returncode) + '): '
                         + result.stderr.decode('utf-8', 'replace').strip())
    return result


def history_range(root):
    """Capture HEAD once, validate both epochs, then return H, checked IDs and old-trailer IDs."""
    settings = history_settings(root)
    inside = git(root, 'rev-parse', '--is-inside-work-tree', accepted=(0, 128))
    if inside.returncode:
        if b'not a git repository' in inside.stderr:
            raise HistoryUnavailable('not a Git checkout')
        raise ValueError('Git repository discovery failed: ' + inside.stderr.decode('utf-8', 'replace').strip())
    if inside.stdout.strip() == b'false':
        raise HistoryUnavailable('not a Git worktree')
    if inside.stdout.strip() != b'true':
        raise ValueError('Unexpected Git worktree status')
    shallow = git(root, 'rev-parse', '--is-shallow-repository').stdout.strip()
    if shallow == b'true':
        raise HistoryUnavailable('shallow clone: complete ancestry unavailable; obtain full history')
    if shallow != b'false':
        raise ValueError('Unexpected Git shallow status')
    head_result = git(root, 'rev-parse', '--verify', '-q', 'HEAD', accepted=(0, 1))
    if head_result.returncode:
        branch = git(root, 'symbolic-ref', '-q', 'HEAD').stdout.decode('utf-8').strip()
        if git(root, 'show-ref', '--verify', '--quiet', branch, accepted=(0, 1)).returncode == 1:
            raise HistoryUnavailable('unborn repository: no first commit')
        raise ValueError('HEAD cannot be resolved')
    head = head_result.stdout.decode('ascii').strip()
    if settings is None:
        raise ValueError(ac.COMMIT_HISTORY + ': missing; initialize reviewed repository boundaries after the first commit')
    boundary, trailer = settings
    for label, commit in (('convention_after', boundary), ('trailer_allowlist_after', trailer)):
        kind = git(root, 'cat-file', '-t', commit).stdout.strip()
        if kind != b'commit':
            raise ValueError(label + ': boundary object is not a commit')
        if git(root, 'merge-base', '--is-ancestor', commit, head, accepted=(0, 1)).returncode:
            raise ValueError(label + ': boundary is not an ancestor of captured HEAD')
    if git(root, 'merge-base', '--is-ancestor', boundary, trailer, accepted=(0, 1)).returncode:
        raise ValueError('convention_after must be an ancestor of or equal to trailer_allowlist_after')
    ids = git(root, 'rev-list', '--reverse', boundary + '..' + head).stdout.decode('ascii').split()
    old = set(git(root, 'rev-list', boundary + '..' + trailer).stdout.decode('ascii').split())
    return head, ids, old


def check_history(root, profiles):
    head, ids, old = history_range(root)
    for commit in ids:
        message = git(root, 'show', '-s', '--format=%B', commit).stdout.decode('utf-8')
        try:
            m.parse_metadata(message.rstrip('\n'), profiles, required=True, other_trailers=commit in old)
            problems = status_problems(message)
            if problems:
                raise ValueError('; '.join(problems))
        except ValueError as error:
            raise ValueError(commit + ': ' + str(error)) from error
    return head, len(ids)


def status_problems(message):
    """Why a `Status:` line is misplaced in the message; an empty list when every one is a single-line paragraph
    that is neither the subject nor the final trailer block."""
    paragraphs = re.split(r'\n[ \t]*\n', message.replace('\r\n', '\n').strip('\n'))
    problems = []
    for index, paragraph in enumerate(paragraphs):
        lines = paragraph.split('\n')
        found = [line for line in lines if re.match(r'\s*Status\s*:', line, re.IGNORECASE)]
        if not found:
            continue
        if index == 0:
            problems.append('Status line in the subject paragraph')
        elif index == len(paragraphs) - 1:
            problems.append('Status line inside the final trailer block')
        elif len(lines) != 1 or not re.fullmatch(r'Status: \S.*', lines[0]):
            problems.append('Status line must be its own paragraph of the form "Status: text"')
    return problems


class CommitHistoryConformanceTest(unittest.TestCase):
    def test_every_commit_after_the_boundary_follows_the_convention(self):
        profiles = json.loads((rp.ROOT / rp.PROFILES).read_text(encoding='utf-8'))
        try:
            head, count = check_history(rp.ROOT, profiles)
        except HistoryUnavailable as reason:
            self.skipTest('no usable Git history to check: ' + str(reason))
        print('Commit history: checked %d commit(s) in the declared exclusive range ending at HEAD %s%s' % (
            count, head[:12], '; zero commits in the declared range' if count == 0 else ''))


class HistoryFixtureTest(unittest.TestCase):
    """All histories and declarations are generic, disposable and independent of this checkout's IDs."""
    VALID = 'Fixture change\n\nClass: tooling\nWorker: Human\n'
    EXTRA = VALID.rstrip('\n') + '\nSigned-off-by: Fixture\n'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='history-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.run_git('init', '-b', 'main')
        self.profiles = json.loads((rp.ROOT / rp.PROFILES).read_text(encoding='utf-8'))
        self.root_commit = self.commit('Unconstrained initial import')
        self.settings(self.root_commit)

    def run_git(self, *args):
        return git(self.root, *args).stdout.decode('utf-8').strip()

    def commit(self, message):
        return self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            'commit', '--allow-empty', '--no-gpg-sign', '-m', message) and self.run_git('rev-parse', 'HEAD')

    def settings(self, boundary, trailer=None):
        value = {'version': 1, 'convention_after': boundary}
        if trailer is not None:
            value['trailer_allowlist_after'] = trailer
        self.path = self.root / ac.COMMIT_HISTORY
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(value), encoding='utf-8')

    def check(self):
        return check_history(self.root, self.profiles)

    def test_omitted_and_explicit_equal_trailer_boundary_pass(self):
        self.commit(self.VALID)
        for trailer in (None, self.root_commit):
            self.settings(self.root_commit, trailer)
            self.assertEqual(self.check()[1], 1)

    def test_distinct_epochs_preserve_extra_trailers_only_through_second_boundary(self):
        legacy = self.commit(self.EXTRA)
        self.settings(self.root_commit, legacy)
        self.commit(self.VALID)
        self.assertEqual(self.check()[1], 2)
        self.commit(self.EXTRA)
        with self.assertRaisesRegex(ValueError, 'Signed-off-by'):
            self.check()

    def test_equal_boundaries_at_head_are_valid_zero_coverage_then_check_new_commits(self):
        self.assertEqual(self.check()[1], 0)
        self.settings(self.root_commit, self.root_commit)
        self.assertEqual(self.check()[1], 0)
        self.commit(self.VALID)
        self.assertEqual(self.check()[1], 1)
        self.commit('Missing metadata')
        with self.assertRaisesRegex(ValueError, 'Class trailer required'):
            self.check()

    def test_before_boundary_is_exempt_and_bad_status_after_it_fails(self):
        boundary = self.commit('Another arbitrary historical message')
        self.settings(boundary)
        self.assertEqual(self.check()[1], 0)
        self.commit('Change\n\nStatus: misplaced\nClass: tooling\nWorker: Human')
        with self.assertRaisesRegex(ValueError, 'Status'):
            self.check()

    def test_bad_metadata_in_first_epoch_still_fails(self):
        trailer = self.commit('No Class or Worker here')
        self.settings(self.root_commit, trailer)
        with self.assertRaisesRegex(ValueError, 'Class trailer required'):
            self.check()

    def test_invalid_merged_side_branch_is_checked(self):
        self.run_git('checkout', '-b', 'side')
        side = self.commit('Bad side commit')
        self.run_git('checkout', 'main')
        self.commit(self.VALID)
        self.run_git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                     'merge', '--no-ff', '--no-gpg-sign', 'side', '-m', self.VALID)
        with self.assertRaisesRegex(ValueError, side):
            self.check()

    def test_both_missing_objects_fail(self):
        for first, second in (('0' * 40, self.root_commit), (self.root_commit, '0' * 40)):
            with self.subTest(first=first, second=second):
                self.settings(first, second)
                with self.assertRaisesRegex(ValueError, 'cat-file'):
                    self.check()

    def test_both_nonancestor_boundaries_fail(self):
        self.run_git('checkout', '-b', 'side')
        side = self.commit(self.VALID)
        self.run_git('checkout', 'main')
        for first, second in ((side, side), (self.root_commit, side)):
            self.settings(first, second)
            with self.assertRaisesRegex(ValueError, 'not an ancestor'):
                self.check()

    def test_reversed_epochs_fail(self):
        later = self.commit(self.VALID)
        self.settings(later, self.root_commit)
        with self.assertRaisesRegex(ValueError, 'ancestor of or equal'):
            self.check()

    def test_tree_and_blob_objects_are_not_commits(self):
        tree = self.run_git('rev-parse', 'HEAD^{tree}')
        source = self.root / 'fixture.txt'; source.write_text('fixture blob')
        blob = self.run_git('hash-object', '-w', str(source))
        for wrong in (tree, blob):
            for first, second in ((wrong, wrong), (self.root_commit, wrong)):
                self.settings(first, second)
                with self.assertRaisesRegex(ValueError, 'not a commit'):
                    self.check()

    def test_declaration_shape_and_ids_are_strict(self):
        for value in ([], {}, {'version': True, 'convention_after': self.root_commit},
                      {'version': 2, 'convention_after': self.root_commit},
                      {'version': 1, 'convention_after': self.root_commit, 'unknown': 0}):
            self.path.write_text(json.dumps(value), encoding='utf-8')
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check()
        for field in ('convention_after', 'trailer_allowlist_after'):
            for bad in (None, 1, '', 'HEAD', 'main', 'refs/tags/v1', 'a' * 7, 'A' * 40, 'g' * 40):
                self.settings(self.root_commit)
                data = json.loads(self.path.read_text(encoding='utf-8')); data[field] = bad
                self.path.write_text(json.dumps(data), encoding='utf-8')
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    self.check()
        self.path.write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'invalid JSON'):
            self.check()

    def test_absent_declaration_fails_in_full_history(self):
        self.path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing; initialize'):
            self.check()

    def test_archive_skip_and_malformed_archive_declaration_failure(self):
        archive = Path(self.temp.name) / 'archive'; archive.mkdir()
        with self.assertRaisesRegex(HistoryUnavailable, 'not a Git checkout'):
            history_range(archive)
        path = archive / ac.COMMIT_HISTORY; path.parent.mkdir(parents=True); path.write_text('{')
        with self.assertRaisesRegex(ValueError, 'invalid JSON'):
            history_range(archive)

    def test_unborn_repository_skips_but_bad_configuration_fails(self):
        root = Path(self.temp.name) / 'unborn'; root.mkdir(); git(root, 'init', '-b', 'main')
        with self.assertRaisesRegex(HistoryUnavailable, 'unborn'):
            history_range(root)
        path = root / ac.COMMIT_HISTORY; path.parent.mkdir(parents=True); path.write_text('null')
        with self.assertRaises(ValueError):
            history_range(root)

    def test_shallow_clone_skips_even_if_boundary_is_present_and_bad_config_fails(self):
        self.commit(self.VALID)
        target = Path(self.temp.name) / 'shallow'
        git(self.root, 'clone', '--depth', '1', self.root.as_uri(), str(target))
        path = target / ac.COMMIT_HISTORY; path.parent.mkdir(parents=True)
        for value in (None, {'version': 1, 'convention_after': self.run_git('rev-parse', 'HEAD')},
                      {'version': 1, 'convention_after': '0' * 40}):
            if value is not None: path.write_text(json.dumps(value))
            with self.assertRaisesRegex(HistoryUnavailable, 'shallow'):
                history_range(target)
        path.write_text('{')
        with self.assertRaisesRegex(ValueError, 'invalid JSON'):
            history_range(target)

    def test_nonregular_indirect_and_unreadable_configuration_fails(self):
        self.path.unlink(); self.path.mkdir()
        with self.assertRaisesRegex(ValueError, 'nonregular'):
            self.check()
        self.path.rmdir(); self.settings(self.root_commit)
        with patch.object(Path, 'is_symlink', return_value=True), self.assertRaisesRegex(ValueError, 'indirect'):
            self.check()
        with patch.object(Path, 'is_junction', return_value=True), self.assertRaisesRegex(ValueError, 'indirect'):
            self.check()
        with patch.object(Path, 'read_text', side_effect=PermissionError('fixture')), self.assertRaisesRegex(ValueError, 'unreadable'):
            history_settings(self.root)

    def test_git_failures_never_become_skips_or_trailer_exemptions(self):
        original = git
        for verb in ('rev-parse', 'cat-file', 'merge-base', 'rev-list', 'show'):
            self.commit(self.VALID)
            def fail(root, *args, **kwargs):
                if args[0] == verb: raise ValueError('injected ' + verb)
                return original(root, *args, **kwargs)
            with self.subTest(verb=verb), patch(__name__ + '.git', side_effect=fail), self.assertRaisesRegex(ValueError, 'injected'):
                self.check()
        with patch('subprocess.run', side_effect=FileNotFoundError('fixture')), self.assertRaisesRegex(ValueError, 'Git executable unavailable'):
            self.check()

    def test_failed_second_range_does_not_silently_remove_exemptions(self):
        trailer = self.commit(self.EXTRA); self.settings(self.root_commit, trailer)
        original = git
        def fail(root, *args, **kwargs):
            if args == ('rev-list', self.root_commit + '..' + trailer):
                raise ValueError('second range failed')
            return original(root, *args, **kwargs)
        with patch(__name__ + '.git', side_effect=fail), self.assertRaisesRegex(ValueError, 'second range failed'):
            self.check()

    def test_head_is_captured_once_and_no_range_reads_head_again(self):
        self.commit(self.VALID)
        with patch(__name__ + '.git', wraps=git) as calls:
            head, count = self.check()
        args = [call.args[1:] for call in calls.call_args_list]
        self.assertEqual(sum('HEAD' in a for a in args), 1)
        self.assertIn(('rev-list', '--reverse', self.root_commit + '..' + head), args)
        self.assertEqual(count, 1)

    def test_zero_count_is_explicit_in_live_check_output(self):
        profiles = self.root / rp.PROFILES; profiles.parent.mkdir(parents=True)
        profiles.write_text(json.dumps(self.profiles), encoding='utf-8')
        output = StringIO()
        with patch.object(rp, 'ROOT', self.root), redirect_stdout(output):
            CommitHistoryConformanceTest().test_every_commit_after_the_boundary_follows_the_convention()
        self.assertIn('checked 0 commit(s)', output.getvalue())
        self.assertIn('zero commits in the declared range', output.getvalue())
        self.assertIn('ending at HEAD ' + self.root_commit[:12], output.getvalue())

    def test_moving_head_does_not_change_the_captured_range(self):
        captured = self.commit(self.VALID)
        original = git
        moved = False
        def move(root, *args, **kwargs):
            nonlocal moved
            if args[0] == 'rev-list' and not moved:
                moved = True
                original(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                         'commit', '--allow-empty', '--no-gpg-sign', '-m', 'New invalid commit outside captured range')
            return original(root, *args, **kwargs)
        with patch(__name__ + '.git', side_effect=move):
            self.assertEqual(self.check(), (captured, 1))
        self.assertNotEqual(self.run_git('rev-parse', 'HEAD'), captured)

    def test_unexpected_git_exit_codes_are_failures(self):
        for code in (1, 128):
            failure = subprocess.CompletedProcess(['git'], code, b'', b'Unexpected failure')
            with patch('subprocess.run', return_value=failure), self.assertRaises(ValueError):
                self.check()
        original = git
        def broken_head(root, *args, **kwargs):
            if args == ('rev-parse', '--verify', '-q', 'HEAD'):
                return subprocess.CompletedProcess(['git'], 1, b'', b'')
            return original(root, *args, **kwargs)
        with patch(__name__ + '.git', side_effect=broken_head), self.assertRaisesRegex(ValueError, 'HEAD cannot be resolved'):
            self.check()

    def test_fresh_export_and_template_copies_require_explicit_replacement(self):
        for route in ('export', 'template'):
            with self.subTest(route=route):
                copy = Path(self.temp.name) / route; copy.mkdir(); git(copy, 'init', '-b', 'main')
                path = copy / ac.COMMIT_HISTORY; path.parent.mkdir(parents=True)
                path.write_bytes(self.path.read_bytes()); git(copy, 'add', '--', ac.COMMIT_HISTORY)
                git(copy, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    'commit', '--no-gpg-sign', '-m', 'Fresh ' + route)
                with self.assertRaises(ValueError): check_history(copy, self.profiles)
                first = git(copy, 'rev-parse', 'HEAD').stdout.decode().strip()
                path.write_text(json.dumps({'version': 1, 'convention_after': first}))
                self.assertEqual(check_history(copy, self.profiles)[1], 0)

    def test_upgrade_does_not_rewrite_distinct_project_epochs(self):
        trailer = self.commit(self.EXTRA); self.settings(self.root_commit, trailer)
        before = self.path.read_bytes()
        framework = self.root / 'governance'; framework.mkdir(); (framework / 'README.md').write_text('new framework')
        self.commit(self.VALID)
        self.assertEqual(self.check()[1], 2)
        self.assertEqual(self.path.read_bytes(), before)


class StatusPlacementTest(unittest.TestCase):
    """The placement rule, on synthetic messages, so it is pinned even where the history is not available."""
    TRAILERS = 'Class: tooling\nWorker: Human'

    def message(self, middle):
        return 'Subject line\n\n' + middle + self.TRAILERS + '\n'

    def test_no_status_line_is_fine(self):
        self.assertEqual(status_problems(self.message('- Detail.\n\n')), [])

    def test_a_status_paragraph_before_the_trailers_is_fine(self):
        self.assertEqual(status_problems(self.message('- Detail.\n\nStatus: design only\n\n')), [])

    def test_a_status_line_inside_the_trailer_block_is_refused(self):
        self.assertEqual(status_problems('Subject\n\n- Detail.\n\nStatus: tests pass\nClass: tooling\nWorker: Human\n'),
                         ['Status line inside the final trailer block'])
        self.assertEqual(status_problems(self.message('- Detail.\n\n').replace('Class:', 'Status: x\nClass:')),
                         ['Status line inside the final trailer block'])

    def test_a_status_line_glued_to_the_body_is_refused(self):
        self.assertEqual(status_problems(self.message('- Detail.\nStatus: nothing implemented\n\n')),
                         ['Status line must be its own paragraph of the form "Status: text"'])

    def test_a_status_line_in_the_subject_paragraph_is_refused(self):
        self.assertEqual(status_problems('Status: draft\n\n- Detail.\n\n' + self.TRAILERS + '\n'),
                         ['Status line in the subject paragraph'])

    def test_a_split_trailer_block_is_refused_by_the_strict_parser(self):
        profiles = json.loads((rp.ROOT / rp.PROFILES).read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            m.parse_metadata('Subject\n\n- Detail.\n\nClass: tooling\n\nWorker: Human\n', profiles, required=True)


if __name__ == '__main__':
    unittest.main()
