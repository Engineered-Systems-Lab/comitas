"""The interpreter pin: a transaction records the interpreter fingerprint at its first step and every later step refuses,
at its start, when the running interpreter is no longer that one. Disposable repositories only.

A test changes the running interpreter by patching the guard's `fingerprint`, the one function every comparison reads."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import checkpoint as c
from governance.tools import runtime_guard as g
from governance.tools.lifecycle import early_approval as ea
from governance.tools.support import local_state
from governance.tests.lifecycle import test_early_approval as early
from governance.tests.worktrees import test_checkpoint_worktrees as wt

DRIFT = 'the interpreter changed since the preview: re-run the preview, nothing was lost yet'


def another():
    """A fingerprint that is not the running interpreter's: the same file, upgraded."""
    return dict(g.fingerprint(), sha256='f' * 64, version='3.99.0')


def drifted():
    return patch.object(g, 'fingerprint', return_value=another())


class PinTest(unittest.TestCase):
    setUp = early.EarlyApprovalTest.setUp
    git, preview, go, prepare, approved = (early.EarlyApprovalTest.git, early.EarlyApprovalTest.preview,
                                           early.EarlyApprovalTest.go, early.EarlyApprovalTest.prepare,
                                           early.EarlyApprovalTest.approved)

    def test_the_preview_records_the_running_interpreter(self):
        self.assertEqual(self.preview()['body']['interpreter'], g.fingerprint())

    def test_a_prepare_without_a_preview_pins_the_running_interpreter_under_the_seal(self):
        packet = self.prepare()
        self.assertEqual(packet['interpreter'], g.fingerprint())
        self.assertEqual(set(packet['interpreter']), set(g.FIELDS))
        self.assertEqual(self.tool.load()['interpreter'], g.fingerprint())
        # The field is under the packet's authentication: an edited pin is a seal mismatch, never a quiet change.
        slot = self.tool.store / c.TRANSACTION
        envelope = json.loads(slot.read_bytes())
        envelope['packet']['interpreter']['sha256'] = '0' * 64
        slot.write_bytes(c.canonical(envelope))
        with self.assertRaisesRegex(ValueError, 'seal mismatch'):
            self.tool.load()

    def test_the_go_keeps_the_pin_of_its_preview_and_a_prepare_on_another_interpreter_refuses_at_its_start(self):
        go = self.go(self.preview())
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                self.prepare(go)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '', 'nothing was staged')
        self.assertFalse((self.tool.store / c.TRANSACTION).exists(), 'no transaction was started')
        packet = self.prepare(go)
        self.assertEqual((packet['state'], packet['interpreter']), ('approved', g.fingerprint()))

    def test_the_go_itself_refuses_when_the_interpreter_changed_since_the_preview(self):
        preview = self.preview()
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                self.go(preview)
        self.assertTrue(self.go(preview)['body']['go'])

    def test_every_later_step_refuses_at_its_start_and_changes_nothing(self):
        _, packet = self.approved()
        before = (self.tool.store / c.TRANSACTION).read_bytes()
        steps = (('seal', self.tool.seal), ('commit', self.tool.commit), ('verify', self.tool.verify),
                 ('approve-consent', lambda: self.tool.approve_consent({})),
                 ('approve', lambda: self.tool.approve(lambda prompt: '')), ('finish', self.tool.finish))
        with drifted():
            for name, step in steps:
                with self.subTest(step=name):
                    with self.assertRaisesRegex(g.Drift, DRIFT):
                        step()
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), before)
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.parent, 'no commit was made')
        self.assertEqual(self.tool.commit()['state'], 'verified', 'the same interpreter carries on')

    def test_reading_and_withdrawing_still_work_on_another_interpreter(self):
        self.approved()
        with drifted():
            self.assertEqual(self.tool.inspect()['state'], 'approved')
            result = self.tool.abandon('the interpreter changed')
        self.assertEqual(result['state'], 'idle')
        self.assertIn('abandoned', result)
        self.assertEqual(self.git('diff', '--cached', '--name-only'), '')

    def test_a_packet_sealed_before_the_pin_existed_is_not_compared(self):
        packet = self.prepare()
        del packet['interpreter']
        self.tool.save(packet)
        with drifted():
            self.assertEqual(self.tool.inspect()['state'], 'prepared')
            self.assertEqual(self.tool.assert_pinned(self.tool.load()), packet)

    def test_the_landing_record_keeps_the_pin_and_the_integration_and_the_carry_read_it(self):
        self.approved()
        committed = self.tool.commit()
        self.tool.finish()
        landing = ea.read_landing(self.tool)
        self.assertEqual(landing['interpreter'], g.fingerprint())
        self.assertEqual(landing['result_sha'], committed['result_sha'])
        ea.assert_landing_pin(self.tool)
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                ea.assert_landing_pin(self.tool)
        landing.pop('interpreter')
        ea.write_landing(self.tool, None, landing)
        with drifted():
            ea.assert_landing_pin(self.tool)  # a record made before the pin existed carries none

    def test_a_worktree_with_no_landing_record_has_nothing_to_compare(self):
        with drifted():
            ea.assert_landing_pin(self.tool)

    def test_the_pin_of_a_go_is_the_first_previews_also_after_a_move(self):
        body = ea.unsign(self.tool, self.go(self.preview()))
        self.assertEqual(ea.go_pin(body), g.fingerprint())
        first = another()
        moved = {'preview': {'interpreter': g.fingerprint()}, 'rebound': {'original': {'preview': {'interpreter': first}}}}
        self.assertEqual(ea.go_pin(moved), first)
        self.assertIsNone(ea.go_pin({'preview': {}}))


class WorktreePinTest(unittest.TestCase):
    create, prepared, committed = wt.WorktreeTest.create, wt.WorktreeTest.prepared, wt.WorktreeTest.committed
    edit, go_record, previewed, save, advance = (wt.GoCarryTest.edit, wt.GoCarryTest.go_record, wt.GoCarryTest.previewed,
                                                 wt.GoCarryTest.save, wt.GoCarryTest.advance)
    LINES = wt.GoCarryTest.LINES
    setUp = wt.GoCarryTest.setUp

    def test_integration_refuses_before_it_takes_the_lock_when_the_interpreter_changed(self):
        t, sha = self.committed()
        self.assertEqual(ea.read_landing(t)['interpreter'], g.fingerprint())
        main = self.flow.main.head()
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                self.flow.integrate(self.path, self.owner, main, sha)
        self.assertEqual(self.flow.main.head(), main, 'main did not move')
        self.assertFalse(local_state.lifecycle_lock(self.flow.main.common).exists(), 'the lifecycle lock was never taken')
        self.flow.integrate(self.path, self.owner, main, sha)
        self.assertEqual(self.flow.main.head(), sha)

    def test_the_carry_of_an_approval_refuses_at_its_start(self):
        t, sha = self.committed()
        main = self.flow.main.head()
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                self.flow.carry(self.path, self.owner, main, sha, 'diff_bound_approval')
        self.assertEqual(c.Checkpoint(self.path).head(), sha)

    def test_the_carry_of_a_go_refuses_at_its_start_and_a_carried_go_keeps_the_first_pin(self):
        t, go = self.previewed()
        main = self.advance()
        out = Path(self.f.temp.name) / 'carried.json'
        status = t.status()
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                self.flow.carry_go(self.path, self.owner, main, go, self.f.message, None, out, 'diff_bound_approval')
        self.assertFalse(out.exists())
        self.assertEqual((t.head(), t.status()), (self.base, status), 'the worktree was not moved')
        self.flow.carry_go(self.path, self.owner, main, go, self.f.message, None, out, 'diff_bound_approval')
        body = ea.unsign(t, json.loads(out.read_text()))
        self.assertEqual(body['preview']['interpreter'], g.fingerprint())
        self.assertEqual(ea.go_pin(body), g.fingerprint())

    def test_a_reconciliation_refuses_when_the_interpreter_changed(self):
        t, packet = self.prepared()
        main = self.advance()
        with drifted():
            with self.assertRaisesRegex(g.Drift, DRIFT):
                self.flow.reconcile(self.path, self.owner, self.base, main)
        self.assertEqual(t.load()['parent'], self.base)


if __name__ == '__main__':
    unittest.main()
