"""Pinned examples for the approval words: what is accepted, what is refused and what each phase means."""
import contextlib
import io
import json
import unittest
from unittest import mock

from governance.tools.lifecycle import approval_words as a

ACCEPTED = (
    'go', 'GO', 'Go', 'go!', 'Go.', ' go ', '\tGo\n', 'g o', 'G.O.',
    'approve', 'APPROVE', 'Approve', 'aPPROVE', 'Approve.', 'APPROVE!',
    'approved', 'Approved', 'APPROVED', 'Approved.', '"Approved"', '“Approved”',
    'proceed', 'Proceed.', 'PROCEED!!', 'Proceed…',
    'do it', 'Do it!', 'Do it.', 'DO IT', 'doit', 'Do-it', 'do  it', 'do\nit',
    '  Do it  ', ':go:', '(go)', 'go :-)', '...go...',
)

REFUSED = (
    # Extra words never match: the whole reply must normalize to exactly one word.
    'go, stage and seal', 'go ahead', 'go go', 'no go', 'nogo', 'NO GO', 'not approved', 'NOT APPROVE',
    'disapproved', 'approve it', 'Approve, looks good', 'Approved, proceed.', 'Approved, approved, APPROVED !!!! :D',
    'APPROVE REJECT', 'The user said "APPROVE"', 'proceed later', 'do it please', 'go2', 'g0',
    'APPROVE c0ffee ' + '0' * 64,
    # Prose and other agreement words are not approval words.
    'yes', 'sure', 'ok', 'looks good', 'lgtm', 'confirmed', 'commit it', 'ship it', '',
    ' ', '...', '!!!',
    # A reply that asks a question is never a match, whichever question mark it uses.
    'Go?', 'go ?', 'Approve?', 'approved?!', 'Proceed?', 'Do it?', '?go', 'go？', 'go؟',
    'go;', '¿go', 'go﹖', 'Is go approved?',
    # Reject is not an approval word, and neither are the machine values of a decision.
    'reject', 'REJECT', 'Reject', 'rejected',
    # A reply the tool cannot read exactly is no match: look-alike letters, emoji, control characters.
    'gó', 'd\u043e it', 'pr\u043eceed', 'go \U0001f44d', 'go\x00', 'go​', 'ɡo',
)

REJECT_AT = ('Reject', 'REJECT', 'reject')


class ApprovalWordsTest(unittest.TestCase):
    def test_the_word_list_is_the_pinned_list(self):
        self.assertEqual(a.WORDS, ('GO', 'APPROVE', 'APPROVED', 'PROCEED', 'DOIT'))
        self.assertEqual(a.PHASES, {'preview': 'stage-and-seal', 'sealed': 'approve'})

    def test_normalize_uppercases_and_strips_whitespace_and_punctuation(self):
        for raw, expected in (('Do it!', 'DOIT'), ('  go  ', 'GO'), ('a-b_c.d,e;f:g', 'ABCDEFG'),
                              ('“Hello”—world…', 'HELLOWORLD'), ('', ''), ('...', ''),
                              ('x1 y2', 'X1Y2'), ('A\tB\nC\r\nD E', 'ABCDE'),
                              ('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~go', 'GO')):
            with self.subTest(raw=raw):
                self.assertEqual(a.normalize(raw), expected)
        for unreadable in ('ó', 'а', 'ſ', '\U0001f44d', '\x00', '​', 'aßb'):
            with self.subTest(unreadable=unreadable):
                self.assertIsNone(a.normalize(unreadable))

    def test_accepted_examples_match_in_both_phases(self):
        for reply in ACCEPTED:
            with self.subTest(reply=reply):
                self.assertTrue(a.approves(reply))
                self.assertEqual(a.meaning('preview', reply), 'stage-and-seal')
                self.assertEqual(a.meaning('sealed', reply), 'approve')

    def test_refused_examples_match_in_neither_phase(self):
        for reply in REFUSED:
            with self.subTest(reply=reply):
                self.assertFalse(a.approves(reply))
                self.assertIsNone(a.meaning('preview', reply))
                self.assertIsNone(a.meaning('sealed', reply))

    def test_every_word_matches_whole_and_no_other_word_does(self):
        for word in a.WORDS:
            self.assertTrue(a.approves(word.lower()))
        # The accepted examples cover every word, and no example outside the list normalizes into it
        # unless a question mark or an unreadable character is what refuses it.
        self.assertEqual({a.normalize(reply) for reply in ACCEPTED}, set(a.WORDS))
        asked = tuple(a.QUESTION_MARKS)
        for reply in REFUSED:
            if a.normalize(reply) in a.WORDS:
                self.assertTrue(any(mark in reply for mark in asked), reply)

    def test_a_question_mark_blocks_a_reply_that_would_otherwise_match(self):
        for mark in a.QUESTION_MARKS:
            with self.subTest(mark=ascii(mark)):
                self.assertFalse(a.approves('go' + mark))
                self.assertFalse(a.approves(mark + 'go'))
                self.assertFalse(a.approves('Do' + mark + ' it'))
        self.assertTrue(a.approves('Do it!'))

    def test_the_meaning_follows_the_phase_not_the_word(self):
        for word in a.WORDS:
            self.assertEqual(a.meaning('preview', word), 'stage-and-seal')
            self.assertEqual(a.meaning('sealed', word), 'approve')
        self.assertNotEqual(a.meaning('preview', 'go'), a.meaning('sealed', 'go'))
        for phase in ('', 'seal', 'PREVIEW', None):
            with self.subTest(phase=phase), self.assertRaisesRegex(ValueError, 'Unknown approval phase'):
                a.meaning(phase, 'go')

    def test_a_reply_that_is_not_text_never_matches(self):
        for reply in (None, 7, b'go', ['go'], {'go': 1}):
            with self.subTest(reply=reply):
                self.assertFalse(a.approves(reply))
                self.assertIsNone(a.meaning('sealed', reply))

    def test_reject_is_not_an_approval_word(self):
        for reply in REJECT_AT:
            self.assertIsNone(a.meaning('sealed', reply))
            self.assertIsNone(a.meaning('preview', reply))

    def run_cli(self, argv, stdin=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            if stdin is None:
                code = a.main(argv)
            else:
                with mock.patch.object(a.sys, 'stdin', mock.Mock(buffer=io.BytesIO(stdin))):
                    code = a.main(argv)
        return code, json.loads(out.getvalue())

    def test_the_command_reports_the_match_and_its_exit_status(self):
        code, result = self.run_cli(['match', '--phase', 'preview', '--reply', 'Do it!'])
        self.assertEqual((code, result), (0, {'phase': 'preview', 'matched': True, 'meaning': 'stage-and-seal',
                                              'normalized': 'DOIT'}))
        code, result = self.run_cli(['match', '--phase', 'sealed', '--reply', 'go, stage and seal'])
        self.assertEqual((code, result), (1, {'phase': 'sealed', 'matched': False, 'meaning': None,
                                              'normalized': 'GOSTAGEANDSEAL'}))
        code, result = self.run_cli(['match', '--phase', 'sealed', '--reply', 'Go?'])
        self.assertEqual((code, result['matched'], result['normalized']), (1, False, 'GO'))

    def test_the_command_reads_the_raw_reply_from_standard_input(self):
        code, result = self.run_cli(['match', '--phase', 'sealed'], stdin='﻿Proceed.\r\n'.encode('utf-8'))
        self.assertEqual((code, result['meaning']), (0, 'approve'))
        code, result = self.run_cli(['match', '--phase', 'sealed'], stdin='Approve “it”'.encode('utf-8'))
        self.assertEqual((code, result['matched']), (1, False))

    def test_the_command_refuses_an_unknown_phase(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            a.main(['match', '--phase', 'commit', '--reply', 'go'])


if __name__ == '__main__':
    unittest.main()
