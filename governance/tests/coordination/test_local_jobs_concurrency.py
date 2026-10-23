"""The writer-lock and concurrent-writer tests of the local Job helper, apart from the rest so the runner cuts them as their own unit.

They need the same disposable checkouts as the other Job tests, so the fixture and the module setup come from
`test_local_jobs`; each test here sets a short lock wait or holds the lock itself.
"""
import threading
import time

from governance.tools.coordination import local_jobs as j

from governance.tests.coordination.test_local_jobs import JobsFixture, record, setUpModule, tearDownModule  # noqa: F401 (the module hooks are used by unittest)


class WriterLockTest(JobsFixture):
    def test_note_waits_briefly_for_a_busy_lock_but_never_breaks_it(self):
        self.job()
        lock = self.primary / '.writer.lock'
        lock.write_text('Interrupted fixture')
        original = (j.LOCK_TRIES, j.LOCK_WAIT)
        j.LOCK_TRIES, j.LOCK_WAIT = 2, 0.01
        self.addCleanup(lambda: (setattr(j, 'LOCK_TRIES', original[0]), setattr(j, 'LOCK_WAIT', original[1])))
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            j.note(self.primary, 'sample_job', 'finding', 'source', 'text')
        self.assertEqual(lock.read_text(), 'Interrupted fixture')

    def test_dispose_waits_for_a_busy_lock_never_breaks_it_and_concurrent_disposals_both_land(self):
        self.job(pending=self.entries(2))
        lock = self.primary / '.writer.lock'
        lock.write_text('Interrupted fixture')
        original = (j.LOCK_TRIES, j.LOCK_WAIT)
        j.LOCK_TRIES, j.LOCK_WAIT = 2, 0.01
        self.addCleanup(lambda: (setattr(j, 'LOCK_TRIES', original[0]), setattr(j, 'LOCK_WAIT', original[1])))
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            self.dispose(1, discard='Noise')
        self.assertEqual(lock.read_text(), 'Interrupted fixture')
        lock.unlink()
        j.LOCK_TRIES, j.LOCK_WAIT = 40, 0.02
        errors = []

        def attempt(position):
            try:
                self.dispose(position, discard='Noise %d' % position)
            except BaseException as error:  # reported to the test thread below
                errors.append(error)
        threads = [threading.Thread(target=attempt, args=(position,)) for position in (1, 2)]
        held = j.locked(self.primary)
        held.__enter__()
        for thread in threads:
            thread.start()
        time.sleep(0.1)
        held.__exit__(None, None, None)
        for thread in threads:
            thread.join(10)
        self.assertEqual(errors, [])
        self.assertEqual([entry['disposition']['reason'] for entry in self.stored()['pending']], ['Noise 1', 'Noise 2'])
        self.assertFalse(lock.exists())

    def test_writer_lock_refuses_and_preserves_existing_lock(self):
        lock = self.primary / '.writer.lock'
        lock.write_text('Interrupted fixture')
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            j.put(self.primary, record(), None)
        self.assertEqual(lock.read_text(), 'Interrupted fixture')


if __name__ == '__main__':
    import unittest
    unittest.main()
