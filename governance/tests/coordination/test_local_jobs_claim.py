"""The Coordinator claim: it creates the Coordinator record, names a holder, and takes over only with a flag and a reason.

The function tests run in a plain folder. The command test builds a disposable repository with an ignored `.local/`, claims
the role through the real launcher and reads the queue, which fails in a new project until the record exists."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from governance.tools.coordination import local_jobs as j
import repo_paths as rp


class ClaimTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / 'coordinator.json'

    def record(self):
        return json.loads(self.path.read_text(encoding='utf-8'))

    def test_it_creates_a_valid_record_with_an_empty_queue_and_the_holder(self):
        result = j.claim_coordinator(self.directory, 'Coordinator thread A')
        data = self.record()
        j.validate(data)
        self.assertEqual((data['worker'], data['queue'], data['updated_at']), ('Coordinator thread A', [], result['since']))
        self.assertEqual(len(data['context']), 1)
        self.assertIn('Claimed by Coordinator thread A at ' + result['since'], data['context'][0]['text'])
        self.assertIsNone(result['previous'])
        self.assertEqual(self.path.read_bytes().count(b'\r'), 0)
        self.assertEqual(sorted(p.name for p in self.directory.iterdir()), ['coordinator.json'])  # no lock left behind

    def test_an_existing_holder_is_named_and_nothing_changes(self):
        j.claim_coordinator(self.directory, 'Coordinator thread A')
        before = self.path.read_bytes()
        for identity in ('Coordinator thread B', 'Coordinator thread A'):
            with self.subTest(identity=identity):
                with self.assertRaisesRegex(ValueError, "held by 'Coordinator thread A'.*lead developer.*--take-over"):
                    j.claim_coordinator(self.directory, identity)
                self.assertEqual(self.path.read_bytes(), before)

    def test_a_take_over_needs_the_flag_and_a_reason_and_records_the_previous_holder_as_superseded(self):
        j.claim_coordinator(self.directory, 'Coordinator thread A')
        data = self.record()
        data.update(queue=['one_job'], priorities={'one_job': 1})
        j.write(self.path, data)
        before = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'needs --reason'):
            j.claim_coordinator(self.directory, 'Coordinator thread B', take_over=True)
        with self.assertRaisesRegex(ValueError, 'belongs to --take-over'):
            j.claim_coordinator(self.directory, 'Coordinator thread B', reason='the lead developer said so')
        self.assertEqual(self.path.read_bytes(), before)
        result = j.claim_coordinator(self.directory, 'Coordinator thread B', take_over=True,
                                     reason='the lead developer named thread B')
        data = self.record()
        j.validate(data)
        self.assertEqual((data['worker'], data['queue'], data['priorities']), ('Coordinator thread B', ['one_job'], {'one_job': 1}))
        self.assertEqual((result['previous'], result['superseded']), ('Coordinator thread A', True))
        text = data['context'][-1]['text']
        for part in (result['since'], 'Coordinator thread B', 'from Coordinator thread A', 'superseded',
                     'the lead developer named thread B'):
            self.assertIn(part, text)
        self.assertEqual(len(data['context']), 2)

    def test_a_released_record_is_taken_over_like_any_other(self):
        j.claim_coordinator(self.directory, 'Coordinator thread A')
        data = self.record()
        data['worker'] = j.RELEASED + 'Coordinator thread A'
        j.write(self.path, data)
        with self.assertRaisesRegex(ValueError, 'RELEASED by'):
            j.claim_coordinator(self.directory, 'Coordinator thread B')
        j.claim_coordinator(self.directory, 'Coordinator thread B', take_over=True, reason='designated by the lead developer')
        self.assertEqual(self.record()['worker'], 'Coordinator thread B')

    def test_there_is_nothing_to_take_over_without_a_record(self):
        with self.assertRaisesRegex(ValueError, 'no Coordinator record'):
            j.claim_coordinator(self.directory, 'Coordinator thread A', take_over=True, reason='x')
        self.assertFalse(self.path.exists())

    def test_the_identity_is_one_trimmed_printable_line_and_not_the_release_marker(self):
        for bad in ('', ' padded', 'two\nlines', 'x' * (j.IDENTITY_LIMIT + 1), 'RELEASED by someone', None, 5):
            with self.subTest(identity=bad):
                with self.assertRaises(ValueError):
                    j.claim_coordinator(self.directory, bad)
        self.assertFalse(self.path.exists())

    def test_an_interrupted_writer_lock_stops_the_claim_and_is_never_broken(self):
        lock = self.directory / '.writer.lock'
        lock.write_bytes(b'')
        original = j.LOCK_TRIES
        j.LOCK_TRIES = 1
        self.addCleanup(setattr, j, 'LOCK_TRIES', original)
        with self.assertRaisesRegex(ValueError, 'Writer active'):
            j.claim_coordinator(self.directory, 'Coordinator thread A')
        self.assertTrue(lock.exists())
        self.assertFalse(self.path.exists())


class CommandTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'new-project'
        self.root.mkdir()
        j.git(self.root, 'init', '-b', 'main')
        (self.root / '.gitignore').write_text('/.local/\n')

    def local_jobs(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
        return subprocess.run([sys.executable, '-B', str(rp.ROOT / rp.TOOLS / 'local_jobs.py'), '--repo', str(self.root), *args],
                              capture_output=True, text=True, timeout=120, cwd=self.temp.name, env=env)

    def test_the_queue_fails_in_a_new_project_until_the_role_is_claimed(self):
        missing = self.local_jobs('queue')
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('coordinator.json', missing.stderr)
        claimed = self.local_jobs('claim-coordinator', '--identity', 'Coordinator thread A')
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        self.assertEqual(json.loads(claimed.stdout)['claimed'], 'Coordinator thread A')
        self.assertTrue((self.root / '.local' / 'coordination' / 'coordinator.json').is_file())
        queue = self.local_jobs('queue')
        self.assertEqual(queue.returncode, 0, queue.stderr)
        self.assertEqual(json.loads(queue.stdout), {'queue': [], 'unqueued': []})

    def test_a_second_claim_names_the_holder_and_exits_nonzero_and_a_take_over_succeeds(self):
        self.local_jobs('claim-coordinator', '--identity', 'Coordinator thread A')
        refused = self.local_jobs('claim-coordinator', '--identity', 'Coordinator thread B')
        self.assertEqual(refused.returncode, 1)
        self.assertIn('Coordinator thread A', refused.stderr)
        self.assertIn('--take-over', refused.stderr)
        taken = self.local_jobs('claim-coordinator', '--identity', 'Coordinator thread B', '--take-over', '--reason',
                                'the lead developer named thread B')
        self.assertEqual(taken.returncode, 0, taken.stderr)
        self.assertEqual(json.loads(taken.stdout)['previous'], 'Coordinator thread A')

    def test_the_claim_and_the_helper_are_gated_commands(self):
        from governance.tools import runtime_guard as g
        self.assertEqual(g.mode_of('local_jobs', ['--repo', '.', 'claim-coordinator', '--identity', 'x'])[0], g.GATED)
        self.assertEqual(g.mode_of('adopt', [])[0], g.GATED)


if __name__ == '__main__':
    unittest.main()
