"""governance/scripts/claims.sh in a disposable repository: usage, the four commands, the primary checkout and the exit code.

The worktree tool is a stub that reports how it was started, so the test pins what the script passes on and nothing of
the tool. Run it from Git Bash."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import repo_paths as rp

SCRIPT = rp.ROOT / 'governance' / 'scripts' / 'claims.sh'
IDENTITY = ['-c', 'user.name=Isolated Test', '-c', 'user.email=test@example.invalid']
STUB = '''import json, os, sys
print(json.dumps({'cwd': os.getcwd(), 'argv': sys.argv[1:]}))
print('stub-diagnostic', file=sys.stderr)
sys.exit(int(os.environ.get('STUB_EXIT', '0')))
'''
USAGE_LINES = ('execution-status', 'status', 'watch', 'clear', 'doctor', 'clear and doctor are for the lead developer, never workers')
MAPPING = (
    (['execution-status'], ['execution-status']),
    (['status'], ['execution-status']),
    (['execution-status', '--json-only'], ['execution-status', '--json-only']),
    (['status', '--owner', 'mission-a'], ['execution-status', '--owner', 'mission-a']),
    (['watch'], ['execution-status', '--watch']),
    (['watch', '--interval', '5', '--polls', '2'], ['execution-status', '--watch', '--interval', '5', '--polls', '2']),
    (['clear'], ['clear']),
    (['clear', '--apply', 'c7'], ['clear', '--apply', 'c7']),
    (['doctor'], ['doctor']),
    (['doctor', '--repair'], ['doctor', '--repair']),
    (['-e'], ['execution-status']),
    (['-e', '--json-only'], ['execution-status', '--json-only']),
    (['-w', '--polls', '1'], ['execution-status', '--watch', '--polls', '1']),
    (['-c'], ['clear']),
    (['-c', '--apply', 'c7'], ['clear', '--apply', 'c7']),
    (['-d'], ['doctor']),
    (['-d', '--repair'], ['doctor', '--repair']),
)


def usable(candidate):
    """Whether this bash starts and runs a command: the WSL launcher in System32 is on the PATH of a PowerShell session and does not."""
    try:
        done = subprocess.run([candidate, '-c', 'echo ok'], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0 and done.stdout.strip() == b'ok'


def find_bash():
    """The first working bash: the one on the PATH, else the Git for Windows one next to git, whichever way the tests were started."""
    candidates = [shutil.which('bash')]
    try:
        exec_path = Path(subprocess.run(['git', '--exec-path'], capture_output=True, text=True, timeout=30).stdout.strip())
        installs = [exec_path.parents[2]] if len(exec_path.parents) > 2 else []
    except (OSError, subprocess.SubprocessError):
        installs = []
    git = shutil.which('git')
    if git:
        installs.append(Path(git).resolve().parents[1])
    for install in installs:
        candidates += [str(install / 'bin' / 'bash.exe'), str(install / 'usr' / 'bin' / 'bash.exe')]
    for candidate in candidates:
        if candidate and Path(candidate).exists() and usable(candidate):
            return candidate
    return None


BASH = find_bash()


@unittest.skipUnless(BASH, 'a working bash is required for the shell entry point: run from Git Bash, or put Git for Windows next to git')
class ClaimsScriptTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='claims-script-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.primary = base / 'primary'
        (self.primary / 'governance' / 'scripts').mkdir(parents=True)
        (self.primary / 'governance' / 'tools').mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SCRIPT, self.primary / 'governance' / 'scripts' / 'claims.sh')
        (self.primary / 'governance' / 'tools' / 'worktree.py').write_text(STUB, newline='\n')
        self.git(self.primary, 'init', '-q', '-b', 'main')
        self.git(self.primary, 'add', '.')
        self.git(self.primary, *IDENTITY, 'commit', '-q', '-m', 'Fixture')
        self.linked = base / 'linked'
        self.git(self.primary, 'worktree', 'add', '-q', '-b', 'tx/linked', str(self.linked))
        self.elsewhere = base / 'elsewhere'
        self.elsewhere.mkdir()
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}

    def git(self, root, *args):
        subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True,
                       env={k: v for k, v in os.environ.items() if not k.startswith('GIT_')})

    def run_script(self, args, checkout=None, cwd=None, extra_env=None):
        script = (checkout or self.primary) / 'governance' / 'scripts' / 'claims.sh'
        return subprocess.run([BASH, str(script), *args], cwd=str(cwd or self.elsewhere),
                              env={**self.environment, **(extra_env or {})}, capture_output=True, text=True,
                              encoding='utf-8')

    def report(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def assertPrimary(self, path):
        self.assertTrue(os.path.samefile(path, self.primary), f'{path} is not {self.primary}')

    def assertForwarded(self, report, expected):
        self.assertEqual(report['argv'][:1], ['--main'])
        self.assertPrimary(report['argv'][1])
        self.assertEqual(report['argv'][2:], expected)
        self.assertPrimary(report['cwd'])

    def test_no_argument_prints_the_usage_and_fails(self):
        result = self.run_script([])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        for line in USAGE_LINES:
            self.assertIn(line, result.stderr)
        self.assertNotIn('stub-diagnostic', result.stderr)

    def test_an_unknown_subcommand_prints_the_usage_and_never_reaches_the_tool(self):
        for name in ('create', 'integrate', 'remove', 'reconcile', 'paths', 'execution_status', 'Clear', '--help',
                     '--apply', '--watch', '-E', '-x', '-ec', '-s', ''):
            with self.subTest(name=name):
                result = self.run_script([name, 'x'])
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertIn('clear and doctor are for the lead developer, never workers', result.stderr)
                self.assertNotIn('stub-diagnostic', result.stderr)

    def test_each_command_maps_to_its_tool_form_from_the_primary(self):
        for args, expected in MAPPING:
            with self.subTest(args=args):
                self.assertForwarded(self.report(self.run_script(args)), expected)

    def test_it_never_adds_apply_or_repair_by_itself(self):
        for args in (['clear'], ['doctor'], ['execution-status'], ['watch'], ['-c'], ['-d'], ['-e'], ['-w']):
            report = self.report(self.run_script(args))
            self.assertNotIn('--apply', report['argv'])
            self.assertNotIn('--repair', report['argv'])

    def test_from_a_linked_worktree_it_runs_in_the_primary_checkout(self):
        for args, expected in MAPPING[:1] + MAPPING[7:8]:
            with self.subTest(args=args):
                self.assertForwarded(self.report(self.run_script(args, checkout=self.linked)), expected)
        self.assertForwarded(self.report(self.run_script(['status'], checkout=self.linked, cwd=self.linked)),
                             ['execution-status'])
        self.assertForwarded(self.report(self.run_script(['status'], checkout=self.linked, cwd=self.linked / 'governance' / 'scripts')),
                             ['execution-status'])

    def test_from_any_folder_of_the_primary(self):
        self.assertForwarded(self.report(self.run_script(['status'], cwd=self.primary / 'governance' / 'scripts')), ['execution-status'])
        self.assertForwarded(self.report(self.run_script(['status'], cwd=self.primary)), ['execution-status'])

    def test_output_and_exit_code_pass_through_unchanged(self):
        direct = subprocess.run([shutil.which('py') or shutil.which('python3') or shutil.which('python'), '-B',
                                 str(self.primary / 'governance' / 'tools' / 'worktree.py')],
                                cwd=str(self.primary), env={**self.environment, 'STUB_EXIT': '7'},
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(direct.returncode, 7)
        for command in ('execution-status', 'clear', 'doctor'):
            with self.subTest(command=command):
                result = self.run_script([command], extra_env={'STUB_EXIT': '7'})
                self.assertEqual(result.returncode, 7)
                self.assertEqual(result.stderr, direct.stderr)
                self.assertEqual(json.loads(result.stdout)['argv'][-1], command)
        self.assertEqual(self.run_script(['status'], extra_env={'STUB_EXIT': '0'}).returncode, 0)


if __name__ == '__main__':
    unittest.main()
