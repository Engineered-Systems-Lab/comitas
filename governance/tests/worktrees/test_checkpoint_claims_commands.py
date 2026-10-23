"""Overlap in execution-status, the watch, clear and doctor, in disposable repositories only."""
from contextlib import closing, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from governance.tools.worktrees import claims
from governance.tests.worktrees.test_checkpoint_execution_status import StatusCase
from governance.tools.worktrees import guard as w
import repo_paths as rp


def entries(*items):
    result = []
    for item in items:
        path, _, subclasses = item.partition(':')
        result.append((path, 'tooling', [s for s in subclasses.split(',') if s]))
    return result


def dead_process():
    child = subprocess.Popen([sys.executable, '-B', '-c', 'pass'])
    started = claims.process_start(child.pid)
    child.wait()
    return child.pid, started


class ClaimsCase(StatusCase):
    def claim(self, owner, *items, job='job_a'):
        return claims.declare_paths(self.common, owner, job, None, entries(*items))

    def database(self):
        return self.common.parent / '.local/claims/claims.sqlite'

    def events(self, name=None):
        with closing(sqlite3.connect(self.database())) as con:
            rows = con.execute('SELECT event, detail FROM events ORDER BY id').fetchall()
        return [(e, json.loads(d)) for e, d in rows if name is None or e == name]


class OverlapStatusTest(ClaimsCase):
    def test_the_same_file_in_two_live_missions_reads_overlap_with_the_probable_conflict_wording(self):
        self.mission('m1')
        self.mission('m2')
        self.claim('m1', 'governance/tools/checkpoint.py:checkpoint', 'a.md:x')
        self.claim('m2', 'governance/tools/checkpoint.py:checkpoint', job='job_b')
        status = self.status()
        self.assertEqual(status['level'], 'OVERLAP')
        self.assertEqual(status['line'], 'OVERLAP: m1 with m2, same file(s): governance/tools/checkpoint.py')
        self.assertEqual([(o['kind'], o['files']) for o in status['overlaps']], [('same_file', ['governance/tools/checkpoint.py'])])
        self.assertEqual({c['owner']: c['level'] for c in status['claims']}, {'m1': 'OVERLAP', 'm2': 'OVERLAP'})
        self.assertEqual(status['windows'], [])
        self.assertIn('overlap: m1 with m2, same file(s): governance/tools/checkpoint.py', status['notes'])

    def test_the_same_subclass_in_different_files_reads_overlap_with_the_weaker_wording(self):
        self.mission('m1')
        self.mission('m2')
        self.claim('m1', 'a.py:checkpoint,decisions')
        self.claim('m2', 'b.py:checkpoint')
        status = self.status()
        self.assertEqual((status['level'], status['line']), ('OVERLAP', 'OVERLAP: m1 with m2, same subclass, different files: checkpoint'))

    def test_missions_on_different_ground_stay_clear_and_the_claims_describe_them(self):
        self.mission('m1')
        self.mission('m2')
        self.claim('m1', 'a.py:x', 'b.py:x')
        self.claim('m2', 'c.py:y')
        status = self.status()
        self.assertEqual((status['level'], status['overlaps'], status['stale_claims']), ('CLEAR', [], 0))
        rows = {c['owner']: c for c in status['claims']}
        self.assertEqual((rows['m1']['level'], rows['m1']['subclasses'], rows['m1']['files'], rows['m1']['job_handle']),
                         ('CLEAR', ['x'], 2, 'job_a'))

    def test_a_claim_whose_worktree_is_gone_is_ignored_for_the_level_and_counted_as_stale(self):
        self.mission('m1')
        self.claim('m1', 'a.py:x')
        self.claim('ghost', 'a.py:x')
        status = self.status()
        self.assertEqual((status['level'], status['stale_claims']), ('CLEAR', 1))
        self.assertEqual([c['owner'] for c in status['claims']], ['m1'])
        self.assertTrue(any('stale claim' in n and 'lead developer' in n for n in status['notes']))

    def test_a_frozen_window_outranks_overlap_and_the_overlap_is_still_listed(self):
        self.mission('m1', 'preparing', lock=True)
        self.mission('m2')
        self.claim('m1', 'a.py:x')
        self.claim('m2', 'a.py:x')
        status = self.status()
        self.assertEqual(status['level'], 'FROZEN')
        self.assertTrue(status['line'].startswith('REFS FROZEN: m1'))
        self.assertEqual(len(status['overlaps']), 1)
        self.assertEqual({c['owner']: c['level'] for c in status['claims']}, {'m1': 'FROZEN', 'm2': 'OVERLAP'})

    def test_an_owner_with_a_window_and_no_path_claim_still_appears_in_the_claims(self):
        self.mission('m1', 'preparing', lock=True)
        status = self.status()
        self.assertEqual([(c['owner'], c['level'], c['files']) for c in status['claims']], [('m1', 'FROZEN', 0)])

    def test_the_owner_filter_describes_that_mission_and_its_partners_only(self):
        for owner in ('m1', 'm2', 'm3', 'm4'):
            self.mission(owner)
        self.claim('m1', 'a.py:x')
        self.claim('m2', 'a.py:x')
        self.claim('m3', 'q.py:q')
        self.claim('m4', 'q.py:q')
        mine = w.execution_status(self.root, 'm1')
        self.assertEqual((mine['level'], mine['owner']), ('OVERLAP', 'm1'))
        self.assertEqual(sorted(c['owner'] for c in mine['claims']), ['m1', 'm2'])
        self.assertEqual([o['owners'] for o in mine['overlaps']], [['m1', 'm2']])
        quiet = w.execution_status(self.root, 'nobody')
        self.assertEqual((quiet['level'], quiet['overlaps'], quiet['claims']), ('CLEAR', [], []))

    def test_after_the_other_mission_lands_the_waiting_one_reads_base_moved_until_it_reconciles(self):
        self.mission('m1')
        self.mission('m2')
        self.claim('m1', 'a.py:x')
        self.claim('m2', 'a.py:x', job='job_b')
        claims.landed(self.common, 'm1', 'job_a', 'f' * 40)
        status = w.execution_status(self.root, 'm2')
        self.assertEqual(status['level'], 'CLEAR', 'the lander released its claims')
        [moved] = status['base_moved']
        self.assertEqual((moved['owner'], moved['main'], moved['by']), ('m2', 'f' * 40, 'm1'))
        self.assertIn('base moved: main advanced to ' + 'f' * 12 + ' by m1; its approval cannot be carried '
                      '(diff_bound_approval is off): prepare again and take a fresh Approve', status['notes'],
                      'the fixture repository has no feature file, so the carry reads off and the note says so')
        self.assertEqual(w.execution_status(self.root, 'm3')['base_moved'], [])
        claims.note_event(self.common, 'm2', 'reconciled', main='f' * 40)
        self.assertEqual(w.execution_status(self.root, 'm2')['base_moved'], [])

    def test_without_a_database_the_claims_are_empty_and_the_level_comes_from_evidence(self):
        self.mission('m1')
        status = self.status()
        self.assertEqual((status['level'], status['claims'], status['database']['state']), ('CLEAR', [], 'missing'))
        self.assertFalse((self.root / '.local').exists())

    def test_a_slice_one_database_reads_without_path_claims(self):
        with patch.object(claims, 'SCHEMA_MINOR', 0), patch.object(claims, 'MIGRATIONS', {}):
            claims.open_window(self.common, 'm1', 'prepare', 'validating')
        self.mission('m1')
        status = self.status()
        self.assertEqual((status['database']['state'], status['claims'], status['level']), ('ok', [], 'CLEAR'))

    def test_an_old_path_claim_gets_only_an_age_flag(self):
        self.mission('m1')
        self.claim('m1', 'a.py:x')
        with closing(sqlite3.connect(self.database())) as con:
            con.execute("UPDATE claims SET since='2020-01-01T00:00:00Z'")
            con.commit()
        [row] = self.status()['claims']
        self.assertEqual(row['flags'], ['age'])

    def test_the_command_prints_the_notes_after_the_line_and_accepts_an_owner(self):
        self.mission('m1')
        self.mission('m2')
        self.claim('m1', 'a.py:x')
        self.claim('m2', 'a.py:x')
        result = subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.WORKTREE), '--main', str(self.root), 'execution-status', '--owner', 'm1'],
                                capture_output=True, text=True, cwd=self.temp.name)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.split('\n')
        self.assertEqual(lines[0], 'OVERLAP: m1 with m2, same file(s): a.py')
        self.assertEqual(lines[1], 'overlap: m1 with m2, same file(s): a.py')
        self.assertEqual(json.loads('\n'.join(lines[2:]))['owner'], 'm1')


class WatchTest(ClaimsCase):
    def setUp(self):
        super().setUp()
        claims.open_window(self.common, 'seed', 'prepare', 'validating')
        claims.close_windows(self.common, 'seed', 'prepare')

    def start(self, everything=False):
        watch = w.Watch(self.root, everything)
        self.assertEqual(watch.step(), [], 'the first poll takes the baseline and reports nothing that is routine')
        return watch

    def test_a_quiet_repository_prints_nothing_poll_after_poll(self):
        watch = self.start()
        self.assertEqual([watch.step() for _ in range(3)], [[], [], []])

    def test_a_landing_prints_one_line_with_the_commit_the_owner_the_job_and_the_time(self):
        watch = self.start()
        claims.landed(self.common, 'm1', 'worker_claims', 'a1b2c3d4e5f6' + '0' * 28)
        [line] = watch.step()
        self.assertRegex(line, r'^LANDED guarded integration finished, commit a1b2c3d4e5f6: owner m1, job worker_claims, at 20\d\d-')
        self.assertEqual(watch.step(), [], 'one line per event')

    def test_events_before_the_watch_started_are_not_replayed(self):
        claims.landed(self.common, 'old', 'old_job', 'b' * 40)
        self.assertEqual(self.start().step(), [])

    def test_failures_an_abandonment_and_a_stopped_integration_each_print_an_attention_line(self):
        watch = self.start()
        claims.note_event(self.common, 'm1', 'attention', reason='prepare failed (packet prepare-failed)', job_handle='job_a')
        claims.note_event(self.common, 'm2', 'attention', reason='abandoned', job_handle='job_b')
        claims.note_event(self.common, 'm3', 'attention', reason='integration stopped: main moved', job_handle=None)
        lines = watch.step()
        self.assertEqual([l.split(':')[0] for l in lines], ['ATTENTION prepare failed (packet prepare-failed)',
                                                           'ATTENTION abandoned', 'ATTENTION integration stopped'])
        self.assertIn('owner m1, job job_a', lines[0])
        self.assertIn('owner m3, job -', lines[2])

    def test_routine_events_stay_quiet_by_default_and_print_with_all(self):
        quiet, loud = self.start(), self.start(everything=True)
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        claims.close_windows(self.common, 'm1', 'prepare')
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        self.assertEqual(quiet.step(), [])
        lines = loud.step()
        self.assertTrue(lines and all(l.startswith('EVENT ') for l in lines), lines)
        self.assertTrue(any('EVENT opened' in l for l in lines) and any('EVENT closed' in l for l in lines))

    def test_a_window_whose_command_died_prints_once_and_again_only_after_it_clears_and_returns(self):
        self.mission('m1', 'preparing', lock=True)
        pid, started = dead_process()
        self.declare('m1', pid=pid, started=started)
        watch = w.Watch(self.root)
        [line] = watch.step()
        self.assertRegex(line, r'^ATTENTION prepare window command died \(pid \d+ is not running\) and the window is still open: owner m1, job job_a')
        self.assertEqual(watch.step(), [])
        claims.close_windows(self.common, 'm1', 'prepare')
        self.assertEqual(watch.step(), [], 'a declared-less window is only undeclared; nothing died')
        self.declare('m1', pid=pid, started=started)
        self.assertEqual(len(watch.step()), 1)

    def test_a_declared_window_with_a_dead_command_and_no_transaction_is_attention(self):
        pid, started = dead_process()
        self.declare('ghost', pid=pid, started=started)
        [line] = w.Watch(self.root).step()
        self.assertIn('ATTENTION prepare window is declared but its command died and no transaction shows it: owner ghost', line)

    def test_a_blocked_or_stopped_job_with_an_open_window_is_attention_and_a_working_job_is_not(self):
        self.mission('m1', 'preparing', lock=True)
        for state, expected in ((('BLOCKED', 'ACTIVE'), 1), (('WIP', 'STOPPED'), 1), (('WIP', 'ACTIVE'), 0), (None, 0)):
            with self.subTest(state=state), patch.object(w, 'job_state', return_value=state):
                lines = w.Watch(self.root).step()
                self.assertEqual(len(lines), expected, lines)
                if expected:
                    self.assertIn('Job record is %s (activity %s) while its window is open: owner m1, job job_a' % state, lines[0])

    def test_a_blocked_job_without_an_open_window_is_not_reported(self):
        with patch.object(w, 'job_state', return_value=('BLOCKED', 'STOPPED')):
            self.assertEqual(w.Watch(self.root).step(), [])


class DegradedWatchTest(ClaimsCase):
    def commit(self):
        self.git('-c', 'user.name=T', '-c', 'user.email=t@example.invalid', 'commit', '-q', '--allow-empty', '-m', 'another')
        return self.git('rev-parse', 'HEAD').strip()

    def test_a_missing_database_prints_one_notice_then_infers_a_landing_from_the_tip_of_main(self):
        watch = w.Watch(self.root)
        [notice] = watch.step()
        self.assertEqual(notice, 'NOTICE: claims database missing: no claims database yet; '
                                 'watching the derived evidence only (packets, locks, worktree list, tip of main)')
        self.assertEqual(watch.step(), [], 'the notice is printed once')
        tip = self.commit()
        [line] = watch.step()
        self.assertTrue(line.startswith('LANDED (inferred, claims database unavailable) main advanced '), line)
        self.assertIn(tip[:12], line)
        self.assertIn('owner -, job -', line)

    def test_a_corrupt_database_falls_back_the_same_way_and_a_failed_packet_is_still_seen(self):
        self.database().parent.mkdir(parents=True)
        self.database().write_bytes(b'garbage' * 300)
        self.mission('m1', 'prepare-failed', job='job_a')
        watch = w.Watch(self.root)
        lines = watch.step()
        self.assertTrue(lines[0].startswith('NOTICE: claims database corrupt'))
        self.assertTrue(lines[1].startswith('ATTENTION prepare failed (packet prepare-failed): owner m1, job job_a'), lines)
        self.assertEqual(watch.step(), [])

    def test_the_database_returning_prints_a_notice_and_a_fresh_baseline(self):
        watch = w.Watch(self.root)
        watch.step()
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        self.assertEqual(watch.step(), ['NOTICE: claims database ok again'])
        claims.landed(self.common, 'm1', 'job_a', 'c' * 40)
        self.assertEqual(len(watch.step()), 1)

    def test_a_database_held_by_a_writer_is_still_read_in_wal_mode_and_the_watch_carries_on(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        blocker = sqlite3.connect(self.database(), isolation_level=None)
        blocker.execute('BEGIN EXCLUSIVE')
        self.addCleanup(blocker.close)
        with patch.object(claims, 'BUSY_SECONDS', 0.2):
            watch = w.Watch(self.root)
            self.assertEqual((watch.step(), watch.step()), ([], []))

    def test_the_loop_prints_each_line_at_once_survives_an_unreadable_repository_and_stops_after_the_polls(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = w.watch(Path(self.temp.name) / 'missing', 0.01, False, polls=2)
        self.assertEqual(code, 0)
        self.assertEqual(output.getvalue().count('NOTICE: the watch could not read the repository'), 2)

    def test_an_interrupt_ends_the_loop_cleanly(self):
        with patch.object(time, 'sleep', side_effect=KeyboardInterrupt), redirect_stdout(io.StringIO()):
            self.assertEqual(w.watch(self.root, 5, False), 0)


class WatchCommandLineTest(ClaimsCase):
    def run_watch(self, *extra):
        return subprocess.Popen([sys.executable, '-B', str(rp.ROOT / rp.WORKTREE), '--main', str(self.root), 'execution-status', '--watch', *extra],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=self.temp.name)

    def test_a_quiet_watch_prints_nothing_and_exits_zero_after_its_polls(self):
        claims.open_window(self.common, 'seed', 'prepare', 'validating')
        claims.close_windows(self.common, 'seed', 'prepare')
        process = self.run_watch('--interval', '0.2', '--polls', '3')
        out, err = process.communicate(timeout=120)
        self.assertEqual((process.returncode, out), (0, ''), err)

    def test_a_landing_written_by_another_process_while_it_runs_is_printed_and_nothing_else(self):
        claims.open_window(self.common, 'seed', 'prepare', 'validating')
        claims.close_windows(self.common, 'seed', 'prepare')
        process = self.run_watch('--interval', '0.25', '--polls', '24')
        deadline = time.time() + 90
        n = 0
        while process.poll() is None and time.time() < deadline:
            n += 1
            claims.landed(self.common, 'm%d' % n, 'worker_claims', '%040x' % n)
            time.sleep(0.3)
        out, err = process.communicate(timeout=60)
        self.assertEqual(process.returncode, 0, err)
        lines = out.splitlines()
        self.assertTrue(lines and all(l.startswith('LANDED ') for l in lines), out)
        self.assertIn('job worker_claims', lines[0])

    def test_without_a_database_the_first_line_is_the_notice(self):
        process = self.run_watch('--interval', '0.2', '--polls', '1')
        out, err = process.communicate(timeout=120)
        self.assertEqual(process.returncode, 0, err)
        self.assertTrue(out.startswith('NOTICE: claims database missing'), out)


class ClearCase(ClaimsCase):
    def setUp(self):
        super().setUp()
        self.mission('live1')
        self.live = self.claim('live1', 'a.py:x', job='job_live')
        self.ghost = self.claim('ghost', 'b.py:y', job='job_ghost')
        self.pid, self.started = dead_process()
        self.stale_window = claims.open_window(self.common, 'ghost2', 'prepare', 'validating', 'job_g2', None, self.pid, self.started)
        self.tty = True

    def clear(self, *ids, apply=False, even=False, answer=None, interactive=True):
        out, err = io.StringIO(), io.StringIO()
        asked = []

        def ask(prompt):
            asked.append(prompt)
            return answer
        with redirect_stdout(out), redirect_stderr(err):
            code = w.clear_command(self.root, apply, list(ids), even, ask=ask, interactive=interactive)
        return code, out.getvalue(), err.getvalue(), asked

    def open_ids(self):
        return sorted(r['id'] for r in claims.open_claims(self.common))


class ClearTest(ClearCase):
    def test_the_dry_run_lists_every_claim_with_its_status_and_evidence_and_changes_nothing(self):
        before = self.open_ids()
        code, out, err, _ = self.clear()
        self.assertEqual(code, 0, err)
        self.assertIn('3 open claim(s)', out)
        self.assertRegex(out, r'c%d\s+LIVE\s+paths\s+live1' % self.live)
        self.assertRegex(out, r'c%d\s+STALE\s+paths\s+ghost ' % self.ghost)
        self.assertRegex(out, r'c%d\s+STALE\s+prepare\s+ghost2' % self.stale_window)
        self.assertIn('worktree not registered; branch gone', out)
        self.assertIn('worktree registered; branch exists; packet none', out)
        self.assertIn('command pid %d not running' % self.pid, out)
        self.assertIn('DRY RUN: would clear c%d, c%d' % (self.ghost, self.stale_window), out)
        self.assertIn('Nothing was cleared', out)
        self.assertEqual(self.open_ids(), before)
        self.assertFalse((self.common.parent / '.local/claims/clearing.log').exists())

    def test_apply_clears_every_stale_claim_and_keeps_the_live_one_and_logs_one_line_each(self):
        code, out, err, _ = self.clear(apply=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.open_ids(), [self.live])
        self.assertIn('cleared c%d (ghost, STALE)' % self.ghost, out)
        self.assertIn('2 claim(s) cleared', out)
        lines = (self.common.parent / '.local/claims/clearing.log').read_text().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(' cleared c' in l and 'forced=false' in l and 'status=STALE' in l for l in lines))
        self.assertEqual([e for e, _ in self.events('cleared')], ['cleared', 'cleared'])

    def test_apply_with_ids_clears_exactly_those_and_accepts_the_bare_number(self):
        code, out, err, _ = self.clear(str(self.ghost), apply=True)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.open_ids(), sorted([self.live, self.stale_window]))

    def test_a_named_id_without_apply_is_still_a_dry_run(self):
        code, out, _, _ = self.clear('c%d' % self.ghost)
        self.assertEqual(code, 0)
        self.assertIn('DRY RUN: would clear c%d.' % self.ghost, out)
        self.assertEqual(len(self.open_ids()), 3)

    def test_an_unknown_id_fails_and_clears_nothing(self):
        code, _, err, _ = self.clear('c999', apply=True)
        self.assertEqual(code, 1)
        self.assertIn('no open claim c999', err)
        self.assertEqual(len(self.open_ids()), 3)

    def test_a_live_claim_is_refused_without_the_extra_flag(self):
        code, _, err, _ = self.clear('c%d' % self.live, apply=True)
        self.assertEqual(code, 1)
        self.assertIn('is LIVE (its transaction exists); add --even-if-live', err)
        self.assertIn(self.live, self.open_ids())

    def test_a_live_claim_needs_a_terminal_and_the_id_typed_again(self):
        name = 'c%d' % self.live
        for answer, interactive, expected_code in ((name, False, 1), ('c1000', True, 1), ('', True, 1)):
            with self.subTest(answer=answer, interactive=interactive):
                code, out, err, asked = self.clear(name, apply=True, even=True, answer=answer, interactive=interactive)
                self.assertEqual(code, expected_code)
                self.assertIn(self.live, self.open_ids())
        code, out, err, asked = self.clear(name, apply=True, even=True, answer=name)
        self.assertEqual(code, 0, err)
        self.assertEqual(asked, ['Type %s again to clear it: ' % name])
        self.assertIn('%s is LIVE: worktree registered' % name, out)
        self.assertNotIn(self.live, self.open_ids())
        self.assertIn('forced=true', (self.common.parent / '.local/claims/clearing.log').read_text())

    def test_even_if_live_without_an_id_is_an_error(self):
        code, _, err, _ = self.clear(apply=True, even=True)
        self.assertEqual(code, 1)
        self.assertIn('--even-if-live needs the id', err)

    def test_the_job_state_is_evidence_and_a_completed_job_with_a_live_transaction_is_a_conflict_not_a_reason(self):
        with patch.object(w, 'job_state', return_value=('COMPLETE', 'STOPPED')):
            code, out, err, _ = self.clear(apply=True)
        self.assertEqual(code, 0, err)
        self.assertIn('Job job_live COMPLETE', out)
        self.assertIn('CONFLICT: the Job record says COMPLETE while its transaction exists; the transaction wins', out)
        self.assertIn(self.live, self.open_ids(), 'the Job record alone never clears a claim')

    def test_a_dead_command_whose_transaction_still_exists_is_live_and_says_clearing_will_not_unfreeze(self):
        self.mission('m5', 'preparing', lock=True)
        window = claims.open_window(self.common, 'm5', 'prepare', 'validating', 'job_five', None, self.pid, self.started)
        code, out, _, _ = self.clear()
        self.assertRegex(out, r'c%d\s+LIVE\s+prepare\s+m5' % window)
        self.assertIn('the derived evidence still freezes the refs: clearing the claim does not unfreeze them', out)
        self.assertIn(window, self.open_ids())

    def test_a_missing_or_corrupt_database_fails_with_a_message(self):
        for claim_file in (self.database(),):
            for suffix in ('', '-wal', '-shm'):
                Path(str(claim_file) + suffix).unlink(missing_ok=True)
        code, _, err, _ = self.clear()
        self.assertEqual(code, 1)
        self.assertIn('FAIL: claims database missing', err)


class DoctorCommandTest(ClaimsCase):
    def doctor(self, repair=False):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = w.doctor_command(self.root, repair)
        return code, out.getvalue(), err.getvalue()

    def test_a_healthy_database_prints_its_checks_and_nothing_to_repair(self):
        self.claim('m1', 'a.py:x')
        code, out, _ = self.doctor()
        self.assertEqual(code, 0)
        self.assertIn('claims database: ok', out)
        self.assertIn('integrity', out)
        self.assertIn('nothing to repair', out)

    def test_orphans_are_reported_as_would_repair_and_repaired_only_with_the_flag(self):
        self.claim('m1', 'a.py:x')
        with closing(sqlite3.connect(self.database())) as con:
            con.execute('INSERT INTO claim_paths(claim_id, path, class, subclass) VALUES (999, "orphan.py", "x", "")')
            con.commit()
        code, out, _ = self.doctor()
        self.assertEqual(code, 0)
        self.assertIn('would repair: delete 1 orphan path row(s)', out)
        self.assertIn('Nothing was changed', out)
        code, out, _ = self.doctor(repair=True)
        self.assertEqual(code, 0)
        self.assertIn('repaired: delete 1 orphan path row(s)', out)
        self.assertIn('claims database: ok', self.doctor()[1])

    def test_a_corrupt_database_fails_the_check_and_repair_moves_it_aside(self):
        self.database().parent.mkdir(parents=True)
        self.database().write_bytes(b'garbage' * 400)
        code, out, _ = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn('would repair: move claims.sqlite', out)
        self.assertTrue(self.database().exists())
        code, out, _ = self.doctor(repair=True)
        self.assertEqual(code, 0)
        self.assertIn('repaired: moved claims.sqlite aside as claims.sqlite.corrupt-', out)
        self.assertFalse(self.database().exists())

    def test_a_missing_database_is_not_an_error(self):
        code, out, _ = self.doctor()
        self.assertEqual(code, 0)
        self.assertIn('claims database: missing', out)

    def test_the_commands_run_from_the_command_line(self):
        for args, expected in ((['doctor'], 'claims database: missing'), (['clear'], 'FAIL: claims database missing')):
            result = subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.WORKTREE), '--main', str(self.root), *args],
                                    capture_output=True, text=True, cwd=self.temp.name)
            self.assertIn(expected, result.stdout + result.stderr)


class HookTest(ClaimsCase):
    """worktree.py main() writes the landing, the release and the reconcile note after the guarded operation succeeds."""
    def run_main(self, action, *extra, fails=False):
        class Flow:
            def __init__(inner, main):
                inner.main = SimpleNamespace(common=self.common)

            def source(inner, path, owner):
                return object()

            def tooling_authority(inner, source):
                return {'ok': True}

            def operation(inner, *args):
                if fails:
                    raise ValueError('main moved')
                return {'state': action}
            integrate = remove = reconcile = operation
        argv = ['worktree.py', '--main', str(self.root), action, '--path', 'p', '--owner', 'm1', '--expected-main', 'a' * 40, *extra]
        with patch.object(w, 'Worktrees', Flow), patch.object(sys, 'argv', argv), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()) as err:
            return w.main(), err.getvalue()

    def test_a_successful_integration_writes_the_landed_event_and_releases_the_claims(self):
        self.claim('m1', 'a.py:x')
        self.claim('m2', 'a.py:x')
        code, _ = self.run_main('integrate', '--expected-tip', 'b' * 40, '--job-handle', 'worker_claims')
        self.assertEqual(code, 0)
        [(_, detail)] = self.events('landed')
        self.assertEqual((detail['commit'], detail['job_handle'], len(detail['overlapped'])), ('b' * 40, 'worker_claims', 1))
        self.assertEqual([c['owner'] for c in claims.read_state(self.common)['paths']], ['m2'])

    def test_a_failed_integration_writes_no_landed_event_and_keeps_the_claims(self):
        self.claim('m1', 'a.py:x')
        code, err = self.run_main('integrate', '--expected-tip', 'b' * 40, fails=True)
        self.assertEqual(code, 1)
        self.assertIn('FAIL: main moved', err)
        self.assertEqual(self.events('landed'), [])
        self.assertEqual(len(claims.read_state(self.common)['paths']), 1)

    def test_an_invalid_job_handle_stops_before_anything_is_written(self):
        self.claim('m1', 'a.py:x')
        code, err = self.run_main('integrate', '--expected-tip', 'b' * 40, '--job-handle', 'Not A Handle')
        self.assertEqual(code, 1)
        self.assertEqual(self.events('landed'), [])

    def test_removal_releases_the_claims_and_a_reconciliation_leaves_a_note(self):
        self.claim('m1', 'a.py:x')
        self.assertEqual(self.run_main('reconcile', '--expected-old-main', 'c' * 40)[0], 0)
        self.assertEqual(len(self.events('reconciled')), 1)
        self.assertEqual(len(claims.read_state(self.common)['paths']), 1)
        self.assertEqual(self.run_main('remove', '--expected-tip', 'b' * 40)[0], 0)
        self.assertEqual(claims.read_state(self.common)['paths'], [])

    def test_a_broken_database_never_fails_a_successful_operation(self):
        self.database().parent.mkdir(parents=True)
        self.database().write_bytes(b'garbage' * 300)
        code, err = self.run_main('integrate', '--expected-tip', 'b' * 40, '--job-handle', 'worker_claims')
        self.assertEqual(code, 0)
        self.assertIn('NOTICE: claims database corrupt', err)


if __name__ == '__main__':
    unittest.main()
