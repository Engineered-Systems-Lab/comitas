"""The machine-local timing record: honest estimates, advisory flags, explicit export and import."""
import contextlib
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import unittest.mock

import checkpoint as c
from governance.tools.support import local_state
from governance.tools.support import timing_record as t
from governance.tests.lifecycle import test_checkpoint as fixtures
import repo_paths as rp

HERE = t.machine()
OTHER = 'linux/cpu4/py3.13.0'
CFG = t.settings({'timing_record': {}})
ROOT = rp.ROOT


def sample(subject, seconds, ok=True, machine=None, run=None):
    n = sample.count = getattr(sample, 'count', 0) + 1
    return t.entry('check:' + subject, seconds, ok, run or f'run-{n}', machine or HERE, now=1_700_000_000 + n)


def history(subject, values, **kwargs):
    return [sample(subject, v, **kwargs) for v in values]


class SettingsTest(unittest.TestCase):
    def test_absent_or_off_means_no_recording(self):
        self.assertIsNone(t.settings({}))
        self.assertIsNone(t.settings({'timing_record': {'mode': 'off'}}))

    def test_defaults_are_the_agreed_starting_values(self):
        self.assertEqual({k: CFG[k] for k in ('window', 'min_samples', 'slow_factor', 'slow_margin_seconds',
                                              'fast_fraction', 'fast_min_mean_seconds')},
                         {'window': 20, 'min_samples': 3, 'slow_factor': 1.5, 'slow_margin_seconds': 10,
                          'fast_fraction': 0.4, 'fast_min_mean_seconds': 20})

    def test_the_shipped_policy_enables_it(self):
        policy = json.loads((ROOT / c.POLICY).read_text(encoding='utf-8'))
        self.assertEqual(t.settings(policy), CFG)

    def test_invalid_configuration_is_refused(self):
        for bad in ({'mode': 'remote'}, {'unknown': 1}, {'window': 0}, {'window': 2.5}, {'min_samples': 1},
                    {'min_samples': 30}, {'slow_factor': 1}, {'fast_fraction': 1}, {'slow_margin_seconds': -1},
                    {'window': True}, {'fast_min_mean_seconds': float('inf')}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                t.settings({'timing_record': bad})


class EntryTest(unittest.TestCase):
    def test_entries_have_one_generic_shape(self):
        value = sample('regressions', 12.5)
        self.assertEqual(set(value), t.ENTRY_KEYS)
        self.assertEqual((value['metric'], value['subject'], value['machine']), ('seconds', 'check:regressions', HERE))

    def test_parsing_is_strict_and_names_the_line(self):
        good = json.dumps(sample('a', 1))
        for bad in ('not json', json.dumps({**sample('a', 1), 'extra': 1}), json.dumps({**sample('a', 1), 'value': -1}),
                    json.dumps({**sample('a', 1), 'subject': 'Bad Subject'}), json.dumps({**sample('a', 1), 'ok': 1}),
                    json.dumps({**sample('a', 1), 'metric': 'bytes'}), json.dumps({**sample('a', 1), 'v': 2})):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'line 2'):
                t.parse(good + '\n' + bad + '\n')
        self.assertEqual(len(t.parse(good + '\n\n' + good + '\n')), 2)


class StatisticsTest(unittest.TestCase):
    def test_basis_depends_on_how_much_history_exists(self):
        self.assertEqual(t.summarize([], 'check:a', HERE, CFG)['basis'], 'none')
        self.assertEqual(t.summarize(history('a', [5, 6]), 'check:a', HERE, CFG)['basis'], 'thin')
        row = t.summarize(history('a', [10, 20, 30]), 'check:a', HERE, CFG)
        self.assertEqual((row['basis'], row['n'], row['mean'], row['min'], row['max']), ('measured', 3, 20, 10, 30))
        self.assertAlmostEqual(row['stdev'], 10)

    def test_other_hardware_is_only_ever_imported_never_measured(self):
        foreign = history('a', [10, 20, 30], machine=OTHER)
        row = t.summarize(foreign, 'check:a', HERE, CFG)
        self.assertEqual((row['basis'], row['local'], row['foreign']), ('imported', 0, 3))
        mixed = history('a', [100], machine=HERE) + foreign
        self.assertEqual(t.summarize(mixed, 'check:a', HERE, CFG)['basis'], 'imported')
        enough = history('a', [1, 1, 1]) + foreign
        row = t.summarize(enough, 'check:a', HERE, CFG)
        self.assertEqual((row['basis'], row['mean']), ('measured', 1))

    def test_only_the_recent_window_counts_and_failures_never_do(self):
        values = [1000] * 5 + [10] * 20
        self.assertEqual(t.summarize(history('a', values), 'check:a', HERE, CFG)['mean'], 10)
        runs = history('a', [10, 10, 10]) + history('a', [999], ok=False)
        row = t.summarize(runs, 'check:a', HERE, CFG)
        self.assertEqual((row['n'], row['mean']), (3, 10))
        narrow = t.settings({'timing_record': {'window': 3, 'min_samples': 3}})
        self.assertEqual(t.summarize(history('a', [100, 1, 1, 1]), 'check:a', HERE, narrow)['mean'], 1)

    def test_subjects_do_not_mix(self):
        runs = history('a', [10, 10, 10]) + history('b', [50, 50, 50])
        self.assertEqual(t.summarize(runs, 'check:a', HERE, CFG)['mean'], 10)


class EstimateTest(unittest.TestCase):
    def test_no_history_gives_no_figure_at_all(self):
        result = t.estimate([], ['check:a', 'check:b'], HERE, CFG)
        self.assertEqual((result['basis'], result['expected_seconds'], result['high_seconds']), ('none', None, None))
        self.assertIn('no estimate', t.describe(result))

    def test_a_gap_makes_the_total_a_lower_bound_never_a_guess_of_zero(self):
        runs = history('a', [60, 60, 60]) + history('b', [5])
        result = t.estimate(runs, ['check:a', 'check:b', 'check:c'], HERE, CFG)
        self.assertEqual(result['basis'], 'partial')
        self.assertEqual(sorted(result['gaps']), ['check:b', 'check:c'])
        self.assertEqual(result['expected_seconds'], 65)  # the thin sample still counts; the unknown adds nothing
        text = t.describe(result)
        self.assertIn('At least', text)
        self.assertIn('lower bound', text)
        self.assertIn('b, c', text)

    def test_complete_history_gives_a_range(self):
        runs = history('a', [60, 60, 60]) + history('b', [10, 20, 30])
        result = t.estimate(runs, ['check:a', 'check:b'], HERE, CFG)
        self.assertEqual(result['basis'], 'measured')
        self.assertEqual(result['expected_seconds'], 80)
        self.assertEqual(result['high_seconds'], 100)  # b contributes mean 20 plus two deviations of 10
        self.assertEqual(t.describe(result), 'About 1m 20s (up to 1m 40s), measured on this machine. Assumes every check runs fresh.')

    def test_imported_history_is_labelled_rough(self):
        runs = history('a', [60, 60, 60], machine=OTHER)
        result = t.estimate(runs, ['check:a'], HERE, CFG)
        self.assertEqual(result['basis'], 'imported')
        self.assertIn('only a rough guide', t.describe(result))

    def test_spans_read_naturally(self):
        self.assertEqual([t.span(s) for s in (0, 5.4, 59.6, 60, 754.6)], ['0s', '5s', '1m 00s', '1m 00s', '12m 35s'])


class DepartureTest(unittest.TestCase):
    def kinds(self, runs, results):
        return [(f['subject'], f['kind']) for f in t.observations(runs, results, HERE, CFG)]

    def test_slow_needs_both_the_factor_and_the_margin(self):
        runs = history('a', [100, 100, 100]) + history('tiny', [1, 1, 1])
        self.assertEqual(self.kinds(runs, [('check:a', 151, True)]), [('check:a', 'slower')])
        self.assertEqual(self.kinds(runs, [('check:a', 149, True)]), [])
        self.assertEqual(self.kinds(runs, [('check:a', 109, True)]), [])
        self.assertEqual(self.kinds(runs, [('check:tiny', 9, True)]), [])  # 9x slower but under the 10 s margin

    def test_fast_flags_a_long_check_that_barely_ran(self):
        runs = history('a', [100, 100, 100]) + history('short', [10, 10, 10])
        self.assertEqual(self.kinds(runs, [('check:a', 39, True)]), [('check:a', 'faster')])
        self.assertEqual(self.kinds(runs, [('check:a', 41, True)]), [])
        self.assertEqual(self.kinds(runs, [('check:short', 1, True)]), [])  # mean below the 20 s floor

    def test_failures_and_thin_history_are_never_flagged(self):
        runs = history('a', [100, 100, 100]) + history('new', [100, 100])
        self.assertEqual(self.kinds(runs, [('check:a', 900, False), ('check:new', 900, True),
                                           ('check:unknown', 900, True)]), [])

    def test_the_flag_carries_its_evidence(self):
        runs = history('a', [100, 100, 100])
        self.assertEqual(t.observations(runs, [('check:a', 200, True)], HERE, CFG),
                         [{'subject': 'check:a', 'kind': 'slower', 'seconds': 200, 'mean': 100, 'runs': 3}])


@contextlib.contextmanager
def ignored_repository():
    """The Git common directory of a disposable repository whose .local folder is ignored."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        subprocess.run(['git', '-C', str(root), 'init', '-q', '-b', 'main'], check=True, capture_output=True)
        (root / '.gitignore').write_text('/.local/\n', encoding='utf-8')
        yield local_state.common_directory(root)


class ExportImportTest(unittest.TestCase):
    def test_round_trip_is_lossless_and_import_is_idempotent(self):
        runs = history('a', [1, 2, 3], machine=OTHER)
        text = t.export_text(runs, 'workstation-a', OTHER, now=1_700_000_000)
        self.assertTrue(text.isascii() and text.endswith('\n') and '\r' not in text)
        self.assertEqual(json.loads(text.splitlines()[0])['label'], 'workstation-a')
        self.assertEqual(t.import_entries(text), runs)
        with ignored_repository() as common:
            record = t.Record(common)
            self.assertEqual(record.merge(t.import_entries(text)), 3)
            self.assertEqual(record.merge(t.import_entries(text)), 0)
            self.assertEqual(record.entries(), runs)

    def test_bad_exports_and_labels_are_refused(self):
        with self.assertRaises(ValueError):
            t.import_entries('')
        with self.assertRaises(ValueError):
            t.import_entries(json.dumps(sample('a', 1)) + '\n')  # no header
        for label in ('', 'Upper', 'has space', '-lead', 'x' * 65):
            with self.subTest(label=label), self.assertRaises(ValueError):
                t.export_text([], label)

    def test_appends_are_whole_lines(self):
        with ignored_repository() as common:
            record = t.Record(common)
            record.append(history('a', [1, 2]))
            record.append(history('a', [3]))
            self.assertEqual([e['value'] for e in record.entries()], [1, 2, 3])
            self.assertEqual(len(record.path.read_text(encoding='ascii').splitlines()), 3)


class CommandTest(unittest.TestCase):
    """The standalone command against a disposable repository, so nothing real is read or written."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        (self.repo / 'governance' / 'config').mkdir(parents=True)
        subprocess.run(['git', '-C', str(self.repo), 'init', '-q', '-b', 'main'], check=True)
        (self.repo / '.gitignore').write_text('/.local/\n', encoding='utf-8')
        (self.repo / c.POLICY).write_text(json.dumps({'timing_record': {}}), encoding='utf-8')

    def record_file(self):
        return local_state.timings_file(local_state.common_directory(self.repo))

    def run_command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = t.main(['--repo', str(self.repo), *args])
        return code, out.getvalue(), err.getvalue()

    def test_the_record_lives_in_the_local_folder_of_the_primary_and_never_in_a_tracked_place(self):
        t.Record(local_state.common_directory(self.repo)).append(history('a', [10, 20, 30]))
        self.assertTrue(self.record_file().is_file())
        self.assertEqual(self.record_file(), self.repo / '.local' / 'timings' / 'checkpoint-timings.jsonl')
        status = subprocess.run(['git', '-C', str(self.repo), 'status', '--porcelain', '-uall'],
                                capture_output=True, text=True).stdout
        self.assertNotIn(local_state.TIMINGS_FILE, status)
        code, out, _ = self.run_command('stats')
        self.assertEqual(code, 0)
        self.assertIn('check:a', out)
        self.assertIn('measured', out)

    def test_stats_lists_a_subject_that_never_succeeded_without_failing(self):
        t.Record(local_state.common_directory(self.repo)).append(history('broken', [10], ok=False))
        code, out, _ = self.run_command('stats')
        self.assertEqual(code, 0)
        self.assertIn('no successful run', out)

    def test_export_never_overwrites_and_import_seeds(self):
        t.Record(local_state.common_directory(self.repo)).append(history('a', [10, 20, 30]))
        target = Path(self.temp.name) / 'timings.jsonl'
        self.assertEqual(self.run_command('export', '--output', str(target), '--label', 'sample')[0], 0)
        code, _, err = self.run_command('export', '--output', str(target), '--label', 'sample')
        self.assertEqual(code, 1)
        self.assertIn('overwrite', err)
        self.record_file().unlink()
        self.assertEqual(self.run_command('import', '--input', str(target))[1].strip(), 'imported 3 new entries')
        self.assertEqual(self.run_command('import', '--input', str(target))[1].strip(), 'imported 0 new entries')

    def test_a_local_folder_that_is_not_ignored_is_refused_and_nothing_is_written(self):
        (self.repo / '.gitignore').write_text('tmp/\n', encoding='utf-8')
        source = Path(self.temp.name) / 'seed.jsonl'
        source.write_text(t.export_text(history('a', [1, 2, 3]), 'sample'), encoding='ascii', newline='\n')
        code, _, err = self.run_command('import', '--input', str(source))
        self.assertEqual(code, 1)
        self.assertIn('not git-ignored', err)
        self.assertFalse((self.repo / '.local').exists())
        self.assertEqual(self.run_command('stats')[0], 0)  # a read needs no folder

    def test_a_policy_without_the_setting_fails_clearly(self):
        (self.repo / c.POLICY).write_text('{}', encoding='utf-8')
        code, _, err = self.run_command('stats')
        self.assertEqual(code, 1)
        self.assertIn('not enabled', err)


class TransactionTest(unittest.TestCase):
    """The checkpoint records after its checks, flags departures and never lets recording affect validation."""

    def setUp(self):
        self.f = fixtures.CheckpointTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.tool = self.f.tool
        self.seconds = {'staged-whitespace': 0.01, 'continuity': 5.0, 'regressions': 100.0}
        self.status = {}

    def enable(self, **overrides):
        self.f.policy.update(timing_record=overrides)
        self.f.profiles['profiles']['test']['limitations'] = 'Disposable timing fixture.'
        self.f.write_json(c.POLICY, self.f.policy)
        self.f.write_json(c.PROFILES, self.f.profiles)

        def runner(argv, root):
            result = c.run(argv, root)
            name = ('staged-whitespace' if argv[0] == 'git' else 'regressions' if 'tests PASS' in ' '.join(argv)
                    else 'continuity')
            return dict(result, duration_seconds=self.seconds[name], exit_code=self.status.get(name, result['exit_code']))
        self.tool.runner = runner

    def entries(self):
        return t.Record(self.tool.common).entries()

    def record_path(self):
        return t.Record(self.tool.common).path

    def prepare_and_withdraw(self):
        try:
            return self.f.prepare()
        finally:
            if (self.tool.store / c.TRANSACTION).exists():
                self.tool.abandon()

    def test_a_seal_appends_one_entry_per_executed_check(self):
        self.enable()
        packet = self.f.prepare()
        self.assertEqual(packet['timing_observations'], [])
        got = {e['subject']: e for e in self.entries()}
        self.assertEqual({k: v['value'] for k, v in got.items()},
                         {'check:staged-whitespace': 0.01, 'check:continuity': 5.0, 'check:regressions': 100.0})
        first = got['check:continuity']
        self.assertEqual((first['ok'], first['machine']), (True, HERE))
        self.assertNotEqual(first['run'], packet['id'])
        self.assertEqual(first['context']['transaction'], packet['id'])
        self.assertEqual(first['context']['parent'], self.f.parent)
        self.assertEqual(first['context']['candidate_tree'], packet['tree'])
        self.assertEqual(first['context']['attribution'], 'declared')
        self.assertIsNone(first['context']['worker'])
        self.assertIsNone(first['context']['session_id'])
        self.assertIsNone(first['context']['sealed_digest'])
        self.assertEqual(first['context']['phase'], 'seal-validation')
        evidence = next(e for e in packet['evidence'] if e['id'] == 'continuity')
        self.assertEqual(first['context']['argv'], evidence['argv'])
        self.assertEqual(first['context']['python']['sha256'], c.digest(Path(c.sys.executable).read_bytes()))

    def test_validation_only_and_seal_have_distinct_runs_and_preserve_prior_entries(self):
        self.enable()
        original = self.tool.prepare(['a.txt'], self.f.message, 'test', self.f.parent,
                                     'refs/heads/main', 'Fixture scope', validate_only=True,
                                     session_id='declared-fixture-chat')
        before = self.entries()
        self.assertEqual(len({e['run'] for e in before}), 1)
        self.assertTrue(all(e['context']['phase'] == 'validation-only' for e in before))
        self.assertTrue(all(e['context']['session_id'] == 'declared-fixture-chat' for e in before))
        self.tool.seal()
        after = self.entries()
        self.assertEqual(after[:len(before)], before)
        self.assertEqual(len({e['run'] for e in after}), 2)
        self.assertTrue(all(e['context']['transaction'] == original['id'] for e in after))
        self.assertTrue(all(e['context']['sealed_digest'] is None for e in after))
        self.assertTrue(all(e['context']['phase'] == 'seal-validation' for e in after[len(before):]))

    def test_frozen_worker_is_copied_as_a_declaration_without_guessing(self):
        self.enable()
        worker = {'identity': 'Declared worker', 'isAI': True, 'configuration': None}
        packet = {'policy': self.f.policy, 'id': 'fixture-transaction', 'parent': self.f.parent,
                  'profile': 'test', 'manifest': ['a.txt'], 'tree': 'a' * 40,
                  'profile_data': {'worker_context': worker}, 'session_id': None,
                  'evidence': [{'id': 'fixture', 'argv': ['fixture-command']}]}
        self.tool.record_timings(packet, [('fixture', 1.0, True)], 'fixture-attempt', 'validation-only')
        entry = self.entries()[0]
        self.assertEqual(entry['context']['worker'], worker)
        self.assertEqual(entry['context']['attribution'], 'declared')
        self.assertIsNone(entry['context']['session_id'])

    def test_invalid_session_declaration_stops_before_staging(self):
        self.enable()
        for value in ('', ' leading', 'line\nbreak', 'x' * 257, 42):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Session ID'):
                self.tool.prepare(['a.txt'], self.f.message, 'test', self.f.parent,
                                  'refs/heads/main', 'Fixture scope', session_id=value)
        self.assertEqual(self.tool.inventory(), [])

    def test_legacy_and_attributed_entries_export_without_inferred_backfill(self):
        legacy = sample('legacy', 2, run='old-transaction')
        attributed = t.entry('check:new', 3, True, 'new-attempt', HERE,
                             {'transaction': 'new-transaction', 'worker': None,
                              'session_id': None, 'sealed_digest': None, 'attribution': 'declared'})
        values = [legacy, attributed]
        self.assertEqual(t.import_entries(t.export_text(values, 'mixed-history')), values)
        self.assertEqual(legacy['context'], {})

    def test_the_record_is_unavailable_to_a_policy_that_does_not_enable_it(self):
        self.tool.runner = c.run
        packet = self.f.prepare()
        self.assertNotIn('timing_observations', packet)
        self.assertEqual(self.entries(), [])

    def test_departures_are_flagged_against_history_and_never_block(self):
        self.enable()
        t.Record(self.tool.common).append(history('regressions', [30, 30, 30]) + history('continuity', [30, 30, 30]))
        packet = self.f.prepare()
        self.assertEqual(sorted((o['subject'], o['kind']) for o in packet['timing_observations']),
                         [('check:continuity', 'faster'), ('check:regressions', 'slower')])
        self.assertEqual(packet['state'], 'prepared')

    def test_failed_checks_are_recorded_but_do_not_enter_statistics(self):
        self.enable()
        self.status['regressions'] = 1
        with self.assertRaisesRegex(ValueError, 'Validation failed'):
            self.f.prepare()
        failed = [e for e in self.entries() if e['subject'] == 'check:regressions']
        self.assertEqual([(e['ok'], e['value']) for e in failed], [(False, 100.0)])
        self.assertEqual(t.summarize(self.entries(), 'check:regressions', HERE, CFG)['basis'], 'none')

    def test_a_broken_record_never_alters_validation(self):
        self.enable()
        t.Record(self.tool.common).path.mkdir(parents=True)  # appending to a directory fails
        packet = self.f.prepare()
        self.assertEqual(packet['state'], 'prepared')
        self.assertIn('unavailable', packet['timing_observations'])

    def test_a_corrupt_record_is_reported_not_trusted(self):
        self.enable()
        self.record_path().parent.mkdir(parents=True)
        self.record_path().write_text('garbage\n', encoding='utf-8')
        packet = self.f.prepare()
        self.assertEqual(packet['state'], 'prepared')
        self.assertIn('line 1', packet['timing_observations']['unavailable'])

    def test_estimate_reads_history_stages_nothing_and_says_when_it_has_none(self):
        self.enable()
        none = self.tool.estimate(['a.txt'])
        self.assertEqual((none['basis'], none['expected_seconds']), ('none', None))
        self.assertEqual(self.tool.inventory(), [])
        self.prepare_and_withdraw()
        self.assertEqual(self.tool.estimate(['a.txt'])['basis'], 'partial')  # one run is too few
        for _ in range(2):
            self.prepare_and_withdraw()
        result = self.tool.estimate(['a.txt'])
        self.assertEqual((result['basis'], round(result['expected_seconds'], 2)), ('measured', 105.01))
        self.assertEqual(self.tool.inventory(), [])
        self.assertEqual(result['profile'], 'test')

    def test_a_runner_without_durations_cannot_break_validation(self):
        self.enable()
        real = self.tool.runner
        self.tool.runner = lambda argv, root: {k: v for k, v in real(argv, root).items() if k != 'duration_seconds'}
        packet = self.f.prepare()
        self.assertEqual((packet['state'], packet['timing_observations'], self.entries()), ('prepared', [], []))

    def test_the_command_line_estimates_without_taking_the_lock(self):
        self.enable()
        manifest = self.f.root.parent / 'scope.json'
        manifest.write_text('["a.txt"]', encoding='utf-8')
        out = io.StringIO()
        with unittest.mock.patch.object(c.sys, 'argv', ['checkpoint.py', '--repo', str(self.f.root), 'estimate',
                                                        '--manifest', str(manifest)]), redirect_stdout(out):
            with self.tool.lock():  # a seal in progress holds the operation lock; estimate must still answer
                self.assertEqual(c.main(), 0)
        result = json.loads(out.getvalue())
        self.assertEqual((result['basis'], result['expected_seconds']), ('none', None))
        self.assertIn('no estimate', result['summary'])

    def test_estimate_requires_the_setting(self):
        with self.assertRaisesRegex(ValueError, 'not enabled'):
            self.tool.estimate(['a.txt'])


if __name__ == '__main__':
    unittest.main()
