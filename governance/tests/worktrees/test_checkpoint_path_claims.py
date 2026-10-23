"""Path claims, overlaps, landings, clearing and the doctor in the claims store, in disposable repositories only."""
from contextlib import closing, redirect_stderr
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from governance.tools.worktrees import claims
from governance.tests.worktrees.test_checkpoint_claims import FakeTool, StoreCase

DECLARER = '''import sys
sys.path.insert(0, sys.argv[1])
from governance.tools.worktrees import claims
from pathlib import Path
common, owner, rounds = Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
paths = sys.argv[5].split(',')
subclass = sys.argv[6].split(',')
for i in range(rounds):
    claims.declare_paths(common, owner, 'job_' + owner.replace('-', '_'), 'w', [(p, 'tooling', subclass) for p in paths])
'''


def entries(*items):
    """(path, class, subclasses) triples from 'path:sub1,sub2' shorthand."""
    result = []
    for item in items:
        path, _, subclasses = item.partition(':')
        result.append((path, 'tooling', [s for s in subclasses.split(',') if s]))
    return result


def active(common):
    return claims.read_state(common)['paths']


class DeclareTest(StoreCase):
    def test_a_declaration_stores_each_path_with_its_class_and_subclasses(self):
        claims.declare_paths(self.common, 'm1', 'job_a', str(self.root), entries('b.py:checkpoint,decisions', 'a.md:rationale'))
        [claim] = active(self.common)
        self.assertEqual((claim['owner'], claim['job_handle'], claim['kind'], claim['state']), ('m1', 'job_a', 'paths', 'declared'))
        self.assertEqual([(p['path'], p['class'], p['subclass']) for p in claim['paths']],
                         [('a.md', 'tooling', 'rationale'), ('b.py', 'tooling', 'checkpoint,decisions')])

    def test_a_newer_declaration_replaces_the_older_one_and_leaves_no_orphan_rows(self):
        claims.declare_paths(self.common, 'm1', 'job_a', 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm1', 'job_a', 'w', entries('b.py:y', 'c.py:y'))
        [claim] = active(self.common)
        self.assertEqual([p['path'] for p in claim['paths']], ['b.py', 'c.py'])
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('SELECT count(*) FROM claim_paths').fetchone()[0], 2)
            self.assertEqual([r[0] for r in con.execute('SELECT event FROM events ORDER BY id')], ['opened', 'closed', 'opened'])

    def test_path_claims_and_windows_of_the_same_owner_coexist_and_close_independently(self):
        claims.declare_paths(self.common, 'm1', 'job_a', 'w', entries('a.py:x'))
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        claims.close_windows(self.common, 'm1', 'prepare')
        self.assertEqual(len(active(self.common)), 1)
        claims.release_paths(self.common, 'm1', 'abandon')
        state = claims.read_state(self.common)
        self.assertEqual((state['paths'], state['windows']), ([], []))

    def test_the_preview_hook_claims_the_manifest_from_the_classification_views(self):
        profile = {'artifact_classification': {
            'governance/tools/x.py': {'resolved': True, 'class': 'tooling', 'subclass': ['checkpoint']},
            'governance/Y.md': {'resolved': True, 'class': 'governance', 'subclass': 'decisions,rationale'},
            'gone.txt': {'resolved': True, 'class': 'record', 'subclass': []}}}
        tool = FakeTool(self.common, self.root)
        self.assertTrue(claims.declare_manifest(tool, profile, 'job_x'))
        [claim] = active(self.common)
        self.assertEqual((claim['owner'], claim['job_handle']), ('m1', 'job_x'))
        self.assertEqual({p['path']: p['subclass'] for p in claim['paths']},
                         {'governance/tools/x.py': 'checkpoint', 'governance/Y.md': 'decisions,rationale', 'gone.txt': ''})

    def test_the_preview_hook_never_raises_when_the_store_is_broken(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b'garbage' * 500)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertFalse(claims.declare_manifest(FakeTool(self.common, self.root), {'artifact_classification': {}}))
        self.assertIn('NOTICE: claims database corrupt', stderr.getvalue())

    def test_abandon_releases_the_path_claims_and_writes_one_attention_event(self):
        claims.declare_paths(self.common, 'm1', 'job_x', 'w', entries('a.py:x'))
        with claims.command_window(FakeTool(self.common, self.root), 'abandon'):
            pass
        self.assertEqual(active(self.common), [])
        with closing(self.raw()) as con:
            rows = con.execute("SELECT owner, detail FROM events WHERE event='attention'").fetchall()
        self.assertEqual([(r[0], r[1]) for r in rows], [('m1', '{"job_handle": "job_x", "reason": "abandoned"}')])

    def test_finish_and_commit_keep_the_path_claims_because_the_work_has_not_landed(self):
        claims.declare_paths(self.common, 'm1', 'job_x', 'w', entries('a.py:x'))
        for action in ('finish', 'commit'):
            with claims.command_window(FakeTool(self.common, self.root), action):
                pass
        self.assertEqual(len(active(self.common)), 1)

    def test_a_failed_prepare_and_an_error_without_a_packet_each_write_one_attention_event(self):
        for state, error, reason in (('prepare-failed', ValueError('checks failed'), 'prepare failed (packet prepare-failed)'),
                                     ('validated', None, None), ('prepared', None, None)):
            with self.subTest(state=state):
                tool = FakeTool(self.common, self.root, packet_state=state)
                try:
                    with claims.command_window(tool, 'prepare'):
                        if error:
                            raise error
                except ValueError:
                    pass
                with closing(self.raw()) as con:
                    reasons = [r[0] for r in con.execute("SELECT detail FROM events WHERE event='attention'")]
                self.assertEqual(any(reason in r for r in reasons) if reason else len(reasons) <= 1, True)
        tool = FakeTool(self.common, self.root)
        tool.load = lambda: (_ for _ in ()).throw(ValueError('no packet'))
        with self.assertRaises(ValueError):
            with claims.command_window(tool, 'seal'):
                raise ValueError('seal stopped for a reason')
        with closing(self.raw()) as con:
            last = con.execute("SELECT detail FROM events WHERE event='attention' ORDER BY id DESC").fetchone()[0]
        self.assertIn('seal stopped: seal stopped for a reason', last)

    def test_a_stopped_integration_writes_attention_and_the_other_operations_do_not(self):
        for operation, expected in (('create', 0), ('remove', 0), ('reconcile', 0), ('integrate', 1)):
            with self.subTest(operation=operation):
                with self.assertRaises(RuntimeError):
                    with claims.lifecycle_window(self.common, operation, 'm1', 'w', 'job_x'):
                        raise RuntimeError('main moved')
                with closing(self.raw()) as con:
                    count = con.execute("SELECT count(*) FROM events WHERE event='attention'").fetchone()[0]
                    detail = con.execute("SELECT detail FROM events WHERE event='attention'").fetchone()
                self.assertEqual(count, expected)
                if expected:
                    self.assertIn('integration stopped: main moved', detail[0])
                    self.assertIn('job_x', detail[0])


class OverlapTest(StoreCase):
    def overlaps(self):
        return claims.compute_overlaps(active(self.common))

    def test_the_same_file_is_reported_first_and_worded_as_a_probable_conflict(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('governance/tools/checkpoint.py:checkpoint', 'a.md:x'))
        claims.declare_paths(self.common, 'm2', None, 'w', entries('governance/tools/checkpoint.py:checkpoint', 'b.md:y'))
        [overlap] = self.overlaps()
        self.assertEqual((overlap['owners'], overlap['kind'], overlap['files']), (['m1', 'm2'], 'same_file', ['governance/tools/checkpoint.py']))
        self.assertEqual(claims.overlap_text(overlap), 'same file(s): governance/tools/checkpoint.py')

    def test_the_same_subclass_with_different_files_is_the_weaker_wording(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:checkpoint,decisions'))
        claims.declare_paths(self.common, 'm2', None, 'w', entries('b.py:checkpoint'))
        [overlap] = self.overlaps()
        self.assertEqual((overlap['kind'], overlap['files'], overlap['subclasses']), ('same_subclass', [], ['checkpoint']))
        self.assertEqual(claims.overlap_text(overlap), 'same subclass, different files: checkpoint')

    def test_no_shared_file_or_subclass_is_no_overlap_and_one_owner_never_overlaps_itself(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x', 'b.py:x'))
        claims.declare_paths(self.common, 'm2', None, 'w', entries('c.py:y'))
        self.assertEqual(self.overlaps(), [])

    def test_paths_compare_without_regard_to_case_and_long_lists_are_cut(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries(*['F%d.py:x' % i for i in range(5)]))
        claims.declare_paths(self.common, 'm2', None, 'w', entries(*['f%d.py:y' % i for i in range(5)]))
        [overlap] = self.overlaps()
        self.assertEqual(len(overlap['files']), 5)
        self.assertTrue(claims.overlap_text(overlap).endswith('(+2 more)'))

    def test_three_owners_give_one_entry_per_overlapping_pair(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm2', None, 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm3', None, 'w', entries('z.py:x'))
        self.assertEqual(sorted(tuple(o['owners']) for o in self.overlaps()), [('m1', 'm2'), ('m1', 'm3'), ('m2', 'm3')])


class LandingTest(StoreCase):
    def landed(self):
        return claims.read_state(self.common)['landed']

    def test_a_landing_releases_the_claims_names_the_overlapped_owners_and_writes_the_event(self):
        claims.declare_paths(self.common, 'm1', 'job_a', 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm2', 'job_b', 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm3', 'job_c', 'w', entries('z.py:other'))
        waiting = [c for c in active(self.common) if c['owner'] == 'm2'][0]['id']
        overlapped = claims.landed(self.common, 'm1', 'job_a', 'a' * 40)
        self.assertEqual(overlapped, [{'owner': 'm2', 'claim': waiting}])
        self.assertEqual(sorted(c['owner'] for c in active(self.common)), ['m2', 'm3'])
        [event] = self.landed()
        self.assertEqual((event['owner'], event['detail']['commit'], event['detail']['job_handle']), ('m1', 'a' * 40, 'job_a'))

    def test_a_landing_without_claims_still_writes_its_event(self):
        self.assertEqual(claims.landed(self.common, 'm1', 'job_a', 'b' * 40), [])
        self.assertEqual([e['owner'] for e in self.landed()], ['m1'])

    def test_the_base_moved_note_shows_for_the_waiting_owner_until_it_declares_again_or_reconciles(self):
        claims.declare_paths(self.common, 'm1', 'job_a', 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm2', 'job_b', 'w', entries('a.py:x'))
        claims.landed(self.common, 'm1', 'job_a', 'c' * 40)

        def moved():
            state = claims.read_state(self.common)
            return claims.base_moved(state['paths'], state['landed'], state['reconciled'])
        [note] = moved()
        self.assertEqual((note['owner'], note['main'], note['by'], note['job_handle']), ('m2', 'c' * 40, 'm1', 'job_a'))
        claims.note_event(self.common, 'm2', 'reconciled', main='c' * 40)
        self.assertEqual(moved(), [])
        claims.declare_paths(self.common, 'm3', None, 'w', entries('q.py:q'))
        claims.declare_paths(self.common, 'm4', 'job_d', 'w', entries('q.py:q'))
        claims.landed(self.common, 'm3', None, 'd' * 40)
        self.assertEqual([n['owner'] for n in moved()], ['m4'])
        claims.declare_paths(self.common, 'm4', 'job_d', 'w', entries('q.py:q'))
        self.assertEqual(moved(), [])

    def test_an_unrelated_waiting_owner_gets_no_note(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm2', None, 'w', entries('z.py:y'))
        claims.landed(self.common, 'm1', None, 'e' * 40)
        state = claims.read_state(self.common)
        self.assertEqual(claims.base_moved(state['paths'], state['landed'], state['reconciled']), [])


class ConcurrentDeclarationTest(StoreCase):
    def test_four_processes_declare_overlapping_manifests_at_once_without_loss_or_corruption(self):
        folder = str(Path(claims.__file__).parents[3])
        claims.declare_paths(self.common, 'seed', None, 'w', entries('seed.py:seed'))  # create the store before the race
        plan = [('p1', 'shared.py,one.py', 'checkpoint'), ('p2', 'shared.py,two.py', 'worktree'),
                ('p3', 'three.py', 'checkpoint'), ('p4', 'four.py', 'unique')]
        processes = [subprocess.Popen([sys.executable, '-B', '-c', DECLARER, folder, str(self.common), owner, '10', paths, sub],
                                      stderr=subprocess.PIPE) for owner, paths, sub in plan]
        for process in processes:
            _, stderr = process.communicate(timeout=180)
            self.assertEqual(process.returncode, 0, stderr.decode(errors='replace'))
        with closing(self.raw()) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(con.execute("SELECT count(*) FROM claims WHERE kind='paths' AND closed_at IS NULL").fetchone()[0], 5)
            self.assertEqual(con.execute('SELECT count(*) FROM claim_paths WHERE claim_id NOT IN '
                                         '(SELECT id FROM claims WHERE closed_at IS NULL)').fetchone()[0], 0)
        found = {(tuple(sorted(o['owners'])), o['kind']) for o in claims.compute_overlaps(active(self.common))}
        self.assertEqual(found, {(('p1', 'p2'), 'same_file'), (('p1', 'p3'), 'same_subclass')})


class ClearingTest(StoreCase):
    def test_clearing_closes_the_named_claims_with_their_evidence_and_drops_their_paths(self):
        a = claims.declare_paths(self.common, 'm1', 'job_a', 'w', entries('a.py:x'))
        claims.declare_paths(self.common, 'm2', 'job_b', 'w', entries('b.py:y'))
        cleared = claims.clear_claims(self.common, {a: {'status': 'STALE', 'forced': False, 'evidence': ['worktree not registered']}})
        self.assertEqual([c['owner'] for c in cleared], ['m1'])
        self.assertEqual([c['owner'] for c in active(self.common)], ['m2'])
        with closing(self.raw()) as con:
            [detail] = [r[0] for r in con.execute("SELECT detail FROM events WHERE event='cleared'")]
            self.assertIn('worktree not registered', detail)
            self.assertEqual(con.execute('SELECT count(*) FROM claim_paths WHERE claim_id=?', (a,)).fetchone()[0], 0)

    def test_an_already_closed_claim_is_skipped_not_cleared_twice(self):
        a = claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        claims.release_paths(self.common, 'm1')
        self.assertEqual(claims.clear_claims(self.common, {a: {'status': 'STALE'}}), [])

    def test_the_clearing_log_appends_one_line_each_and_refuses_an_indirect_file(self):
        claims.clearing_log(self.common, 'one')
        claims.clearing_log(self.common, 'two')
        self.assertEqual((self.root / '.local/claims/clearing.log').read_text(), 'one\ntwo\n')
        target = self.root / '.local/claims/elsewhere.log'
        target.write_text('x')
        (self.root / '.local/claims/clearing.log').unlink()
        try:
            os.symlink(target, self.root / '.local/claims/clearing.log')
        except (OSError, NotImplementedError):
            self.skipTest('cannot create a symlink here')
        with self.assertRaises(claims.ClaimsError):
            claims.clearing_log(self.common, 'three')

    def test_open_claims_lists_windows_and_paths_with_their_rows(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        claims.open_window(self.common, 'm2', 'prepare', 'validating')
        rows = claims.open_claims(self.common)
        self.assertEqual(sorted((r['kind'], r['owner'], len(r['paths'])) for r in rows), [('paths', 'm1', 1), ('window', 'm2', 0)])
        self.assertEqual(claims.read_state(self.common)['state'], 'ok')


class DoctorTest(StoreCase):
    def names(self, report):
        return {c['name']: c['ok'] for c in report['checks']}

    def test_a_healthy_database_has_nothing_to_repair(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        report = claims.doctor(self.common)
        self.assertEqual((report['state'], report['repairs']), ('ok', []))
        self.assertTrue(all(self.names(report).values()))

    def test_a_missing_database_is_reported_as_missing_and_nothing_is_created(self):
        self.assertEqual(claims.doctor(self.common)['state'], 'missing')
        self.assertFalse((self.root / '.local').exists())

    def test_orphan_path_rows_and_empty_claims_are_found_and_repaired_exactly(self):
        claims.declare_paths(self.common, 'm1', None, 'w', entries('a.py:x'))
        empty = claims.declare_paths(self.common, 'm2', None, 'w', entries('b.py:y'))
        with closing(self.raw()) as con:
            con.execute('INSERT INTO claim_paths(claim_id, path, class, subclass) VALUES (999, "orphan.py", "x", "")')
            con.execute('DELETE FROM claim_paths WHERE claim_id=?', (empty,))
            con.commit()
        report = claims.doctor(self.common)
        self.assertEqual(report['state'], 'repairable')
        self.assertEqual({r['action'] for r in report['repairs']}, {'delete_orphan_paths', 'close_empty_claims'})
        before = self.database.read_bytes()
        self.assertEqual(claims.doctor(self.common)['state'], 'repairable')
        self.assertEqual(self.database.read_bytes(), before, 'the report changes nothing')
        done = claims.repair(self.common, report)
        self.assertEqual(len(done), 2)
        self.assertEqual(claims.doctor(self.common)['state'], 'ok')
        self.assertEqual([c['owner'] for c in active(self.common)], ['m1'])

    def test_a_corrupt_database_is_quarantined_and_a_fresh_one_starts_empty(self):
        self.database.parent.mkdir(parents=True)
        self.database.write_bytes(b'this is not a database' * 200)
        report = claims.doctor(self.common)
        self.assertEqual((report['state'], [r['action'] for r in report['repairs']]), ('corrupt', ['quarantine']))
        claims.repair(self.common, report)
        self.assertFalse(self.database.exists())
        self.assertEqual(len(list(self.database.parent.glob('claims.sqlite.corrupt-*'))), 1)
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        self.assertEqual(claims.doctor(self.common)['state'], 'ok')

    def test_another_major_version_is_reported_and_has_no_repair(self):
        claims.open_window(self.common, 'm1', 'prepare', 'validating')
        with closing(self.raw()) as con:
            con.execute('PRAGMA user_version = 2000')
        report = claims.doctor(self.common)
        self.assertEqual((report['state'], report['repairs']), ('schema_incompatible', []))

    def test_an_older_minor_is_reported_without_a_repair_because_the_next_write_migrates(self):
        with patch.object(claims, 'SCHEMA_MINOR', 0), patch.object(claims, 'MIGRATIONS', {}):
            claims.open_window(self.common, 'm1', 'prepare', 'validating')
        report = claims.doctor(self.common)
        self.assertEqual((report['state'], report['repairs']), ('ok', []))
        self.assertIn('older than the tool', [c['detail'] for c in report['checks'] if c['name'] == 'schema'][0])

    def test_an_indirect_claims_folder_is_refused_by_the_doctor(self):
        target = Path(self.temp.name) / 'elsewhere'
        target.mkdir()
        (self.root / '.local').mkdir()
        try:
            os.symlink(target, self.root / '.local' / 'claims', target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('cannot create a symlink here')
        self.assertEqual(claims.doctor(self.common)['state'], 'refused')


class PreviewHookTest(unittest.TestCase):
    """The real preview of the checkpoint tool writes the claims, in the fixture repository of the early approval tests."""
    def setUp(self):
        from governance.tests.lifecycle import test_early_approval as fixtures
        self.base = fixtures.EarlyApprovalTest('setUp')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        with (self.base.root / '.gitignore').open('a', newline='\n') as stream:
            stream.write('/.local/\n')
        claims._noticed.clear()
        self.tool = self.base.tool

    def preview(self, manifest, job_handle=None):
        return self.tool.preview(manifest, self.base.message, None, self.base.parent, 'refs/heads/main', job_handle)

    def test_a_preview_claims_the_manifest_for_the_owner_with_the_job_handle(self):
        self.preview(['a.txt', 'b.txt'], 'worker_claims')
        [claim] = active(self.tool.common)
        self.assertEqual((claim['owner'], claim['job_handle']), ('main', 'worker_claims'))
        self.assertEqual([(p['path'], p['class']) for p in claim['paths']], [('a.txt', 'tooling'), ('b.txt', 'tooling')])

    def test_a_second_preview_replaces_the_claims_and_the_preview_still_stages_nothing(self):
        self.preview(['a.txt', 'b.txt'])
        self.preview(['a.txt'])
        [claim] = active(self.tool.common)
        self.assertEqual([p['path'] for p in claim['paths']], ['a.txt'])
        self.assertEqual(self.base.git('diff', '--cached', '--name-only'), '')

    def test_an_invalid_job_handle_is_refused_and_a_broken_database_never_stops_a_valid_preview(self):
        with self.assertRaises(ValueError):
            self.preview(['a.txt'], 'Not A Handle')
        database = self.tool.common.parent / '.local/claims/claims.sqlite'
        database.parent.mkdir(parents=True)
        database.write_bytes(b'garbage' * 300)
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = self.preview(['a.txt'])
        self.assertIn('body', result)
        self.assertIn('NOTICE: claims database corrupt', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
