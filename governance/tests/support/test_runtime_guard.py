"""The runtime guard: version check, GIT_* scrub, lifecycle gate with its three modes, and the interpreter fingerprint."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from governance.tools import runtime_guard as g
from governance.tools.continuity import artifact_classification as ac
from governance.tools.support import local_state
import repo_paths as rp

GUARD = rp.ROOT / rp.TOOLS / 'runtime_guard.py'
CLEAN = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}


def launchers():
    """The files of the tools folder that are commands: they have a `__main__` block, and the guard is not one of them."""
    found = []
    for path in sorted((rp.ROOT / rp.TOOLS).glob('*.py')):
        if path.name != 'runtime_guard.py' and "__name__ == '__main__'" in path.read_text(encoding='utf-8'):
            found.append(path)
    return found


def refused(function, *args, **kwargs):
    """(exit code, standard error) of a call that must end the process."""
    error = io.StringIO()
    with contextlib.redirect_stderr(error), unittest.TestCase().assertRaises(SystemExit) as caught:
        function(*args, **kwargs)
    return caught.exception.code, error.getvalue()


class SyntaxTest(unittest.TestCase):
    def test_the_guard_parses_as_an_old_python_3_so_it_can_fail_clearly(self):
        # No walrus, match, positional-only parameter, parenthesized context manager or except*: nothing a 3.7 parser rejects.
        ast.parse(GUARD.read_text(encoding='utf-8'), feature_version=(3, 7))

    def test_the_guard_uses_only_the_standard_library_and_never_waits_or_searches(self):
        source = GUARD.read_text(encoding='utf-8')
        imported = set(re.findall(r'^(?:import|from) ([A-Za-z0-9_]+)', source, re.M))
        self.assertTrue(imported <= set(sys.stdlib_module_names), imported)
        for forbidden in ('sleep', 'lifecycle-wait', 'urllib', 'which(', 'glob(', 'winreg', 'py -3'):
            self.assertFalse(forbidden in source, forbidden)

    def test_the_deliberate_copy_of_the_lifecycle_lock_location_agrees_with_local_state(self):
        self.assertEqual((g.LOCAL_FOLDER, g.LOCKS_FOLDER, g.LIFECYCLE_LOCK, g.GIT_DIRECTORY),
                         (local_state.LOCAL, local_state.LOCKS, local_state.LIFECYCLE_LOCK, local_state.GIT_DIRECTORY))
        self.assertEqual(g.ROOT, rp.ROOT)


class RegistryTest(unittest.TestCase):
    def test_every_mode_is_one_of_the_three(self):
        for name, entry in g.REGISTRY.items():
            with self.subTest(tool=name):
                modes = [entry] if isinstance(entry, str) else list(entry['modes'].values())
                self.assertTrue(modes and set(modes) <= set(g.MODES))

    def test_every_subcommand_of_the_tools_that_have_them_is_registered(self):
        # The parsers are the source of the subcommand names; a new subcommand must be a deliberate entry.
        for name, pattern in (('local_jobs', r"sub\.add_parser\('([a-z-]+)'"), ('worktree', r"sub\.add_parser\('([a-z-]+)'"),
                              ('timing_record', r"sub\.add_parser\('([a-z-]+)'")):
            implementation = {'local_jobs': 'coordination/local_jobs.py', 'worktree': 'worktrees/guard.py',
                              'timing_record': 'support/timing_record.py'}[name]
            source = (rp.ROOT / rp.TOOLS / implementation).read_text(encoding='utf-8')
            names = set(re.findall(pattern, source))
            if name == 'worktree':
                names |= {'create', 'integrate', 'remove', 'reconcile', 'carry'}
            with self.subTest(tool=name):
                self.assertGreaterEqual(len(names), 3)
                self.assertEqual(names, set(g.REGISTRY[name]['modes']))

    def test_the_reads_write_nothing_and_the_writes_are_gated(self):
        reads = {'local_jobs': {'paths', 'list', 'pointer', 'read', 'queue', 'kinds', 'brief'},
                 'worktree': {'paths', 'execution-status'}, 'timing_record': {'stats'}}
        for name, expected in reads.items():
            modes = g.REGISTRY[name]['modes']
            self.assertEqual({sub for sub, mode in modes.items() if mode == g.READ}, expected, name)
            local = {'timing_record': {'export', 'import'}}.get(name, set())
            self.assertEqual({sub for sub, mode in modes.items() if mode == g.LOCAL}, local, name)
            self.assertEqual({sub for sub, mode in modes.items() if mode == g.GATED}, set(modes) - expected - local, name)
        self.assertEqual(g.REGISTRY['checkpoint'], g.GATED)
        self.assertEqual(g.REGISTRY['run_tests'], g.LOCAL)
        self.assertEqual(g.REGISTRY['commit_metadata'], g.GATED)

    def test_an_unregistered_tool_or_subcommand_is_gated(self):
        self.assertEqual(g.mode_of('brand_new_tool', []), (g.GATED, None))
        self.assertEqual(g.mode_of('worktree', ['--main', '.', 'brand-new']), (g.GATED, 'brand-new'))
        self.assertEqual(g.mode_of('local_jobs', []), (g.GATED, None))
        self.assertEqual(g.mode_of('worktree', ['--main']), (g.GATED, None))

    def test_the_subcommand_is_found_after_the_valued_global_options(self):
        self.assertEqual(g.mode_of('worktree', ['--main', 'C:/x', 'execution-status', '--json-only']), (g.READ, 'execution-status'))
        self.assertEqual(g.mode_of('local_jobs', ['--repo', '.', '--primary', 'queue']), (g.READ, 'queue'))
        self.assertEqual(g.mode_of('local_jobs', ['--repo=.', 'complete', 'job']), (g.GATED, 'complete'))
        # A value that looks like a read subcommand is still a value, also under an argparse abbreviation.
        self.assertEqual(g.mode_of('local_jobs', ['--repo', 'queue', 'put']), (g.GATED, 'put'))
        self.assertEqual(g.mode_of('local_jobs', ['--rep', 'queue', 'put']), (g.GATED, 'put'))
        self.assertEqual(g.mode_of('worktree', ['--ma', 'paths', 'integrate']), (g.GATED, 'integrate'))

    def test_an_extension_states_its_own_mode_but_never_overrides_the_registry(self):
        self.assertEqual(g.mode_of('some_extension_script', [], g.LOCAL), (g.LOCAL, None))
        self.assertEqual(g.mode_of('some_extension_script', [], 'WHATEVER'), (g.GATED, None))
        self.assertEqual(g.mode_of('checkpoint', ['prepare'], g.READ), (g.GATED, None))


class VersionTest(unittest.TestCase):
    def test_an_old_interpreter_stops_with_one_message_and_nothing_is_searched_for(self):
        for version in ((3, 12, 9, 'final', 0), (3, 9, 1, 'final', 0), (3, 0, 0, 'final', 0)):
            with self.subTest(version=version), patch.object(sys, 'version_info', version), \
                    patch.object(g.subprocess, 'run', side_effect=AssertionError('must not search or launch')):
                code, message = refused(g.enter, 'worktree', ['execution-status'])
            self.assertEqual(code, 1)
            self.assertEqual(message.count('\n'), 1)
            self.assertIn('require Python 3.13 or later', message)
            self.assertIn('Nothing was searched for or downloaded', message)

    def test_a_current_interpreter_passes(self):
        g.check_version()


class ScrubTest(unittest.TestCase):
    def test_every_git_variable_goes_except_exactly_git_pager_cat(self):
        environment = {'PATH': 'p', 'GIT_EDITOR': 'true', 'git_dir': 'x', 'GIT_PAGER': 'cat', 'GIT_CONFIG_COUNT': '1',
                       'NOT_GIT_X': '1'}
        self.assertEqual(g.scrub(environment), ['GIT_CONFIG_COUNT', 'GIT_EDITOR', 'git_dir'])
        self.assertEqual(environment, {'PATH': 'p', 'GIT_PAGER': 'cat', 'NOT_GIT_X': '1'})
        self.assertEqual(g.scrub({'GIT_PAGER': 'less'}), ['GIT_PAGER'])
        self.assertEqual(g.scrub({}), [])

    def test_enter_scrubs_the_process_environment_and_names_but_never_shows_values(self):
        with patch.dict(os.environ, {'GIT_EDITOR': 'secret-value-1', 'GIT_DIR': 'secret-value-2', 'GIT_PAGER': 'cat'}):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                result = g.enter('show_classification', [])
            self.assertEqual(result['scrubbed'], ['GIT_DIR', 'GIT_EDITOR'])
            self.assertNotIn('GIT_EDITOR', os.environ)
            self.assertNotIn('GIT_DIR', os.environ)
            self.assertEqual(os.environ['GIT_PAGER'], 'cat')
        self.assertIn('GIT_DIR, GIT_EDITOR', error.getvalue())
        self.assertNotIn('secret-value', error.getvalue())

    def test_enter_sets_no_bytecode_and_is_silent_when_nothing_was_scrubbed(self):
        before = sys.dont_write_bytecode
        self.addCleanup(setattr, sys, 'dont_write_bytecode', before)
        sys.dont_write_bytecode = False
        with patch.dict(os.environ, CLEAN, clear=True):
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                g.enter('show_classification', [])
        self.assertTrue(sys.dont_write_bytecode)
        self.assertEqual(error.getvalue(), '')


class GateTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True, env=CLEAN)
        self.lock = self.root / '.local' / 'locks' / g.LIFECYCLE_LOCK
        self.lock.parent.mkdir(parents=True)
        environment = patch.dict(os.environ, CLEAN, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def enter(self, tool, argv):
        return g.enter(tool, argv, root=self.root)

    def test_without_the_lock_every_mode_passes(self):
        for tool, argv in (('checkpoint', ['prepare']), ('worktree', ['--main', '.', 'integrate']), ('run_tests', []),
                           ('worktree', ['--main', '.', 'execution-status'])):
            with self.subTest(tool=tool, argv=argv):
                self.assertIn(self.enter(tool, argv)['mode'], g.MODES)

    def test_a_gated_tool_is_refused_at_once_while_the_lock_exists(self):
        self.lock.write_text('')
        for tool, argv in (('checkpoint', ['--repo', '.', 'prepare']), ('worktree', ['--main', '.', 'integrate']),
                           ('worktree', ['--main', '.', 'create']), ('local_jobs', ['--repo', '.', 'complete', 'job']),
                           ('local_jobs', ['--repo', '.', 'prune']), ('local_jobs', ['put']),
                           ('commit_metadata', []), ('brand_new_tool', []), ('worktree', ['--main', '.', 'brand-new'])):
            with self.subTest(tool=tool, argv=argv):
                code, message = refused(self.enter, tool, argv)
                self.assertEqual(code, 1)
                self.assertIn('refused at once', message)
                self.assertIn('Nothing was started and nothing was waited for', message)
                self.assertIn(tool, message)

    def test_read_and_local_tools_pass_while_the_lock_exists(self):
        self.lock.write_text('')
        for tool, argv, mode in (('worktree', ['--main', '.', 'execution-status', '--json-only'], g.READ),
                                 ('worktree', ['--main', '.', 'paths', '--owner', 'x'], g.READ),
                                 ('local_jobs', ['--repo', '.', 'queue'], g.READ), ('local_jobs', ['list'], g.READ),
                                 ('timing_record', ['stats'], g.READ), ('validate_continuity', [], g.READ),
                                 ('show_classification', ['x'], g.READ), ('approval_words', ['match'], g.READ),
                                 ('run_tests', ['-s', 'x'], g.LOCAL),
                                 ('timing_record', ['--repo', '.', 'export', '--output', 'x'], g.LOCAL),
                                 ('timing_record', ['import', '--input', 'x'], g.LOCAL)):
            with self.subTest(tool=tool, argv=argv):
                self.assertEqual(self.enter(tool, argv)['mode'], mode)
        self.assertEqual(g.enter('some_extension_script', [], extension_mode=g.LOCAL, root=self.root)['mode'], g.LOCAL)

    def test_the_refusal_does_not_wait(self):
        self.lock.write_text('')
        with patch('time.sleep', side_effect=AssertionError('the guard must not wait')):
            self.assertEqual(refused(self.enter, 'checkpoint', ['prepare'])[0], 1)

    def test_a_gated_tool_fails_closed_when_the_shared_git_directory_cannot_be_found(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            code, message = refused(g.enter, 'checkpoint', ['prepare'], root=elsewhere)
            self.assertEqual(code, 1)
            self.assertIn('lifecycle lock cannot be checked', message)
            self.assertEqual(g.enter('run_tests', [], root=elsewhere)['mode'], g.LOCAL)

    def test_a_linked_worktree_sees_the_lock_in_the_shared_directory(self):
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=T', '-c', 'user.email=t@example.invalid',
                        'commit', '-q', '--allow-empty', '-m', 'x'], check=True, capture_output=True, env=CLEAN)
        linked = self.root.parent / (self.root.name + '-linked')
        subprocess.run(['git', '-C', str(self.root), 'worktree', 'add', '-q', '-b', 'side', str(linked)],
                       check=True, capture_output=True, env=CLEAN)
        self.addCleanup(lambda: subprocess.run(['git', '-C', str(self.root), 'worktree', 'remove', '--force', str(linked)],
                                               capture_output=True, env=CLEAN))
        self.lock.write_text('')
        self.assertEqual(refused(g.enter, 'checkpoint', ['prepare'], root=linked)[0], 1)
        self.lock.unlink()
        self.assertEqual(g.enter('checkpoint', ['prepare'], root=linked)['mode'], g.GATED)


class FingerprintTest(unittest.TestCase):
    def test_the_fingerprint_names_the_running_interpreter_and_its_file(self):
        now = g.fingerprint()
        self.assertEqual(set(now), set(g.FIELDS))
        self.assertEqual(Path(now['executable']), Path(os.path.realpath(sys.executable)))
        self.assertEqual(now['size'], Path(now['executable']).stat().st_size)
        self.assertRegex(now['sha256'], '^[0-9a-f]{64}$')
        self.assertEqual(now['version'], sys.version.split()[0])
        self.assertEqual(now, g.fingerprint())

    def test_the_same_interpreter_passes_and_no_pin_compares_nothing(self):
        g.assert_pin(g.fingerprint())
        g.assert_pin(None)

    def test_any_changed_field_refuses_with_the_one_message_and_names_the_field(self):
        now = g.fingerprint()
        for field, other in (('executable', 'C:/elsewhere/python.exe'), ('size', now['size'] + 1),
                             ('sha256', '0' * 64), ('version', '3.99.0')):
            with self.subTest(field=field):
                with self.assertRaises(g.Drift) as caught:
                    g.assert_pin(dict(now, **{field: other}))
                self.assertTrue(str(caught.exception).startswith(
                    'the interpreter changed since the preview: re-run the preview, nothing was lost yet'))
                self.assertIn(field, str(caught.exception))
        self.assertTrue(issubclass(g.Drift, ValueError))

    def test_a_malformed_pin_is_a_change_never_a_pass(self):
        for pin in ({}, {'executable': 'x'}, 'text', 3, [], dict(g.fingerprint(), extra=1)):
            with self.subTest(pin=pin), self.assertRaises(g.Drift):
                g.assert_pin(pin)

    def test_a_patch_version_upgrade_changes_the_file_and_is_caught_by_the_hash(self):
        now = g.fingerprint()
        with self.assertRaises(g.Drift):
            g.assert_pin(dict(now, sha256='1' * 64), now)

    def test_an_interpreter_that_reports_no_file_cannot_be_fingerprinted(self):
        with patch.object(sys, 'executable', ''), self.assertRaises(ValueError):
            g.fingerprint()
        with patch.object(sys, 'executable', str(rp.ROOT / 'no-such-python')), self.assertRaises(ValueError):
            g.fingerprint()


class CheckTest(unittest.TestCase):
    def run_guard(self, *args, **extra):
        return subprocess.run([sys.executable, '-B', str(GUARD), *args], capture_output=True, text=True, timeout=60,
                              env=dict(CLEAN, **extra), cwd=tempfile.gettempdir())

    def test_check_prints_the_fingerprint_and_the_scrubbed_names_without_values(self):
        result = self.run_guard('--check', GIT_EDITOR='secret-value', GIT_PAGER='cat')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['interpreter'], g.fingerprint())
        self.assertEqual(report['scrubbed'], ['GIT_EDITOR'])
        self.assertEqual(report['minimum'], '3.13')
        self.assertIn('GIT_EDITOR', result.stderr)
        self.assertNotIn('secret-value', result.stdout + result.stderr)

    def test_check_with_nothing_scrubbed_and_help(self):
        self.assertEqual(json.loads(self.run_guard('--check').stdout)['scrubbed'], [])
        self.assertIn('usage', self.run_guard('--help').stdout.lower())
        self.assertNotEqual(self.run_guard().returncode, 0)


class WiringTest(unittest.TestCase):
    def test_every_thin_launcher_is_registered_and_enters_first_under_its_own_name(self):
        commands = launchers()
        self.assertGreaterEqual(len(commands), 9)
        for path in commands:
            with self.subTest(launcher=path.name):
                text = path.read_text(encoding='utf-8')
                self.assertIn(path.stem, g.REGISTRY)
                self.assertIn("runtime_guard.enter('%s')" % path.stem, text)
                # The guard is entered before the launcher imports any implementation or tool package.
                self.assertLess(text.index('runtime_guard.enter('), text.index('governance.t') if 'from governance.t' in text
                                else len(text))

    def test_every_registered_name_is_a_launcher_or_the_guard_itself(self):
        self.assertEqual(set(g.REGISTRY), {path.stem for path in launchers()} | {'runtime_guard'})

    def test_the_retired_admission_shim_and_its_registration_are_gone(self):
        for retired in ('governance_python.py', 'support/governance_python.py'):
            self.assertFalse((rp.ROOT / rp.TOOLS / retired).exists(), retired)
        self.assertFalse(hasattr(ac, 'REGISTRATION'))
        self.assertFalse(hasattr(rp, 'GOVERNANCE_PYTHON'))

    def test_an_extension_script_states_its_own_mode_and_the_core_registry_names_no_extension(self):
        layer = rp.ROOT / 'governance' / 'extensions'
        guard = GUARD.read_text(encoding='utf-8')
        for folder in sorted(p for p in layer.glob('*') if p.is_dir()):
            self.assertNotIn(folder.name, guard)
            for script in folder.rglob('*.py'):
                text = script.read_text(encoding='utf-8')
                if 'runtime_guard.enter(' in text:
                    with self.subTest(script=script.name):
                        self.assertIn('extension_mode=runtime_guard.', text)
                        self.assertNotIn(script.stem, guard)

    def test_a_launcher_enters_the_guard_before_its_tool_and_a_read_tool_runs_from_anywhere(self):
        result = subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.TOOLS / 'approval_words.py'), 'match', '--phase',
                                 'preview', '--reply', 'go'], capture_output=True, text=True, timeout=60, env=CLEAN,
                                cwd=tempfile.gettempdir())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['matched'])

    def test_the_retired_anchor_is_gone_from_the_classification_module(self):
        self.assertFalse(hasattr(ac, 'LIFECYCLE_LOCK'))

    def test_a_launcher_scrubs_the_host_environment_and_names_what_it_removed(self):
        result = subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.TOOLS / 'approval_words.py'), 'match', '--phase',
                                 'preview', '--reply', 'go'], capture_output=True, text=True, timeout=60,
                                env=dict(CLEAN, GIT_EDITOR='true'), cwd=tempfile.gettempdir())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('scrubbed inherited GIT_* variables: GIT_EDITOR', result.stderr)


class LockLocationTest(unittest.TestCase):
    """The guard's own copy of the lifecycle lock's location finds the same file as local_state, from any checkout."""

    def setUp(self):
        environment = patch.dict(os.environ, CLEAN, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()

    def git(self, *args):
        env = dict(CLEAN, GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='noreply@example.invalid',
                   GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='noreply@example.invalid')
        subprocess.run(['git', *args], check=True, capture_output=True, env=env)

    def test_it_agrees_with_local_state_in_a_main_checkout_a_linked_worktree_and_a_clone(self):
        main = self.base / 'main'
        self.git('init', '-q', '-b', 'main', str(main))
        self.git('-C', str(main), 'commit', '-q', '--allow-empty', '-m', 'start')
        linked, clone = self.base / 'linked', self.base / 'clone'
        self.git('-C', str(main), 'worktree', 'add', '-q', '--detach', str(linked))
        self.git('clone', '-q', str(main), str(clone))
        for checkout in (main, linked, clone):
            with self.subTest(checkout=checkout.name):
                expected = local_state.lifecycle_lock(local_state.common_directory(checkout))
                self.assertEqual(g.lifecycle_lock_path(checkout), expected)
        self.assertEqual(g.lifecycle_lock_path(linked), main / '.local' / 'locks' / 'worktree-workflow.lock')
        self.assertEqual(g.lifecycle_lock_path(clone), clone / '.local' / 'locks' / 'worktree-workflow.lock')

    def test_a_repository_without_a_primary_checkout_is_refused_and_a_gated_tool_stops(self):
        bare = self.base / 'bare.git'
        self.git('init', '-q', '--bare', str(bare))
        with self.assertRaisesRegex(ValueError, 'no primary checkout'):
            g.lifecycle_lock_path(bare)
        code, message = refused(g.gate, 'checkpoint', None, root=bare)
        self.assertEqual(code, 1)
        self.assertIn('lifecycle lock cannot be checked', message)
        self.assertIn('Nothing was started', message)

    def test_the_old_place_in_the_git_directory_is_not_a_lock_any_more(self):
        main = self.base / 'main'
        self.git('init', '-q', '-b', 'main', str(main))
        (main / '.git' / g.LIFECYCLE_LOCK).write_text('')
        self.assertFalse(g.lifecycle_active(main))


if __name__ == '__main__':
    unittest.main()
