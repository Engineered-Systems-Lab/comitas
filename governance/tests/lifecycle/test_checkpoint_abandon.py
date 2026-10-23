"""Abandon withdraws an uncommitted transaction without touching content, in disposable repositories only."""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

import checkpoint as c
from governance.tests.lifecycle import test_checkpoint as fixtures


class AbandonTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.CheckpointTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.tool = self.f.tool

    def abandon(self, reason='Candidate restructured before approval'):
        return self.tool.abandon(reason)

    def slot(self):
        return self.tool.store / c.TRANSACTION

    def test_a_prepared_transaction_is_withdrawn_without_touching_content(self):
        self.f.prepare()
        packet_id = json.loads(self.slot().read_bytes())['packet']['id']
        self.assertEqual(self.tool.inventory(), ['a.txt'])
        result = self.abandon()
        self.assertEqual((result['state'], result['was'], result['unstaged']), ('idle', 'prepared', ['a.txt']))
        self.assertEqual((result['abandoned'], result['manifest'], result['reason']),
                         (packet_id, ['a.txt'], 'Candidate restructured before approval'))
        self.assertFalse(result['approval_withdrawn'])
        self.assertEqual(self.tool.inventory(), [])
        self.assertFalse(self.slot().exists())
        self.assertEqual((self.f.root / 'a.txt').read_text(), 'new\n')
        self.assertEqual(self.tool.head(), self.f.parent)
        # The slot is free again: a new transaction can be prepared.
        self.assertEqual(self.f.prepare()['state'], 'prepared')

    def test_nothing_is_archived_anywhere(self):
        self.f.prepare()
        def names():  # the working tree and the store, which is in the Git directory
            return sorted(p.name for p in self.f.root.rglob('*')
                          if p.is_file() and ('.git' not in p.parts or p.is_relative_to(self.tool.store)))
        before = names()
        self.abandon()
        after = names()
        self.assertEqual([n for n in before if n not in after], [c.TRANSACTION])
        self.assertEqual([n for n in after if n not in before], [])

    def test_an_approved_transaction_loses_its_approval(self):
        self.f.ready()
        result = self.abandon()
        self.assertTrue(result['approval_withdrawn'])
        self.assertEqual(result['was'], 'approved')
        with self.assertRaises((ValueError, OSError)):
            self.tool.commit()
        self.assertEqual(self.tool.head(), self.f.parent)

    def test_the_reason_is_optional_but_not_blank(self):
        self.f.prepare()
        for blank in ('', '   '):
            with self.subTest(blank=blank), self.assertRaisesRegex(ValueError, 'must not be blank'):
                self.tool.abandon(blank)
        self.assertTrue(self.slot().exists())
        self.assertIsNone(self.tool.abandon()['reason'])

    def test_a_transaction_that_began_to_commit_cannot_be_abandoned(self):
        self.f.ready()
        self.tool.commit()
        before = self.slot().read_bytes()
        with self.assertRaisesRegex(ValueError, 'Cannot abandon a transaction in state'):
            self.abandon()
        self.assertEqual(self.slot().read_bytes(), before)

    def test_it_refuses_when_head_has_moved(self):
        self.f.prepare()
        (self.f.root / 'b.txt').write_text('moved\n')
        self.f.git('add', '--', 'b.txt')
        self.f.git('commit', '-m', 'Unrelated movement')
        with self.assertRaisesRegex(ValueError, 'HEAD or branch moved'):
            self.abandon()
        self.assertTrue(self.slot().exists())

    def test_it_never_touches_staged_paths_outside_the_transaction(self):
        self.f.prepare()
        (self.f.root / 'b.txt').write_text('someone elses staged work\n')
        self.f.git('add', '--', 'b.txt')
        before = self.tool.inventory()
        with self.assertRaisesRegex(ValueError, 'outside the transaction'):
            self.abandon()
        self.assertEqual(self.tool.inventory(), before)
        self.assertTrue(self.slot().exists())

    def test_a_tampered_packet_is_refused(self):
        self.f.prepare()
        envelope = json.loads(self.slot().read_bytes())
        envelope['packet']['delegation'] = 'forged'
        self.slot().write_text(json.dumps(envelope), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Transaction seal mismatch'):
            self.abandon()
        self.assertEqual(self.tool.inventory(), ['a.txt'])

    def test_an_interrupted_abandon_is_finished_by_repeating_it(self):
        self.f.prepare()
        real_git = self.tool.git

        def failing(*args, **kwargs):
            if args and args[0] == 'restore':
                raise ValueError('simulated interruption while unstaging')
            return real_git(*args, **kwargs)

        with patch.object(self.tool, 'git', side_effect=failing), \
                self.assertRaisesRegex(ValueError, 'simulated interruption'):
            self.abandon()
        self.assertTrue(self.slot().exists())
        self.assertEqual(self.tool.inventory(), ['a.txt'])
        self.assertEqual(self.abandon()['state'], 'idle')
        self.assertEqual(self.tool.inventory(), [])
        self.assertFalse(self.slot().exists())

    def test_abandoning_with_no_transaction_is_a_no_op(self):
        self.assertEqual(self.abandon(), {'state': 'idle'})

    def test_the_command_line_wires_through_the_same_checks(self):
        self.f.prepare()
        environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        script = c.__file__  # the real script, run against the disposable repository
        done = subprocess.run([sys.executable, '-B', script, '--repo', str(self.f.root), 'abandon',
                               '--reason', 'CLI fixture'], capture_output=True, text=True, env=environment)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual((result['state'], result['reason'], result['unstaged']), ('idle', 'CLI fixture', ['a.txt']))
        self.assertEqual(self.tool.inventory(), [])
        again = subprocess.run([sys.executable, '-B', script, '--repo', str(self.f.root), 'abandon'],
                               capture_output=True, text=True, env=environment)
        self.assertEqual((again.returncode, json.loads(again.stdout)), (0, {'state': 'idle'}))

    def test_abandonable_states_stop_before_any_commit(self):
        self.assertEqual(c.ABANDONABLE, ('preparing', 'prepare-failed', 'validated', 'prepared', 'approved'))
        for state in ('committing', 'commit-attempted', 'verified', 'follow-up-pending'):
            self.assertNotIn(state, c.ABANDONABLE)


if __name__ == '__main__':
    unittest.main()
