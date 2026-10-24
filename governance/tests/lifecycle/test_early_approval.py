"""The early conditional approval: eligibility, exact binding, fallback, the switch and the landing notice.

Every mutation happens in a disposable Git repository; the real repository is only read.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.lifecycle import approval_words
from governance.tools.continuity import artifact_classification as ac
import checkpoint as c
from governance.tools.lifecycle import early_approval as ea
from governance.tools.lifecycle import review_render
from ..fake_git import FakeGit

# (path, class, profile, boundary, approval-sensitivity), sorted by path.
ROWS = [
    ('a.txt', 'tooling', 'test', '-', '-'),
    ('b.txt', 'tooling', 'test', '-', 'medium'),
    ('docs/*.md', 'governance', 'test', '-', '-'),
    ('h.txt', 'tooling', 'test', '-', 'high'),
    ('m.txt', 'tooling', 'test', '-', 'medium'),
    ('runtime.txt', 'runtime', 'test', 'runtime', '-'),
    ('governance/tools/*.py', 'tooling', 'test', '-', '-'),
    ('project-governance/config/checkpoint-features.toml', 'tooling', 'test', '-', 'high'),
]
ROWS.sort()  # the table parser requires ASCII ordinal order
SWITCH_ON = '# fixture\nearly_approval_reach = "low"\n'
SWITCH_MEDIUM = '# fixture\nearly_approval_reach = "medium"\n'


def table_text():
    lines = ['# Fixture classification', '', ac.HEADER, ac.SEPARATOR]
    lines += ['| `%s` | %s | %s | %s | - | - | - | - | %s |' % row for row in ROWS]
    return '\n'.join(lines) + '\n'


class EarlyApprovalTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                             if not k.startswith('GIT_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='early-approval-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Isolated Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.autocrlf', 'true')
        (self.root / 'governance' / 'tools').mkdir(parents=True, exist_ok=True)
        (self.root / 'governance' / 'config').mkdir(parents=True, exist_ok=True)
        (self.root / ea.FEATURES).parent.mkdir(parents=True, exist_ok=True)
        (self.root / 'governance').mkdir(exist_ok=True)
        (self.root / 'docs').mkdir()
        policy = {'version': 4, 'prohibited_components': ['.git', 'work', 'tmp', '__pycache__'],
                  'required_checks': ['continuity', 'regressions'],
                  'commands': {'continuity': ['{python}', '-B', '-c', 'print("PASS")'],
                               'regressions': ['{python}', '-B', '-c', 'print("tests PASS")']}}
        profiles = {'version': 4, 'profiles': {'test': {
            'checks': [{'id': name, 'argv': argv} for name, argv in policy['commands'].items()],
            'limitations': 'Disposable fixture commands only.', 'runtime_checks': ['regressions']}},
            'impact': {'version': 1, 'invariants': {}, 'symbol_maps': {}},
            'commit_metadata': {'version': 2, 'classes': ['record', 'governance', 'tooling', 'runtime'],
                                'subclasses': []}}
        (self.root / c.TABLE).write_text(table_text(), encoding='utf-8', newline='\n')
        (self.root / c.POLICY).write_text(json.dumps(policy), encoding='utf-8')
        (self.root / c.PROFILES).write_text(json.dumps(profiles), encoding='utf-8')
        (self.root / ea.FEATURES).write_text(SWITCH_ON, encoding='utf-8', newline='\n')
        for name in ('a.txt', 'b.txt', 'h.txt', 'm.txt', 'runtime.txt'):
            (self.root / name).write_text('old\n')
        (self.root / '.gitignore').write_text('ignored/\n__pycache__/\ntmp/\n')
        self.git('add', '--', '.')
        self.git('commit', '-m', 'Initial fixture')
        self.parent = self.git('rev-parse', 'HEAD').strip()
        self.message = Path(self.temp.name) / 'message.txt'
        self.message.write_bytes(b'Exact subject\n\n## Topic\n- Make an edit.\n')
        (self.root / 'a.txt').write_text('new\n')
        self.tool = c.Checkpoint(self.root)
        for name, replacement in (('committed_checkpoint', lambda tool: tool.git(
                'log', '-1', '--format=%H', 'HEAD', '--', 'runtime.txt').decode().strip()),
                                  ('durable_continuity', None)):
            gate = patch.object(c.Checkpoint, name, autospec=True, side_effect=replacement) if replacement \
                else patch.object(c.Checkpoint, name, return_value=None)
            gate.start()
            self.addCleanup(gate.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode()

    def preview(self, manifest=None):
        return self.tool.preview(manifest or ['a.txt'], self.message, None, self.parent, 'refs/heads/main')

    def go(self, envelope, reply='go', turn='fixture-user-turn-1', **override):
        body = envelope['body']
        consent = {'source': 'active-task-user', 'conversation_id': 'disposable-active-task', 'user_message_id': turn,
                   'channel': 'text-token', 'response': reply, 'preview_id': body['id'],
                   'preview_sha256': ea.digest(ea.canonical(body))}
        consent.update(override)
        return ea.record_go(self.tool, envelope, consent)

    def prepare(self, early=None, manifest=None):
        return self.tool.prepare(manifest or ['a.txt'], self.message, 'test', self.parent, 'refs/heads/main',
                                 'Lead developer delegated this exact fixture scope', early_go=early)

    def approved(self):
        go = self.go(self.preview())
        packet = self.prepare(go)
        self.assertEqual(packet['state'], 'approved')
        return go, packet

    # ------------------------------------------------------------------ eligibility
    def test_an_ordinary_manifest_is_eligible_and_names_the_exact_candidate(self):
        body = self.preview()['body']
        self.assertTrue(body['eligible'], body['reasons'])
        self.assertEqual(body['parent'], self.parent)
        self.assertEqual(body['manifest'], ['a.txt'])
        self.assertEqual(body['check_ids'], ['continuity', 'regressions'])
        self.assertEqual((body['switch']['on'], body['switch']['reach']), (True, 'low'))
        packet = self.prepare()
        self.assertEqual(body['tree'], packet['tree'])  # the preview named the very tree that staging produced
        self.assertEqual(body['message_sha256'], packet['message_sha256'])

    def test_the_candidate_tree_holds_a_new_script_as_executable_and_other_files_unchanged(self):
        (self.root / 'new.sh').write_text('#!/bin/sh\n')
        tree = ea.candidate_tree(self.tool, ['new.sh', 'a.txt'])
        modes = {row.split('\t')[1]: row.split()[0] for row in self.git('ls-tree', '-r', tree).splitlines()}
        self.assertEqual((modes['new.sh'], modes['a.txt']), ('100755', '100644'))

    def test_preview_is_read_only(self):
        before = (self.git('status', '--porcelain'), self.git('rev-parse', 'HEAD'), self.git('diff', '--cached'))
        self.preview()
        self.assertEqual((self.git('status', '--porcelain').replace('?? tmp/\n', ''), self.git('rev-parse', 'HEAD'),
                          self.git('diff', '--cached')), (before[0].replace('?? tmp/\n', ''), before[1], before[2]))
        self.assertFalse((self.tool.store / c.TRANSACTION).exists())

    def test_a_medium_path_makes_the_manifest_not_eligible_at_reach_low(self):
        (self.root / 'b.txt').write_text('changed\n')
        body = self.preview(['b.txt'])['body']
        self.assertFalse(body['eligible'])
        self.assertEqual(body['sensitive_paths'], {'b.txt': 'medium'})

    def test_a_high_path_makes_the_manifest_not_eligible(self):
        (self.root / 'h.txt').write_text('changed\n')
        body = self.preview(['h.txt'])['body']
        self.assertFalse(body['eligible'])
        self.assertEqual(body['sensitive_paths'], {'h.txt': 'high'})

    def test_a_mixed_manifest_is_not_eligible(self):
        (self.root / 'b.txt').write_text('changed\n')
        body = self.preview(['a.txt', 'b.txt'])['body']
        self.assertFalse(body['eligible'])
        self.assertEqual(body['sensitive_paths'], {'b.txt': 'medium'})

    def test_the_runtime_boundary_is_eligible_like_any_other_path(self):
        (self.root / 'runtime.txt').write_text('changed\n')
        body = self.preview(['runtime.txt'])['body']
        self.assertTrue(body['eligible'], body['reasons'])
        packet = self.prepare(self.go(self.preview(['runtime.txt'])), manifest=['runtime.txt'])
        self.assertTrue(packet['runtime'])
        self.assertEqual((packet['state'], packet['early_approval']['applied']), ('approved', True))
        self.assertEqual(ea.sealed_reasons(packet, 'low'), [])

    def test_the_runtime_boundary_keeps_the_reach_and_the_kill_switch(self):
        (self.root / 'runtime.txt').write_text('changed\n')
        envelope = self.preview(['runtime.txt'])
        self.reach_with('# fixture\nearly_approval_reach = "off"\n')
        self.assertFalse(self.preview(['runtime.txt'])['body']['eligible'])
        with self.assertRaisesRegex(ValueError, 'switch is off'):
            self.go(envelope)
        text = (self.root / c.TABLE).read_text(encoding='utf-8')
        marked = text.replace('| `runtime.txt` | runtime | test | runtime | - | - | - | - | - |',
                              '| `runtime.txt` | runtime | test | runtime | - | - | - | - | medium |')
        self.assertNotEqual(text, marked)
        (self.root / c.TABLE).write_text(marked, encoding='utf-8', newline='\n')
        self.git('add', '--', c.TABLE)
        self.git('commit', '-m', 'Grade the runtime path medium')
        self.parent = self.git('rev-parse', 'HEAD').strip()
        self.reach_with(SWITCH_ON)
        self.assertFalse(self.preview(['runtime.txt'])['body']['eligible'])
        self.reach_with(SWITCH_MEDIUM)
        self.assertTrue(self.preview(['runtime.txt'])['body']['eligible'])

    def test_a_candidate_cannot_lift_the_runtime_boundary_by_dropping_its_own_cell(self):
        text = (self.root / c.TABLE).read_text(encoding='utf-8')
        lifted = text.replace('| `runtime.txt` | runtime | test | runtime | - | - | - | - | - |',
                              '| `runtime.txt` | runtime | test | - | - | - | - | - | - |')
        self.assertNotEqual(text, lifted)
        (self.root / c.TABLE).write_text(lifted, encoding='utf-8', newline='\n')
        (self.root / 'runtime.txt').write_text('changed\n')
        packet = self.tool.prepare(['runtime.txt'], self.message, 'test', self.parent, 'refs/heads/main', 'delegated')
        self.assertTrue(packet['runtime'], 'the HEAD table still puts the path on the runtime boundary')
        self.assertEqual(packet['profile_data']['runtime_checks'], ['regressions'])

    def test_unmarked_and_unknown_paths_fail_closed(self):
        _, profiles = self.tool.data()
        table = self.tool.table(None, profiles)
        self.assertEqual(table.ineligible_paths(['nowhere/new.py'], 'low', table), ['nowhere/new.py'])
        self.assertEqual(table.ineligible_paths(['nowhere/new.py'], 'medium', table), ['nowhere/new.py'])
        self.assertEqual(table.ineligible_paths(['a.txt'], 'low', table), [])
        self.assertEqual(table.ineligible_paths([], 'low', table), [])

    def test_a_not_eligible_preview_cannot_receive_a_go(self):
        for name in ('b.txt', 'h.txt'):
            (self.root / name).write_text('changed\n')
            with self.subTest(name), self.assertRaisesRegex(ValueError, 'not eligible'):
                self.go(self.preview([name]))

    # ----------------------------------------------------------------- the go
    def test_only_an_approval_word_is_a_go_and_a_question_is_not(self):
        envelope = self.preview()
        for reply in ('go', 'Go!', 'APPROVE', 'approved', 'Proceed.', 'do it', 'DO IT'):
            with self.subTest(reply=reply):
                self.assertEqual(self.go(envelope, reply)['body']['go']['response'], reply)
        for reply in ('go?', 'looks good', 'no go', 'go, stage and seal', 'sure', 'reject', 'REJECT'):
            with self.subTest(reply=reply), self.assertRaisesRegex(ValueError, 'not an approval word'):
                self.go(envelope, reply)

    def test_the_go_must_come_from_the_active_task_user_on_a_declared_channel(self):
        envelope = self.preview()
        for field, value in (('source', 'worker'), ('channel', 'conversation'), ('conversation_id', ' ')):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.go(envelope, **{field: value})
        with self.assertRaisesRegex(ValueError, 'Invalid lead developer go input'):
            ea.record_go(self.tool, envelope, {'source': 'active-task-user'})

    def test_the_go_must_name_the_presented_preview(self):
        first = self.preview()
        (self.root / 'governance' / 'tools' / 'x.py').write_text('x = 1\n')
        other = self.tool.preview(['a.txt', 'governance/tools/x.py'], self.message, None, self.parent, 'refs/heads/main')
        with self.assertRaisesRegex(ValueError, 'does not name'):
            self.go(first, preview_id=other['body']['id'])
        with self.assertRaisesRegex(ValueError, 'does not name'):
            self.go(first, preview_sha256=ea.digest(b'another preview'))

    def test_a_preview_or_go_record_that_was_edited_is_refused(self):
        envelope = self.preview()
        forged = json.loads(json.dumps(envelope))
        forged['body']['tree'] = '0' * 40
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            self.go(forged)
        go = self.go(envelope)
        forged_go = json.loads(json.dumps(go))
        forged_go['body']['go']['response'] = 'proceed'
        self.assertEqual(self.prepare(forged_go)['early_approval']['applied'], False)

    def test_the_go_is_refused_when_the_working_content_changed_after_the_preview(self):
        envelope = self.preview()
        (self.root / 'a.txt').write_text('different\n')
        with self.assertRaisesRegex(ValueError, 'working content changed'):
            self.go(envelope)

    # ---------------------------------------------------------------- carrying
    def test_an_identical_sealed_candidate_is_approved_and_commits(self):
        go, packet = self.approved()
        self.assertEqual(packet['approval']['kind'], ea.RECEIPT_KIND)
        self.assertTrue(packet['early_approval']['applied'])
        self.assertEqual(packet['early_approval']['attempt'], 1)
        result = self.tool.commit()
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(result['tree'], go['body']['preview']['tree'])
        self.assertEqual(self.tool.verify(), result)  # verification does not read the switch again
        self.assertTrue(ea.uses(self.tool)[packet['early_approval']['go_id']]['committed'])

    def test_a_changed_tree_manifest_or_message_returns_to_the_sealed_review(self):
        go = self.go(self.preview())
        (self.root / 'a.txt').write_text('edited after the go\n')
        packet = self.prepare(go)
        self.assertEqual(packet['state'], 'prepared')
        self.assertFalse(packet['early_approval']['applied'])
        self.assertTrue(any('tree' in r for r in packet['early_approval']['reasons']))
        self.assertNotIn('approval', packet)
        self.tool.abandon()

        (self.root / 'a.txt').write_text('new\n')
        (self.root / 'governance' / 'tools' / 'x.py').write_text('x = 1\n')
        packet = self.prepare(go, ['a.txt', 'governance/tools/x.py'])
        self.assertFalse(packet['early_approval']['applied'])
        self.assertTrue(any('manifest' in r for r in packet['early_approval']['reasons']))
        self.tool.abandon()

        (self.root / 'governance' / 'tools' / 'x.py').unlink()
        self.message.write_bytes(b'Another subject\n\n## Topic\n- Make another edit.\n')
        packet = self.prepare(go)
        self.assertFalse(packet['early_approval']['applied'])
        self.assertTrue(any('message_sha256' in r for r in packet['early_approval']['reasons']))

    def test_a_failed_check_never_carries_the_go(self):
        go = self.go(self.preview())

        def failing(argv, root):
            return {'argv': argv, 'exit_code': 1 if '-c' in argv else 0, 'stdout': '', 'stderr': '',
                    'duration_seconds': 0.0}
        tool = c.Checkpoint(self.root, runner=failing)
        with self.assertRaisesRegex(ValueError, 'Validation failed'):
            tool.prepare(['a.txt'], self.message, 'test', self.parent, 'refs/heads/main', 'delegated', early_go=go)
        packet = tool.load()
        self.assertEqual(packet['state'], 'prepare-failed')
        self.assertNotIn('approval', packet)

    def test_a_moved_parent_does_not_carry_the_go(self):
        go = self.go(self.preview())
        (self.root / 'docs' / 'note.md').write_text('note\n')
        self.git('add', '--', 'docs/note.md')
        self.git('commit', '-m', 'Main advanced')
        moved = self.git('rev-parse', 'HEAD').strip()
        packet = self.tool.prepare(['a.txt'], self.message, 'test', moved, 'refs/heads/main', 'delegated', early_go=go)
        self.assertEqual(packet['state'], 'prepared')
        self.assertFalse(packet['early_approval']['applied'])
        self.assertTrue(any('parent' in r for r in packet['early_approval']['reasons']))
        self.tool.abandon()
        self.parent = moved
        stale = self.preview()  # previews are bound to the parent they name, so a go after main moved is refused
        self.git('commit', '--allow-empty', '-m', 'Main advanced again')
        with self.assertRaisesRegex(ValueError, 'HEAD or branch moved'):
            self.go(stale)

    def test_a_re_prepare_that_reproduces_the_candidate_keeps_the_go_up_to_the_cap(self):
        go = self.go(self.preview())
        for attempt in range(1, ea.ATTEMPT_CAP + 1):
            with self.subTest(attempt=attempt):
                packet = self.prepare(go)
                self.assertEqual(packet['early_approval']['attempt'], attempt)
                self.assertEqual(packet['state'], 'approved')
                self.tool.abandon()
        packet = self.prepare(go)
        self.assertEqual(packet['state'], 'prepared')
        self.assertFalse(packet['early_approval']['applied'])
        self.assertTrue(any('cap' in r for r in packet['early_approval']['reasons']))

    def test_a_used_go_cannot_be_used_again(self):
        _, packet = self.approved()
        self.tool.commit()
        again = ea.start(self.tool, packet['early_approval']['go'], self.parent, 'refs/heads/main')
        self.assertFalse(again['applied'])
        self.assertTrue(any('already used' in r for r in again['reasons']))

    def test_an_edited_receipt_or_packet_fails_at_commit(self):
        _, packet = self.approved()
        forged = json.loads(json.dumps(packet))
        forged['approval']['go']['body']['go']['user_message_id'] = 'other-turn'
        self.tool.save(forged)
        with self.assertRaises(ValueError):
            self.tool.commit()
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.parent)

    # ----------------------------------------------------------- the switch
    def switch_with(self, text):
        (self.root / ea.FEATURES).write_text(text, encoding='utf-8', newline='\n')
        self.git('add', '--', ea.FEATURES)
        self.git('commit', '--allow-empty', '-m', 'Hand flip of the switch')
        return self.git('rev-parse', 'HEAD').strip()

    def reach_with(self, text):
        """Commit the feature file on main and follow the parent, keeping the working edits."""
        self.parent = self.switch_with(text)

    def test_only_the_strings_low_and_medium_turn_the_switch_on(self):
        cases = ((SWITCH_ON, 'low'), (SWITCH_MEDIUM, 'medium'), ('early_approval_reach = "off"\n', 'off'),
                 ('early_approval_reach = "high"\n', 'off'), ('early_approval_reach = "Low"\n', 'off'),
                 ('early_approval_reach = "low "\n', 'off'), ('early_approval_reach = ""\n', 'off'),
                 ('early_approval_reach = true\n', 'off'), ('early_approval_reach = false\n', 'off'),
                 ('early_approval_reach = 1\n', 'off'), ('early_approval_reach = ["low"]\n', 'off'),
                 ('# nothing here\n', 'off'), ('other = "low"\n', 'off'), ('early_approval_reach = low\n', 'off'),
                 ('early_conditional_approval = true\n', 'off'),  # the old key reads as a missing one
                 ('early_approval_reach = "low"\nearly_approval_reach = "low"\n', 'off'),
                 ('early_approval_reach = "medium"\nextra = "ignored"\n', 'medium'), ('\x00\xff', 'off'), ('', 'off'))
        for text, expected in cases:
            with self.subTest(text=text):
                self.switch_with(text)
                self.assertEqual(ea.switch(self.tool)[0], expected)

    def test_a_missing_or_unreadable_feature_file_is_off(self):
        self.git('rm', '-q', '--', ea.FEATURES)
        self.git('commit', '-m', 'Remove the switch file')
        self.assertEqual(ea.switch(self.tool)[0], ea.OFF)
        with patch.object(c.Checkpoint, 'git', side_effect=ValueError('Git failed')):
            self.assertEqual(ea.switch(self.tool)[0], ea.OFF)

    def test_the_switch_is_read_from_main_not_from_the_working_tree(self):
        (self.root / ea.FEATURES).write_text('early_approval_reach = "off"\n', encoding='utf-8', newline='\n')
        self.assertEqual(ea.switch(self.tool)[0], 'low')  # the uncommitted edit changes nothing
        self.assertTrue(self.preview()['body']['eligible'])

    def test_off_at_the_preview_is_not_eligible_and_at_the_go_is_refused(self):
        with patch.object(ea, 'switch', return_value=(ea.OFF, 'off for the test')):
            self.assertFalse(self.preview()['body']['eligible'])
        envelope = self.preview()
        with patch.object(ea, 'switch', return_value=(ea.OFF, 'off for the test')), \
                self.assertRaisesRegex(ValueError, 'switch is off'):
            self.go(envelope)

    def test_off_at_the_seal_falls_back_and_off_at_the_commit_refuses(self):
        go = self.go(self.preview())
        with patch.object(ea, 'switch', return_value=(ea.OFF, 'off for the test')):
            packet = self.prepare(go)
        self.assertEqual(packet['state'], 'prepared')
        self.assertTrue(any('switch is off' in r for r in packet['early_approval']['reasons']))
        self.tool.abandon()
        packet = self.prepare(go)
        self.assertEqual(packet['state'], 'approved')
        with patch.object(ea, 'switch', return_value=(ea.OFF, 'off for the test')), \
                self.assertRaisesRegex(ValueError, 'seal must be redone'):
            self.tool.commit()
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.parent)
        self.assertEqual(self.tool.commit()['state'], 'verified')

    def test_off_at_integration_stops_an_early_approved_tip_only(self):
        _, packet = self.approved()
        result = self.tool.commit()
        self.tool.finish()
        landing = ea.read_landing(self.tool)
        self.assertEqual(landing['approval_path'], 'early')
        ea.assert_integration(self.tool, self.tool, result['result_sha'])
        with patch.object(ea, 'switch', return_value=(ea.OFF, 'off for the test')):
            with self.assertRaisesRegex(ValueError, 'seal must be redone'):
                ea.assert_integration(self.tool, self.tool, result['result_sha'])
            ea.assert_integration(self.tool, self.tool, 'f' * 40)  # another tip is not this transaction's

    # ------------------------------------------------------------- the reach
    def test_reach_low_is_todays_behaviour_and_a_medium_path_keeps_the_sealed_approve(self):
        (self.root / 'b.txt').write_text('changed\n')
        envelope = self.preview(['b.txt'])
        self.assertFalse(envelope['body']['eligible'])
        self.assertTrue(any('above the early approval reach (low)' in r for r in envelope['body']['reasons']))
        packet = self.prepare(manifest=['b.txt'])
        self.assertEqual(packet['state'], 'prepared')
        self.assertEqual(packet['profile_data']['artifact_classification']['b.txt']['approval_sensitivity'], 'medium')

    def test_reach_medium_carries_a_medium_path_through_to_the_commit(self):
        self.reach_with(SWITCH_MEDIUM)
        (self.root / 'b.txt').write_text('changed\n')
        envelope = self.preview(['b.txt'])
        self.assertTrue(envelope['body']['eligible'], envelope['body']['reasons'])
        self.assertEqual(envelope['body']['switch']['reach'], 'medium')
        packet = self.prepare(self.go(envelope), manifest=['b.txt'])
        self.assertEqual((packet['state'], packet['early_approval']['applied'], packet['early_approval']['reach']),
                         ('approved', True, 'medium'))
        self.assertEqual(self.tool.commit()['state'], 'verified')

    def test_reach_medium_still_carries_a_low_path_and_a_mixed_low_and_medium_manifest(self):
        self.reach_with(SWITCH_MEDIUM)
        (self.root / 'b.txt').write_text('changed\n')
        for manifest in (['a.txt'], ['a.txt', 'b.txt']):
            with self.subTest(manifest=manifest):
                self.assertTrue(self.preview(manifest)['body']['eligible'])

    def test_a_high_path_never_carries_whatever_the_reach(self):
        for text in (SWITCH_ON, SWITCH_MEDIUM):
            with self.subTest(text=text):
                self.reach_with(text)
                (self.root / 'h.txt').write_text('changed\n' + text)
                for manifest in (['h.txt'], ['a.txt', 'h.txt'], ['b.txt', 'h.txt']):
                    body = self.preview(manifest)['body']
                    self.assertFalse(body['eligible'], manifest)
                    self.assertEqual(body['sensitive_paths']['h.txt'], 'high')
                packet = self.prepare(manifest=['h.txt'])
                self.assertEqual(packet['state'], 'prepared')
                self.tool.abandon()

    def test_a_high_path_in_the_sealed_view_blocks_a_forged_go(self):
        self.reach_with(SWITCH_MEDIUM)
        (self.root / 'b.txt').write_text('changed\n')
        go = self.go(self.preview(['b.txt']))
        packet = self.prepare(go, manifest=['b.txt'])
        self.assertEqual(packet['state'], 'approved')
        packet['profile_data']['artifact_classification']['b.txt']['approval_sensitivity'] = 'high'
        for reach in ('low', 'medium'):
            self.assertTrue(ea.sealed_reasons(packet, reach))

    def test_the_sealed_view_with_a_missing_or_unknown_sensitivity_never_carries(self):
        _, packet = self.approved()
        for value in ('yes', 'Medium', '', None, True, 'critical'):
            with self.subTest(value=value):
                packet['profile_data']['artifact_classification']['a.txt']['approval_sensitivity'] = value
                self.assertTrue(ea.sealed_reasons(packet, 'low'))
                self.assertTrue(ea.sealed_reasons(packet, 'medium'))
        del packet['profile_data']['artifact_classification']['a.txt']['approval_sensitivity']
        self.assertTrue(ea.sealed_reasons(packet, 'medium'))
        packet['profile_data']['artifact_classification']['a.txt']['approval_sensitivity'] = 'low'
        self.assertEqual(ea.sealed_reasons(packet, 'low'), [])
        for reach in ('off', 'high', '', None, 'LOW'):
            self.assertTrue(ea.sealed_reasons(packet, reach), reach)

    def test_a_lower_reach_at_the_commit_refuses_a_medium_candidate(self):
        self.reach_with(SWITCH_MEDIUM)
        (self.root / 'b.txt').write_text('changed\n')
        packet = self.prepare(self.go(self.preview(['b.txt'])), manifest=['b.txt'])
        self.assertEqual(packet['state'], 'approved')
        with patch.object(ea, 'switch', return_value=('low', 'reach low')), \
                self.assertRaisesRegex(ValueError, 'no longer covers the sealed paths'):
            self.tool.commit()
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.parent)

    def test_a_receipt_without_a_recorded_reach_is_refused(self):
        _, packet = self.approved()
        prepared = {k: v for k, v in packet.items() if k != 'approval'}
        prepared['state'] = 'prepared'
        prepared['early_approval'] = {k: v for k, v in prepared['early_approval'].items() if k != 'reach'}
        with self.assertRaises(ValueError):
            ea.assert_receipt(self.tool, prepared, packet['approval'])

    def test_a_go_for_a_medium_preview_is_refused_when_the_reach_dropped_to_low(self):
        self.reach_with(SWITCH_MEDIUM)
        (self.root / 'b.txt').write_text('changed\n')
        envelope = self.preview(['b.txt'])
        self.assertTrue(envelope['body']['eligible'])
        with patch.object(ea, 'switch', return_value=('low', 'reach low')), \
                self.assertRaisesRegex(ValueError, 'above the early approval reach'):
            self.go(envelope)

    # --------------------------------------------------------- notice and review
    def test_the_landing_notice_for_an_early_and_for_a_sealed_approval(self):
        self.approved()
        result = self.tool.commit()
        self.tool.finish()
        self.assertFalse((self.tool.store / ea.EARLY_USES).exists())
        notice = self.tool.landing()
        for text in ('## Landing notice', 'your go at the preview ("go"', result['result_sha'], 'Checks: 3 of 3 passed',
                     '`a.txt`', 'git revert ' + result['result_sha'][:7], 'Nothing was pushed', 'Exact subject'):
            self.assertIn(text, notice)
        self.assertIn('| Integrated | yes, main is now', notice)  # the fixture commits on main itself

        (self.root / 'a.txt').write_text('second\n')
        self.parent = self.git('rev-parse', 'HEAD').strip()
        self.tool.prepare(['a.txt'], self.message, 'test', self.parent, 'refs/heads/main', 'delegated')
        review = self.tool.present()
        self.tool.approve_consent({'source': 'active-task-user', 'conversation_id': 'x', 'user_message_id': 'y',
                                   'channel': 'text-token', 'response': 'Proceed', 'decision': 'approve',
                                   'transaction_id': review['transaction_id'], 'prepared_sha256': review['prepared_sha256']})
        self.tool.commit()
        self.tool.finish()
        sealed = self.tool.landing()
        self.assertIn('| Approved by | the sealed Approve |', sealed)
        self.assertNotIn('go at the preview', sealed)

    def test_the_landing_notice_needs_a_finished_transaction(self):
        with self.assertRaisesRegex(ValueError, 'No finished transaction'):
            self.tool.landing()

    def test_a_sealed_review_says_why_the_go_did_not_apply_and_names_the_words(self):
        go = self.go(self.preview())
        (self.root / 'a.txt').write_text('edited after the go\n')
        self.prepare(go)
        text = self.tool.present()['review']
        self.assertIn('### Early approval', text)
        self.assertIn('did not approve this candidate', text)
        self.assertIn('reply **go**, **approve**, **approved**, **proceed** or **do it** to approve, or **reject** to reject',
                      text)

    def test_the_display_words_are_the_matcher_words(self):
        self.assertEqual(tuple(approval_words.normalize(w) for w in approval_words.DISPLAY), approval_words.WORDS)
        self.assertTrue(all(approval_words.approves(w) for w in approval_words.DISPLAY))

    def test_a_sensitive_path_is_flagged_in_the_sealed_review(self):
        for name, flag in (('b.txt', 'approval-medium'), ('h.txt', 'approval-high')):
            with self.subTest(name):
                (self.root / name).write_text('changed\n')
                self.tool.prepare([name], self.message, 'test', self.parent, 'refs/heads/main', 'delegated')
                self.assertIn(flag, self.tool.present()['review'])
                self.tool.abandon()

    def test_a_candidate_cannot_lower_its_own_sensitivity(self):
        self.reach_with(SWITCH_MEDIUM)
        text = (self.root / c.TABLE).read_text(encoding='utf-8')
        (self.root / c.TABLE).write_text(text.replace('| `h.txt` | tooling | test | - | - | - | - | - | high |',
                                                      '| `h.txt` | tooling | test | - | - | - | - | - | - |'),
                                         encoding='utf-8', newline='\n')
        self.assertNotEqual(text, (self.root / c.TABLE).read_text(encoding='utf-8'))
        (self.root / 'h.txt').write_text('changed\n')
        body = self.preview(['h.txt'])['body']
        self.assertFalse(body['eligible'])
        self.assertEqual(body['sensitive_paths'], {'h.txt': 'high'})
        packet = self.tool.prepare(['h.txt'], self.message, 'test', self.parent, 'refs/heads/main', 'delegated')
        entry = packet['profile_data']['artifact_classification']['h.txt']
        self.assertEqual((entry['head_sensitivity'], entry['candidate_sensitivity'], entry['approval_sensitivity']),
                         ('high', 'low', 'high'))
        self.assertTrue(ea.sealed_reasons(packet, 'medium'))
        self.assertIn('sensitivity-lowering-ignored', self.tool.present()['review'])

    def test_a_low_path_carries_no_sensitivity_flag(self):
        self.tool.prepare(['a.txt'], self.message, 'test', self.parent, 'refs/heads/main', 'delegated')
        review = self.tool.present()['review']
        self.assertNotIn('approval-medium', review)
        self.assertNotIn('approval-high', review)

    # ------------------------------------------------------------------ the readable preview
    def readable(self, manifest=None):
        """The preview and the Markdown block the tool renders for it, as `checkpoint.py preview` shows them."""
        review = {}
        envelope = self.tool.preview(manifest or ['a.txt'], self.message, None, self.parent, 'refs/heads/main',
                                     review=review)
        return envelope, review_render.render_preview(dict(review, body=envelope['body']))

    def test_the_readable_preview_names_every_path_with_class_and_size_and_the_whole_message(self):
        (self.root / 'docs' / 'x.md').write_text('one\ntwo\n')
        (self.root / 'b.txt').write_text('changed\nmore\n')
        envelope, text = self.readable(['a.txt', 'b.txt', 'docs/x.md'])
        self.assertEqual(text.splitlines()[0], '## Preview')
        self.assertIn('### Manifest: 3 paths, governance + tooling (mixed), profile `test`, +5 -2 lines', text)
        self.assertIn('| `a.txt` | tooling | - | low | broad | +1 -1 |', text)
        self.assertIn('| `b.txt` | tooling | - | medium | approval-medium, broad | +2 -1 |', text)
        self.assertIn('| `docs/x.md` | governance | - | low | broad | new +2 |', text)
        self.assertIn('Exact subject\n\n## Topic\n- Make an edit.\n', text)  # the full message in a fence
        self.assertIn(repr(self.message.read_bytes().decode()), text)  # and its escaped form
        self.assertIn(envelope['body']['message_sha256'], text)
        self.assertIn('`' + self.parent[:7] + '`', text)
        self.assertIn('`' + envelope['body']['tree'][:7] + '`', text)
        self.assertIn(str(self.root), text)
        self.assertIn('IntelliJ', text)
        for check in ('staged-whitespace', 'continuity', 'regressions'):
            self.assertIn('| %s |' % check, text)
        self.assertIn('### Review focus', text)

    def test_an_eligible_preview_says_the_go_approves_the_exact_candidate(self):
        envelope, text = self.readable()
        self.assertTrue(envelope['body']['eligible'])
        self.assertIn('Eligible: every path is within the early approval reach `low`', text)
        self.assertIn('to approve this exact candidate (tree `%s`) if every check passes' % envelope['body']['tree'][:7], text)
        self.assertIn('returns to the sealed review', text)
        self.assertNotIn('the go only stages and seals', text)

    def test_an_ineligible_preview_names_the_reason_and_says_the_go_only_stages_and_seals(self):
        (self.root / 'h.txt').write_text('changed\n')
        envelope, text = self.readable(['a.txt', 'h.txt'])
        self.assertFalse(envelope['body']['eligible'])
        self.assertIn('Not eligible:', text)
        self.assertIn('- paths graded high, which no reach admits: h.txt', text)
        self.assertIn('| `h.txt` | tooling | - | high |', text)
        self.assertIn('so the go only stages and seals', text)
        self.assertNotIn('to approve this exact candidate', text)

    def test_a_switch_that_reads_off_is_a_stated_reason(self):
        self.git('rm', '-q', '--cached', '--', ea.FEATURES)
        self.git('commit', '-q', '-m', 'Remove the switch file')
        head = self.git('rev-parse', 'HEAD').strip()
        review = {}
        envelope = self.tool.preview(['a.txt'], self.message, None, head, 'refs/heads/main', review=review)
        text = review_render.render_preview(dict(review, body=envelope['body']))
        self.assertIn('the early conditional approval switch is off', text)
        self.assertIn('| Early approval reach | `off` |', text)
        self.assertIn('so the go only stages and seals', text)

    def test_the_rendering_is_deterministic_and_changes_nothing_in_the_record(self):
        review = {}
        envelope = self.tool.preview(['a.txt'], self.message, None, self.parent, 'refs/heads/main', review=review)
        before = ea.canonical(envelope)
        facts = dict(review, body=envelope['body'])
        self.assertEqual(review_render.render_preview(facts), review_render.render_preview(facts))
        self.assertEqual(ea.canonical(envelope), before)
        self.assertEqual(set(envelope['body']), set(ea.unsign(self.tool, self.tool.preview(
            ['a.txt'], self.message, None, self.parent, 'refs/heads/main'))))  # the record has no new field

    def test_a_go_built_from_the_printed_values_passes_and_another_digest_is_refused_with_a_pointer(self):
        import re
        review = {}
        envelope = self.tool.preview(['a.txt'], self.message, None, self.parent, 'refs/heads/main', review=review)
        before = ea.canonical(envelope)
        text = review_render.render_preview(dict(review, body=envelope['body']))
        printed = dict(re.findall(r'^(preview_id|preview_sha256): (\S+)$', text, re.M))
        self.assertEqual(printed, {'preview_id': envelope['body']['id'],
                                   'preview_sha256': ea.digest(ea.canonical(envelope['body']))})
        recorded = self.go(envelope, preview_id=printed['preview_id'], preview_sha256=printed['preview_sha256'])
        self.assertEqual(recorded['body']['preview'], envelope['body'])
        with self.assertRaisesRegex(ValueError, 'copy preview_id and preview_sha256 from the rendered preview block') as raised:
            self.go(envelope, preview_sha256=ea.digest(b'another preview'))
        self.assertNotIn(printed['preview_sha256'], str(raised.exception))  # the expected digest is never revealed
        self.assertEqual(ea.canonical(envelope), before)  # the record is unchanged by the rendering

    def test_the_command_prints_the_block_and_writes_it_beside_the_unchanged_record(self):
        import io
        manifest = Path(self.temp.name) / 'scope.json'
        manifest.write_text('["a.txt"]')
        record = Path(self.temp.name) / 'preview.json'
        out = io.StringIO()
        out.buffer = io.BytesIO()
        argv = ['checkpoint.py', '--repo', str(self.root), 'preview', '--manifest', str(manifest), '--message',
                str(self.message), '--expected-head', self.parent, '--expected-branch', 'refs/heads/main',
                '--output', str(record)]
        with patch.object(c.sys, 'argv', argv), patch.object(c.sys, 'stdout', out):
            self.assertEqual(c.main(), 0)
        printed = out.buffer.getvalue()
        self.assertEqual(printed, (Path(self.temp.name) / 'preview.md').read_bytes())
        self.assertNotIn(b'\r', printed)
        self.assertTrue(printed.startswith(b'## Preview\n'))
        stored = record.read_bytes()
        envelope = json.loads(stored)
        self.assertEqual(stored, ea.canonical(envelope))  # the machine record is the same bytes as before
        self.assertEqual(ea.unsign(self.tool, envelope)['tree'], ea.candidate_tree(self.tool, ['a.txt']))
        self.assertIn(ea.unsign(self.tool, envelope)['id'].encode(), printed)
        with patch.object(c.sys, 'argv', argv), patch.object(c.sys, 'stdout', io.StringIO()), \
                patch.object(c.sys, 'stderr', io.StringIO()):  # a second preview never overwrites the first
            self.assertEqual(c.main(), 1)
        self.assertEqual(record.read_bytes(), stored)
        self.assertEqual(printed, (Path(self.temp.name) / 'preview.md').read_bytes())

    def test_the_readable_preview_cannot_share_the_records_name(self):
        import io
        manifest = Path(self.temp.name) / 'scope.json'
        manifest.write_text('["a.txt"]')
        argv = ['checkpoint.py', '--repo', str(self.root), 'preview', '--manifest', str(manifest), '--message',
                str(self.message), '--expected-head', self.parent, '--expected-branch', 'refs/heads/main',
                '--output', str(Path(self.temp.name) / 'preview.md')]
        with patch.object(c.sys, 'argv', argv), patch.object(c.sys, 'stdout', io.StringIO()), \
                patch.object(c.sys, 'stderr', io.StringIO()) as err:
            self.assertEqual(c.main(), 1)
        self.assertIn('must not end in .md', err.getvalue())
        self.assertFalse((Path(self.temp.name) / 'preview.md').exists())


class StubRow:
    def __init__(self, profile='test'):
        self.profile = profile


class StubTable:
    """Just enough of a table: the sensitivity grades per path and which paths have a row."""
    def __init__(self, grades, blocked=()):
        self.grades, self.blocked = grades, set(blocked)

    def sensitivity(self, path, head):
        return self.grades[path]

    def resolve(self, path):
        return StubRow('blocked' if path in self.blocked else 'test') if self.grades[path]['candidate'] != 'none' else None

    def ineligible_paths(self, paths, reach, head):
        return sorted(p for p in paths if self.grades[p]['level'] not in ea.artifact_classification.REACH_ALLOWS.get(reach, ()))


def grade(head, candidate, level):
    return {'head': head, 'candidate': candidate, 'level': level}


class ReasonCasesTest(unittest.TestCase):
    """The refusal names the case that occurs, one line per case with its paths; who is eligible is unchanged."""
    GRADES = {
        'docs/a.md': grade('medium', 'medium', 'medium'),
        'new/one.py': grade('none', 'low', 'high'),
        'new/two.py': grade('none', 'medium', 'high'),
        'gov/rule.md': grade('high', 'high', 'high'),
        'gone/x.md': grade('none', 'none', 'high'),
        'blocked.bin': grade('high', 'high', 'high'),
        'ok.txt': grade('low', 'low', 'low'),
    }

    def reasons(self, manifest, reach='low'):
        table = StubTable(self.GRADES, blocked=['blocked.bin'])
        return ea.reasons_not_eligible(table, table, manifest, reach, 'why')

    def test_each_case_is_named_with_its_paths_on_one_line(self):
        reasons = self.reasons(sorted(set(self.GRADES) - {'ok.txt'}))
        self.assertEqual(reasons, [
            'paths above the early approval reach (low), effective sensitivity medium: docs/a.md',
            'paths with no row at HEAD (' + ea.NO_ROW_HINT + '): new/one.py, new/two.py',
            'paths graded high, which no reach admits: gov/rule.md',
            'paths unclassified or blocked (no usable row, an unknown value or a blocked profile): blocked.bin, gone/x.md'])

    def test_an_empty_case_is_not_printed_and_an_eligible_manifest_has_no_reason(self):
        self.assertEqual(self.reasons(['ok.txt']), [])
        self.assertEqual(self.reasons(['docs/a.md'], 'medium'), [])
        self.assertEqual(self.reasons(['new/one.py', 'ok.txt']),
                         ['paths with no row at HEAD (' + ea.NO_ROW_HINT + '): new/one.py'])

    def test_the_new_path_hint_says_how_to_avoid_it(self):
        self.assertIn('a folder or pattern row on main, added once, makes later files here eligible', ea.NO_ROW_HINT)
        self.assertIn('counted high', ea.NO_ROW_HINT)

    def test_a_head_table_that_cannot_be_read_is_unclassified_not_a_missing_row(self):
        table = StubTable(self.GRADES)
        reasons = ea.reasons_not_eligible(table, None, ['new/one.py'], 'low', 'why')
        self.assertEqual(len(reasons), 1)
        self.assertTrue(reasons[0].startswith('paths unclassified or blocked'), reasons)

    def sealed(self, entries):
        return {'manifest': sorted(entries), 'profile_data': {'artifact_classification': entries}}

    def test_the_sealed_review_names_the_same_cases(self):
        def entry(head, candidate, level, **more):
            return dict(resolved=True, approval_sensitivity=level, head_sensitivity=head,
                        candidate_sensitivity=candidate, profile='test', **more)
        packet = self.sealed({
            'docs/a.md': entry('medium', 'medium', 'medium'), 'new/one.py': entry('none', 'low', 'high'),
            'gov/rule.md': entry('high', 'high', 'high'), 'blocked.bin': entry('high', 'high', 'high', ) | {'profile': 'blocked'},
            'unknown.txt': {'resolved': False}, 'norecord.txt': {'resolved': True, 'approval_sensitivity': 'low'},
            'ok.txt': entry('low', 'low', 'low')})
        self.assertEqual(ea.sealed_reasons(packet, 'low'), [
            'paths above the early approval reach (low), effective sensitivity medium: docs/a.md',
            'paths with no row at HEAD (' + ea.NO_ROW_HINT + '): new/one.py',
            'paths graded high, which no reach admits: gov/rule.md',
            'paths unclassified or blocked (no usable row, an unknown value or a blocked profile): '
            'blocked.bin, norecord.txt, unknown.txt'])
        self.assertEqual(ea.sealed_reasons(packet, 'medium')[0].split(':')[0],
                         'paths with no row at HEAD (' + ea.NO_ROW_HINT + ')')
        self.assertEqual(ea.sealed_reasons(self.sealed({'ok.txt': entry('low', 'low', 'low')}), 'low'), [])

    def test_an_unknown_reach_refuses_every_path_and_stays_a_reason(self):
        packet = self.sealed({'ok.txt': dict(resolved=True, approval_sensitivity='low', head_sensitivity='low',
                                             candidate_sensitivity='low', profile='test')})
        self.assertTrue(ea.sealed_reasons(packet, 'bogus'))


class TestSettingsTest(unittest.TestCase):
    def test_each_feature_file_maps_to_its_run_settings(self):
        sequential = 'is not the boolean true'
        table = [
            ('parallel_tests = true', True, 0, 'is true'),
            ('parallel_tests = true\ntest_workers = 0', True, 0, 'is true'),
            ('parallel_tests = true\ntest_workers = 4', True, 4, 'is true'),
            ('parallel_tests = false\ntest_workers = 4', False, 4, sequential),
            ('early_approval_reach = "low"', False, 0, sequential),  # missing key
            ('parallel_tests = "true"', False, 0, sequential),
            ('parallel_tests = 1', False, 0, sequential),
            ('parallel_tests = true\ntest_workers = -1', False, 0, 'whole number'),
            ('parallel_tests = true\ntest_workers = 2.5', False, 0, 'whole number'),
            ('parallel_tests = true\ntest_workers = true', False, 0, 'whole number'),
            ('parallel_tests = true\ntest_workers = "4"', False, 0, 'whole number'),
            ('parallel_tests = true\nparallel_tests = true', False, 0, 'invalid'),  # duplicate key
            ('parallel_tests = [', False, 0, 'invalid'),
            (b'parallel_tests = \xff', False, 0, 'invalid'),
            (None, False, 0, 'missing'),  # no file on main
        ]
        for text, parallel, workers, why in table:
            with self.subTest(text=text):
                found = ea.test_settings(FakeGit(None if text is None else {ea.FEATURES: text}))
                self.assertEqual((found['parallel'], found['workers']), (parallel, workers))
                self.assertIn(why, found['reason'])


if __name__ == '__main__':
    unittest.main()
