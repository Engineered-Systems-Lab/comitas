"""The last-preview record of a mission, in a disposable repository with a real linked mission worktree.

`MissionCase` is also the fixture of the Job lookup tests (`governance/tests/coordination/test_job_where.py`).
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import patch

from governance.tests.lifecycle import test_early_approval as fixtures
from governance.tools.lifecycle import early_approval as ea
from governance.tools.lifecycle import preview_record
from governance.tools.support import mission_layout
from governance.tools.worktrees import claims
import checkpoint as c

OWNER = 'm1'
HANDLE = 'job_demo'


class MissionCase(unittest.TestCase):
    """A repository `repo` with a mission worktree `tx/m1` laid out as the worktree workflow lays it out."""

    def setUp(self):
        self.base = fixtures.EarlyApprovalTest('setUp')
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.repo = self.base.root
        with (self.repo / '.gitignore').open('a', newline='\n') as stream:
            stream.write('/.local/\n')
        claims._noticed.clear()
        self.layout = mission_layout.layout(self.repo, OWNER)
        self.work = Path(self.layout['worktree'])
        self.review = Path(self.layout['review'])
        self.work.parent.mkdir(parents=True)
        self.git('worktree', 'add', '--lock', '--reason', 'transaction:' + OWNER, '-b', 'tx/' + OWNER, str(self.work), 'HEAD')
        self.review.mkdir(parents=True)
        (self.work / 'a.txt').write_text('new\n')
        self.tool = c.Checkpoint(self.work)
        self.parent = self.base.parent
        self.counter = 0

    def git(self, *args, cwd=None):
        return subprocess.check_output(['git', '-C', str(cwd or self.repo), *args], stderr=subprocess.PIPE).decode().strip()

    def go(self, result):
        """The agent's recording of a go at this preview, validated by the tool like the command line does."""
        body = result['body']
        consent = {'source': 'active-task-user', 'conversation_id': 'disposable-active-task', 'user_message_id': 'turn-1',
                   'channel': 'text-token', 'response': 'go', 'preview_id': body['id'],
                   'preview_sha256': ea.digest(ea.canonical(body))}
        return ea.record_go(self.tool, result, consent)

    def preview(self, handle=HANDLE, record=True, manifest=('a.txt',)):
        """A real preview of the mission; with `record`, the way the command line writes it, output file included."""
        self.counter += 1
        result = self.tool.preview(list(manifest), self.base.message, None, self.parent, 'refs/heads/tx/' + OWNER, handle)
        output = self.review / ('preview-%d.json' % self.counter)
        readable = output.with_suffix('.md')
        ea.write_new(output, ea.canonical(result))
        ea.write_new(readable, b'preview\n')
        note = preview_record.write(self.tool, result, output, readable, handle) if record else None
        return result, output, note


class PreviewRecordTest(MissionCase):
    def test_a_preview_leaves_one_authenticated_record_in_the_review_folder(self):
        result, output, note = self.preview()
        self.assertIsNone(note)
        body = preview_record.load(self.tool, self.review / preview_record.FILE)
        self.assertEqual((body['preview_id'], body['tree'], body['parent'], body['owner'], body['job_handle']),
                         (result['body']['id'], result['body']['tree'], self.parent, OWNER, HANDLE))
        self.assertEqual((Path(body['output']), body['go']), (output.resolve(), None))

    def test_a_new_preview_replaces_the_pointer_and_adds_a_history_record_and_nothing_else_is_written_beside_them(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        body = preview_record.load(self.tool, self.review / preview_record.FILE)
        self.assertNotEqual(first['body']['id'], second['body']['id'])
        self.assertEqual(body['preview_id'], second['body']['id'])
        self.assertEqual(sorted(p.name for p in self.review.iterdir()),
                         sorted(['last-preview.json', 'preview-1.json', 'preview-1.md', 'preview-2.json', 'preview-2.md',
                                 'history-%s.json' % first['body']['id'], 'history-%s.json' % second['body']['id']]))

    def test_every_preview_leaves_an_authenticated_history_record_in_the_mission_s_order(self):
        results = [self.preview()[0]['body'] for _ in range(3)]
        entries, problems = preview_record.history(self.tool, self.review)
        self.assertEqual(problems, [])
        self.assertEqual([(e['seq'], e['preview_id'], e['tree'], e['parent'], e['branch'], e['owner'], e['job_handle']) for e in entries],
                         [(n, r['id'], r['tree'], self.parent, 'refs/heads/tx/' + OWNER, OWNER, HANDLE) for n, r in enumerate(results, 1)])
        self.assertEqual(preview_record.load(self.tool, self.review / preview_record.FILE)['preview_id'], results[2]['id'])  # the pointer is still the newest
        body = results[0]
        preview_record.add_history(self.tool, self.review, body, HANDLE)  # recording a preview again keeps its place
        self.assertEqual([e['seq'] for e in preview_record.history(self.tool, self.review)[0]], [1, 2, 3])

    def test_a_history_record_that_was_edited_or_is_not_what_its_name_says_is_reported_and_left_out(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        path = preview_record.history_path(self.review, first['body']['id'])
        envelope = json.loads(path.read_bytes())
        envelope['body']['tree'] = '0' * 40
        path.write_bytes(ea.canonical(envelope))
        (self.review / ('history-%s.json' % ('a' * 32))).write_bytes(preview_record.history_path(self.review, second['body']['id']).read_bytes())
        (self.review / 'history-notes.json').write_bytes(b'{}')
        entries, problems = preview_record.history(self.tool, self.review)
        self.assertEqual([e['preview_id'] for e in entries], [second['body']['id']])
        self.assertEqual(len(problems), 3)
        self.assertTrue(any('does not follow the pattern' in problem for problem in problems))

    def test_a_preview_made_before_the_history_has_none_and_the_next_one_starts_it(self):
        self.preview()
        for path in self.review.glob('history-*.json'):
            path.unlink()
        self.assertEqual(preview_record.history(self.tool, self.review), ([], []))
        second, _, _ = self.preview()
        self.assertEqual([(e['seq'], e['preview_id']) for e in preview_record.history(self.tool, self.review)[0]], [(1, second['body']['id'])])

    def test_an_output_named_like_a_history_record_is_left_as_the_preview_wrote_it(self):
        result = self.tool.preview(['a.txt'], self.base.message, None, self.parent, 'refs/heads/tx/' + OWNER)
        output = preview_record.history_path(self.review, result['body']['id'])
        ea.write_new(output, ea.canonical(result))
        before = output.read_bytes()
        self.assertIn('reserved name', preview_record.write(self.tool, result, output, self.review / 'x.md', None))
        self.assertEqual(output.read_bytes(), before)

    def test_a_go_is_added_to_the_record_of_its_own_preview_only(self):
        result, _, _ = self.preview()
        go = self.go(result)
        go_file = self.review / 'go.json'
        self.assertIsNone(preview_record.attach_go(self.tool, result['body'], go, go_file))
        body = preview_record.load(self.tool, self.review / preview_record.FILE)
        self.assertEqual((body['go']['file'], body['go']['go_id']), (str(go_file.resolve()), ea.go_id(go['body'])))
        newer, _, _ = self.preview()
        self.assertIn('for another preview', preview_record.attach_go(self.tool, result['body'], go, go_file))
        self.assertIsNone(preview_record.load(self.tool, self.review / preview_record.FILE)['go'])
        self.assertEqual(newer['body']['id'], preview_record.load(self.tool, self.review / preview_record.FILE)['preview_id'])

    def test_a_go_for_a_preview_without_a_record_says_so_and_writes_nothing(self):
        result, _, _ = self.preview(record=False)
        go = self.go(result)
        self.assertIn('has none', preview_record.attach_go(self.tool, result['body'], go, self.review / 'go.json'))
        self.assertFalse((self.review / preview_record.FILE).exists())

    def test_an_edited_record_or_one_of_another_checkout_does_not_verify(self):
        self.preview()
        path = self.review / preview_record.FILE
        envelope = json.loads(path.read_bytes())
        envelope['body']['tree'] = '0' * 40
        path.write_bytes(ea.canonical(envelope))
        with self.assertRaises(ValueError):
            preview_record.load(self.tool, path)

    def test_without_a_review_folder_or_a_mission_branch_no_record_is_made_and_the_note_says_why(self):
        shutil.rmtree(self.review)
        result = self.tool.preview(['a.txt'], self.base.message, None, self.parent, 'refs/heads/tx/' + OWNER)
        self.assertIn('review folder', preview_record.write(self.tool, result, self.work / 'o.json', self.work / 'o.md', None))
        self.assertFalse(self.review.exists())
        elsewhere = {'body': dict(result['body'], branch='refs/heads/main')}
        self.assertIn('not a mission branch', preview_record.write(self.tool, elsewhere, 'o.json', 'o.md', None))

    def test_an_output_named_like_the_record_is_left_as_the_preview_wrote_it(self):
        result = self.tool.preview(['a.txt'], self.base.message, None, self.parent, 'refs/heads/tx/' + OWNER)
        output = self.review / preview_record.FILE
        ea.write_new(output, ea.canonical(result))
        before = output.read_bytes()
        note = preview_record.write(self.tool, result, output, self.review / 'x.md', None)
        self.assertIn('reserved name', note)
        self.assertEqual(output.read_bytes(), before)
        go = self.go(result)
        self.assertIn('reserved name', preview_record.attach_go(self.tool, result['body'], go, output))
        self.assertEqual(output.read_bytes(), before)

    def test_a_held_records_lock_makes_a_preview_and_a_go_write_nothing_and_say_why_and_is_left_alone(self):
        first, _, _ = self.preview()
        go = self.go(first)
        lock = self.review / preview_record.LOCK
        lock.write_bytes(b'')
        before = {p.name: p.read_bytes() for p in self.review.iterdir()}
        with patch.object(preview_record, 'WAIT_SECONDS', 0):
            _, _, note = self.preview()
            self.assertIn('records lock', note)
            self.assertIn('records lock', preview_record.attach_go(self.tool, first['body'], go, self.review / 'go.json'))
        self.assertTrue(lock.exists(), 'a lock that is not ours is never removed')
        after = {p.name: p.read_bytes() for p in self.review.iterdir() if p.name not in ('preview-2.json', 'preview-2.md')}
        self.assertEqual(after, before)  # no pointer and no history record was written
        lock.unlink()
        second, _, note = self.preview()
        self.assertIsNone(note)
        self.assertFalse(lock.exists())
        self.assertEqual([(e['seq'], e['preview_id']) for e in preview_record.history(self.tool, self.review)[0]],
                         [(1, first['body']['id']), (2, second['body']['id'])])

    def test_a_preview_waits_for_the_lock_and_writes_once_it_is_free(self):
        lock = self.review / preview_record.LOCK
        lock.write_bytes(b'')
        waits = []

        def released(seconds):  # the holder (a review's save) finishes during the first wait
            waits.append(seconds)
            lock.unlink()
        with preview_record.records_lock(self.review, 5, sleep=released):
            self.assertTrue(lock.exists())
        self.assertEqual((waits, lock.exists()), ([preview_record.POLL_SECONDS], False))

    def test_a_history_file_nested_too_deeply_is_refused_alone_and_the_next_preview_still_records(self):
        first, _, _ = self.preview()
        (self.review / ('history-%s.json' % ('d' * 32))).write_text('[' * 200000)
        entries, problems = preview_record.history(self.tool, self.review)
        self.assertEqual(([e['preview_id'] for e in entries], problems),
                         ([first['body']['id']], ['history-%s.json: nested too deeply to be parsed' % ('d' * 32)]))
        second, _, note = self.preview()
        self.assertIsNone(note)
        self.assertEqual([(e['seq'], e['preview_id']) for e in preview_record.history(self.tool, self.review)[0]],
                         [(1, first['body']['id']), (3, second['body']['id'])])  # the refused file keeps a number of its own

    def test_a_refused_newest_history_record_keeps_its_number_and_the_next_preview_comes_after_it(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        preview_record.history_path(self.review, second['body']['id']).write_text('[' * 200000)
        third, _, note = self.preview()
        self.assertIsNone(note)
        entries, problems = preview_record.history(self.tool, self.review)
        self.assertEqual([(e['seq'], e['preview_id']) for e in entries], [(1, first['body']['id']), (3, third['body']['id'])])
        self.assertEqual(len(problems), 1)  # round 2 stays a round, unavailable, and nobody takes its number

    def test_a_refused_record_older_than_the_newest_verified_one_opens_no_new_gap(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        old = preview_record.history_path(self.review, first['body']['id'])
        old.write_text('[' * 200000)
        newest = preview_record.history_path(self.review, second['body']['id']).stat().st_mtime
        os.utime(old, (newest - 60, newest - 60))  # refused, and older than round 2, which verifies
        numbers = [self.preview()[0]['body']['id'] for _ in range(2)]
        self.assertEqual([(e['seq'], e['preview_id']) for e in preview_record.history(self.tool, self.review)[0]],
                         [(2, second['body']['id']), (3, numbers[0]), (4, numbers[1])])

    def test_a_refused_newest_record_keeps_its_number_when_an_older_file_is_missing(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        third, _, _ = self.preview()
        preview_record.history_path(self.review, first['body']['id']).unlink()  # round 1's file is gone, round 2 verifies
        preview_record.history_path(self.review, third['body']['id']).write_text('[' * 200000)  # and round 3 is refused
        fourth, _, note = self.preview()
        self.assertIsNone(note)
        self.assertEqual([(e['seq'], e['preview_id']) for e in preview_record.history(self.tool, self.review)[0]],
                         [(2, second['body']['id']), (4, fourth['body']['id'])])

    def test_an_output_named_like_the_records_lock_is_named_at_once_and_nothing_waits_for_it(self):
        result = self.tool.preview(['a.txt'], self.base.message, None, self.parent, 'refs/heads/tx/' + OWNER)
        output = self.review / preview_record.LOCK
        ea.write_new(output, ea.canonical(result))
        with patch.object(preview_record, 'records_lock', side_effect=AssertionError('no wait for its own output')):
            note = preview_record.write(self.tool, result, output, self.review / 'x.md', None)
            self.assertIn('reserved name of the records lock', note)
            self.assertIn('move it', note)
            self.assertIn('reserved name of the records lock', preview_record.attach_go(self.tool, result['body'], self.go(result), output))
        self.assertFalse((self.review / preview_record.FILE).exists())

    def test_the_layout_the_record_uses_equals_the_lifecycle_guards(self):
        from governance.tools.worktrees import guard
        self.assertEqual(guard.layout(self.repo, OWNER), self.layout)
        with self.assertRaises(ValueError):
            mission_layout.layout(self.repo, '_review')


if __name__ == '__main__':
    unittest.main()
