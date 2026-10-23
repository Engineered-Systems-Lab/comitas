"""Checkpoint mutations are confined to disposable Git repositories."""
import base64
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification as ac
import checkpoint as c
from ..fake_git import FakeGit
import repo_paths as rp

# The fixture repositories classify their paths with a real, minimal artifact classification table.
FIXTURE_ROWS = [  # (path, class, profile, boundary), sorted by path
    ('a.txt', 'tooling', 'test', '-'),
    ('b.txt', 'tooling', 'test', '-'),
    ('devlog/*.md', 'record', 'test', '-'),
    ('docs/*.md', 'governance', 'test', '-'),
    ('frozen/', 'governance', 'test', 'frozen'),
    ('new-component/*.txt', 'tooling', 'test', '-'),
    ('runtime.txt', 'runtime', 'test', 'runtime'),
    ('scripts/*.sh', 'tooling', 'test', '-'),
    ('governance/tools/*.py', 'tooling', 'test', '-'),
]
FIXTURE_ROWS.sort()  # the table parser requires ASCII ordinal order


CONTRACT_WITH_FACT = '{"version": 1, "facts": {"implementation_checkpoint": {}}}\n'


def fixture_table(rows=None):
    lines = ['# Fixture classification', '', ac.HEADER, ac.SEPARATOR]
    lines += ['| `%s` | %s | %s | %s | - | - | - | - | - |' % row for row in (rows or FIXTURE_ROWS)]
    return '\n'.join(lines) + '\n'


def ignore_local_folder(tool):
    """Ignore the machine-local .local folder of the fixture repository without touching any tracked file."""
    (tool.common / 'info').mkdir(exist_ok=True)
    with (tool.common / 'info' / 'exclude').open('a', newline='\n') as stream:
        stream.write('/.local/\n')


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                             if not k.startswith('GIT_')}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory(prefix='checkpoint-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'repo'
        self.root.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Isolated Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'core.autocrlf', 'true')
        (self.root / 'governance' / 'tools').mkdir(parents=True, exist_ok=True)
        (self.root / 'governance' / 'config').mkdir(parents=True, exist_ok=True)
        for name in (*c.DRIVER_MODULES, 'lifecycle/commit_metadata'):  # the facade and its four components are the driver
            (self.root / 'governance' / 'tools' / (name + '.py')).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(c.__file__).parent / (name + '.py'), self.root / 'governance' / 'tools' / (name + '.py'))
        self.policy = {'version': 4, 'prohibited_components': ['.git', 'work', 'tmp', '__pycache__'],
                       'required_checks': ['continuity', 'regressions'],
                       'commands': {
                           'continuity': ['{python}', '-B', '-c', 'print("PASS")'],
                           'regressions': ['{python}', '-B', '-c', 'print("tests PASS")']}}
        self.profiles = {'version': 4, 'profiles': {'test': {
            'checks': [{'id': name, 'argv': argv} for name, argv in self.policy['commands'].items()],
            'limitations': 'Disposable fixture commands only.', 'runtime_checks': ['regressions']}},
            'impact': {'version': 1, 'invariants': {}, 'symbol_maps': {}},
            'commit_metadata': {'version': 2, 'classes': ['record', 'governance', 'tooling', 'runtime'],
                                'subclasses': []}}
        (self.root / 'governance').mkdir(exist_ok=True)
        (self.root / c.TABLE).write_text(fixture_table(), encoding='utf-8', newline='\n')
        self.write_json(c.POLICY, self.policy)
        self.write_json(c.PROFILES, self.profiles)
        for name in ('a.txt', 'b.txt', 'runtime.txt'):
            (self.root / name).write_text('old\n')
        (self.root / '.gitignore').write_text('ignored/\n__pycache__/\ntmp/\n')
        self.git('add', '--', 'a.txt', 'b.txt', 'runtime.txt', '.gitignore', 'governance')
        self.git('commit', '-m', 'Initial fixture')
        self.parent = self.git('rev-parse', 'HEAD').strip()
        self.message = Path(self.temp.name) / 'message.txt'
        self.message.write_bytes(b'Exact subject\n\nBody with trailing spaces  \n# literal comment\n\n')
        (self.root / 'a.txt').write_text('new\n')
        self.tool = c.Checkpoint(self.root)
        ignore_local_folder(self.tool)
        # Synthetic transaction fixtures declare project-evidence stand-ins here.
        self.checkpoint_gate = patch.object(c.Checkpoint, 'committed_checkpoint', autospec=True,
            side_effect=lambda tool: tool.git('log', '-1', '--format=%H', 'HEAD', '--', 'runtime.txt').decode().strip())
        self.checkpoint_gate.start()
        self.addCleanup(self.checkpoint_gate.stop)
        self.durable_gate = patch.object(c.Checkpoint, 'durable_continuity', return_value=None)
        self.durable_gate.start()
        self.addCleanup(self.durable_gate.stop)

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE).decode()

    def write_json(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding='utf-8')

    def prepare(self, manifest=None):
        return self.tool.prepare(manifest or ['a.txt'], self.message, 'test', self.parent,
                                 'refs/heads/main', 'Lead developer delegated this exact fixture scope')

    def approve(self):
        with redirect_stdout(io.StringIO()):
            return self.tool.approve(lambda prompt: prompt.split('Type exactly: ')[1].split('\n')[0])

    def ready(self):
        self.prepare()
        self.approve()

    def consent(self, presented, response='APPROVE', source='active-task-user', channel='text-token',
                decision=None, turn='fixture-user-turn-2'):
        return {'source': source, 'conversation_id': 'disposable-active-task',
                'user_message_id': turn, 'channel': channel, 'response': response,
                'decision': decision or ('reject' if 'reject' in response.casefold() else 'approve'),
                'transaction_id': presented['transaction_id'],
                'prepared_sha256': presented['prepared_sha256']}

    def test_prepare_stages_scripts_as_executable_and_keeps_every_other_mode(self):
        (self.root / 'scripts').mkdir()
        (self.root / 'scripts' / 'old.sh').write_text('#!/bin/sh\n')
        self.git('add', '--chmod=+x', '--', 'scripts/old.sh')
        self.git('commit', '-m', 'Track a script')
        self.parent = self.git('rev-parse', 'HEAD').strip()
        (self.root / 'scripts' / 'old.sh').write_text('#!/bin/sh\necho changed\n')
        (self.root / 'scripts' / 'new.sh').write_text('#!/bin/sh\n')
        self.prepare(['a.txt', 'scripts/new.sh', 'scripts/old.sh'])
        modes = {row.split('\t')[1]: row.split()[0] for row in self.git('ls-files', '-s').splitlines()}
        self.assertEqual((modes['scripts/new.sh'], modes['scripts/old.sh'], modes['a.txt']),
                         ('100755', '100755', '100644'))

    def test_consent_present_receipt_commit_exact_identity(self):
        p = self.prepare()
        review = self.tool.present()
        self.assertEqual(review['packet'], p)
        self.assertEqual(review['full_exact_message'], repr(self.message.read_bytes().decode()))
        self.assertEqual(review['approval_request'], {
            'transaction_id': p['id'], 'prepared_sha256': c.digest(c.canonical(p)),
            'controls': [{'label': 'Approve', 'value': 'approve'}, {'label': 'Reject', 'value': 'reject'}],
            'text_tokens': [{'token': 'APPROVE', 'decision': 'approve'},
                            {'token': 'REJECT', 'decision': 'reject'}]})
        consent = self.consent(review)
        approved = self.tool.approve_consent(consent)
        self.assertEqual(approved['approval'], {'prepared_sha256': c.digest(c.canonical(p)),
                                                'kind': 'approver-decision-v1', 'decision': consent})
        result = self.tool.commit()
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(result['parent'], p['parent'])
        self.assertEqual(result['tree'], p['tree'])
        self.assertEqual(self.tool.git('cat-file', 'commit', result['result_sha']).split(b'\n\n', 1)[1],
                         self.message.read_bytes())
        self.assertEqual(self.tool.verify(), result)
        # A consumed decision cannot be replayed into a second authorization.
        with self.assertRaisesRegex(ValueError, 'sealed prepared'):
            self.tool.approve_consent(consent)
        self.tool.finish()

    def test_exclusive_evidence_never_replaces_an_audit_artifact(self):
        path = self.root / 'audit.json'
        c.exclusive_evidence(path, b'first')
        c.exclusive_evidence(path, b'first')
        with self.assertRaisesRegex(ValueError, 'collision'):
            c.exclusive_evidence(path, b'changed')
        self.assertEqual(path.read_bytes(), b'first')

    def test_decision_channel_is_provenance_not_authorization(self):
        p = self.prepare()
        review = self.tool.present()
        request = review['approval_request']
        responses = {}
        for control in request['controls']:
            responses.update({control['label']: control['value'], control['value']: control['value']})
        for entry in request['text_tokens']:
            responses.update({entry['token']: entry['decision'], '  ' + entry['token'] + '\n': entry['decision']})
        self.assertEqual(responses, {
            'Approve': 'approve', 'approve': 'approve', 'APPROVE': 'approve', '  APPROVE\n': 'approve',
            'Reject': 'reject', 'reject': 'reject', 'REJECT': 'reject', '  REJECT\n': 'reject'})
        for channel in c.DECISION_CHANNELS:
            for response, decision in responses.items():
                with self.subTest(channel=channel, response=response):
                    inputs = self.consent(review, response, channel=channel, decision=decision)
                    self.assertEqual(self.tool.consent_receipt(p, inputs)['decision']['decision'], decision)
        # A selected Approve control, a typed Approve (a host answer of unprovable origin) and a typed
        # APPROVE authorize the same seal; only the recorded channel/response provenance differs.
        receipts = []
        for channel, response in (('interactive-control', 'Approve'), ('text-token', 'Approve'),
                                  ('interactive-control', 'APPROVE'), ('text-token', 'APPROVE')):
            with self.subTest(approve=(channel, response)):
                self.tool.save(p)
                approved = self.tool.approve_consent(self.consent(review, response, channel=channel))
                self.assertEqual(approved['state'], 'approved')
                self.tool.assert_approval(p, approved['approval'])
                self.assertEqual(approved['approval']['decision']['channel'], channel)
                receipts.append({k: v for k, v in approved['approval']['decision'].items()
                                 if k not in ('channel', 'response')})
        self.assertTrue(all(r == receipts[0] for r in receipts))
        self.assertEqual(self.tool.commit()['state'], 'verified')

    def test_decision_prose_near_tokens_and_worker_sources_rejected(self):
        self.prepare()
        review = self.tool.present()
        original = (self.tool.store / c.TRANSACTION).read_bytes()
        legacy_token = 'APPROVE ' + review['transaction_id'] + ' ' + review['prepared_sha256']
        # No prose interpretation: only a whole reply that is one approval word matches, on every channel.
        for channel in c.DECISION_CHANNELS:
            for text in ('Approved, approved, APPROVED !!!! :D', 'APPROVE it', 'Approve, looks good', 'yes', 'sure',
                         'Approved, proceed.', 'looks good', 'The user said "APPROVE"', 'APPROVE REJECT',
                         'NOT APPROVE', 'go, stage and seal', 'NOGO', 'Go?', 'Approve?', 'Do it?', legacy_token):
                with self.subTest(channel=channel, text=text), \
                        self.assertRaisesRegex(ValueError, 'Unrecognized lead developer decision'):
                    self.tool.approve_consent(self.consent(review, text, channel=channel, decision='approve'))
        # Provenance stays declared: an undeclared channel fails closed even with an exact token.
        for channel in ('conversation', '', None):
            with self.subTest(channel=channel), self.assertRaisesRegex(ValueError, 'Unknown lead developer decision channel'):
                self.tool.approve_consent(self.consent(review, 'APPROVE', channel=channel, decision='approve'))
        for response, decision in (('APPROVE', 'reject'), ('REJECT', 'approve')):
            with self.subTest(declared=decision), self.assertRaisesRegex(ValueError, 'does not match the lead developer'):
                self.tool.approve_consent(self.consent(review, response, decision=decision))
        for source in ('worker', 'repository', 'tool-output', 'quoted-user', 'prior-conversation'):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, 'user-authored'):
                self.tool.approve_consent(self.consent(review, source=source))
        missing = self.consent(review)
        missing.pop('source')
        legacy = dict(self.consent(review), text='Approved.', intent='approve-exact-commit')
        for invalid in (missing, legacy):
            with self.assertRaisesRegex(ValueError, 'Invalid lead developer decision'):
                self.tool.approve_consent(invalid)
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.tool.commit()

    def test_approval_words_approve_the_exact_seal_and_leave_reject_exact(self):
        p = self.prepare()
        review = self.tool.present()
        for channel in c.DECISION_CHANNELS:
            for text in ('Approved.', 'aPPROVE', 'Go', 'go!', 'Proceed.', 'Do it.', 'do it', 'APPROVE!'):
                with self.subTest(channel=channel, text=text):
                    self.tool.save(p)
                    approved = self.tool.approve_consent(self.consent(review, text, channel=channel, decision='approve'))
                    self.assertEqual(approved['state'], 'approved')
                    self.assertEqual(approved['approval']['decision']['response'], text)
                    self.tool.assert_approval(p, approved['approval'])
        # An approval word cannot be declared as a reject, and a reject reply is still only the exact REJECT.
        with self.assertRaisesRegex(ValueError, 'does not match the lead developer'):
            self.tool.approve_consent(self.consent(review, 'Do it.', decision='reject'))
        for text in ('Reject.', 'rejected', 'no go', 'REJECT it'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'Unrecognized lead developer decision'):
                self.tool.approve_consent(self.consent(review, text, decision='reject'))
        self.tool.save(p)
        self.tool.approve_consent(self.consent(review, 'Do it.', turn='fixture-user-turn-5'))
        self.assertEqual(self.tool.commit()['state'], 'verified')

    def test_decision_wrong_transaction_or_digest_fails(self):
        self.prepare()
        review = self.tool.present()
        original = (self.tool.store / c.TRANSACTION).read_bytes()
        for field, value in (('transaction_id', 'other-transaction'), ('prepared_sha256', '0' * 64)):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'currently presented'):
                self.tool.approve_consent(dict(self.consent(review), **{field: value}))
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.tool.commit()

    def test_reject_never_authorizes_and_withdraws_recorded_approval(self):
        self.prepare()
        review = self.tool.present()
        original = (self.tool.store / c.TRANSACTION).read_bytes()
        for channel, response in (('text-token', 'REJECT'), ('interactive-control', 'Reject'),
                                  ('text-token', 'Reject'), ('interactive-control', 'REJECT')):
            with self.subTest(channel=channel, response=response), self.assertRaisesRegex(ValueError, 'rejected'):
                self.tool.approve_consent(self.consent(review, response, channel=channel))
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.tool.commit()
        self.tool.approve_consent(self.consent(review, 'Approve', channel='interactive-control'))
        with self.assertRaisesRegex(ValueError, 'rejected'):
            self.tool.approve_consent(self.consent(review, 'REJECT', turn='fixture-user-turn-3'))
        self.assertEqual(self.tool.load(), json.loads(original)['packet'])
        with self.assertRaisesRegex(ValueError, 'No sealed lead developer approval'):
            self.tool.commit()
        self.tool.approve_consent(self.consent(review, turn='fixture-user-turn-4'))
        self.assertEqual(self.tool.commit()['state'], 'verified')

    def test_superseded_or_rejecting_receipts_cannot_commit(self):
        p = self.prepare()
        review = self.tool.present()
        sha = c.digest(c.canonical(p))
        natural = {'source': 'active-task-user', 'conversation_id': 'disposable-active-task',
                   'user_message_id': 'fixture-user-turn-2', 'text': 'Approved.',
                   'intent': 'approve-exact-commit', 'transaction_id': p['id'], 'prepared_sha256': sha}
        for receipt in ({'prepared_sha256': sha, 'kind': 'approver-consent-v1', 'consent': natural},
                        {'prepared_sha256': sha, 'kind': 'approver-decision-v1',
                         'decision': self.consent(review, 'REJECT')}):
            with self.subTest(kind=receipt['kind']):
                self.tool.save(dict(p, approval=receipt, state='approved'))
                with self.assertRaisesRegex(ValueError, 'Invalid exact approval receipt'):
                    self.tool.commit()
                self.assertEqual(self.tool.load()['state'], 'approved')
                self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.parent)

    def test_consent_cli_source_boundary_without_interactive_terminal(self):
        self.prepare()
        consent = self.consent(self.tool.present(), source='worker')
        external = Path(self.temp.name) / 'consent.json'
        external.write_text(json.dumps(consent), encoding='utf-8')
        argv = [sys.executable, '-B', c.__file__, '--repo', str(self.root),
                'approve-consent', '--consent', str(external)]
        rejected = subprocess.run(argv, capture_output=True)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(b'user-authored', rejected.stderr)
        self.assertEqual(self.tool.load()['state'], 'prepared')
        consent['source'] = 'active-task-user'
        external.write_text(json.dumps(consent), encoding='utf-8')
        accepted = subprocess.run(argv, capture_output=True, input=b'')
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
        self.assertEqual(json.loads(accepted.stdout)['approval']['decision'], consent)

    def test_consent_packet_changes_require_fresh_consent(self):
        p = self.prepare()
        consent = self.consent(self.tool.present())
        self.tool.approve_consent(consent)
        (self.root / 'a.txt').write_text('changed after consent\n')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.commit()
        (self.root / 'a.txt').write_text('new\n')
        # Simulate a new authenticated seal with different evidence, manifest or message facts.
        for field in ('evidence', 'manifest', 'message_b64', 'parent', 'tree'):
            successor = dict(p)
            successor[field] = 'changed-bound-fact'
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'currently presented'):
                self.tool.consent_receipt(successor, consent)
            with self.subTest(receipt=field), self.assertRaisesRegex(ValueError, 'currently presented'):
                self.tool.assert_approval(successor, self.tool.load()['approval'])

    def test_consent_recording_interruption_and_retry_idempotent(self):
        self.prepare()
        consent = self.consent(self.tool.present())
        original = (self.tool.store / c.TRANSACTION).read_bytes()
        with patch.object(c.os, 'replace', side_effect=OSError('fixture before atomic replacement')):
            with self.assertRaises(OSError):
                self.tool.approve_consent(consent)
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), original)
        save = self.tool.save
        def interrupted(packet):
            save(packet)
            raise KeyboardInterrupt('fixture after receipt persisted')
        with patch.object(self.tool, 'save', side_effect=interrupted), self.assertRaises(KeyboardInterrupt):
            self.tool.approve_consent(consent)
        recorded = (self.tool.store / c.TRANSACTION).read_bytes()
        restarted = c.Checkpoint(self.root)
        with patch.object(restarted, 'save', side_effect=AssertionError('retry must not rewrite')):
            self.assertEqual(restarted.approve_consent(consent)['state'], 'approved')
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), recorded)
        other = dict(consent, user_message_id='different-user-turn')
        with self.assertRaisesRegex(ValueError, 'Different approval'):
            restarted.approve_consent(other)

    def test_consent_tampered_parent_tree_message_remain_blocked(self):
        p = self.prepare()
        self.tool.approve_consent(self.consent(self.tool.present()))
        result = self.tool.commit()
        for field in ('parent', 'tree', 'message_b64'):
            altered = dict(result)
            altered[field] = p['message_b64'] if field == 'message_b64' else '0' * 40
            if field == 'message_b64':
                altered[field] = base64.b64encode(b'Changed message\n').decode()
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'parent/tree/exact message'):
                self.tool.commit_identity(altered)

    def worker_overrides(self):
        return {'GIT_PAGER': 'cat', 'GIT_CONFIG_COUNT': '2',
                'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': str(self.root.resolve()),
                'GIT_CONFIG_KEY_1': 'safe.directory', 'GIT_CONFIG_VALUE_1': str(self.root.resolve()) + '/*'}

    def test_worker_overrides_complete_transaction(self):
        with patch.dict(os.environ, self.worker_overrides()):
            self.tool = c.Checkpoint(self.root)
            self.ready()
            self.tool = c.Checkpoint(self.root)
            self.assertEqual(self.tool.commit()['state'], 'verified')

    def worker_prepare(self):
        with patch.dict(os.environ, self.worker_overrides()):
            self.tool = c.Checkpoint(self.root)
            return self.prepare()

    def test_worker_prepare_ordinary_approve_worker_commit(self):
        prepared = self.worker_prepare()
        self.tool = c.Checkpoint(self.root)
        self.assertNotEqual(self.tool.configuration(), prepared['configuration'])
        approved = self.approve()
        self.assertEqual(approved['configuration'], prepared['configuration'])
        self.assertEqual(approved['approval']['prepared_sha256'], c.digest(c.canonical(prepared)))
        with self.assertRaisesRegex(ValueError, 'Stale Git configuration'):
            self.tool.commit()
        self.assertEqual(self.tool.load()['state'], 'approved')
        self.assertEqual(self.tool.head(), self.parent)
        with patch.dict(os.environ, self.worker_overrides()):
            result = c.Checkpoint(self.root).commit()
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(self.tool.git('cat-file', 'commit', result['result_sha']).split(b'\n\n', 1)[1],
                         self.message.read_bytes())

    def assert_ordinary_approval_rejects(self, mutate, error, during_prompt=False):
        self.worker_prepare()
        self.tool = c.Checkpoint(self.root)
        called = []
        def confirm(prompt):
            called.append(True)
            mutate()
            return prompt.split('Type exactly: ')[1].split('\n')[0]
        if not during_prompt:
            mutate()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, error):
            self.tool.approve(confirm)
        self.assertEqual(bool(called), during_prompt)
        envelope = json.loads((self.tool.store / c.TRANSACTION).read_bytes())
        self.assertEqual(envelope['packet']['state'], 'prepared')
        self.assertNotIn('approval', envelope['packet'])

    def test_ordinary_approval_rejects_stale_head(self):
        self.assert_ordinary_approval_rejects(lambda: self.git('commit', '-m', 'Concurrent'), 'Stale HEAD')

    def test_ordinary_approval_rejects_stale_branch(self):
        self.assert_ordinary_approval_rejects(lambda: self.git('symbolic-ref', 'HEAD', 'refs/heads/other'), 'Git failed|Stale HEAD')

    def test_ordinary_approval_rejects_stale_tree(self):
        def mutate():
            (self.root / 'a.txt').write_text('changed\n')
            self.git('add', 'a.txt')
        self.assert_ordinary_approval_rejects(mutate, 'Stale staged tree')

    def test_ordinary_approval_rejects_stale_inventory(self):
        self.assert_ordinary_approval_rejects(lambda: self.git('reset', 'HEAD', '--', 'a.txt'), 'Stale staged manifest')

    def test_ordinary_approval_rejects_stale_working(self):
        self.assert_ordinary_approval_rejects(lambda: (self.root / 'b.txt').write_text('changed'), 'Stale working')

    def test_ordinary_approval_rejects_stale_message(self):
        self.assert_ordinary_approval_rejects(lambda: self.message.write_text('changed'), 'Stale message')

    def test_ordinary_approval_rechecks_after_prompt(self):
        self.assert_ordinary_approval_rejects(lambda: self.message.write_text('changed'), 'Stale message', True)

    def test_ordinary_approval_rejects_stale_policy(self):
        self.assert_ordinary_approval_rejects(lambda: (self.root / c.POLICY).write_text('{}'), 'Stale working')

    def test_ordinary_approval_rejects_stale_profile(self):
        def mutate():  # still a loadable profile file, but not the sealed bytes
            self.profiles['profiles']['test']['limitations'] = 'Edited after sealing.'
            self.write_json(c.PROFILES, self.profiles)
        self.assert_ordinary_approval_rejects(mutate, 'Stale working')

    def test_ordinary_approval_authenticates_packet(self):
        def mutate():
            path = self.tool.store / c.TRANSACTION
            envelope = json.loads(path.read_bytes())
            envelope['packet']['message_b64'] = base64.b64encode(b'Forged').decode()
            path.write_text(json.dumps(envelope))
        self.assert_ordinary_approval_rejects(mutate, 'seal mismatch')

    def test_ordinary_approval_keeps_hook_protection(self):
        self.assert_ordinary_approval_rejects(
            lambda: (self.root / '.git/hooks/commit-msg').write_text('#!/bin/sh\nexit 0\n'), 'hooks')

    def test_cross_environment_commit_rejects_later_configuration_change(self):
        self.worker_prepare()
        self.tool = c.Checkpoint(self.root)
        self.approve()
        self.git('config', 'user.name', 'Changed after preparation')
        with patch.dict(os.environ, self.worker_overrides()):
            with self.assertRaisesRegex(ValueError, 'Stale Git configuration'):
                c.Checkpoint(self.root).commit()
        self.assertEqual(self.tool.head(), self.parent)

    def test_worker_override_optional_forms(self):
        pair = self.worker_overrides()
        pair.pop('GIT_PAGER')
        for overrides in ({}, {'GIT_PAGER': 'cat'}, pair):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                self.assertEqual(c.Checkpoint(self.root).head(), self.parent)

    def test_worker_overrides_reject_malformed_changed_additional(self):
        valid = self.worker_overrides()
        cases = [{k: v for k, v in valid.items() if k != missing}
                 for missing in valid if missing != 'GIT_PAGER']
        for key, values in {
                'GIT_PAGER': ['', 'less', 'cat ', 'cat; echo unsafe'],
                'GIT_CONFIG_COUNT': ['', '0', '1', '3', '02', '-1', 'two'],
                'GIT_CONFIG_KEY_0': ['core.hooksPath', 'Safe.Directory', 'safe.directory '],
                'GIT_CONFIG_KEY_1': ['user.name', 'safe.directory '],
                'GIT_CONFIG_VALUE_0': ['*', str(self.root.parent), str(self.root) + '/'],
                'GIT_CONFIG_VALUE_1': ['*', str(self.root.parent) + '/*',
                                       str(self.root) + '-sibling/*', str(self.root),
                                       str(self.root) + '/**', str(self.root) + '/child/*'],
                'GIT_CONFIG_KEY_2': ['safe.directory'],
                'GIT_CONFIG_VALUE_2': ['*'],
                'GIT_CONFIG_PARAMETERS': ["'safe.directory=*'"],
                'GIT_INDEX_FILE': ['other-index'],
                'GIT_UNKNOWN': [''],
        }.items():
            cases.extend(dict(valid, **{key: value}) for value in values)
        cases.append(dict(valid, GIT_CONFIG_KEY_0=valid['GIT_CONFIG_KEY_1'],
                          GIT_CONFIG_VALUE_0=valid['GIT_CONFIG_VALUE_1'],
                          GIT_CONFIG_VALUE_1=valid['GIT_CONFIG_VALUE_0']))
        for overrides in cases:
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides), \
                    patch.object(c.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'GIT_'):
                    c.Checkpoint(self.root)
                run.assert_not_called()

    def test_worker_configuration_removal_is_stale(self):
        with patch.dict(os.environ, self.worker_overrides()):
            self.tool = c.Checkpoint(self.root)
            self.ready()
        with self.assertRaisesRegex(ValueError, 'Stale Git configuration'):
            c.Checkpoint(self.root).commit()
        self.assertEqual(self.tool.head(), self.parent)

    def test_worker_configuration_addition_is_stale(self):
        self.ready()
        with patch.dict(os.environ, self.worker_overrides()):
            with self.assertRaisesRegex(ValueError, 'Stale Git configuration'):
                c.Checkpoint(self.root).commit()
        self.assertEqual(self.tool.head(), self.parent)

    def test_changed_environment_on_existing_tool_rejected(self):
        self.ready()
        with patch.dict(os.environ, {'GIT_PAGER': 'less'}):
            with self.assertRaisesRegex(ValueError, 'GIT_PAGER'):
                self.tool.commit()
        self.assertEqual(self.tool.head(), self.parent)

    def test_worker_overrides_keep_hook_protection(self):
        with patch.dict(os.environ, self.worker_overrides()):
            self.tool = c.Checkpoint(self.root)
            (self.root / '.git/hooks/commit-msg').write_text('#!/bin/sh\nexit 0\n')
            with self.assertRaisesRegex(ValueError, 'hooks'):
                self.prepare()
            self.assertEqual(self.tool.inventory(), [])

    def test_prepare_explicit_manifest(self):
        p = self.prepare()
        self.assertEqual(p['state'], 'prepared')
        self.assertEqual(p['manifest'], ['a.txt'])
        self.assertEqual(len(p['evidence']), 3)
        self.assertTrue(all(x['exit_code'] == 0 for x in p['evidence']))

    def test_preexisting_index_rejected_unchanged(self):
        self.git('add', 'a.txt')
        before = self.git('write-tree')
        with self.assertRaisesRegex(ValueError, 'Pre-existing'):
            self.prepare()
        self.assertEqual(self.git('write-tree'), before)

    def test_unchanged_manifest_entry_rejected(self):
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.prepare(['a.txt', 'b.txt'])
        self.assertEqual(self.tool.load()['state'], 'prepare-failed')
        self.assertEqual(self.tool.inventory(), ['a.txt'])

    def test_extra_staged_path(self):
        self.ready()
        (self.root / 'b.txt').write_text('different\n')
        self.git('add', 'b.txt')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.tool.commit()
        self.assertEqual(self.tool.head(), self.parent)

    def test_invalid_manifest_paths(self):
        for name in ('../outside', '/absolute', 'C:/outside', 'a*', 'engine', './a.txt',
                     'a.txt/', 'a\\b', 'a/../b', '-option', 'a.txt:stream', 'a.txt.', 'a\nfile', 'CON', 'COM1.txt'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.tool.paths([name], self.policy)

    def test_duplicate_manifest(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.tool.paths(['a.txt', 'A.txt'], self.policy)

    def test_transient_ignored_frozen_rejection(self):
        for folder in ('work', 'tmp', 'ignored', 'frozen'):
            (self.root / folder).mkdir(exist_ok=True)
            (self.root / folder / 'file').write_text('data')
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                self.tool.paths([folder + '/file'], self.policy)

    def test_validation_failure_keeps_index_and_evidence(self):
        original = self.tool.runner
        self.tool.runner = lambda argv, root: dict(original(argv, root), exit_code=1)
        with self.assertRaisesRegex(ValueError, 'Validation failed'):
            self.prepare()
        self.assertEqual(self.tool.inventory(), ['a.txt'])
        self.assertEqual(self.tool.load()['evidence'][0]['exit_code'], 1)

    def test_required_suite_cannot_be_omitted(self):
        self.profiles['profiles']['test']['checks'].pop()
        self.write_json(c.PROFILES, self.profiles)
        with self.assertRaisesRegex(ValueError, 'missing required'):
            self.prepare()
        self.assertEqual(self.tool.inventory(), [])

    def test_required_command_cannot_be_substituted(self):
        self.profiles['profiles']['test']['checks'][0]['argv'] = ['echo', 'PASS']
        self.write_json(c.PROFILES, self.profiles)
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.prepare()

    def test_approval_denied(self):
        self.prepare()
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(ValueError, 'not supplied'):
            self.tool.approve(lambda _: 'yes')
        self.assertEqual(self.tool.load()['state'], 'prepared')

    def test_forged_approved_boolean(self):
        self.prepare()
        path = self.tool.store / c.TRANSACTION
        data = json.loads(path.read_bytes())
        data['packet'].update(approved=True, state='approved')
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'seal mismatch'):
            self.tool.commit()

    def test_exact_message_packet_tampering(self):
        self.ready()
        path = self.tool.store / c.TRANSACTION
        data = json.loads(path.read_bytes())
        data['packet']['message_b64'] = base64.b64encode(b'Other message').decode()
        path.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'seal mismatch'):
            self.tool.commit()

    def test_stale_message_file(self):
        self.ready()
        self.message.write_text('edited message')
        with self.assertRaisesRegex(ValueError, 'Stale message'):
            self.tool.commit()

    def test_stale_working_content(self):
        self.ready()
        (self.root / 'a.txt').write_text('newer\n')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.commit()

    def test_stale_unrelated_working_content(self):
        self.ready()
        (self.root / 'b.txt').write_text('unrelated mutation\n')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.commit()

    def test_stale_index_content(self):
        self.ready()
        (self.root / 'a.txt').write_text('newer\n')
        self.git('add', 'a.txt')
        with self.assertRaisesRegex(ValueError, 'Stale staged tree'):
            self.tool.commit()

    def test_stale_head(self):
        self.ready()
        self.git('commit', '-m', 'Concurrent commit')
        with self.assertRaisesRegex(ValueError, 'Stale HEAD'):
            self.tool.commit()

    def test_stale_policy(self):
        self.ready()
        with (self.root / c.POLICY).open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.commit()

    def test_stale_profile(self):
        self.ready()
        with (self.root / c.PROFILES).open('a') as stream:
            stream.write(' ')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.commit()

    def test_commit_exact_bytes_and_handoff(self):
        (self.root / 'b.txt').write_text('unrelated work\n')
        self.ready()
        p = self.tool.commit()
        raw = self.tool.git('cat-file', 'commit', p['result_sha'])
        self.assertEqual(raw.split(b'\n\n', 1)[1], self.message.read_bytes())
        self.assertEqual(p['parent'], self.parent)
        self.assertEqual(self.git('rev-parse', 'HEAD^{tree}').strip(), p['tree'])
        self.assertEqual(p['state'], 'verified')
        handoff = json.loads((self.tool.store / 'handoff.json').read_bytes())
        self.assertEqual(handoff['manifest'], ['a.txt'])
        self.assertEqual(handoff['evidence'], p['evidence'])
        self.assertEqual(handoff['result_sha'], self.tool.head())
        self.assertIn('b.txt', p['post_status'])
        self.assertEqual((self.root / 'b.txt').read_text(), 'unrelated work\n')

    def new_component(self):
        component = self.root / 'new-component'
        component.mkdir()
        (component / 'source.txt').write_text('new component\n')
        (component / 'unrelated.txt').write_text('preserve me\n')
        return ['new-component/source.txt']

    def test_new_component_commit_and_repeat_verification(self):
        prepared = self.prepare(self.new_component())
        self.assertIn('?? new-component/', prepared['residual'])
        self.assertIn('?? new-component/unrelated.txt', prepared['prepared_status'])
        self.approve()
        result = self.tool.commit()
        self.assertEqual(result['state'], 'verified')
        self.assertEqual(self.tool.inventory(), [])
        self.assertIn('new-component/source.txt', self.git('ls-files'))
        self.assertEqual((self.root / 'new-component/unrelated.txt').read_text(), 'preserve me\n')
        handoff = (self.tool.store / 'handoff.json').read_bytes()
        self.tool.verify()
        self.assertEqual((self.tool.store / 'handoff.json').read_bytes(), handoff)

    def test_new_component_unrelated_status_change_rejected(self):
        self.prepare(self.new_component())
        self.approve()
        # Interrupt after Git commits, before verification, as in restart recovery.
        with patch.object(self.tool, 'verify', return_value=None):
            self.tool.commit()
        # Transient untracked content is excluded from hashes but still guarded by status.
        (self.root / 'work').mkdir()
        (self.root / 'work/residual.txt').write_text('unrelated transient')
        with self.assertRaisesRegex(ValueError, 'Post-commit unrelated status changed'):
            self.tool.verify()
        self.assertEqual(self.tool.load()['state'], 'commit-attempted')

    def test_new_component_unrelated_content_change_rejected(self):
        self.prepare(self.new_component())
        self.approve()
        with patch.object(self.tool, 'verify', return_value=None):
            self.tool.commit()
        (self.root / 'new-component/unrelated.txt').write_text('changed\n')
        with self.assertRaisesRegex(ValueError, 'Post-commit working content changed'):
            self.tool.verify()

    def test_message_without_final_newline(self):
        self.message.write_bytes(b'Subject without newline')
        self.ready()
        p = self.tool.commit()
        self.assertEqual(self.tool.git('cat-file', 'commit', p['result_sha']).split(b'\n\n', 1)[1],
                         self.message.read_bytes())

    def test_commit_failure_no_cleanup_or_retry(self):
        self.ready()
        original = subprocess.run
        def failed(argv, **kwargs):
            if 'commit' in argv:
                return subprocess.CompletedProcess(argv, 1, b'', b'injected commit failure')
            return original(argv, **kwargs)
        with patch.object(c.subprocess, 'run', side_effect=failed), self.assertRaisesRegex(ValueError, 'Commit failed'):
            self.tool.commit()
        self.assertEqual(self.tool.head(), self.parent)
        self.assertEqual(self.tool.inventory(), ['a.txt'])
        self.assertEqual(self.tool.load()['state'], 'commit-attempted')
        with self.assertRaisesRegex(ValueError, 'No sealed'):
            self.tool.commit()

    def test_post_commit_mismatch_rejected(self):
        self.ready()
        p = self.tool.load()
        p['state'] = 'committing'
        self.tool.save(p)
        self.git('commit', '-m', 'Wrong message')
        with self.assertRaisesRegex(ValueError, 'message mismatch'):
            self.tool.verify()
        self.assertFalse((self.tool.store / 'handoff.json').exists())

    def test_resume_after_commit_before_verification(self):
        self.ready()
        p = self.tool.load()
        p['state'] = 'committing'
        self.tool.save(p)
        self.tool.git('commit', '--cleanup=verbatim', '--file=-', data=self.message.read_bytes())
        resumed = c.Checkpoint(self.root)
        self.assertEqual(resumed.verify()['state'], 'verified')
        artifact = (resumed.store / 'handoff.json').read_bytes()
        resumed.verify()
        self.assertEqual((resumed.store / 'handoff.json').read_bytes(), artifact)

    def test_second_transaction_blocked(self):
        self.ready()
        self.tool.commit()
        with self.assertRaisesRegex(ValueError, 'Existing transaction'):
            self.prepare()

    def test_explicit_deletion(self):
        (self.root / 'b.txt').unlink()
        self.prepare(['b.txt'])
        self.approve()
        self.assertEqual(self.tool.commit()['state'], 'verified')

    def test_wrong_root_or_branch(self):
        with self.assertRaisesRegex(ValueError, 'exact repository root'):
            c.Checkpoint(self.root / 'governance' / 'tools')
        with self.assertRaisesRegex(ValueError, 'Unexpected HEAD/branch'):
            self.tool.prepare(['a.txt'], self.message, 'test', self.parent, 'refs/heads/wrong', 'scope')

    def test_cli_has_no_unattended_approval(self):
        self.prepare()
        p = subprocess.run([sys.executable, '-B', str(Path(c.__file__).resolve()), '--repo', str(self.root),
                            'approve'], input=b'yes\n', capture_output=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn(b'interactive lead developer terminal', p.stderr)

    def test_active_hook_rejected(self):
        (self.root / '.git/hooks/commit-msg').write_text('#!/bin/sh\nexit 0\n')
        with self.assertRaisesRegex(ValueError, 'hooks'):
            self.prepare()

    def test_operation_lock(self):
        with self.tool.lock(), self.assertRaisesRegex(ValueError, 'lock exists'):
            with self.tool.lock():
                pass

    def test_runtime_pending_is_not_pass(self):
        (self.root / 'runtime.txt').write_text('new runtime\n')
        self.prepare(['runtime.txt'])
        self.approve()
        original = self.tool.runner
        def post_fail(argv, root):
            return dict(original(argv, root), exit_code=1, stdout='',
                        stderr='FAIL: Stale implementation checkpoint: runtime history has advanced\n')
        self.tool.runner = post_fail
        with patch.object(c.Checkpoint, 'runtime_pending', return_value='checkpoint committed; continuity follow-up pending'):
            result = self.tool.commit()
        self.assertEqual(result['state'], 'follow-up-pending')
        self.assertNotEqual(result['continuity'], 'PASS')
        self.assertEqual(result['post_continuity']['exit_code'], 1)
        with self.assertRaisesRegex(ValueError, 'Existing transaction'):
            self.prepare()

    def test_runtime_unrelated_failure_blocked(self):
        (self.root / 'runtime.txt').write_text('new runtime\n')
        self.prepare(['runtime.txt'])
        self.approve()
        self.tool.runner = lambda argv, root: {'argv': argv, 'exit_code': 1, 'stdout': '',
                                               'stderr': 'FAIL: unrelated frozen mutation', 'duration_seconds': 0}
        with self.assertRaisesRegex(ValueError, 'unexpected failure'):
            self.tool.commit()
        self.assertEqual(self.tool.load()['state'], 'verification-blocked')
        self.assertFalse((self.tool.store / 'handoff.json').exists())

    def test_pending_classifier_independent_checks(self):
        from governance.tools.continuity import validate_continuity as v
        result = {'exit_code': 1, 'stdout': '',
                  'stderr': 'FAIL: Stale implementation checkpoint: runtime history has advanced\n'}
        contract = {'completion_evidence_fact': 'completion', 'facts': {
            'completion': {'source': 'report.md#evidence'},
            'frozen_contracts_and_fixtures_unchanged': {'source': 'git:frozen-invariants'}}}
        with patch.object(v, 'validate_docs') as docs, \
                patch.object(v, 'read', return_value=json.dumps(contract)), \
                patch.object(v, 'extract_facts', return_value={'implementation_checkpoint': self.parent}), \
                patch.object(v, 'frozen_invariants') as frozen, \
                patch.object(v, 'source_text', return_value='anchored evidence'), \
                patch.object(self.tool, 'git', FakeGit({'report.md': 'anchored evidence'})):
            state = self.tool.runtime_pending({'result_sha': 'a' * 40}, result)
            self.assertIn('pending', state)
            docs.assert_called_once_with(self.root)
            frozen.assert_called_once_with(self.root, 'a' * 40)

    def test_intent_to_add_is_preexisting_staging(self):
        (self.root / 'intent.txt').write_text('intent')
        self.git('add', '--intent-to-add', 'intent.txt')
        with self.assertRaisesRegex(ValueError, 'Pre-existing'):
            self.prepare()

    def test_partial_staging_failure_reports_actual_inventory(self):
        original = self.tool.git
        def fail_after_add(*args, **kwargs):
            value = original(*args, **kwargs)
            if args[0] == 'add':
                raise ValueError('injected staging interruption')
            return value
        with patch.object(self.tool, 'git', side_effect=fail_after_add), self.assertRaisesRegex(ValueError, 'No rollback'):
            self.prepare()
        self.assertEqual(self.tool.load()['actual_inventory'], ['a.txt'])
        self.assertEqual(self.tool.head(), self.parent)

    def test_stale_git_configuration(self):
        self.ready()
        self.git('config', 'core.autocrlf', 'false')
        with self.assertRaisesRegex(ValueError, 'Stale Git configuration'):
            self.tool.commit()

    def test_post_commit_tree_and_parent_mismatch(self):
        self.ready()
        p = self.tool.commit()
        for field in ('tree', 'parent'):
            changed = dict(p, state='committing')
            changed[field] = '0' * 40
            self.tool.save(changed)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'mismatch'):
                self.tool.verify()

    def test_shipped_profile_rejects_runtime(self):
        original = rp.ROOT
        policy = json.loads((original / c.POLICY).read_bytes())
        profiles = json.loads((original / c.PROFILES).read_bytes())
        with self.assertRaisesRegex(ValueError, 'declares no runtime gates'):
            self.tool.check_commands(profiles['profiles']['tooling'], policy, True)

    def test_validation_command_failure_is_captured(self):
        result = c.run(['no-such-checkpoint-test-executable-29184'], self.root)
        self.assertEqual(result['exit_code'], -1)
        self.assertTrue(result['stderr'])

    def test_untracked_input_content_is_sealed(self):
        extra = self.root / 'extra.txt'
        extra.write_text('before')
        self.ready()
        extra.write_text('after')
        with self.assertRaisesRegex(ValueError, 'Stale working'):
            self.tool.commit()


class ScratchLifecycleTest(unittest.TestCase):
    setUp = CheckpointTest.setUp
    git = CheckpointTest.git
    write_json = CheckpointTest.write_json
    prepare = CheckpointTest.prepare
    approve = CheckpointTest.approve
    ready = CheckpointTest.ready

    def test_verified_cleanup_is_idempotent_and_next_transaction_starts(self):
        self.ready()
        committed = self.tool.commit()
        self.assertEqual(self.tool.store, self.root / '.git' / 'governance')  # the Git directory, not the working tree
        self.assertEqual(self.tool.finish()['result_sha'], committed['result_sha'])
        self.assertEqual(self.tool.finish(), {'state': 'idle'})
        self.assertEqual(sorted(p.name for p in self.tool.store.iterdir()), sorted([c.RECEIPT_KEY, ac.LANDING]))  # the landing record outlives finish
        self.parent = self.tool.head()
        (self.root / 'a.txt').write_text('another transaction\n')
        self.assertEqual(self.prepare()['state'], 'prepared')

    def test_prepared_and_failed_transactions_cannot_finish(self):
        self.prepare()
        before = (self.tool.store / c.TRANSACTION).read_bytes()
        with self.assertRaisesRegex(ValueError, 'No commit attempt'):
            self.tool.finish()
        self.assertEqual((self.tool.store / c.TRANSACTION).read_bytes(), before)

    def test_cleanup_restart_preserves_unknown_files(self):
        self.ready(); self.tool.commit()
        unknown = self.tool.store / 'operator-notes.txt'
        unknown.write_text('preserve')
        real_unlink = Path.unlink
        def interrupt(path, *args, **kwargs):
            if path.name == c.TRANSACTION:
                raise OSError('cleanup interrupted')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupt), self.assertRaises(OSError):
            self.tool.finish()
        self.assertFalse((self.tool.store / 'handoff.json').exists())
        self.assertTrue((self.tool.store / 'cleanup.json').exists())
        resumed = c.Checkpoint(self.root)
        self.assertEqual(resumed.finish()['state'], 'idle')
        self.assertEqual(unknown.read_text(), 'preserve')

    def test_cleanup_rejects_changed_journal_and_working_content(self):
        self.ready(); self.tool.commit()
        real_unlink = Path.unlink
        def interrupt(path, *args, **kwargs):
            if path.name == c.TRANSACTION:
                raise OSError('cleanup interrupted')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', interrupt), self.assertRaises(OSError):
            self.tool.finish()
        (self.root / 'b.txt').write_text('unrelated change')
        with self.assertRaisesRegex(ValueError, 'validated state changed'):
            self.tool.finish()
        (self.tool.store / 'cleanup.json').write_text('{}')
        with self.assertRaises((KeyError, ValueError)):
            self.tool.finish()
        self.assertTrue((self.tool.store / c.TRANSACTION).exists())

    def test_the_store_is_outside_the_working_tree_and_needs_no_ignore_rule(self):
        (self.root / '.gitignore').write_text('')
        tool = c.Checkpoint(self.root)
        self.assertEqual(tool.store, tool.gitdir / 'governance')
        self.assertFalse(tool.store.is_relative_to(self.root / 'tmp'))
        self.assertEqual(self.git('status', '--porcelain', '--untracked-files=all', '--', 'tmp', 'governance/.git').strip(), '')

    def test_a_store_that_is_a_link_is_rejected(self):
        self.tool.store.rmdir()
        try:
            os.symlink(Path(self.temp.name), self.tool.store, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links are not available here')
        self.addCleanup(lambda: os.path.islink(self.tool.store) and os.rmdir(self.tool.store))
        with self.assertRaisesRegex(ValueError, 'Symlink transaction store prohibited'):
            c.Checkpoint(self.root)

    def test_a_missing_key_stops_the_tool_and_is_never_made_again(self):
        self.prepare()
        key = self.tool.store / c.RECEIPT_KEY
        key.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing local receipt key'):
            self.tool.key()
        with self.assertRaisesRegex(ValueError, 'receipt key is missing.*holds .*transaction.json'):
            self.tool.ensure_key()
        self.assertFalse(key.exists())

    def test_the_key_is_made_at_the_first_use_of_an_empty_store(self):
        key = self.tool.store / c.RECEIPT_KEY
        key.unlink(missing_ok=True)
        for leftover in self.tool.store.iterdir():
            leftover.unlink()
        self.tool.ensure_key()
        self.assertEqual(len(key.read_bytes()), 32)
        if os.name != 'nt':
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)

    def test_repeat_verify_checks_approval_and_branch(self):
        self.ready(); packet = self.tool.commit()
        self.git('checkout', '-b', 'other')
        with self.assertRaisesRegex(ValueError, 'branch/configuration'):
            self.tool.verify()
        self.git('checkout', 'main')
        packet['approval']['confirmation'] = 'forged'
        self.tool.save(packet)
        with self.assertRaisesRegex(ValueError, 'approval receipt'):
            self.tool.verify()

    def pending(self):
        (self.root / 'runtime.txt').write_text('new runtime\n')
        self.prepare(['runtime.txt']); self.approve()
        with patch.object(c.Checkpoint, 'runtime_pending', return_value='pending'):
            return self.tool.commit()

    def test_pending_cannot_cleanup_and_unrelated_continuation_blocked(self):
        self.pending()
        with self.assertRaisesRegex(ValueError, 'Continuity pending'):
            self.tool.finish()
        self.tool.continue_records()
        self.assertEqual(self.tool.continue_records()['state'], 'follow-up-pending')
        with self.assertRaisesRegex(ValueError, 'only continuity records'):
            self.prepare()
        with self.assertRaisesRegex(ValueError, 'still pending'):
            self.tool.finish()

    def test_continuity_followup_commits_and_cleans_both_packets(self):
        previous = self.pending()
        self.tool.continue_records()
        (self.root / 'devlog').mkdir()
        (self.root / 'devlog/runtime.md').write_text('Durable runtime identity: ' + previous['result_sha'])
        self.parent = self.tool.head()
        self.prepare(['devlog/runtime.md']); self.approve()
        packet = self.tool.commit()
        self.assertEqual(packet['parent'], previous['result_sha'])
        self.tool.finish()
        self.assertFalse((self.tool.store / 'pending.json').exists())
        self.assertIn(previous['result_sha'], self.git('show', 'HEAD:devlog/runtime.md'))

    def test_pending_tamper_blocks_preparation(self):
        self.pending(); self.tool.continue_records()
        (self.tool.store / 'pending.json').write_text('{}')
        with self.assertRaises((ValueError, KeyError)):
            self.prepare(['devlog/new.md'])
        self.assertEqual(self.tool.inventory(), [])


class RequiredContinuityCheckpointTest(unittest.TestCase):
    setUp = CheckpointTest.setUp
    git = CheckpointTest.git
    write_json = CheckpointTest.write_json
    prepare = CheckpointTest.prepare
    approve = CheckpointTest.approve
    ready = CheckpointTest.ready

    def test_uncommitted_progress_cannot_complete(self):
        self.durable_gate.stop()
        (self.root / 'docs').mkdir()
        progress = self.root / rp.PROGRESS
        progress.write_text('previous durable state\n')
        self.git('add', '--', rp.PROGRESS)
        self.git('commit', '-m', 'Initial continuity record')
        self.parent = self.tool.head()
        progress.write_text('uncommitted continuity repair\n')
        self.ready()
        with self.assertRaisesRegex(ValueError, 'Progress must be committed'):
            self.tool.commit()
        self.assertEqual(self.tool.load()['state'], 'verification-blocked')
        with self.assertRaises(ValueError):
            self.tool.finish()

    def test_required_log_and_index_must_be_committed(self):
        self.durable_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text('fixture committed progress\n')
        from governance.tools.continuity import validate_continuity as continuity
        (self.root / 'devlog').mkdir()
        name = 'devlog/runtime.md'
        log = self.root / name
        log.write_text(f'Repository checkpoint: `{self.parent}`\n')
        index = self.root / 'devlog/README.md'
        index.write_text('Log index\n')
        (self.root / continuity.CONTRACT).write_text('{}')
        self.git('add', '--', 'devlog', rp.PROGRESS, continuity.CONTRACT)
        self.git('commit', '-m', 'Fixture records without indexed runtime')
        index.write_text('[Runtime](runtime.md)\n')
        with patch.object(continuity, 'verify_repository', return_value=(None,
                    {'implementation_checkpoint': self.parent})), \
                patch.object(continuity, 'validate_docs', return_value=[name]):
            with self.assertRaisesRegex(ValueError, 'indexed in the committed'):
                self.tool.durable_continuity()
            self.git('add', '--', 'devlog/README.md')
            self.git('commit', '-m', 'Index the runtime')
            self.tool.durable_continuity()
            log.write_text(log.read_text() + 'uncommitted evidence\n')
            with self.assertRaisesRegex(ValueError, 'log must be committed'):
                self.tool.durable_continuity()

    def test_committed_runtime_obligation_survives_scratch_loss(self):
        self.checkpoint_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text(
            '| implementation_checkpoint | ' + self.parent + ' |\n')
        self.git('add', '--', rp.PROGRESS)
        self.git('commit', '-m', 'Existing durable checkpoint')
        (self.root / 'runtime.txt').write_text('new runtime history\n')
        self.git('add', '--', 'runtime.txt')
        self.git('commit', '-m', 'Runtime whose local packet is unavailable')
        self.assertFalse((self.tool.store / c.TRANSACTION).exists())
        with self.assertRaisesRegex(ValueError, 'only continuity records'):
            self.tool.preparation_gate(['a.txt'])
        self.tool.preparation_gate([rp.PROGRESS])

    def test_outstanding_runtime_permits_records_in_the_configured_log_folder_only(self):
        self.checkpoint_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text(
            '| implementation_checkpoint | ' + self.parent + ' |\n')
        settings = self.root / ac.DEVLOG
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"version": 1, "folder": "notes", "date_format": "%Y%m%d"}\n')
        self.git('add', '--', rp.PROGRESS, ac.DEVLOG)
        self.git('commit', '-m', 'Existing durable checkpoint and log settings')
        (self.root / 'runtime.txt').write_text('new runtime history\n')
        self.git('add', '--', 'runtime.txt')
        self.git('commit', '-m', 'Runtime whose local packet is unavailable')
        self.tool.preparation_gate(['notes/20261016-entry.md', rp.PROGRESS])
        for name in ('devlog/20261016-entry.md', 'a.txt'):
            with self.assertRaisesRegex(ValueError, 'only continuity records'):
                self.tool.preparation_gate([name])

    def test_outstanding_runtime_with_the_devlog_off_permits_the_progress_record_only(self):
        self.checkpoint_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text(
            '| implementation_checkpoint | ' + self.parent + ' |\n')
        settings = self.root / ac.DEVLOG
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"enabled": false}\n')
        self.git('add', '--', rp.PROGRESS, ac.DEVLOG)
        self.git('commit', '-m', 'Existing durable checkpoint and a project without a devlog')
        (self.root / 'runtime.txt').write_text('new runtime history\n')
        self.git('add', '--', 'runtime.txt')
        self.git('commit', '-m', 'Runtime whose local packet is unavailable')
        self.tool.preparation_gate([rp.PROGRESS])
        for name in ('devlog/20261016-entry.md', 'a.txt'):
            with self.assertRaisesRegex(ValueError, 'only continuity records'):
                self.tool.preparation_gate([name])
        settings.write_text('{"enabled": true}\n')
        self.tool.preparation_gate(['devlog/20261016-entry.md', rp.PROGRESS])

    def test_completion_with_the_devlog_off_needs_no_log_entry_and_with_it_on_still_does(self):
        self.durable_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text('fixture committed progress\n')
        from governance.tools.continuity import validate_continuity as continuity
        settings = self.root / ac.DEVLOG
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"enabled": false}\n')
        self.git('add', '--', rp.PROGRESS, ac.DEVLOG)
        self.git('commit', '-m', 'Fixture records without any devlog')
        with patch.object(continuity, 'verify_repository', return_value=(None,
                    {'implementation_checkpoint': self.parent})), \
                patch.object(continuity, 'validate_docs', return_value=[]):
            self.tool.durable_continuity()
            settings.write_text('{"enabled": true}\n')
            with self.assertRaisesRegex(ValueError, 'one matching log'):
                self.tool.durable_continuity()
            settings.unlink()
            with self.assertRaisesRegex(ValueError, 'one matching log'):
                self.tool.durable_continuity()

    def test_missing_working_progress_cannot_disable_preparation_or_completion(self):
        self.checkpoint_gate.stop()
        self.durable_gate.stop()
        for operation in (lambda: self.tool.preparation_gate(['a.txt']), self.tool.durable_continuity):
            with self.assertRaisesRegex(ValueError, 'Missing continuity evidence: docs/PROGRESS.md'):
                operation()
        self.assertEqual(self.tool.inventory(), [])
        self.assertEqual(self.tool.head(), self.parent)

    def test_missing_or_malformed_committed_checkpoint_cannot_disable_preparation(self):
        self.checkpoint_gate.stop()
        (self.root / 'docs').mkdir()
        path = self.root / rp.PROGRESS
        row = '| implementation_checkpoint | ' + self.parent + ' |\n'
        for text in ('No checkpoint row\n', '| implementation_checkpoint | nope |\n',
                     '| implementation_checkpoint | |\n', row + row,
                     row.rstrip() + ' extra |\n'):
            with self.subTest(text=text):
                path.write_text(text)
                self.git('add', '--', rp.PROGRESS)
                self.git('commit', '-m', 'Malformed fixture continuity record')
                head = self.tool.head()
                # A valid proposed repair must not disguise malformed committed evidence.
                path.write_text(row)
                with self.assertRaisesRegex(ValueError, 'Committed implementation_checkpoint'):
                    self.tool.preparation_gate(['a.txt'])
                self.assertEqual(self.tool.head(), head)
                self.assertEqual(self.tool.inventory(), [])

    def adoption_fixture(self, devlog=True):
        """HEAD holds a progress record without the row; the working tree holds the adopting change."""
        self.checkpoint_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text('# Progress\n\nNo checkpoint row yet.\n')
        settings = self.root / ac.DEVLOG
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"enabled": true}\n')
        self.git('add', '--', rp.PROGRESS, ac.DEVLOG)
        self.git('commit', '-m', 'Runtime rows without a checkpoint row')
        latest = self.git('log', '-1', '--format=%H', 'HEAD', '--', 'runtime.txt').strip()
        (self.root / rp.PROGRESS).write_text('| implementation_checkpoint | ' + latest + ' |\n')
        (self.root / ac.PROJECT_CONTRACT).write_text(CONTRACT_WITH_FACT)
        (self.root / 'devlog').mkdir()
        (self.root / 'devlog/20261016-entry.md').write_text('Repository checkpoint: `' + latest + '`\n')
        return latest, [rp.PROGRESS, ac.PROJECT_CONTRACT, 'devlog/20261016-entry.md', 'devlog/README.md']

    def test_the_change_that_adds_the_first_checkpoint_row_passes_the_gate(self):
        latest, manifest = self.adoption_fixture()
        self.tool.preparation_gate(manifest)

    def test_the_adopting_change_of_a_project_without_a_devlog_needs_no_entry(self):
        latest, manifest = self.adoption_fixture()
        (self.root / ac.DEVLOG).write_text('{"enabled": false}\n')
        self.tool.preparation_gate([n for n in manifest if not n.startswith('devlog/')])

    def test_an_adoption_short_of_the_row_the_fact_or_the_entry_is_refused(self):
        latest, manifest = self.adoption_fixture()
        progress, contract = self.root / rp.PROGRESS, self.root / ac.PROJECT_CONTRACT
        row = progress.read_text()
        without = lambda name: [n for n in manifest if not n.startswith(name)]
        cases = (
            ('no row', lambda: progress.write_text('No row\n'), manifest, 'exactly one row'),
            ('two rows', lambda: progress.write_text(row + row), manifest, 'exactly one row'),
            ('not the latest runtime commit', lambda: progress.write_text(
                '| implementation_checkpoint | ' + 'a' * 40 + ' |\n'), manifest, 'latest runtime commit'),
            ('no contract fact', lambda: contract.write_text('{"facts": {}}\n'), manifest, 'the fact'),
            ('contract not in the change', lambda: None, without(ac.PROJECT_CONTRACT), 'the fact'),
            ('no devlog entry', lambda: None, without('devlog/'), 'devlog entry'),
            ('touches a runtime path', lambda: None, manifest + ['runtime.txt'], 'runtime paths'),
        )
        for label, damage, names, message in cases:
            with self.subTest(label):
                damage()
                with self.assertRaisesRegex(ValueError, 'Adopting implementation_checkpoint: .*' + message):
                    self.tool.preparation_gate(names)
                progress.write_text(row)
                contract.write_text(CONTRACT_WITH_FACT)
        with self.subTest('progress record not in the change'):
            with self.assertRaisesRegex(ValueError, 'Committed implementation_checkpoint'):
                self.tool.preparation_gate(manifest[1:])
        self.assertEqual(self.tool.inventory(), [])

    def test_a_project_with_a_committed_row_keeps_the_committed_row_rule(self):
        latest, manifest = self.adoption_fixture()
        self.git('add', '--', rp.PROGRESS)
        self.git('commit', '-m', 'Add the checkpoint row')
        self.tool.preparation_gate(['a.txt'])  # The row names the latest runtime commit: any change is allowed.
        (self.root / 'runtime.txt').write_text('new runtime history\n')
        self.git('add', '--', 'runtime.txt')
        self.git('commit', '-m', 'Runtime moves past the committed row')
        with self.assertRaisesRegex(ValueError, 'only continuity records'):
            self.tool.preparation_gate(['a.txt', *manifest])  # What adopts without a row is no exemption with one.
        self.tool.preparation_gate([rp.PROGRESS, 'devlog/20261016-entry.md'])

    def test_missing_committed_progress_is_not_replaced_by_working_repair(self):
        self.checkpoint_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text(
            '| implementation_checkpoint | ' + self.parent + ' |\n')
        with self.assertRaisesRegex(ValueError, 'Git failed'):
            self.tool.preparation_gate(['a.txt'])

    def test_completion_requires_contract_even_after_a_reported_check_pass(self):
        self.durable_gate.stop()
        (self.root / 'docs').mkdir()
        (self.root / rp.PROGRESS).write_text('fixture progress\n')
        self.git('add', '--', rp.PROGRESS)
        self.git('commit', '-m', 'Disposable continuity fixture')
        from governance.tools.continuity import validate_continuity as continuity
        # Isolate contract admission from unrelated document/link obligations.
        with patch.object(continuity, 'validate_docs', return_value=[]):
            for data in (None, '{', '{}'):
                with self.subTest(contract=data):
                    path = self.root / continuity.CONTRACT
                    if data is not None:
                        path.write_text(data)
                    with self.assertRaises((ValueError, KeyError)):
                        self.tool.completed_continuity({'exit_code': 0})


if __name__ == '__main__':
    unittest.main()
