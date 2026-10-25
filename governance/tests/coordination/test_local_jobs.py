"""Disposable repository tests for local handoff integrity and lifecycle."""
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from governance.tools.coordination import local_jobs as j
import repo_paths as rp


def record():
    return {'version': 1, 'handle': 'sample_job', 'status': 'WIP', 'worker': 'Test worker',
            'activity': 'UNKNOWN', 'updated_at': '2026-10-08T12:00:00Z', 'step': 'Inspect inputs',
            'waiting': None, 'pending': [{'kind': 'rationale', 'text': 'Pending reason',
                                       'source': 'Fixture human statement'}],
            'commits': [], 'records': []}


TEMPLATE = {}


def setUpModule():
    """Build the baseline repository once; no test touches it, each copies it (git init and a commit cost far more)."""
    TEMPLATE['folder'] = tempfile.TemporaryDirectory(prefix='local-jobs-template-')
    root = Path(TEMPLATE['folder'].name) / 'primary'
    root.mkdir()
    j.git(root, 'init', '-b', 'main')
    j.git(root, 'config', 'user.name', 'Fixture')
    j.git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / '.gitignore').write_text('/.local/\n')
    j.git(root, 'add', '.gitignore')
    j.git(root, 'commit', '-m', 'Fixture baseline')
    TEMPLATE['root'] = root
    TEMPLATE['tip'] = j.git(root, 'rev-parse', 'HEAD')


def tearDownModule():
    TEMPLATE.pop('folder').cleanup()


class JobsFixture(unittest.TestCase):
    """The disposable primary and linked checkouts and the helpers the Job tests share; it has no tests of its own."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'primary'
        shutil.copytree(TEMPLATE['root'], self.root)  # a worktree registration holds absolute paths, so it is added below
        self.tip = TEMPLATE['tip']
        self.linked = self.root.parent / 'linked'
        j.git(self.root, 'worktree', 'add', '-b', 'job', str(self.linked))
        self.directory = j.safe_directory(self.linked, create=True)
        self.primary = j.safe_directory(self.root, create=True)

    SETTLED = {'kind': 'discarded', 'reason': 'Fixture: nothing to keep', 'at': '2026-10-13T12:00:00Z'}

    def job(self, **changes):
        """A Job record in primary storage. Unless `pending` is given, its one entry is already settled for the close."""
        data = dict(record(), **changes)
        if 'pending' not in changes:
            data['pending'] = [dict(entry, disposition=self.SETTLED) for entry in data['pending']]
        j.put(self.primary, data, None)
        return data

    def entries(self, count):
        return [{'kind': kind, 'text': 'Entry %d' % number, 'source': 'Fixture'}
                for number, kind in enumerate(('decision', 'finding', 'handoff', 'rationale', 'c_log')[:count], 1)]

    def stored(self, name='sample_job'):
        return j.read(self.primary / (name + '.json'))

    def dispose(self, position, **how):
        return j.dispose(self.primary, self.root, 'sample_job', position, **how)


class LocalJobsTest(JobsFixture):
    def test_pointer_reads_current_snapshot_and_detects_delayed_signal(self):
        path = Path(j.put(self.primary, record(), None))
        signal = j.pointer(self.primary, 'sample_job')
        original = path.read_bytes()
        first = j.resolve_pointer(self.primary, signal)
        self.assertTrue(first['matches_signal'])
        self.assertEqual(first['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(j.resolve_pointer(self.primary, signal), first)
        self.assertEqual(path.read_bytes(), original)
        updated = record()
        updated['step'] = 'New progress after signal'
        j.put(self.primary, updated, first['sha256'])
        delayed = j.resolve_pointer(self.primary, signal)
        self.assertFalse(delayed['matches_signal'])
        self.assertEqual(delayed['record']['step'], updated['step'])
        self.assertEqual(delayed['signaled_sha256'], first['sha256'])
        self.assertEqual(delayed['pointer'], j.pointer(self.primary, 'sample_job'))

    def test_pointer_rejects_unsafe_missing_and_invalid_records(self):
        digest = 'a' * 64
        for value in ('local:sample_job@' + digest, 'primary:../sample_job@' + digest,
                      'primary:sample_job@abc', 'primary:sample_job@' + digest.upper(),
                      'primary:bad__name@' + digest):
            with self.assertRaises(ValueError):
                j.resolve_pointer(self.primary, value)
        with self.assertRaisesRegex(ValueError, 'Missing'):
            j.pointer(self.primary, 'sample_job')
        path = self.primary / 'sample_job.json'
        path.write_text('{"version":1,"version":1}')
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            j.resolve_pointer(self.primary, 'primary:sample_job@' + digest)

    def test_coordinator_pointer_keeps_ownership_and_schema_unchanged(self):
        data = {'version': 1, 'worker': 'Coordinator',
                'updated_at': '2026-10-09T12:00:00Z', 'queue': [], 'context': []}
        path = Path(j.put(self.primary, data, None))
        original = path.read_bytes()
        signal = j.pointer(self.primary, 'coordinator')
        self.assertEqual(j.resolve_pointer(self.primary, signal)['record'], data)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.primary / '.writer.lock').exists())

    def test_primary_discovery_from_nested_linked_directory(self):
        nested = self.linked / 'nested'
        nested.mkdir()
        self.assertEqual(j.roots(nested), (self.linked.resolve(), self.root.resolve()))

    def test_ignored_records_do_not_dirty_worktree_and_update_requires_digest(self):
        path = Path(j.put(self.directory, record(), None))
        self.assertEqual(j.git(self.linked, 'status', '--porcelain'), '')
        updated = record()
        updated['activity'] = 'PAUSED'
        with self.assertRaisesRegex(ValueError, 'changed'):
            j.put(self.directory, updated, None)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        j.put(self.directory, updated, sha)
        self.assertEqual(j.read(path)['activity'], 'PAUSED')

    def test_transfer_conflict_preserves_both_then_moves_exact_bytes(self):
        path = Path(j.put(self.directory, record(), None))
        original = path.read_bytes()
        target = Path(j.put(self.primary, record(), None))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            j.transfer(self.directory, self.primary)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(target.read_bytes(), original)
        target.unlink()
        self.assertEqual(j.transfer(self.directory, self.primary), ['sample_job.json'])
        self.assertFalse(path.exists())
        self.assertEqual(target.read_bytes(), original)

    def test_terminal_pending_material_rejected(self):
        data = record()
        for state in j.TERMINAL:
            data['status'] = state
            with self.assertRaisesRegex(ValueError, 'pending'):
                j.validate(data)

    def test_prune_only_integrated_terminal_records_and_updates_queue(self):
        data = record()
        data.update(status='COMPLETE', pending=[], commits=[self.tip], records=['Fixture baseline'])
        j.put(self.primary, data, None)
        j.put(self.primary, {'version': 1, 'worker': 'Fixture coordinator',
                           'updated_at': data['updated_at'], 'queue': ['sample_job'], 'context': []}, None)
        self.assertEqual(j.prune(self.primary, self.root), ['sample_job'])
        self.assertEqual(j.read(self.primary / 'coordinator.json')['queue'], [])
        self.assertEqual(j.prune(self.primary, self.root), [])

    def test_unintegrated_terminal_record_is_preserved(self):
        (self.linked / 'new.txt').write_text('Candidate')
        j.git(self.linked, 'add', 'new.txt')
        j.git(self.linked, 'commit', '-m', 'Unintegrated')
        data = record()
        data.update(status='COMPLETE', pending=[], commits=[j.git(self.linked, 'rev-parse', 'HEAD')])
        path = Path(j.put(self.directory, data, None))
        with self.assertRaises(subprocess.CalledProcessError):
            j.prune(self.directory, self.root)
        self.assertTrue(path.exists())

    def test_live_records_are_not_pruned(self):
        path = Path(j.put(self.primary, record(), None))
        self.assertEqual(j.prune(self.primary, self.root), [])
        self.assertTrue(path.exists())

    def test_validation_rejects_unsafe_handles_waiting_without_reason_and_duplicate_keys(self):
        for field, value in [('handle', '../escape'), ('status', 'RUNNING'), ('activity', 'LIKELY')]:
            data = record()
            data[field] = value
            with self.assertRaises(ValueError):
                j.validate(data)
        data = record()
        data['status'] = 'BLOCKED'
        with self.assertRaisesRegex(ValueError, 'detail'):
            j.validate(data)
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            json.loads('{"version":1,"version":1}', object_pairs_hook=j.unique)

    def test_a_job_reference_is_a_plain_handle_or_two_handles_joined_by_two_hyphens(self):
        for value in ('service_read', 'a', 'service_build--service_read', 'a--b', 'x1--y2_z3', 'a' * 64 + '--' + 'b' * 64):
            with self.subTest(valid=value):
                j.job_reference(value)
        for value in ('a-b', 'a--b--c', 'a---b', 'a----b', '--b', 'a--', '--', '', '-', 'a-', '-a', 'a_-b', 'a-_b',
                      'A--b', 'a--B', 'Service_build--service_read', 'a__b--c', 'a--_b', 'a_--b', '_a--b', 'a--b_',
                      'coordinator--a', 'a--coordinator', 'a' * 65 + '--b', 'a--' + 'b' * 65,
                      'a--b ', ' a--b', 'a -- b', 'a––b', 'a—b', 'a－－b',
                      'a/../b--c', 'a--b/c', None, 7, ['a--b']):
            with self.subTest(invalid=value), self.assertRaisesRegex(ValueError, 'job handle'):
                j.job_reference(value)

    def test_the_handle_rule_itself_is_unchanged_and_still_refuses_a_hyphen(self):
        for value in ('a--b', 'has-hyphen'):
            with self.assertRaisesRegex(ValueError, 'job handle'):
                j.handle(value)
        j.handle('service_read')

    def test_priorities_validate_without_guessing_old_queue_order(self):
        data = {'version': 1, 'worker': 'Coordinator', 'updated_at': record()['updated_at'],
                'queue': ['sample_job'], 'context': []}
        self.assertEqual(j.validate(copy.deepcopy(data)), data)
        for value in (None, 1, 100):
            j.validate(dict(data, priorities={'sample_job': value}))
        for value in (True, False, 0, -1, 1.5, '1'):
            with self.assertRaisesRegex(ValueError, 'Priority'):
                j.validate(dict(data, priorities={'sample_job': value}))
        with self.assertRaisesRegex(ValueError, 'not queued'):
            j.validate(dict(data, priorities={'other_job': 1}))

    def test_queue_sort_keeps_blocked_unset_and_missing_records(self):
        data = record()
        data.update(status='BLOCKED', waiting='Needs lead developer')
        j.put(self.primary, data, None)
        j.put(self.primary, {'version': 1, 'worker': 'Coordinator',
                            'updated_at': data['updated_at'],
                            'queue': ['z_job', 'sample_job', 'a_job', 'unset_job'],
                            'context': [], 'priorities': {'z_job': 2, 'a_job': 2, 'sample_job': 1}}, None)
        before = {p.name: p.read_bytes() for p in self.primary.iterdir()}
        rows = j.queued(self.primary)['queue']
        self.assertEqual([r['handle'] for r in rows],
                         ['sample_job', 'a_job', 'z_job', 'unset_job'])
        self.assertEqual(rows[0]['status'], 'BLOCKED')
        self.assertEqual(rows[0]['waiting'], 'Needs lead developer')
        self.assertIsNone(rows[1]['record'])
        self.assertIsNone(rows[-1]['priority'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.primary.iterdir()})

    def put_job(self, handle, status='PENDING', **fields):
        data = record()
        data.update(handle=handle, status=status, **fields)
        j.put(self.primary, data, self.digest(handle))

    def digest(self, name):
        path = self.primary / (name + '.json')
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def order(self, **fields):
        j.put(self.primary, self.coordinator(**fields), self.digest('coordinator'))
        return [row['handle'] for row in j.queued(self.primary)['queue']]

    def test_queue_runs_in_the_order_of_execution(self):
        # In flight (WIP, or waiting for the lead developer) first, then priority with unset last, then ready before
        # blocked inside a priority, then the handle.
        self.put_job('running_low', 'WIP')
        self.put_job('waiting_low', 'PENDING', awaiting_lead={'for': 'preview'})
        self.put_job('ready_a', 'PENDING')
        self.put_job('ready_z', 'PENDING')
        self.put_job('blocked_a', 'PENDING')
        self.put_job('blocked_b', 'PENDING')
        self.put_job('ready_unset', 'PENDING')
        self.put_job('prereq', 'PENDING')
        queue = ['ready_unset', 'blocked_a', 'ready_z', 'running_low', 'blocked_b', 'ready_a', 'waiting_low']
        self.assertEqual(self.order(
            queue=queue, priorities={'running_low': 9, 'waiting_low': 8, 'ready_a': 3, 'ready_z': 3, 'blocked_a': 3,
                                     'blocked_b': 2},
            dependencies={'blocked_a': ['prereq'], 'blocked_b': ['prereq']}),
            ['waiting_low', 'running_low', 'blocked_b', 'ready_a', 'ready_z', 'blocked_a', 'ready_unset'])
        # List position never matters: the same queue in another order gives the same result.
        self.assertEqual(self.order(
            queue=list(reversed(queue)), priorities={'running_low': 9, 'waiting_low': 8, 'ready_a': 3, 'ready_z': 3,
                                                     'blocked_a': 3, 'blocked_b': 2},
            dependencies={'blocked_a': ['prereq'], 'blocked_b': ['prereq']}),
            ['waiting_low', 'running_low', 'blocked_b', 'ready_a', 'ready_z', 'blocked_a', 'ready_unset'])

    def test_an_epic_sits_where_its_first_ready_member_would_sort(self):
        self.put_job('first_step', 'PENDING')
        self.put_job('second_step', 'PENDING')
        self.put_job('late_job', 'PENDING')
        self.put_job('other_ready', 'PENDING')
        self.put_job('blocker', 'PENDING')
        epics = {'plan_epic': {'jobs': ['second_step', 'first_step']}}
        common = dict(queue=['other_ready', 'plan_epic', 'late_job'], epics=epics,
                      dependencies={'second_step': ['first_step'], 'late_job': ['blocker']})
        # Its first member is ready, so the band ties with the other ready priority-1 Job and sorts by handle.
        self.assertEqual(self.order(priorities={'other_ready': 1, 'plan_epic': 1, 'late_job': 1}, **common),
                         ['other_ready', 'plan_epic', 'late_job'])
        self.assertEqual(self.order(priorities={'other_ready': 2, 'plan_epic': 1, 'late_job': 1}, **common),
                         ['plan_epic', 'late_job', 'other_ready'])
        # Its members keep their dependency order inside the band.
        queue = j.queued(self.primary)['queue']
        self.assertEqual([job['handle'] for job in queue[0]['jobs']], ['first_step', 'second_step'])
        # A member in flight lifts the whole band above priority.
        self.put_job('first_step', 'WIP')
        self.assertEqual(self.order(priorities={'other_ready': 1, 'plan_epic': 5, 'late_job': 1}, **common),
                         ['plan_epic', 'other_ready', 'late_job'])
        # An Epic whose members all wait on an unmet prerequisite sorts after the ready Jobs of its priority.
        self.put_job('first_step', 'PENDING')
        blocked = dict(common, dependencies={'first_step': ['blocker'], 'second_step': ['first_step']})
        self.assertEqual(self.order(priorities={'other_ready': 1, 'plan_epic': 1, 'late_job': 1}, **blocked),
                         ['late_job', 'other_ready', 'plan_epic'])

    def test_prune_removes_priority_with_terminal_job(self):
        data = record()
        data.update(status='COMPLETE', pending=[], commits=[self.tip])
        j.put(self.primary, data, None)
        j.put(self.primary, {'version': 1, 'worker': 'Coordinator',
                            'updated_at': data['updated_at'], 'context': [],
                            'queue': ['sample_job', 'next_job'],
                            'priorities': {'sample_job': 1, 'next_job': None}}, None)
        j.prune(self.primary, self.root)
        self.assertEqual(j.read(self.primary / 'coordinator.json')['priorities'], {'next_job': None})

    def coordinator(self, **fields):
        data = {'version': 1, 'worker': 'Coordinator', 'updated_at': record()['updated_at'],
                'context': [], 'queue': []}
        data.update(fields)
        return data

    def test_epic_validation_rejects_duplicate_membership_and_dependency_cycles(self):
        good = self.coordinator(queue=['mission_epic'], epics={'mission_epic': {'jobs': ['job_a', 'job_b']}},
                                dependencies={'job_b': ['job_a']})
        j.validate(good)
        variants = [
            dict(good, epics={'mission_epic': {'jobs': ['job_a', 'job_a']}}),
            dict(good, queue=['mission_epic', 'job_a']),
            dict(good, dependencies={'job_a': ['job_b'], 'job_b': ['job_a']}),
            dict(good, dependencies={'job_a': ['job_a']}),
            dict(good, dependencies={'job_a': ['mission_epic']}),
            dict(good, dependencies={'mission_epic': ['job_a']}),
            dict(good, dependencies={'job_a': ['job_b', 'job_b']}),
            dict(good, epics={'mission_epic': {'jobs': []}}),
            dict(good, queue=['mission_epic', 'other_epic'],
                 epics={'mission_epic': {'jobs': ['job_a']}, 'other_epic': {'jobs': ['job_a']}})
        ]
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ValueError):
                j.validate(data)

    def test_parallel_epic_branches_cross_epic_dependencies_and_unqueued_jobs(self):
        data = record()
        data.update(handle='job_a', status='COMPLETE', pending=[], commits=[self.tip])
        j.put(self.primary, data, None)
        for name in ('job_b', 'job_c', 'job_d', 'outside_job'):
            data = record()
            data['handle'] = name
            j.put(self.primary, data, None)
        j.put(self.primary, self.coordinator(
            queue=['mission_epic', 'other_epic'],
            epics={'mission_epic': {'jobs': ['job_d', 'job_c', 'job_b', 'job_a']},
                   'other_epic': {'jobs': ['other_job']}},
            dependencies={'job_b': ['job_a'], 'job_c': ['job_a'],
                          'job_d': ['job_b', 'job_c'], 'other_job': ['job_d', 'missing_job']}), None)
        report = j.queued(self.primary)
        jobs = report['queue'][0]['jobs']
        self.assertEqual([r['handle'] for r in jobs], ['job_a', 'job_b', 'job_c', 'job_d'])
        self.assertTrue(jobs[1]['dependency_ready'])
        self.assertTrue(jobs[2]['dependency_ready'])
        self.assertEqual(jobs[3]['unsatisfied_dependencies'], ['job_b', 'job_c'])
        self.assertEqual(report['queue'][1]['jobs'][0]['missing_dependencies'], ['missing_job'])
        self.assertEqual([r['handle'] for r in report['unqueued']], ['outside_job'])

    def test_completed_but_unintegrated_dependency_does_not_make_job_ready(self):
        (self.linked / 'new.txt').write_text('Candidate')
        j.git(self.linked, 'add', 'new.txt')
        j.git(self.linked, 'commit', '-m', 'Unintegrated dependency')
        data = record()
        data.update(status='COMPLETE', pending=[], commits=[j.git(self.linked, 'rev-parse', 'HEAD')])
        j.put(self.primary, data, None)
        j.put(self.primary, self.coordinator(queue=['next_job'],
                                           dependencies={'next_job': ['sample_job']}), None)
        self.assertFalse(j.queued(self.primary)['queue'][0]['dependency_ready'])

    def test_prune_removes_epic_members_edges_and_final_empty_epic(self):
        data = record()
        data.update(status='COMPLETE', pending=[], commits=[self.tip])
        j.put(self.primary, data, None)
        j.put(self.primary, self.coordinator(queue=['mission_epic', 'next_job'],
            epics={'mission_epic': {'jobs': ['sample_job']}}, priorities={'mission_epic': 1},
            dependencies={'next_job': ['sample_job']}), None)
        j.prune(self.primary, self.root)
        coordinator = j.read(self.primary / 'coordinator.json')
        self.assertEqual(coordinator['epics'], {})
        self.assertEqual(coordinator['queue'], ['next_job'])
        self.assertEqual(coordinator['priorities'], {})
        self.assertEqual(coordinator['dependencies'], {'next_job': []})

    def test_cancelled_dependency_is_preserved_and_does_not_make_job_ready(self):
        data = record()
        data.update(status='CANCELLED', pending=[])
        path = Path(j.put(self.primary, data, None))
        coordinator = Path(j.put(self.primary, self.coordinator(queue=['next_job'],
                                    dependencies={'next_job': ['sample_job']}), None))
        before = coordinator.read_bytes()
        self.assertFalse(j.queued(self.primary)['queue'][0]['dependency_ready'])
        with self.assertRaisesRegex(ValueError, 'Cancelled prerequisite'):
            j.prune(self.primary, self.root)
        self.assertTrue(path.exists())
        self.assertEqual(coordinator.read_bytes(), before)

    def test_worker_local_prune_cannot_discard_scheduled_dependency(self):
        data = record()
        data.update(status='COMPLETE', pending=[], commits=[self.tip])
        path = Path(j.put(self.directory, data, None))
        j.put(self.primary, self.coordinator(queue=['next_job'],
                                           dependencies={'next_job': ['sample_job']}), None)
        with self.assertRaisesRegex(ValueError, 'Transfer scheduled'):
            j.prune(self.directory, self.root)
        self.assertTrue(path.exists())

    def test_epic_handle_and_job_record_cannot_collide(self):
        j.put(self.primary, record(), None)
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            j.put(self.primary, self.coordinator(queue=['sample_job'],
                epics={'sample_job': {'jobs': ['another_job']}}), None)
        j.put(self.primary, self.coordinator(queue=['mission_epic'],
            epics={'mission_epic': {'jobs': ['another_job']}}), None)
        data = record()
        data['handle'] = 'mission_epic'
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            j.put(self.primary, data, None)
        local = Path(j.put(self.directory, data, None))
        with self.assertRaisesRegex(ValueError, 'conflicts'):
            j.transfer(self.directory, self.primary)
        self.assertTrue(local.exists())

    def test_report_refuses_malformed_notes_and_missing_coordinator(self):
        with self.assertRaisesRegex(ValueError, 'Missing'):
            j.queued(self.primary)
        j.put(self.primary, self.coordinator(queue=['sample_job']), None)
        (self.primary / 'sample_job.json').write_text('{invalid')
        with self.assertRaises(ValueError):
            j.queued(self.primary)

    def test_queue_cli_preserves_unicode_with_ascii_console(self):
        data = record()
        data['worker'] = 'Worker \u2014 dedicated'
        j.put(self.primary, data, None)
        j.put(self.primary, self.coordinator(queue=['sample_job']), None)
        result = subprocess.run(
            [sys.executable, '-B', str(rp.ROOT / 'governance/tools/local_jobs.py'),
             '--repo', str(self.root), 'queue'],
            env=dict(os.environ, PYTHONIOENCODING='ascii'),
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['queue'][0]['worker'], data['worker'])

    def test_description_and_kind_are_optional_and_validated(self):
        j.validate(record())
        for kind in j.KINDS:
            j.validate(dict(record(), description='One short sentence.', kind=kind))
        for field, value in [('kind', 'chore'), ('kind', None), ('kind', ''), ('kind', ['fix']),
                             ('description', ''), ('description', None), ('description', 7),
                             ('description', ' padded'), ('description', 'two\nlines'),
                             ('description', 'tab\tseparated'), ('description', 'x' * (j.DESCRIPTION_LIMIT + 1))]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                j.validate(dict(record(), **{field: value}))
        j.validate(dict(record(), description='x' * j.DESCRIPTION_LIMIT))
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            j.validate(dict(record(), category='fix'))

    def test_awaiting_lead_is_optional_structured_and_validated(self):
        session = '0b8f3a52-4c1d-4e6a-9f27-3d5c8a1e7b90'
        for what in j.AWAITING:
            j.validate(dict(record(), awaiting_lead={'for': what}))
            for vendor in j.LINKED_VENDORS:
                j.validate(dict(record(), awaiting_lead={'for': what, 'session': {'vendor': vendor, 'id': session}}))
        j.validate(dict(record(), awaiting_lead={'for': 'preview', 'session': {'vendor': 'other_ai', 'id': 'chat-42'}}))
        bad = [None, 'preview', {}, {'for': 'go'}, {'for': 'preview', 'extra': 1}, {'for': None},
               {'for': 'preview', 'session': None}, {'for': 'preview', 'session': {'vendor': 'claude'}},
               {'for': 'preview', 'session': {'vendor': 'claude', 'id': 'not-a-uuid'}},
               {'for': 'preview', 'session': {'vendor': 'codex', 'id': session + '/../x'}},
               {'for': 'preview', 'session': {'vendor': 'Claude', 'id': session}},
               {'for': 'preview', 'session': {'vendor': 'x y', 'id': 'a'}},
               {'for': 'preview', 'session': {'vendor': 'other', 'id': ''}},
               {'for': 'preview', 'session': {'vendor': 'other', 'id': 'a b'}},
               {'for': 'preview', 'session': {'vendor': 'other', 'id': 'x' * 129}},
               {'for': 'preview', 'session': {'vendor': 'claude', 'id': session, 'extra': 1}}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                j.validate(dict(record(), awaiting_lead=value))
        with self.assertRaisesRegex(ValueError, 'terminal'):
            j.validate(dict(record(), status='CANCELLED', pending=[], awaiting_lead={'for': 'preview'}))
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            j.validate(self.coordinator(awaiting_lead={'for': 'preview'}))

    def test_queue_rows_carry_awaiting_lead_and_pending_counts_but_never_pending_text(self):
        waiting = {'for': 'approval', 'session': {'vendor': 'claude', 'id': '0b8f3a52-4c1d-4e6a-9f27-3d5c8a1e7b90'}}
        j.put(self.primary, dict(record(), awaiting_lead=waiting), None)
        j.put(self.primary, dict(record(), handle='plain_job', pending=[]), None)
        j.put(self.primary, self.coordinator(queue=['sample_job', 'plain_job', 'ghost_job']), None)
        rows = {row['handle']: row for row in j.queued(self.primary)['queue']}
        self.assertEqual((rows['sample_job']['awaiting_lead'], rows['sample_job']['pending_count']), (waiting, 1))
        self.assertEqual((rows['plain_job']['awaiting_lead'], rows['plain_job']['pending_count']), (None, 0))
        self.assertEqual((rows['ghost_job']['awaiting_lead'], rows['ghost_job']['pending_count']), (None, None))
        self.assertNotIn('Pending reason', json.dumps(rows))

    def test_created_at_is_optional_timezone_aware_and_survives_rename(self):
        j.validate(dict(record(), created_at='2026-10-08T09:00:00-04:00'))
        for value in (None, 7, '', 'yesterday', '2026-10-08T09:00:00'):
            with self.subTest(value=value), self.assertRaises((ValueError, TypeError)):
                j.validate(dict(record(), created_at=value))
        j.put(self.primary, dict(record(), created_at='2026-10-08T09:00:00Z'), None)
        j.rename(self.primary, 'sample_job', 'renamed_job')
        self.assertEqual(j.read(self.primary / 'renamed_job.json')['created_at'], '2026-10-08T09:00:00Z')
        j.put(self.primary, self.coordinator(queue=['renamed_job']), None)
        self.assertEqual(j.queued(self.primary)['queue'][0]['created_at'], '2026-10-08T09:00:00Z')

    def test_description_and_kind_are_not_accepted_on_the_coordinator_record(self):
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            j.validate(self.coordinator(kind='fix'))

    def test_queue_rows_carry_description_and_work_kind_beside_the_row_type(self):
        j.put(self.primary, dict(record(), description='Tidy the sample files.', kind='cleanup'), None)
        j.put(self.primary, dict(record(), handle='plain_job'), None)
        j.put(self.primary, self.coordinator(queue=['sample_job', 'plain_job', 'ghost_job', 'mission_epic'],
                                           epics={'mission_epic': {'jobs': ['member_job']}}), None)
        rows = {row['handle']: row for row in j.queued(self.primary)['queue']}
        self.assertEqual((rows['sample_job']['description'], rows['sample_job']['work_kind'],
                          rows['sample_job']['kind']), ('Tidy the sample files.', 'cleanup', 'job'))
        for name in ('plain_job', 'ghost_job'):
            self.assertEqual((rows[name]['description'], rows[name]['work_kind']), (None, None))
        self.assertEqual(rows['mission_epic']['kind'], 'epic')

    def test_kinds_command_prints_the_one_list(self):
        result = subprocess.run([sys.executable, '-B', str(rp.ROOT / 'governance/tools/local_jobs.py'), 'kinds'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), j.KINDS)
        self.assertTrue(all(isinstance(text, str) and text for text in j.KINDS.values()))

    def schedule(self):
        j.put(self.primary, dict(record(), handle='old_name', description='Keep me.', kind='fix'), None)
        for name in ('first_job', 'last_job'):
            j.put(self.primary, dict(record(), handle=name), None)
        j.put(self.primary, self.coordinator(
            queue=['first_job', 'mission_epic', 'old_name'], priorities={'old_name': 2, 'first_job': 1},
            epics={'mission_epic': {'jobs': ['last_job', 'other_member']}},
            dependencies={'last_job': ['old_name'], 'old_name': ['first_job']}), None)

    def test_rename_rewrites_record_queue_priority_and_dependencies_in_one_step(self):
        self.schedule()
        old_path = self.primary / 'old_name.json'
        before = j.read(old_path)
        result = j.rename(self.primary, 'old_name', 'new_name')
        self.assertEqual(result['renamed'], {'from': 'old_name', 'to': 'new_name'})
        self.assertFalse(old_path.exists())
        renamed = j.read(self.primary / 'new_name.json')
        kept = lambda data: {key: value for key, value in data.items() if key not in ('handle', 'updated_at', 'pending')}
        self.assertEqual(kept(renamed), kept(before))
        self.assertEqual(renamed['pending'][:-1], before['pending'])
        self.assertEqual(renamed['pending'][-1]['kind'], 'handoff')
        self.assertIn('Renamed from old_name to new_name', renamed['pending'][-1]['text'])
        queue = j.read(self.primary / 'coordinator.json')
        self.assertEqual(queue['queue'], ['first_job', 'mission_epic', 'new_name'])
        self.assertEqual(queue['priorities'], {'new_name': 2, 'first_job': 1})
        self.assertEqual(queue['dependencies'], {'last_job': ['new_name'], 'new_name': ['first_job']})
        self.assertFalse((self.primary / '.writer.lock').exists())

    def test_rename_rewrites_epic_membership_in_place(self):
        j.put(self.primary, dict(record(), handle='member_one'), None)
        j.put(self.primary, self.coordinator(queue=['mission_epic'], priorities={'mission_epic': 1},
            epics={'mission_epic': {'jobs': ['other_job', 'member_one']}}, dependencies={'other_job': ['member_one']}), None)
        j.rename(self.primary, 'member_one', 'member_two')
        queue = j.read(self.primary / 'coordinator.json')
        self.assertEqual(queue['epics'], {'mission_epic': {'jobs': ['other_job', 'member_two']}})
        self.assertEqual(queue['dependencies'], {'other_job': ['member_two']})
        self.assertEqual(queue['priorities'], {'mission_epic': 1})

    def test_rename_without_a_coordinator_record_renames_only_the_job_file(self):
        j.put(self.primary, record(), None)
        self.assertFalse(j.rename(self.primary, 'sample_job', 'renamed_job')['coordinator_updated'])
        self.assertEqual(j.read(self.primary / 'renamed_job.json')['handle'], 'renamed_job')

    def test_rename_refusals_change_nothing(self):
        self.schedule()
        j.put(self.primary, dict(record(), handle='done_job', status='COMPLETE', pending=[], commits=[self.tip]), None)
        before = {path.name: path.read_bytes() for path in self.primary.iterdir()}
        for old, new, message in [('old_name', 'first_job', 'already has a record'),
                                  ('old_name', 'old_name', 'equals'),
                                  ('old_name', 'other_member', 'already in the queue'),
                                  ('old_name', 'mission_epic', 'already in the queue'),
                                  ('missing_job', 'fresh_name', 'Missing'),
                                  ('mission_epic', 'fresh_name', 'Missing'),
                                  ('done_job', 'fresh_name', 'Terminal'),
                                  ('old_name', 'Bad-Name', 'handle'),
                                  ('old_name', 'coordinator', 'handle')]:
            with self.subTest(old=old, new=new), self.assertRaisesRegex(ValueError, message):
                j.rename(self.primary, old, new)
            self.assertEqual(before, {path.name: path.read_bytes() for path in self.primary.iterdir()})

    def test_rename_refuses_a_name_that_is_only_a_dependency_endpoint(self):
        j.put(self.primary, record(), None)
        j.put(self.primary, self.coordinator(queue=['sample_job'], dependencies={'sample_job': ['planned_job']}), None)
        with self.assertRaisesRegex(ValueError, 'already in the queue'):
            j.rename(self.primary, 'sample_job', 'planned_job')

    def test_rename_removes_the_new_record_when_the_coordinator_write_fails(self):
        self.schedule()
        before = {path.name: path.read_bytes() for path in self.primary.iterdir()}
        real = j.write

        def failing(path, data):
            if path.name == 'coordinator.json':
                raise OSError('Disk full fixture')
            real(path, data)
        j.write = failing
        self.addCleanup(setattr, j, 'write', real)
        with self.assertRaisesRegex(OSError, 'Disk full'):
            j.rename(self.primary, 'old_name', 'new_name')
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.primary.iterdir()})

    def test_rename_command_always_uses_primary_storage_from_a_linked_checkout(self):
        self.schedule()
        result = subprocess.run(
            [sys.executable, '-B', str(rp.ROOT / 'governance/tools/local_jobs.py'), '--repo', str(self.linked), 'rename',
             'old_name', 'new_name'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['renamed'], {'from': 'old_name', 'to': 'new_name'})
        self.assertTrue((self.primary / 'new_name.json').exists())
        self.assertFalse((self.directory / 'new_name.json').exists())

    # ---- note and complete ------------------------------------------------------------------------

    def land(self, name='landed.txt'):
        """A commit on main of the disposable primary checkout; returns its full id."""
        (self.root / name).write_text('landed\n')
        j.git(self.root, 'add', name)
        j.git(self.root, 'commit', '-m', 'Land ' + name)
        return j.git(self.root, 'rev-parse', 'HEAD')

    def fake_status(self, windows=(), landings=()):
        original = j.execution_snapshot
        j.execution_snapshot = lambda primary: ('FROZEN' if windows else 'CLEAR', list(windows), list(landings))
        self.addCleanup(setattr, j, 'execution_snapshot', original)

    def files(self):
        return {path.name: path.read_bytes() for path in self.primary.iterdir()}

    def launcher(self, *arguments):
        return subprocess.run([sys.executable, '-B', str(rp.ROOT / 'governance/tools/local_jobs.py'), '--repo', str(self.linked),
                               *arguments], capture_output=True, text=True)

    def test_note_appends_one_entry_from_files_with_utf8_and_a_byte_order_mark(self):
        self.job()
        text = Path(self.temp.name) / 'text.txt'
        text.write_bytes(b'\xef\xbb\xbfCaf\xc3\xa9 \xe2\x80\x94 line one\r\nline two\r\n')
        source = Path(self.temp.name) / 'source.txt'
        source.write_bytes('Lead developer, verbatim: \u201cyes\u201d'.encode('utf-8'))
        for arguments in (['--source', str(source)], ['--source', 'Plain source text']):
            done = self.launcher('--primary', 'note', 'sample_job', '--kind', 'finding', *arguments, '--text-file', str(text))
            self.assertEqual(done.returncode, 0, done.stderr)
        entries = j.read(self.primary / 'sample_job.json')['pending']
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[1], {'kind': 'finding', 'source': 'Lead developer, verbatim: \u201cyes\u201d',
                                      'text': 'Caf\u00e9 \u2014 line one\nline two'})
        self.assertEqual(entries[2]['source'], 'Plain source text')
        self.assertNotIn(b'\r', (self.primary / 'sample_job.json').read_bytes())

    def test_note_refuses_a_terminal_job_a_bad_kind_and_an_empty_text_without_writing(self):
        self.job()
        before = self.files()
        for kind, text in (('Not A Kind', 'text'), ('finding', '   ')):
            with self.assertRaises(ValueError):
                j.note(self.primary, 'sample_job', kind, 'source', text)
        self.assertEqual(before, self.files())
        j.put(self.primary, dict(record(), handle='done_job', status='COMPLETE', pending=[], commits=['a' * 40]), None)
        with self.assertRaisesRegex(ValueError, 'terminal'):
            j.note(self.primary, 'done_job', 'finding', 'source', 'text')

    def test_a_clean_landing_closes_the_job_with_full_commit_ids_and_prunes_through_the_command(self):
        self.job()
        tip = self.land()
        done = self.launcher('complete', 'sample_job', '--commit', tip[:10], '--prune')
        self.assertEqual(done.returncode, 0, done.stderr)
        report = json.loads(done.stdout)
        self.assertEqual(report['commits'], [tip])
        self.assertEqual(report['landing_check'], dict.fromkeys(j.STEPS, 'passed'))
        self.assertEqual(report['pruned'], ['sample_job'])
        self.assertFalse((self.primary / 'sample_job.json').exists())

    def test_complete_marks_the_record_without_pruning(self):
        self.job(activity='ACTIVE')
        tip = self.land()
        self.fake_status()
        j.complete(self.primary, self.root, 'sample_job', [tip])
        data = j.read(self.primary / 'sample_job.json')
        self.assertEqual((data['status'], data['activity'], data['pending'], data['commits']),
                         ('COMPLETE', 'STOPPED', [], [tip]))
        with self.assertRaisesRegex(ValueError, 'terminal'):
            j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_a_commit_that_is_not_on_main_refuses_at_step_one(self):
        self.job()
        j.git(self.root, 'checkout', '-q', '-b', 'side')
        side = self.land('side.txt')
        j.git(self.root, 'checkout', '-q', 'main')
        self.fake_status()
        before = self.files()
        with self.assertRaisesRegex(ValueError, r'step 1 of 5.*not an ancestor of main'):
            j.complete(self.primary, self.root, 'sample_job', [side])
        self.assertEqual(before, self.files())

    def test_a_dirty_main_refuses_at_step_two(self):
        self.job()
        tip = self.land()
        (self.root / 'stray.txt').write_text('uncommitted\n')
        self.fake_status()
        before = self.files()
        with self.assertRaisesRegex(ValueError, r'step 2 of 5.*stray.txt'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        self.assertEqual(before, self.files())

    def test_a_worktree_left_behind_refuses_at_step_three_found_by_name_or_by_its_tip(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/left-behind', tip)
        left = self.root.parent / 'left'
        j.git(self.root, 'worktree', 'add', str(left), 'tx/left-behind')
        self.fake_status()
        for owners in ((), ('left-behind',)):
            with self.assertRaisesRegex(ValueError, r'step 3 of 5.*(branch tx/left-behind still exists|still registered)'):
                j.complete(self.primary, self.root, 'sample_job', [tip], owners=owners)
        j.git(self.root, 'worktree', 'remove', str(left))
        with self.assertRaisesRegex(ValueError, r'step 3 of 5.*branch tx/left-behind still exists'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        j.git(self.root, 'branch', '-d', 'tx/left-behind')
        j.complete(self.primary, self.root, 'sample_job', [tip], owners=('left-behind',))

    def test_another_jobs_fresh_branch_at_the_same_commit_does_not_block_the_close(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        # named by a window or a landing event of another Job: never taken for this one
        self.fake_status([{'owner': 'next-mission', 'job_handle': 'another_job', 'scope': 'prepare', 'since': 'x'}])
        j.complete(self.primary, self.root, 'sample_job', [tip])
        self.assertEqual(j.read(self.primary / 'sample_job.json')['status'], 'COMPLETE')

    def test_a_landing_event_of_another_job_keeps_its_branch_out_of_the_match(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        self.fake_status(landings=[{'owner': 'next-mission', 'detail': {'job_handle': 'another_job'}}])
        j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_owner_decides_and_the_branch_at_the_commit_is_not_looked_at(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        self.fake_status()
        with self.assertRaisesRegex(ValueError, r'step 3 of 5.*branch tx/next-mission still exists'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        j.complete(self.primary, self.root, 'sample_job', [tip], owners=('real-mission',))
        self.assertEqual(j.read(self.primary / 'sample_job.json')['status'], 'COMPLETE')

    def test_own_evidence_names_the_mission_before_any_branch_is_looked_at(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        j.git(self.root, 'branch', 'tx/own-mission', tip)
        self.fake_status(landings=[{'owner': 'own-mission', 'detail': {'job_handle': 'sample_job'}}])
        with self.assertRaisesRegex(ValueError, r'step 3 of 5.*branch tx/own-mission still exists'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        j.git(self.root, 'branch', '-d', 'tx/own-mission')
        j.complete(self.primary, self.root, 'sample_job', [tip])

    def declare(self, owner, job_handle):
        from governance.tools.worktrees import claims
        common = Path(j.git(self.root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
        claims.open_window(common, owner, 'prepare', 'validated', job_handle)
        return common

    def test_another_jobs_branch_known_only_from_the_claims_database_is_left_out_of_the_match(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        self.fake_status()
        self.declare('next-mission', 'another_job')
        j.complete(self.primary, self.root, 'sample_job', [tip])
        self.assertEqual(j.read(self.primary / 'sample_job.json')['status'], 'COMPLETE')

    def test_a_branch_whose_claim_names_no_job_is_not_adopted(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        self.fake_status()
        self.declare('next-mission', None)
        j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_a_branch_whose_claims_name_two_jobs_is_not_adopted(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/next-mission', tip)
        self.fake_status()
        self.declare('next-mission', 'another_job')
        self.declare('next-mission', 'sample_job')
        j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_a_job_identified_through_claims_alone_still_refuses_while_its_branch_exists(self):
        self.job()
        j.git(self.root, 'branch', 'tx/own-mission', 'HEAD')  # not at the commit being closed: only the claim names it
        tip = self.land()
        self.fake_status()
        self.declare('own-mission', 'sample_job')
        with self.assertRaisesRegex(ValueError, r'step 3 of 5.*branch tx/own-mission still exists'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        self.assertEqual(j.read(self.primary / 'sample_job.json')['status'], 'WIP')

    def test_claims_alone_close_nothing_when_the_mission_is_gone_but_the_other_checks_fail(self):
        self.job()
        tip = self.land()
        self.fake_status()
        self.declare('gone-mission', 'sample_job')
        (self.root / 'stray.txt').write_text('uncommitted\n')
        with self.assertRaisesRegex(ValueError, r'step 2 of 5'):
            j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_a_leftover_folder_refuses_unless_named_and_then_becomes_a_rider_in_the_coordinator_record(self):
        self.job()
        j.put(self.primary, {'version': 1, 'worker': 'Coordinator', 'updated_at': record()['updated_at'],
                             'context': [], 'queue': ['sample_job']}, None)
        tip = self.land()
        container = self.root.parent / '_worktrees' / self.root.name
        for leftover in (container / 'gone-mission', container / '_review' / 'gone-mission'):
            leftover.mkdir(parents=True)
            self.fake_status()
            with self.assertRaisesRegex(ValueError, r'step 3 of 5.*left behind.*gone-mission.*Coordinator record'):
                j.complete(self.primary, self.root, 'sample_job', [tip], owners=('gone-mission',))
            leftover.rmdir()
        leftover.mkdir()
        j.complete(self.primary, self.root, 'sample_job', [tip], owners=('gone-mission',),
                   leftovers=[str(leftover)])
        coordinator = j.read(self.primary / 'coordinator.json')
        self.assertEqual(len(coordinator['context']), 1)
        self.assertIn(str(leftover), coordinator['context'][0]['text'])
        self.assertEqual(coordinator['queue'], ['sample_job'])
        self.assertFalse((self.primary / 'worktree_cleanup.json').exists())
        self.assertEqual(j.read(self.primary / 'sample_job.json')['status'], 'COMPLETE')

    def test_a_leftover_without_a_coordinator_record_refuses_with_one_message_and_changes_nothing(self):
        self.job()
        tip = self.land()
        self.fake_status()
        before = self.files()
        with self.assertRaisesRegex(ValueError, 'No Coordinator record'):
            j.complete(self.primary, self.root, 'sample_job', [tip], leftovers=['C:/somewhere'])
        self.assertEqual(before, self.files())

    def test_a_job_frozen_by_its_own_owner_refuses_at_step_four(self):
        self.job()
        tip = self.land()
        window = {'owner': 'mission-1', 'job_handle': 'sample_job', 'scope': 'prepare', 'since': '2026-10-13T10:00:00Z'}
        self.fake_status([window])
        with self.assertRaisesRegex(ValueError, r'step 4 of 5.*frozen by mission-1'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        self.fake_status([dict(window, owner='mission-2', job_handle='another_job')])
        j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_a_landing_event_names_the_owner_whose_leftover_branch_is_then_checked(self):
        self.job()
        tip = self.land()
        j.git(self.root, 'branch', 'tx/landed-owner', 'HEAD~1')
        self.fake_status(landings=[{'owner': 'landed-owner', 'detail': {'job_handle': 'sample_job'}}])
        with self.assertRaisesRegex(ValueError, r'step 3 of 5.*tx/landed-owner'):
            j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_record_commits_that_differ_refuse_at_step_five_and_equal_ones_pass(self):
        self.job(commits=['b' * 40])
        tip = self.land()
        self.fake_status()
        with self.assertRaisesRegex(ValueError, r'step 5 of 5.*' + 'b' * 40):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        data = j.read(self.primary / 'sample_job.json')
        data['commits'] = [tip]
        j.put(self.primary, data, hashlib.sha256((self.primary / 'sample_job.json').read_bytes()).hexdigest())
        j.complete(self.primary, self.root, 'sample_job', [tip])

    def test_entries_without_a_disposition_refuse_the_close_and_nothing_is_written(self):
        self.job(pending=self.entries(2))
        j.put(self.primary, dict(record(), handle='next_job', pending=[]), None)
        tip = self.land()
        self.fake_status()
        before = self.files()
        with self.assertRaisesRegex(ValueError, r'without a disposition \(1 decision, 2 finding\)'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        with self.assertRaisesRegex(ValueError, r'without a disposition \(2 finding\)'):
            j.complete(self.primary, self.root, 'sample_job', [tip], routes={1: 'next_job'})
        self.assertEqual(before, self.files())
        j.complete(self.primary, self.root, 'sample_job', [tip], routes={1: 'next_job', 2: 'next_job'})
        moved = self.stored('next_job')['pending']
        self.assertEqual([entry['text'] for entry in moved], ['Entry 1', 'Entry 2'])
        self.assertIn('routed from sample_job', moved[-1]['source'])
        self.assertNotIn('disposition', moved[0])
        closed = self.stored()
        self.assertIn('next_job', closed['records'])
        self.assertEqual([(item['entry'], item['disposition']['kind'], item['disposition']['ref']) for item in closed['disposed']],
                         [(1, 'routed', 'next_job'), (2, 'routed', 'next_job')])

    def test_complete_routes_only_entries_that_have_no_disposition_yet(self):
        self.job(pending=self.entries(2))
        j.put(self.primary, dict(record(), handle='next_job', pending=[]), None)
        j.dispose(self.primary, self.root, 'sample_job', 1, discard='Not needed')
        tip = self.land()
        self.fake_status()
        before = self.files()
        with self.assertRaisesRegex(ValueError, 'already has a disposition'):
            j.complete(self.primary, self.root, 'sample_job', [tip], routes={1: 'next_job', 2: 'next_job'})
        self.assertEqual(before, self.files())
        j.complete(self.primary, self.root, 'sample_job', [tip], routes={2: 'next_job'})
        self.assertEqual([item['disposition']['kind'] for item in self.stored()['disposed']], ['discarded', 'routed'])

    def test_routing_to_a_missing_job_refuses_and_changes_nothing(self):
        self.job(pending=self.entries(1))
        tip = self.land()
        self.fake_status()
        before = self.files()
        with self.assertRaises((ValueError, OSError)):
            j.complete(self.primary, self.root, 'sample_job', [tip], routes={1: 'nowhere'})
        self.assertEqual(before, self.files())

    def test_a_failed_write_puts_every_earlier_file_back(self):
        self.job(pending=self.entries(1))
        j.put(self.primary, dict(record(), handle='next_job', pending=[]), None)
        tip = self.land()
        self.fake_status()
        before = self.files()
        real = j.write

        def failing(path, data):
            if path.name == 'sample_job.json':
                raise OSError('Disk full fixture')
            real(path, data)
        j.write = failing
        self.addCleanup(setattr, j, 'write', real)
        with self.assertRaisesRegex(OSError, 'Disk full'):
            j.complete(self.primary, self.root, 'sample_job', [tip], routes={1: 'next_job'})
        j.write = real
        self.assertEqual(before, self.files())

    # ---- dispose ----------------------------------------------------------------------------------

    def test_every_kind_of_disposition_is_stored_on_the_entry_which_stays_where_it_was(self):
        self.job(pending=self.entries(5))
        j.put(self.primary, dict(record(), handle='next_job', pending=[]), None)
        tip = self.land('where.txt')
        reported = self.dispose(1, route='next_job')
        self.assertEqual(reported['undisposed'], [2, 3, 4, 5])
        self.dispose(2, recorded_in=tip[:9])
        self.dispose(3, recorded_in='where.txt')
        self.dispose(4, recorded_in='next_job')
        self.dispose(5, discard='Superseded by the new design')
        data = self.stored()
        self.assertEqual([entry['text'] for entry in data['pending']], ['Entry %d' % n for n in range(1, 6)])
        found = [entry['disposition'] for entry in data['pending']]
        self.assertEqual([item['kind'] for item in found], ['routed', 'recorded', 'recorded', 'recorded', 'discarded'])
        self.assertEqual((found[0]['ref'], found[1]['ref'], found[1]['check']), ('next_job', tip, 'commit'))
        self.assertEqual([item.get('check') for item in found[2:4]], ['path', 'job'])
        self.assertEqual(found[4]['reason'], 'Superseded by the new design')
        self.assertTrue(all(item['at'] for item in found))
        moved = self.stored('next_job')['pending']
        self.assertEqual(len(moved), 1)
        self.assertNotIn('disposition', moved[0])
        self.assertIn('routed from sample_job', moved[0]['source'])
        self.assertIn('next_job', data['records'])
        self.assertEqual(data['status'], 'WIP')

    def test_a_reference_is_checked_as_far_as_the_tool_can_and_an_unverifiable_one_needs_the_flag(self):
        self.job(pending=self.entries(5))
        j.git(self.root, 'checkout', '-q', '-b', 'side')
        side = self.land('side.txt')
        j.git(self.root, 'checkout', '-q', 'main')
        (self.root / 'untracked.txt').write_text('not on main\n')
        before = self.files()
        for reference, flag in ((side, False), (side, True), ('untracked.txt', False), ('nobody_here', False),
                                ('../outside', False), ('C:/elsewhere/file.md', False), ('   ', True)):
            with self.assertRaises(ValueError, msg=reference):
                self.dispose(1, recorded_in=reference, unchecked=flag)
        self.assertEqual(before, self.files())
        self.dispose(1, recorded_in='nobody_here', unchecked=True)
        self.dispose(2, recorded_in='.gitignore#anything', unchecked=True)
        self.dispose(3, recorded_in='.gitignore')
        found = [entry['disposition'] for entry in self.stored()['pending'][:3]]
        self.assertEqual([(item['ref'], item['check']) for item in found],
                         [('nobody_here', 'unchecked'), ('.gitignore#anything', 'path'), ('.gitignore', 'path')])
        with self.assertRaisesRegex(ValueError, '--unchecked belongs'):
            self.dispose(4, discard='Reason', unchecked=True)

    def test_a_discard_needs_a_reason_and_reads_a_utf8_file(self):
        self.job(pending=self.entries(2))
        before = self.files()
        for reason in ('', '   '):
            with self.assertRaisesRegex(ValueError, 'reason'):
                self.dispose(1, discard=reason)
        self.assertEqual(before, self.files())
        reason = Path(self.temp.name) / 'reason.txt'
        reason.write_bytes(b'\xef\xbb\xbfSuperseded \xe2\x80\x94 caf\xc3\xa9\r\nsecond line\r\n')
        self.dispose(1, discard=str(reason))
        self.assertEqual(self.stored()['pending'][0]['disposition']['reason'], 'Superseded \u2014 caf\u00e9\nsecond line')
        self.assertNotIn(b'\r', (self.primary / 'sample_job.json').read_bytes())

    def test_dispose_refusals_change_nothing(self):
        self.job(pending=self.entries(2))
        j.put(self.primary, dict(record(), handle='done_job', status='COMPLETE', pending=[], commits=['a' * 40]), None)
        self.dispose(2, discard='Done')
        before = self.files()
        cases = [dict(position=0, discard='x'), dict(position=3, discard='x'), dict(position=2, discard='again'),
                 dict(position=1, route='sample_job'), dict(position=1, route='nowhere'), dict(position=1, route='done_job'),
                 dict(position=1), dict(position=1, discard='x', recorded_in='.gitignore')]
        for case in cases:
            with self.assertRaises(ValueError, msg=str(case)):
                self.dispose(case.pop('position'), **case)
        with self.assertRaisesRegex(ValueError, 'terminal'):
            j.dispose(self.primary, self.root, 'done_job', 1, discard='x')
        with self.assertRaisesRegex(ValueError, 'No record'):
            j.dispose(self.primary, self.root, 'missing_job', 1, discard='x')
        self.assertEqual(before, self.files())

    def test_a_failed_route_write_puts_the_destination_back(self):
        self.job(pending=self.entries(1))
        j.put(self.primary, dict(record(), handle='next_job', pending=[]), None)
        before = self.files()
        real = j.write

        def failing(path, data):
            if path.name == 'sample_job.json':
                raise OSError('Disk full fixture')
            real(path, data)
        j.write = failing
        self.addCleanup(setattr, j, 'write', real)
        with self.assertRaisesRegex(OSError, 'Disk full'):
            self.dispose(1, route='next_job')
        j.write = real
        self.assertEqual(before, self.files())

    def test_the_closed_record_keeps_the_trail_until_it_is_pruned(self):
        long_text = 'A decision with   spaces\nand lines ' + 'x' * 200
        self.job(pending=[{'kind': 'decision', 'text': long_text, 'source': 'Fixture'},
                          {'kind': 'finding', 'text': 'Plain', 'source': 'Fixture'},
                          {'kind': 'handoff', 'text': 'Unverified claim', 'source': 'Fixture'}])
        tip = self.land('trail.txt')
        self.dispose(1, recorded_in=tip)
        self.dispose(2, discard='Noise')
        with self.assertRaisesRegex(ValueError, r'without a disposition \(3 handoff\)'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        self.dispose(3, recorded_in='nobody_here', unchecked=True)
        self.fake_status()
        result = j.complete(self.primary, self.root, 'sample_job', [tip])
        self.assertEqual(result['unchecked'], [3])
        self.assertEqual([(item['entry'], item['disposition'], item['check']) for item in result['disposed']],
                         [(1, 'recorded', 'commit'), (2, 'discarded', None), (3, 'recorded', 'unchecked')])
        closed = self.stored()
        self.assertEqual((closed['status'], closed['pending']), ('COMPLETE', []))
        trail = closed['disposed']
        self.assertEqual([item['kind'] for item in trail], ['decision', 'finding', 'handoff'])
        self.assertEqual(trail[0]['excerpt'], ' '.join(long_text.split())[:97] + '...')
        self.assertEqual(len(trail[0]['excerpt']), j.EXCERPT_LIMIT)
        self.assertEqual(trail[2]['disposition']['check'], 'unchecked')
        self.assertEqual(j.prune(self.primary, self.root), ['sample_job'])
        self.assertFalse((self.primary / 'sample_job.json').exists())

    def test_an_old_record_without_dispositions_still_reads_and_must_be_disposed(self):
        self.job(pending=self.entries(2))
        self.assertEqual(self.stored()['pending'], self.entries(2))
        tip = self.land()
        self.fake_status()
        with self.assertRaisesRegex(ValueError, 'without a disposition'):
            j.complete(self.primary, self.root, 'sample_job', [tip])
        self.assertNotIn('disposed', self.stored())

    def test_malformed_dispositions_and_trails_are_refused_and_coordinator_context_takes_none(self):
        at = '2026-10-13T12:00:00Z'
        good = dict(self.entries(1)[0])
        bad = [{'kind': 'forgotten', 'reason': 'x', 'at': at}, {'kind': 'discarded', 'reason': ' ', 'at': at},
               {'kind': 'discarded', 'reason': 'x'}, {'kind': 'discarded', 'reason': 'x', 'at': 'yesterday'},
               {'kind': 'discarded', 'reason': 'x', 'at': at, 'extra': 1}, {'kind': 'routed', 'ref': 'Bad Handle', 'at': at},
               {'kind': 'routed', 'ref': 'coordinator', 'at': at}, {'kind': 'recorded', 'ref': 'x', 'at': at},
               {'kind': 'recorded', 'ref': 'x', 'check': 'trusted', 'at': at}, {'kind': 'recorded', 'ref': ' x', 'check': 'path', 'at': at},
               'discarded']
        for value in bad:
            with self.assertRaises(ValueError, msg=str(value)):
                j.validate(dict(record(), pending=[dict(good, disposition=value)]))
        j.validate(dict(record(), pending=[dict(good, disposition={'kind': 'recorded', 'ref': 'x', 'check': 'unchecked', 'at': at})]))
        trail = {'entry': 1, 'kind': 'decision', 'excerpt': 'Entry 1', 'disposition': {'kind': 'discarded', 'reason': 'x', 'at': at}}
        j.validate(dict(record(), pending=[], disposed=[trail]))
        for broken in (dict(trail, entry=0), dict(trail, excerpt=''), dict(trail, disposition={'kind': 'nope'}),
                       {key: value for key, value in trail.items() if key != 'kind'}):
            with self.assertRaises(ValueError):
                j.validate(dict(record(), pending=[], disposed=[broken]))
        coordinator = {'version': 1, 'worker': 'Coordinator', 'updated_at': at, 'queue': [], 'context': []}
        j.validate(dict(coordinator, context=[good]))
        with self.assertRaises(ValueError):
            j.validate(dict(coordinator, context=[dict(good, disposition=dict(bad[1], reason='x'))]))
        with self.assertRaises(ValueError):
            j.validate(dict(coordinator, disposed=[trail]))
        with self.assertRaises(ValueError):
            j.validate(dict(record(), disposed={}))

    def test_queue_counts_entries_with_and_without_a_disposition_alike(self):
        self.job(pending=self.entries(3))
        j.put(self.primary, {'version': 1, 'worker': 'Coordinator', 'updated_at': '2026-10-13T12:00:00Z',
                             'queue': ['sample_job'], 'context': []}, None)
        self.dispose(1, discard='Noise')
        row = j.queued(self.primary)['queue'][0]
        self.assertEqual(row['pending_count'], 3)
        self.assertNotIn('disposition', json.dumps(row))

    def test_the_commands_work_from_a_linked_checkout_in_primary_storage(self):
        self.job(pending=self.entries(3))
        j.put(self.primary, dict(record(), handle='next_job', pending=[]), None)
        tip = self.land('cli.txt')
        reason = Path(self.temp.name) / 'reason.txt'
        reason.write_bytes('Obsolete \u2014 caf\u00e9'.encode('utf-8'))
        for arguments in (['1', '--recorded-in', 'cli.txt'], ['2', '--route', 'next_job'], ['3', '--discard', str(reason)]):
            done = self.launcher('--primary', 'dispose', 'sample_job', *arguments)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn('"disposed": "sample_job"', done.stdout)
        found = [entry['disposition'] for entry in self.stored()['pending']]
        self.assertEqual([item['kind'] for item in found], ['recorded', 'routed', 'discarded'])
        self.assertEqual(found[2]['reason'], 'Obsolete \u2014 caf\u00e9')
        failed = self.launcher('dispose', 'sample_job', '1', '--discard', 'again')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('FAIL: Entry 1 already has a disposition', failed.stderr)
        self.fake_status()
        done = self.launcher('complete', 'sample_job', '--commit', tip[:10])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(self.stored()['status'], 'COMPLETE')

    def test_the_removed_recorded_flag_fails_loudly_and_changes_nothing(self):
        self.job(pending=self.entries(1))
        tip = self.land()
        before = self.files()
        failed = self.launcher('complete', 'sample_job', '--commit', tip[:10], '--recorded')
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('--recorded was removed', failed.stderr)
        self.assertEqual(before, self.files())

    def test_records_with_a_byte_order_mark_are_read(self):
        path = Path(j.put(self.primary, record(), None))
        path.write_bytes(b'\xef\xbb\xbf' + path.read_bytes())
        self.assertEqual(j.read(path)['handle'], 'sample_job')

class BriefTest(unittest.TestCase):
    """`brief` prints the worker brief from the live document, writes nothing and fails without printing a brief."""
    REAL = rp.ROOT / 'governance' / 'LOCAL-COORDINATION.md'
    OLD = 'The tools write and clear the windows; never announce one.'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'primary'
        shutil.copytree(TEMPLATE['root'], self.root)
        self.document = self.root / j.COORDINATION_DOCUMENT
        self.document.parent.mkdir()
        shutil.copyfile(self.REAL, self.document)  # a copy of the real document, so a drift in its block fails here

    def text(self):
        return self.document.read_bytes().decode('utf-8')

    def cli(self, *arguments):
        return subprocess.run([sys.executable, '-B', str(rp.ROOT / 'governance/tools/local_jobs.py'), '--repo', str(self.root),
                               'brief', *arguments], capture_output=True)

    def snapshot(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes() for path in self.root.rglob('*')
                if path.is_file() and '.git' not in path.relative_to(self.root).parts}

    def real_block(self):
        """The block as the real document holds it, found without the tool's own parser."""
        found = re.findall(r'^```text\n(COORDINATION \(.*?)\n```$', self.REAL.read_bytes().decode('utf-8').replace('\r\n', '\n'),
                           re.S | re.M)
        self.assertEqual(len(found), 1)
        return found[0]

    def test_the_block_printed_is_the_block_in_the_real_document_with_the_placeholders_filled(self):
        shown = j.brief(self.root, 'sample_job')
        block = self.real_block()
        self.assertIn('<primary-checkout>', block)
        self.assertIn('<your handle>', block)
        self.assertIn(block.replace('<primary-checkout>', self.root.as_posix()).replace('<primary>', self.root.as_posix())
                      .replace('<your handle>', 'sample_job'), shown)
        self.assertNotIn('<primary', shown)
        self.assertNotIn('<your handle>', shown)
        self.assertIn('"Coordinator"', shown)
        self.assertIn('remove your owned worktree and branch and any superseded ones', shown)
        self.assertIn('\n  governance/ROLE-WORKER.md\n', shown)
        self.assertIn('- If a README under `governance/extensions/` sets a rule for how you present a preview or a sealed review, '
                      'follow it.', shown)
        self.assertTrue(shown.index('READ FIRST') < shown.index(j.COORDINATION_TITLE) < shown.index('IF MAIN MOVES')
                        < shown.index('RULES THAT STAY') < shown.index('\nSCOPE\n'))

    def test_changing_the_document_changes_the_brief(self):
        self.assertIn(self.OLD, j.brief(self.root, 'sample_job'))
        self.assertIn(self.OLD, self.text())
        self.document.write_bytes(self.text().replace(self.OLD, 'A line the fixture wrote.').encode('utf-8'))
        shown = j.brief(self.root, 'sample_job')
        self.assertIn('A line the fixture wrote.', shown)
        self.assertNotIn(self.OLD, shown)

    def test_a_missing_or_broken_block_fails_and_prints_no_brief(self):
        title = j.COORDINATION_TITLE
        text = self.text()
        cases = {
            'not found': text.replace(title, 'COORDINATION renamed'),
            '2 found': text + '\n```text\n' + title + '\n- another\n```\n',
            'not closed': text[:text.index(title) + len(title)] + '\n- one line and no closing fence\n',
            'lacks <your handle>': text.replace('<your handle>', 'a handle'),
            'lacks <primary-checkout>': text.replace('<primary-checkout>', 'a checkout'),
        }
        for message, broken in cases.items():
            self.document.write_bytes(broken.encode('utf-8'))
            with self.assertRaisesRegex(ValueError, message):
                j.brief(self.root, 'sample_job')
            done = self.cli('sample_job')
            self.assertNotEqual(done.returncode, 0, message)
            self.assertEqual(done.stdout, b'', message)
            self.assertIn(b'FAIL:', done.stderr)
        self.document.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing coordination document'):
            j.brief(self.root, 'sample_job')

    def test_a_bad_handle_fails_with_the_helper_rule_and_prints_nothing(self):
        for name in ('', 'Bad', 'bad-name', 'coordinator', 'a__b', 'x' * 65, '../up'):
            with self.assertRaisesRegex(ValueError, 'Invalid or reserved job handle'):
                j.brief(self.root, name)
        done = self.cli('Bad-Handle')
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(done.stdout, b'')

    def test_a_job_without_a_record_gets_a_note_and_one_with_a_record_does_not(self):
        self.assertIn('NOTE: no record exists for sample_job yet', j.brief(self.root, 'sample_job'))
        j.put(j.safe_directory(self.root, create=True), record(), None)
        self.assertNotIn('no record exists', j.brief(self.root, 'sample_job'))
        self.assertIn('--primary pointer sample_job', j.brief(self.root, 'sample_job'))

    def test_the_brief_names_the_coordinator_for_both_setups_and_has_no_literal_wait_on_a_name(self):
        shown = j.brief(self.root, 'sample_job')
        self.assertIn('the session named "Coordinator" or, where a session started you, that session', shown)
        self.assertIn('end-of-turn report', shown)
        self.assertNotIn('messaging name is', shown)

    def test_stagger_adds_its_paragraph_and_extra_file_is_printed_verbatim_under_the_pointer(self):
        plain = j.brief(self.root, 'sample_job')
        self.assertNotIn('STAGGERED LANDING', plain)
        self.assertIn('SCOPE\nThe scope lives in your Job record', plain)
        self.assertNotIn('pastes', plain)
        self.assertIn('start your PREPARE only after the Coordinator says the other landing is done',
                      j.brief(self.root, 'sample_job', stagger=True))
        scope = Path(self.temp.name) / 'scope.txt'
        scope.write_bytes(b'\xef\xbb\xbf\r\nWHAT: caf\xc3\xa9 \xe2\x80\x94 one\r\n  indented\r\n\r\nlast\r\n\r\n')
        shown = j.brief(self.root, 'sample_job', extra_file=scope)
        self.assertIn('SCOPE\nThe scope lives in your Job record', shown)
        self.assertIn('the record wins):\nWHAT: café — one\n  indented\n\nlast\n\nThis brief reads only', shown)
        self.assertNotIn('\r', shown)
        scope.write_bytes(b' \r\n\r\n')
        with self.assertRaisesRegex(ValueError, 'empty'):
            j.brief(self.root, 'sample_job', extra_file=scope)
        scope.write_bytes(b'\xff\xfe\x00')
        with self.assertRaises(ValueError):
            j.brief(self.root, 'sample_job', extra_file=scope)
        with self.assertRaisesRegex(ValueError, 'not found'):
            j.brief(self.root, 'sample_job', extra_file=Path(self.temp.name) / 'absent.txt')

    def test_the_output_is_lf_only_and_the_same_from_the_command_line_for_either_line_ending_of_the_document(self):
        expected = j.brief(j.roots(self.root)[1], 'sample_job', stagger=True)
        self.assertNotIn('\r', expected)
        for ending in ('\n', '\r\n'):
            self.document.write_bytes(self.text().replace('\r\n', '\n').replace('\n', ending).encode('utf-8'))
            done = self.cli('sample_job', '--stagger')
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertNotIn(b'\r', done.stdout)
            self.assertEqual(done.stdout.decode('utf-8'), expected)

    def test_it_reads_only(self):
        j.put(j.safe_directory(self.root, create=True), record(), None)
        before = self.snapshot()
        status = j.git(self.root, 'status', '--porcelain')
        j.brief(self.root, 'sample_job', stagger=True)
        self.assertEqual(self.cli('sample_job').returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(j.git(self.root, 'status', '--porcelain'), status)
        self.assertFalse((self.root / j.RELATIVE / '.writer.lock').exists())

    def test_the_reading_list_names_files_that_exist(self):
        for item in j.BRIEF_READING:
            self.assertTrue((self.REAL.parents[1] / item).is_file(), item)


if __name__ == '__main__':
    unittest.main()
