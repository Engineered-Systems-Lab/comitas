"""Test runner for the policy's test checks: one command that fans the tests out over worker processes.

A check stays one command that exits 0 only when every test passes; the checkpoint tool still runs its checks one
after another. This script discovers the tests as `python -m unittest discover -s START -p PATTERN` does (`-s` may be repeated
to discover in several folders, as the framework's tests and the configuration keep their tests apart), with one addition:
a start folder inside a package (it and the folders above it carry an `__init__.py`) is discovered from the folder above
the outermost package, as `unittest discover -s START -t TOP` does, so its tests keep dotted names rooted at the
repository (`governance.tests.lifecycle.test_checkpoint`). Any other folder is discovered exactly as unittest does. It then runs
them in parallel or, when the committed feature switch says so, sequentially through unittest itself.
The mode is read from `project-governance/config/checkpoint-features.toml` on main's tip (keys `parallel_tests` and
`test_workers`, see early_approval.test_settings), never from the environment, and is printed first so that the
sealed evidence says which way the tests ran. Python 3.13+, standard library only.

Parallel mode: one worker process per core (or the cap), a dynamic queue of units, output captured per unit and
printed together, each worker with its own temporary folder under one short per-run folder in `tmp`, a sibling of the
main checkout's folder. A unit is one test id; a class that has setUpClass or tearDownClass, and a module that has setUpModule, tearDownModule or load_tests,
is one unit, so a fixture runs once per unit. `--shuffle SEED` shuffles the units and the order inside a unit, for
periodic independence runs.
"""
import argparse
import contextlib
import hashlib
import io
import multiprocessing
import os
from pathlib import Path
import queue
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from governance.tools.lifecycle import early_approval

ROOT = Path(__file__).resolve().parents[2]

STATUS_WORDS = {'ok': 'ok', 'FAIL': 'FAIL', 'ERROR': 'ERROR', 'skip': 'skipped', 'xfail': 'expected failure',
                'xpass': 'unexpected success'}
PASSING = ('ok', 'skip', 'xfail')


class Repository:
    """The one method early_approval.test_settings needs: read Git output from the repository at `root`."""

    def __init__(self, root):
        self.root = root

    def git(self, *args):
        done = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True)
        if done.returncode != 0:
            raise ValueError('git ' + ' '.join(args) + ' failed')
        return done.stdout


def walk(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from walk(item)
        else:
            yield item


def top_level(start):
    """The folder a start folder is discovered from: itself, or the folder above the outermost package it sits in."""
    top = Path(start).resolve()
    while (top / '__init__.py').is_file():
        top = top.parent
    return top


def discover_all(loader, starts, pattern):
    """The tests of every start folder, discovered as `unittest discover -s START -t TOP -p PATTERN` does for each."""
    suite = unittest.TestSuite()
    for start in starts:
        suite.addTests(loader.discover(start, pattern, top_level_dir=str(top_level(start))))
    return suite


def make_loader(patterns):
    loader = unittest.TestLoader()
    if patterns:  # The same wrapping `unittest -k` applies.
        loader.testNamePatterns = [p if '*' in p else '*%s*' % p for p in patterns]
    return loader


def resolvable(test):
    """True when the test can be loaded again by its dotted name in another process."""
    cls = type(test)
    target = sys.modules.get(cls.__module__)
    for part in cls.__qualname__.split('.'):
        target = getattr(target, part, None)
    return target is cls


def has_class_fixture(cls):
    return any(getattr(cls, name).__func__ is not getattr(unittest.TestCase, name).__func__
               for name in ('setUpClass', 'tearDownClass'))


def has_module_fixture(module):
    return any(hasattr(module, name) for name in ('setUpModule', 'tearDownModule', 'load_tests'))


def plan(tests):
    """(units, local): units are (kind, name, ids); local are tests that only the discovering process can run."""
    modules, classes, singles, local = {}, {}, [], []
    for test in tests:
        cls = type(test)
        if not resolvable(test):
            local.append(test)
        elif has_module_fixture(sys.modules[cls.__module__]):
            modules.setdefault(cls.__module__, []).append(test.id())
        elif has_class_fixture(cls):
            classes.setdefault(cls.__module__ + '.' + cls.__qualname__, []).append(test.id())
        else:
            singles.append(test.id())
    units = [('module', name, ids) for name, ids in modules.items()]
    units += [('class', name, ids) for name, ids in classes.items()]
    units.sort(key=lambda unit: -len(unit[2]))  # The biggest fixtures first, so they never end the run.
    units += [('test', name, [name]) for name in singles]
    return units, local


def seeded(seed, name):
    return hashlib.sha256((seed + name).encode()).hexdigest()


class Statuses(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.status = {}

    def addSuccess(self, test):
        self.status[test.id()] = 'ok'
        super().addSuccess(test)

    def addFailure(self, test, err):
        self.status[test.id()] = 'FAIL'
        super().addFailure(test, err)

    def addError(self, test, err):
        self.status[test.id()] = 'ERROR'
        super().addError(test, err)

    def addSkip(self, test, reason):
        self.status[test.id()] = 'skip'
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        self.status[test.id()] = 'xfail'
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        self.status[test.id()] = 'xpass'
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            self.status[test.id()] = 'FAIL' if issubclass(err[0], test.failureException) else 'ERROR'
        super().addSubTest(test, subtest, err)


def run_unit(loader, names, shuffle):
    """Run the named tests in this process; the captured output and a status per test id."""
    if shuffle is not None:
        names = sorted(names, key=lambda n: seeded(shuffle, n))
    suite = unittest.TestSuite([loader.loadTestsFromName(name) for name in names])
    stream = io.StringIO()
    with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
        result = unittest.TextTestRunner(stream=stream, verbosity=0, resultclass=Statuses).run(suite)
    return result.status, stream.getvalue()


def worker(number, tasks, results, config):
    """A worker process: its own temporary folder, its own captured file descriptors, units until told to stop."""
    folder = Path(config['temp']) / ('w%d' % number)
    folder.mkdir(parents=True, exist_ok=True)
    for key in ('TEMP', 'TMP', 'TMPDIR'):
        os.environ[key] = str(folder)
    tempfile.tempdir = str(folder)
    log_path = folder / 'descriptors.log'
    log = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(log, 1)
    os.dup2(log, 2)
    sys.stdout = open(1, 'w', encoding='utf-8', errors='replace', closefd=False)
    sys.stderr = open(2, 'w', encoding='utf-8', errors='replace', closefd=False)
    for top in reversed(config['tops']):
        sys.path.insert(0, top)
    loader = make_loader(config['patterns'])
    shuffle = config['shuffle']
    if shuffle is not None:
        loader.sortTestMethodsUsing = lambda a, b: (seeded(shuffle, a) > seeded(shuffle, b)) - \
                                                   (seeded(shuffle, a) < seeded(shuffle, b))
    while True:
        unit = tasks.get()
        if unit is None:
            return
        kind, name, ids = unit
        results.put(('start', number, name))
        before = os.fstat(log).st_size
        started = time.monotonic()
        try:
            status, output = run_unit(loader, [name] if kind == 'module' else ids, shuffle)
        except BaseException as error:  # A unit that cannot even load is a failure of that unit, never a crash.
            status, output = {i: 'ERROR' for i in ids}, 'the unit could not run: %r\n' % (error,)
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        with open(log_path, 'rb') as stray:
            stray.seek(before)
            noise = stray.read().decode('utf-8', 'replace')
        results.put(('done', number, name, {'status': status, 'output': output + noise,
                                            'seconds': time.monotonic() - started}))


def main_checkout_root(root=ROOT):
    """The root of the main checkout of the repository that holds `root`: the parent of Git's common directory.

    In a linked worktree the common directory is the main checkout's `.git`; in the main checkout and in a clone it
    is the checkout's own `.git`. Raises ValueError when Git cannot say.
    """
    try:
        done = subprocess.run(['git', '-C', str(root), 'rev-parse', '--git-common-dir'], capture_output=True)
    except OSError as error:
        raise ValueError('git could not run: %s' % (error,))
    if done.returncode != 0:
        raise ValueError('git rev-parse --git-common-dir failed')
    common = Path(done.stdout.decode('utf-8', 'replace').strip())
    return (Path(root) / common).resolve().parent  # An absolute answer wins over the join.


def scratch_folder(root=ROOT):
    """The folder `tmp`, a sibling of the main checkout's folder (created when missing)."""
    folder = main_checkout_root(root).parent / 'tmp'
    folder.mkdir(exist_ok=True)
    return folder


def short_temp_base():
    """A short per-run folder for the worker folders: long default paths hit Windows path limits.

    It is made inside `tmp`, a sibling of the main checkout's folder, so a killed run leaves it where the
    developer can find it and TMP/TEMP are not involved. Only this folder is ever removed by the run. When `tmp`
    cannot be used, the earlier behavior applies and the runner says so.
    """
    try:
        return tempfile.mkdtemp(prefix='t', dir=scratch_folder())
    except (OSError, ValueError) as error:
        print('test-runner: scratch folder next to the main checkout unavailable (%s); using the earlier location'
              % (error,), flush=True)
    if os.name == 'nt':
        try:
            return tempfile.mkdtemp(prefix='t', dir=os.path.splitdrive(tempfile.gettempdir())[0] + os.sep)
        except OSError:
            pass
    return tempfile.mkdtemp(prefix='t')


def run_parallel(args, tests, workers, label):
    units, local = plan(tests)
    modules = {}
    for test in tests:
        modules[type(test).__module__] = modules.get(type(test).__module__, 0) + 1
    print('%s units=%d tests=%d modules=%d' % (label, len(units) + len(local), len(tests), len(modules)), flush=True)
    for name in sorted(modules):
        print('  module %s: %d tests' % (name, modules[name]), flush=True)
    started = time.monotonic()
    status, outputs = {}, []
    for test in local:  # Only this process can load these (for example a module skipped at import).
        buffer = io.StringIO()
        runner = unittest.TextTestRunner(stream=buffer, verbosity=0, resultclass=Statuses)
        outcome = runner.run(test)
        status.update(outcome.status)
        if not outcome.wasSuccessful():
            outputs.append(('local test ' + test.id(), buffer.getvalue()))
    if args.shuffle is not None:
        random.Random(args.shuffle).shuffle(units)
    base = short_temp_base()
    context = multiprocessing.get_context('spawn')
    tasks, results = context.Queue(), context.Queue()
    count = max(1, min(workers, len(units)))
    config = {'temp': base, 'tops': [str(top_level(start)) for start in args.start], 'patterns': args.k,
              'shuffle': args.shuffle}
    try:
        for unit in units:
            tasks.put(unit)
        for _ in range(count):
            tasks.put(None)
        processes = [context.Process(target=worker, args=(n, tasks, results, config), daemon=True)
                     for n in range(count)]
        for process in processes:
            process.start()
        waiting = {name: ids for _, name, ids in units}
        running = {}
        while waiting:
            try:
                message = results.get(timeout=1)
            except queue.Empty:
                for number, process in enumerate(processes):
                    if not process.is_alive() and process.exitcode not in (0, None) and number in running:
                        name = running.pop(number)
                        died(waiting.pop(name), status, outputs, name, process.exitcode)
                if waiting and not any(p.is_alive() for p in processes):
                    for name in list(waiting):
                        died(waiting.pop(name), status, outputs, name, 'no worker left')
                continue
            if message[0] == 'start':
                running[message[1]] = message[2]
                continue
            _, number, name, payload = message
            running.pop(number, None)
            waiting.pop(name, None)
            status.update(payload['status'])
            if any(s not in PASSING for s in payload['status'].values()):
                outputs.append(('unit ' + name, payload['output']))
        for process in processes:
            process.join(30)
            if process.is_alive():
                process.terminate()
        tasks.cancel_join_thread()
    finally:
        shutil.rmtree(base, ignore_errors=True)
    expected = {test.id() for test in tests}
    for missing in sorted(expected - status.keys()):
        status[missing] = 'ERROR'
    elapsed = time.monotonic() - started
    return report(args, status, outputs, expected, elapsed)


def died(ids, status, outputs, name, code):
    for test_id in ids:
        status[test_id] = 'ERROR'
    outputs.append(('unit ' + name, 'the worker process stopped (%s) while running this unit\n' % (code,)))


def report(args, status, outputs, expected, elapsed):
    if args.v:
        for test_id in sorted(status):
            print('%s ... %s' % (test_id, STATUS_WORDS.get(status[test_id], status[test_id])))
    for title, text in outputs:
        print('=' * 70 + '\n' + title + '\n' + '-' * 70 + '\n' + text.rstrip() + '\n')
    counts = {}
    for value in status.values():
        counts[value] = counts.get(value, 0) + 1
    failed = counts.get('FAIL', 0) + counts.get('xpass', 0)
    errors = counts.get('ERROR', 0)
    unexpected = sorted(set(status) - expected)
    for test_id in sorted(t for t, v in status.items() if v not in PASSING):
        print('not passing: ' + test_id)
    for test_id in unexpected:
        print('ran but was not discovered: ' + test_id)
    print('-' * 70)
    print('Ran %d tests in %.3fs\n' % (len(status), elapsed))
    extras = []
    if counts.get('skip'):
        extras.append('skipped=%d' % counts['skip'])
    if counts.get('xfail'):
        extras.append('expected failures=%d' % counts['xfail'])
    if failed or errors or unexpected:
        print('FAILED (%s)' % ', '.join(['failures=%d' % failed, 'errors=%d' % errors] + extras))
        return 1
    print('OK' + (' (%s)' % ', '.join(extras) if extras else ''))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run discovered unittest tests in parallel or sequentially.')
    parser.add_argument('-s', '--start', action='append', default=None,
                        help='folder to discover tests in; repeat it to discover in several folders')
    parser.add_argument('-p', '--pattern', default='test*.py')
    parser.add_argument('-k', action='append', default=[])
    parser.add_argument('-v', action='store_true')
    parser.add_argument('--shuffle', default=None, help='shuffle units and order inside a unit with this seed')
    args = parser.parse_args(argv)
    args.start = args.start or ['.']
    settings = early_approval.test_settings(Repository(Path.cwd()))
    if not settings['parallel']:
        print('test-runner: mode=sequential (%s)%s' % (
            settings['reason'], ' shuffle ignored' if args.shuffle is not None else ''), flush=True)
        if len(args.start) == 1:
            unittest.main(module=None, exit=True, argv=['unittest', 'discover', '-s', args.start[0], '-t', str(top_level(args.start[0])), '-p', args.pattern] +
                          (['-v'] if args.v else []) + [x for p in args.k for x in ('-k', p)])
        suite = discover_all(make_loader(args.k), args.start, args.pattern)
        if not suite.countTestCases():
            print('test-runner: no tests found', file=sys.stderr)
            return 5
        return 0 if unittest.TextTestRunner(verbosity=2 if args.v else 1).run(suite).wasSuccessful() else 1
    loader = make_loader(args.k)
    tests = list(walk(discover_all(loader, args.start, args.pattern)))
    if not tests:
        print('test-runner: no tests found', file=sys.stderr)
        return 5
    cores = os.cpu_count() or 1
    workers = cores if settings['workers'] == 0 else min(settings['workers'], cores)
    label = 'test-runner: mode=parallel workers=%d (cores=%d, test_workers=%d; %s)%s' % (
        workers, cores, settings['workers'], settings['reason'],
        '' if args.shuffle is None else ' shuffle=' + args.shuffle)
    return run_parallel(args, tests, workers, label)


if __name__ == '__main__':
    sys.exit(main())
