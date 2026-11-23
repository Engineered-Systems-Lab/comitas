"""The read-only lookup from a Job handle to its live work, on a disposable repository with a real mission worktree."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from governance.tests.lifecycle.test_early_approval_preview_record import HANDLE, OWNER, MissionCase
from governance.tools import runtime_guard
from governance.tools.coordination import job_where, local_jobs
from governance.tools.continuity import artifact_classification as anchors
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

    def test_the_preview_history_lists_each_preview_with_its_findings_and_response_and_marks_the_last(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        old, new = first['body']['id'], second['body']['id']
        (self.review / ('review-%s.md' % old)).write_text('findings\n')
        (self.review / ('response-%s.md' % old)).write_text('response\n')
        before = snapshot(self.repo)
        history = self.mission()['history']
        self.assertEqual(snapshot(self.repo), before, 'the lookup wrote something')
        self.assertEqual((history['found'], history['refused'], history['last_in_history']), (True, [], True))
        self.assertEqual([(e['seq'], e['preview_id'], e['tree'], e['parent'], e['is_last'], e['findings_file'], e['response_file'])
                          for e in history['entries']],
                         [(1, old, first['body']['tree'], self.parent, False, True, True),
                          (2, new, second['body']['tree'], self.parent, True, False, False)])
        text = job_where.render_text(self.where())
        self.assertIn('preview history (2)', text)
        self.assertIn('(last preview); findings no, response no', text)
        self.assertIn('findings yes, response yes', text)

    def test_a_tampered_or_misnamed_history_record_is_listed_as_refused_and_never_used(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        path = self.review / preview_record.history_path(self.review, first['body']['id']).name
        envelope = json.loads(path.read_bytes())
        envelope['body']['tree'] = 'f' * 40
        path.write_bytes(ea.canonical(envelope))
        (self.review / 'history-notes.json').write_text('{}')
        found = self.mission()
        history = found['history']
        self.assertEqual([e['preview_id'] for e in history['entries']], [second['body']['id']])
        self.assertEqual({r['name']: r['status'] for r in history['refused']},
                         {path.name: 'refused, not trusted', 'history-notes.json': 'refused, not trusted'})
        self.assertNotIn('f' * 40, json.dumps(history))
        self.assertIn('history-notes.json: refused, not trusted', job_where.render_text(self.where()))
        self.assertTrue(any('history: ' + path.name + ' refused' in line for line in self.where()['cannot_find']))

    def test_a_worktree_where_git_fails_is_read_through_the_primary_and_the_failure_is_named_never_called_gone(self):
        result, _, _ = self.preview()
        real = local_jobs.run_git
        work = self.work.resolve()

        def refusing(repo, *args):
            if Path(repo).resolve() == work:
                return subprocess.CompletedProcess(['git'], 128, b'', b'fatal: detected dubious ownership in repository' + chr(10).encode() + b'more')
            return real(repo, *args)

        with patch.object(local_jobs, 'run_git', refusing):
            found = self.mission()
            text = job_where.render_text(self.where())
        self.assertEqual(found['state'], 'live')
        self.assertEqual((found['preview']['found'], found['preview']['id']), (True, result['body']['id']))
        self.assertEqual(found['history']['found'], True)
        self.assertIsNone(found['worktree']['uncommitted_paths'])
        self.assertEqual(found['worktree']['uncommitted_paths_reason'],
                         'cannot be read inside the worktree: git failed (exit 128): fatal: detected dubious ownership in repository')
        self.assertIn('uncommitted paths: unknown, cannot be read inside the worktree', text)
        self.assertNotIn('is gone', json.dumps(found['preview']) + json.dumps(found['history']))
        with patch.object(local_jobs, 'run_git', refusing):
            cannot = self.where()['cannot_find']
        self.assertTrue(any('dubious ownership' in line for line in cannot), cannot)

    def test_gone_is_only_a_worktree_that_is_not_registered_or_whose_folder_is_missing_and_a_disagreeing_gitdir_is_refused(self):
        self.preview()
        folder_row = {'worktree': str(self.work.parent / 'nowhere')}
        self.assertEqual(job_where.store_of(self.tool.common, None)[1], 'the worktree is gone: it is not registered')
        self.assertEqual(job_where.store_of(self.tool.common, folder_row)[1], 'the worktree is gone: its folder is missing')
        store, why = job_where.store_of(self.tool.common, {'worktree': str(self.work)})
        self.assertEqual((store, why), (local_state.checkout_store(self.work), None))
        with patch.object(local_state, 'checkout_git_directory', return_value=self.repo / 'elsewhere'):
            store, why = job_where.store_of(self.tool.common, {'worktree': str(self.work)})
        self.assertIsNone(store)
        self.assertIn('differ', why)

    def test_a_store_the_primary_does_not_identify_or_a_linked_store_is_refused_and_the_landing_says_it_cannot_be_read(self):
        self.preview()
        row = {'worktree': str(self.work)}
        with patch.object(job_where, 'registered_gitdir', return_value=None):
            store, why = job_where.store_of(self.tool.common, row)
            found = self.mission()
        self.assertIsNone(store)
        self.assertIn('does not identify a Git directory', why)
        self.assertEqual(found['preview']['found'], False)
        self.assertIn('does not identify', found['preview']['reason'])
        self.assertTrue(found['landing']['reason'].startswith('cannot be read: the primary'), found['landing'])
        self.assertTrue(found['transaction']['reason'].startswith('cannot be read: '))
        with patch.object(local_state, 'indirect', side_effect=lambda path: path.name == 'governance'):
            store, why = job_where.store_of(self.tool.common, row)
        self.assertIsNone(store)
        self.assertIn('link or not a plain folder', why)

    def test_an_access_error_is_reported_as_cannot_be_read_and_never_as_absence(self):
        self.preview()
        real = Path.stat
        targets = {'work': str(self.work.resolve()), 'transaction': anchors.TRANSACTION, 'landing': anchors.LANDING}

        def refusing(selected):
            def stat(path, *args, **kwargs):
                if str(path) == targets['work'] if selected == 'work' else Path(path).name == targets[selected]:
                    raise PermissionError(13, 'Access is denied')
                return real(path, *args, **kwargs)
            return stat

        with patch.object(Path, 'stat', refusing('work')):
            store, why = job_where.store_of(self.tool.common, {'worktree': str(self.work)})
        self.assertIsNone(store)
        self.assertIn('cannot be accessed', why)
        self.assertNotIn('is gone', why)
        for selected, key in (('transaction', 'transaction'), ('landing', 'landing')):
            with patch.object(Path, 'stat', refusing(selected)):
                found = self.mission()[key]
            self.assertEqual(found['found'], False)
            self.assertTrue(found['reason'].startswith('cannot be read: '), found)
        self.assertFalse(job_where.present(self.work / 'no-such-file'))
        self.assertTrue(job_where.present(self.work, folder=True))
        self.assertFalse(job_where.present(self.work / 'a.txt', folder=True))

    def test_an_inaccessible_worktree_folder_a_git_that_cannot_start_and_an_unreadable_findings_file_are_named_in_the_whole_lookup(self):
        first, _, _ = self.preview()
        old = first['body']['id']
        (self.review / ('review-%s.md' % old)).write_text('findings\n')
        real = Path.stat
        work = str(self.work.resolve())
        findings = 'review-%s.md' % old

        def refusing(path, *args, **kwargs):
            if str(path) == work or Path(path).name == findings:
                raise PermissionError(13, 'Access is denied')
            return real(path, *args, **kwargs)

        with patch.object(Path, 'stat', refusing):
            result = self.where()
            found = result['missions'][0]
            text = job_where.render_text(result)
        self.assertIsNone(found['worktree']['folder_exists'])
        self.assertIn('cannot be accessed', found['worktree']['folder_reason'])
        self.assertIn('the worktree folder cannot be accessed', found['worktree']['uncommitted_paths_reason'])
        self.assertNotIn('folder missing', text)
        self.assertIn('(folder cannot be accessed', text)
        self.assertIn('uncommitted paths: unknown', text)
        [entry] = found['findings']['files']
        self.assertEqual(entry['name'], findings)
        self.assertTrue(entry['status'].startswith('cannot be read'), entry['status'])
        real_run = local_jobs.run_git

        def not_started(repo, *args):
            if Path(repo).resolve() == self.work.resolve():
                raise FileNotFoundError(2, 'git not found')
            return real_run(repo, *args)

        with patch.object(local_jobs, 'run_git', not_started):
            found = self.mission()
        self.assertEqual((found['preview']['found'], found['history']['found']), (True, True))
        self.assertIn('git could not be started', found['worktree']['uncommitted_paths_reason'])

    def test_an_unreadable_findings_or_response_file_is_unknown_never_no(self):
        first, _, _ = self.preview()
        old = first['body']['id']
        (self.review / ('review-%s.md' % old)).write_text('findings\n')
        real = Path.stat

        def refusing(path, *args, **kwargs):
            if Path(path).name == 'review-%s.md' % old:
                raise PermissionError(13, 'Access is denied')
            return real(path, *args, **kwargs)

        with patch.object(Path, 'stat', refusing):
            result = self.where()
        [entry] = result['missions'][0]['history']['entries']
        self.assertEqual((entry['findings_file'], entry['response_file']), (None, False))
        self.assertIn('cannot be read', entry['unreadable'][0])
        self.assertIn('findings unknown, response no', job_where.render_text(result))
        self.assertTrue(any('history #1' in line for line in result['cannot_find']))

    def test_an_inaccessible_review_folder_a_directory_named_like_a_response_and_a_too_deeply_nested_history_file(self):
        first, _, _ = self.preview()
        old = first['body']['id']
        (self.review / ('response-%s.md' % old)).mkdir()
        (self.review / ('review-%s.md' % old)).write_text('findings\n')
        findings = self.mission()['findings']
        self.assertEqual([(r['name'], r['status']) for r in findings['responses']], [('response-%s.md' % old, 'not a plain file')])
        self.assertEqual(findings['files'][0]['response_status'], 'no response yet')
        self.assertFalse(self.mission()['history']['entries'][0]['response_file'])
        real = Path.stat
        folder = str(self.review)

        def refusing(path, *args, **kwargs):
            if str(path) == folder:
                raise PermissionError(13, 'Access is denied')
            return real(path, *args, **kwargs)

        with patch.object(Path, 'stat', refusing):
            result = self.where()
        found = result['missions'][0]
        self.assertIsNone(found['review_folder']['exists'])
        text = job_where.render_text(result)
        self.assertNotIn('(missing)', text)
        self.assertIn('cannot be accessed', text)
        (self.review / ('history-%s.json' % ('d' * 32))).write_text('[' * 200000)
        found = self.mission()  # the file is refused alone and the round of the real preview is still listed
        self.assertEqual(found['history']['found'], True)
        self.assertEqual([e['preview_id'] for e in found['history']['entries']], [old])
        self.assertEqual(found['history']['refused'], [{'name': 'history-%s.json' % ('d' * 32), 'status': 'refused, not trusted',
                                                        'reason': 'nested too deeply to be parsed'}])
        self.assertEqual(found['preview']['found'], True)

    def test_a_relative_gitdir_pointer_is_read_from_its_metadata_folder_and_an_unreadable_worktrees_folder_is_named(self):
        self.preview()
        [entry] = [e for e in (self.tool.common / 'worktrees').iterdir()]
        pointer = entry / 'gitdir'
        absolute = pointer.read_text(encoding='utf-8')
        pointer.write_text(os.path.relpath(absolute.strip(), entry).replace(os.sep, '/') + '\n', encoding='utf-8')
        try:
            store, why = job_where.store_of(self.tool.common, {'worktree': str(self.work)})
            self.assertEqual((store, why), (local_state.checkout_store(self.work), None))
        finally:
            pointer.write_text(absolute, encoding='utf-8')
        with patch.object(Path, 'iterdir', side_effect=PermissionError(13, 'Access is denied')):
            store, why = job_where.store_of(self.tool.common, {'worktree': str(self.work)})
        self.assertIsNone(store)
        self.assertIn('worktree data cannot be read', why)

    def test_an_unreadable_response_or_findings_file_is_never_read_as_absence_of_its_partner(self):
        first, _, _ = self.preview()
        second, _, _ = self.preview()
        old, new = first['body']['id'], second['body']['id']
        for name in ('review-%s.md' % old, 'response-%s.md' % old, 'review-%s.md' % new, 'response-%s.md' % new):
            (self.review / name).write_text('text\n')
        real = Path.stat
        refused = {'response-%s.md' % old, 'review-%s.md' % new}

        def refusing(path, *args, **kwargs):
            if Path(path).name in refused:
                raise PermissionError(13, 'Access is denied')
            return real(path, *args, **kwargs)

        with patch.object(Path, 'stat', refusing):
            findings = self.mission()['findings']
        by_name = {item['name']: item.get('response_status') for item in findings['files']}
        self.assertEqual(by_name['review-%s.md' % old], 'unknown: the response response-%s.md cannot be read' % old)
        statuses = {item['name']: item['status'] for item in findings['responses']}
        self.assertEqual(statuses['response-%s.md' % new], 'the findings file for this preview cannot be read')
        self.assertNotIn('no response yet', json.dumps(findings))

    def test_a_readable_git_file_that_names_no_git_directory_refuses_the_store(self):
        self.preview()
        self.assertTrue((self.work / '.git').read_bytes())  # readable; its content is taken as naming no Git directory
        with patch.object(local_state, 'checkout_git_directory', return_value=None):
            store, why = job_where.store_of(self.tool.common, {'worktree': str(self.work)})
        self.assertIsNone(store)
        self.assertIn('names no Git directory', why)

    def test_an_unreadable_preview_output_or_readable_block_is_unknown_never_missing(self):
        _, output, _ = self.preview()
        real = Path.stat
        refused = {output.name, output.with_suffix('.md').name}

        def refusing(path, *args, **kwargs):
            if Path(path).name in refused:
                raise PermissionError(13, 'Access is denied')
            return real(path, *args, **kwargs)

        with patch.object(Path, 'stat', refusing):
            result = self.where()
        preview = result['missions'][0]['preview']
        self.assertEqual((preview['file']['exists'], preview['readable_file']['exists']), (None, None))
        self.assertIn('cannot be read', preview['file']['reason'])
        self.assertNotIn('(missing)', job_where.render_text(result))

    def test_a_response_counts_in_the_history_only_under_its_exact_name(self):
        first, _, _ = self.preview()
        old = first['body']['id']
        (self.review / ('review-%s.md' % old)).write_text('findings\n')
        (self.review / ('response-%s.md' % old.upper())).write_text('response\n')
        found = self.mission()
        [entry] = found['history']['entries']
        self.assertFalse(entry['response_file'])
        self.assertEqual(found['findings']['files'][0]['response_status'], 'no response yet')

    def test_a_preview_before_the_history_existed_has_no_history_and_the_lookup_says_so(self):
        self.preview(record=False)
        history = self.mission()['history']
        self.assertEqual(history['found'], False)
        self.assertIn('no history record was found', history['reason'])
        self.assertIn('preview history: no history record', job_where.render_text(self.where()))

    def test_the_history_is_not_current_when_the_last_preview_is_unreadable(self):
        self.preview()
        (self.review / preview_record.FILE).write_text('not json')
        history = self.mission()['history']
        self.assertEqual((history['found'], history['last_in_history'], history['entries'][0]['is_last']), (True, None, None))

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
