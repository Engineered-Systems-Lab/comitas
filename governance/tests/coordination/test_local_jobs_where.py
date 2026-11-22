"""The read-only lookup from a Job handle to its live work, on a disposable repository with a real mission worktree."""
import io
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

from governance.tests.lifecycle.test_early_approval_preview_record import HANDLE, OWNER, MissionCase
from governance.tools import runtime_guard
from governance.tools.coordination import job_where, local_jobs
from governance.tools.lifecycle import early_approval as ea
from governance.tools.lifecycle import preview_record
from governance.tools.support import local_state
from governance.tools.worktrees import claims


def snapshot(root):
    """Every path under the repository folder, its container and its Git directory, with size: a read must change none."""
    found = {}
    for base in (root, root.parent):
        for path in sorted(base.rglob('*')):
            if '.lock' in path.name or path.name.endswith(('-shm', '-wal')):  # SQLite's own index files of a read
                continue
            try:
                found[str(path)] = path.stat().st_size if path.is_file() else -1
            except OSError:
                found[str(path)] = -2
    return found


class LookupTest(MissionCase):
    def where(self, handle=HANDLE):
        return job_where.where(self.repo, handle)

    def mission(self, handle=HANDLE):
        [found] = self.where(handle)['missions']
        return found

    def test_a_handle_with_no_mission_says_so_and_reads_nothing_into_existence(self):
        before = snapshot(self.repo)
        result = self.where('job_nobody')
        self.assertEqual(result['missions'], [])
        self.assertIn('no mission is claimed under this handle', result['cannot_find'][0])
        self.assertEqual(result['job']['found'], False)
        self.assertEqual(snapshot(self.repo), before)

    def test_a_live_mission_is_found_by_its_preview_claim_with_its_worktree_branch_and_review_folder(self):
        result, output, _ = self.preview()
        before = snapshot(self.repo)
        found = self.mission()
        self.assertEqual(snapshot(self.repo), before, 'the lookup wrote something')
        self.assertEqual((found['owner'], found['state']), (OWNER, 'live'))
        self.assertEqual(found['branch'], {'ref': 'refs/heads/tx/' + OWNER, 'exists': True, 'tip': self.parent})
        self.assertEqual(Path(found['worktree']['path']), self.work.resolve())
        self.assertEqual(found['worktree']['uncommitted_paths'], 1)
        self.assertEqual(Path(found['review_folder']['path']), self.review)
        self.assertEqual(found['base']['commits_ahead_of_main'], 0)
        self.assertEqual(found['claims']['open'][0]['kind'], 'paths')
        preview = found['preview']
        self.assertEqual((preview['found'], preview['id'], preview['tree'], preview['parent']),
                         (True, result['body']['id'], result['body']['tree'], self.parent))
        self.assertEqual((Path(preview['file']['path']), preview['file']['exists'], preview['file']['matches_record']),
                         (output.resolve(), True, True))
        self.assertTrue(preview['parent_is_worktree_head'])
        self.assertEqual(preview['go'], {'recorded': False})
        self.assertEqual(found['landing']['found'], False)

    def test_a_go_shows_on_the_last_preview_and_a_re_preview_clears_it(self):
        first, _, _ = self.preview()
        preview_record.attach_go(self.tool, first['body'], self.go(first), self.review / 'go.json')
        self.assertTrue(self.mission()['preview']['go']['recorded'])
        second, _, _ = self.preview()
        found = self.mission()['preview']
        self.assertEqual((found['id'], found['go']), (second['body']['id'], {'recorded': False}))

    def test_a_preview_made_before_the_record_existed_is_reported_as_having_no_record(self):
        self.preview(record=False)
        found = self.mission()
        self.assertEqual(found['preview']['found'], False)
        self.assertIn('no preview record was found', found['preview']['reason'])
        self.assertIn('Nothing is guessed', found['preview']['reason'])

    def test_an_edited_record_is_refused_and_its_content_is_not_used(self):
        self.preview()
        path = self.review / preview_record.FILE
        envelope = json.loads(path.read_bytes())
        envelope['body']['tree'] = 'f' * 40
        path.write_bytes(ea.canonical(envelope))
        preview = self.mission()['preview']
        self.assertEqual(preview['found'], False)
        self.assertIn('not authentic', preview['reason'])
        self.assertNotIn('f' * 40, json.dumps(preview))

    def test_findings_files_are_listed_with_their_preview_and_only_the_last_one_is_current(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        old, new = first['body']['id'], second['body']['id']
        for name in ('review-%s.md' % old, 'review-%s.md' % new, 'review-notes.md'):
            (self.review / name).write_text('findings\n')
        status = {item['name']: (item['preview_id'], item['status']) for item in self.mission()['findings']['files']}
        self.assertEqual(status, {'review-%s.md' % old: (old, 'for an older preview, not current'),
                                  'review-%s.md' % new: (new, 'for the last preview'),
                                  'review-notes.md': (None, 'name does not follow the pattern')})

    def test_response_files_are_paired_with_their_findings_by_preview_id(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        old, new = first['body']['id'], second['body']['id']
        orphan = 'c' * 32
        for name in ('review-%s.md' % old, 'review-%s.md' % new, 'response-%s.md' % old, 'response-%s.md' % orphan,
                     'response-x.md'):
            (self.review / name).write_text('text\n')
        findings = self.mission()['findings']
        by_name = {item['name']: (item['response'], item['response_status']) for item in findings['files']}
        self.assertEqual(by_name, {'review-%s.md' % old: ('response-%s.md' % old, 'answered'),
                                   'review-%s.md' % new: (None, 'no response yet')})
        self.assertEqual({(r['name'], r['status']) for r in findings['responses']},
                         {('response-%s.md' % orphan, 'no findings file for this preview'),
                          ('response-x.md', 'name does not follow the pattern')})
        self.assertIn('no response yet', job_where.render_text(self.where()))

    def test_findings_are_never_called_current_when_the_last_preview_is_unknown(self):
        self.preview(record=False)
        (self.review / ('review-' + 'a' * 32 + '.md')).write_text('findings\n')
        [item] = self.mission()['findings']['files']
        self.assertEqual(item['status'], 'last preview unknown')

    def test_an_open_transaction_is_reported_with_its_state(self):
        self.preview()
        self.assertEqual(self.mission()['transaction']['found'], False)
        packet = {'id': 'abc', 'state': 'prepared', 'parent': self.parent, 'tree': 't' * 40, 'repo': self.tool.identity()}
        self.tool.save(packet)
        found = self.mission()['transaction']
        self.assertEqual((found['found'], found['state'], found['id']), (True, 'prepared', 'abc'))

    def test_the_landing_record_of_a_standing_worktree_gives_commit_parent_tree_and_branch(self):
        self.preview()
        ea.write_landing(self.tool, {}, {'result_sha': 'c' * 40, 'parent': self.parent, 'tree': 't' * 40,
                                         'branch': 'refs/heads/tx/' + OWNER, 'subject': 'Subject', 'approval_path': 'sealed'})
        landing = self.mission()['landing']
        self.assertEqual((landing['found'], landing['commit'], landing['parent'], landing['branch'], landing['on_main']),
                         (True, 'c' * 40, self.parent, 'refs/heads/tx/' + OWNER, False))
        self.assertIn('landing record', landing['source'])

    def test_after_the_removal_the_landing_comes_from_the_claims_database_and_git_and_nothing_else_is_guessed(self):
        self.preview()
        self.git('add', 'a.txt', cwd=self.work)
        self.git('commit', '-m', 'Landed work', cwd=self.work)
        commit = self.git('rev-parse', 'HEAD', cwd=self.work)
        self.git('checkout', '--', 'a.txt')
        self.git('merge', '--ff-only', 'tx/' + OWNER)
        claims.landed(self.tool.common, OWNER, HANDLE, commit)
        self.git('worktree', 'unlock', str(self.work))
        self.git('worktree', 'remove', '--force', str(self.work))
        self.git('branch', '-d', 'tx/' + OWNER)
        shutil.rmtree(self.review)
        found = self.mission()
        self.assertEqual((found['state'], found['branch']['exists']), ('gone', False))
        landing = found['landing']
        self.assertEqual((landing['commit'], landing['parent'], landing['subject'], landing['branch'], landing['on_main']),
                         (commit, self.parent, 'Landed work', 'refs/heads/tx/' + OWNER, True))
        self.assertIn('claims database', landing['source'])
        self.assertEqual(found['claims']['last_closed']['reason'], 'landed')
        self.assertEqual(found['preview']['found'], False)
        self.assertIn('review folder does not exist', found['preview']['reason'])

    def test_it_answers_while_a_lifecycle_operation_runs_and_says_so(self):
        self.preview()
        lock = local_state.lifecycle_lock(self.tool.common)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('')
        result = self.where()
        self.assertTrue(result['lifecycle_operation']['active'])
        self.assertIn('stale or torn', result['lifecycle_operation']['note'])
        self.assertIn('stale or torn', job_where.render_text(result))
        self.assertEqual(result['missions'][0]['preview']['found'], True)

    def test_another_handle_on_the_same_mission_is_named_never_merged(self):
        self.preview()
        claims.open_window(self.tool.common, OWNER, 'prepare', 'validating', job_handle='job_other')
        found = self.mission()
        self.assertEqual(found['claims']['also_named_under_other_handles'], ['job_other'])
        self.assertEqual(self.where('job_other')['missions'][0]['owner'], OWNER)

    def test_the_text_form_names_the_mission_the_preview_and_what_could_not_be_found(self):
        self.preview()
        text = job_where.render_text(self.where())
        self.assertIn('Mission %s (live)' % OWNER, text)
        self.assertIn('last preview', text)
        self.assertIn('no go recorded', text)
        self.assertIn('Cannot find: ' + 'no mission', job_where.render_text(self.where('job_nobody')))


class CommandTest(MissionCase):
    def run_where(self, *extra):
        out = io.TextIOWrapper(io.BytesIO(), encoding='utf-8', newline='')
        with patch.object(sys, 'argv', ['local_jobs.py', '--repo', str(self.repo), 'where', *extra]), \
                patch.object(sys, 'stdout', out):
            local_jobs.main()
            out.flush()
            return out.buffer.getvalue().decode('utf-8')

    def test_the_command_prints_the_text_form_then_the_json_and_json_only_prints_the_json_alone(self):
        self.preview()
        both = self.run_where(HANDLE)
        self.assertTrue(both.startswith('Job %s' % HANDLE) and '\r' not in both)
        text, _, data = both.partition('\n\n{')
        self.assertEqual(json.loads('{' + data)['missions'][0]['owner'], OWNER)
        self.assertEqual(json.loads(self.run_where(HANDLE, '--json-only'))['handle'], HANDLE)

    def test_an_invalid_handle_is_refused(self):
        with self.assertRaises(ValueError):
            self.run_where('Not A Handle')

    def test_the_command_is_registered_as_a_read_in_the_runtime_guard(self):
        mode, sub = runtime_guard.mode_of('local_jobs', ['--repo', '.', 'where', HANDLE])
        self.assertEqual((mode, sub), (runtime_guard.READ, 'where'))
        lock = local_state.lifecycle_lock(self.tool.common)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text('')
        env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(runtime_guard.enter('local_jobs', ['where', HANDLE], root=self.repo)['mode'], runtime_guard.READ)
            with self.assertRaises(SystemExit):
                runtime_guard.enter('local_jobs', ['note', HANDLE], root=self.repo)


if __name__ == '__main__':
    unittest.main()
