"""The transaction lifecycle: prepare, preview, approval, commit, verify, continuity and finish.

One of the four components of the checkpoint tool; governance/tools/checkpoint.py composes them.
"""
from governance.tools.lifecycle import approval_words
from governance.tools.continuity import artifact_classification
import base64
from governance.tools.worktrees import claims
from governance.tools.lifecycle import early_approval
import hashlib
import hmac
import json
from governance.tools.coordination import local_jobs
import os
from pathlib import Path
import posixpath
from governance.tools.lifecycle import review_render
import secrets
import subprocess
from governance.tools.lifecycle.checkpoint_state import CLEANUP, EARLY_USES, HANDOFF, PENDING, REF_POLICY, TRANSACTION, atomic, canonical, digest, require


# States before any commit has begun: only these transactions can be abandoned.
ABANDONABLE = ('preparing', 'prepare-failed', 'validated', 'prepared', 'approved')

# G018 lead developer decisions: one exact response lookup, never language interpretation.
# Validate the decision, not the presentation mechanism: the channel is recorded
# provenance and never changes what an exact control label, token or value means.
DECISION_CONTROLS = (('Approve', 'approve'), ('Reject', 'reject'))

DECISION_TOKENS = (('APPROVE', 'approve'), ('REJECT', 'reject'))

DECISION_CHANNELS = ('interactive-control', 'text-token')


class Lifecycle:
    def abandon(self, reason=None):
        """Withdraw a transaction that has not begun to commit, changing no content.

        Only the transaction's own staged paths are unstaged and the scratch slot is cleared. Every working
        byte, ref and unrelated index entry is untouched. The returned result is the whole record: scratch is
        disposable and a withdrawn seal can never be reused, so nothing is archived. Safe to repeat.
        """
        require(reason is None or (isinstance(reason, str) and reason.strip()), 'The reason, if given, must not be blank')
        self.quiet_operation()
        slot = self.store / TRANSACTION
        if not slot.exists():
            return {'state': 'idle'}
        packet = self.load()
        require(packet['state'] in ABANDONABLE,
                'Cannot abandon a transaction in state %s; use inspect, verify or finish' % packet['state'])
        require(self.head() == packet['parent'] and self.branch() == packet['branch'],
                'HEAD or branch moved since preparation; inspect instead of abandoning')
        staged = self.inventory()
        stray = sorted(set(staged) - set(packet['manifest']))
        require(not stray, 'The index holds paths outside the transaction; they are not touched: ' + ', '.join(stray))
        if staged:
            self.git('restore', '--staged', '--', *staged)
        require(self.inventory() == [], 'Unstaging did not clear the transaction paths')
        slot.unlink()
        return {'state': 'idle', 'abandoned': packet['id'], 'was': packet['state'],
                'approval_withdrawn': 'approval' in packet, 'manifest': packet['manifest'], 'unstaged': staged,
                'reason': reason.strip() if reason else None}

    def preparation_gate(self, manifest):
        require(not (self.store / CLEANUP).exists(), 'Resume finish before preparation')
        if self.branch() == 'refs/heads/main':
            require(self.git('worktree', 'list', '--porcelain', '-z').decode().count('worktree ') == 1,
                    'Main is integration territory while other worktrees exist')
        # Committed continuity owns the obligation even if local scratch is lost.
        from governance.tools.continuity import validate_continuity as continuity
        runtime_paths = continuity.scope(self.root)[1]
        if runtime_paths:  # A project with no runtime boundary has no runtime checkpoint to keep current.
            checkpoint = self.committed_checkpoint()
            latest = self.git('log', '-1', '--format=%H', 'HEAD', '--', *runtime_paths).decode().strip()
            require(latest, 'Runtime history unavailable for committed continuity')
            if checkpoint != latest:
                require(all(self.continuity_record(n) for n in manifest),
                        'Outstanding runtime continuity permits only continuity records')
        pending = self.store / PENDING
        if pending.exists():
            previous = self.load_path(pending)
            self.commit_identity(previous)
            require(self.head() == previous['result_sha'], 'Pending runtime HEAD changed')
            require(all(self.continuity_record(n) for n in manifest),
                    'Pending runtime permits only continuity records')

    def continuity_record(self, name):
        """Whether a path is a continuity record: the progress record, or an entry of the devlog folder when the
        project keeps a devlog (a project that sets `enabled` to false has no such folder)."""
        settings = artifact_classification.devlog_settings(self.root)
        return name == 'docs/PROGRESS.md' or (settings['enabled'] and name.startswith(settings['folder'] + '/'))

    def commit_identity(self, packet):
        # Reconstruct the original approval, including after a restarted verification.
        prepared = {k: v for k, v in packet.items() if k not in
                    ('approval', 'commit_result', 'result_sha', 'post_continuity', 'post_status',
                     'follow_up', 'continuity')}
        prepared['state'] = 'prepared'
        sha = packet.get('result_sha', self.head())
        headers, message = self.git('cat-file', 'commit', sha).split(b'\n\n', 1)
        require([x[7:].decode() for x in headers.splitlines() if x.startswith(b'parent ')] == [packet['parent']]
                and b'tree ' + packet['tree'].encode() in headers.splitlines()
                and message == base64.b64decode(packet['message_b64']),
                'Post-commit parent/tree/exact message mismatch')
        self.assert_approval(prepared, packet.get('approval'))

    def continue_records(self):
        # Release the active slot only into another bounded transaction, not completion.
        pending = self.store / PENDING
        if pending.exists():
            packet = self.load_path(pending)
            self.commit_identity(packet)
            require(packet['state'] == 'follow-up-pending' and self.head() == packet['result_sha']
                    and not (self.store / TRANSACTION).exists(), 'Pending continuation conflict')
            return packet
        packet = self.verify()
        require(packet['state'] == 'follow-up-pending', 'Only pending runtime may continue records')
        # Atomic rename preserves the entire seal. No history archive is created.
        os.rename(self.store / TRANSACTION, pending)
        return packet

    def finish(self):
        journal = self.store / CLEANUP
        if not journal.exists():
            if not (self.store / TRANSACTION).exists():
                require(not (self.store / PENDING).exists(), 'Runtime continuity still pending')
                require(not (self.store / HANDOFF).exists(), 'Orphan handoff requires review')
                return {'state': 'idle'}
            packet = self.verify()
            require(packet['state'] == 'verified', 'Continuity pending; complete records before cleanup')
            previous = None
            if (self.store / PENDING).exists():
                previous = self.load_path(self.store / PENDING)
                self.commit_identity(previous)
                require(packet['parent'] == previous['result_sha'], 'Follow-up parent mismatch')
            # Commit identity is the durable result; continuity records are now committed.
            # Save the exact deletion inventory before removing anything, for restart safety.
            audits = packet.get('reconciliation_audits', [])
            import re
            require(all(re.fullmatch(r'(stale-[0-9a-f]{64}|reconcile-[0-9a-f]{40}-[0-9a-f]{40})\.json', n)
                        for n in audits), 'Invalid reconciliation audit inventory')
            for name in audits:
                self.load_path(self.store / name)
            early_approval.write_landing(self, packet, self.landing_facts(packet, previous))
            files = {name: digest((self.store / name).read_bytes()) for name in
                     (TRANSACTION, PENDING, HANDOFF, EARLY_USES, *audits) if (self.store / name).exists()}
            value = {'repo': self.identity(), 'head': self.head(), 'branch': self.branch(), 'files': files,
                     'configuration': self.configuration(), 'working': packet['working'],
                     'manifest': packet['manifest'], 'policy': packet['policy'], 'status': self.status()}
            body = canonical(value)
            atomic(journal, canonical({'packet': value,
                   'mac': hmac.new(self.key(), body, hashlib.sha256).hexdigest()}))
        value = self.load_path(journal)
        self.quiet_operation()
        require(self.head() == value['head'] and self.branch() == value['branch'] and not self.inventory(),
                'Cleanup HEAD/branch/index changed')
        require(self.configuration() == value['configuration'] and self.status() == value['status']
                and self.snapshot(value['manifest'], value['policy']) == value['working'],
                'Cleanup validated state changed')
        import re
        require(all(n in (TRANSACTION, PENDING, HANDOFF, EARLY_USES) or
                    re.fullmatch(r'(stale-[0-9a-f]{64}|reconcile-[0-9a-f]{40}-[0-9a-f]{40})\.json', n)
                    for n in value['files']),
                'Invalid cleanup inventory')
        for name, expected in value['files'].items():
            path = self.store / name
            require(not path.is_symlink(), 'Symlink cleanup target')
            if path.exists():
                require(digest(path.read_bytes()) == expected, 'Cleanup content changed')
        for name in value['files']:
            (self.store / name).unlink(missing_ok=True)
        journal.unlink()
        return {'state': 'idle', 'result_sha': value['head']}

    def preview(self, manifest, message_file, worker_context, expected_head, expected_branch, job_handle=None,
                review=None):
        """Read-only: name the exact candidate and say whether a go may approve it. Stages nothing, runs no suite.

        The one write is outside the repository and best effort: the manifest's paths and subclasses become this
        mission's claims in the local claims database, so another worker can see the overlap. It decides nothing.
        A `review` dict, when given, is filled with the derived facts that `review_render.render_preview` shows beside
        the record; the record itself is unchanged by it.
        """
        self.quiet_operation()
        require(self.head() == expected_head and self.branch() == expected_branch, 'Unexpected HEAD/branch')
        policy, profiles = self.data()
        manifest = self.paths(manifest, policy)
        _, message = self.read_message(message_file)
        name, profile, runtime = self.derive_selection(manifest, message.decode('utf-8'), policy, profiles,
                                                       worker_context, 'auto', expected_branch)
        self.check_commands(profile, policy, runtime)
        self.ensure_key()
        facts = early_approval.preview_facts(self, manifest, message, profile, self.table(policy, profiles),
                                             self.head_table(profiles),
                                             expected_head, expected_branch)
        if job_handle is not None:
            local_jobs.job_reference(job_handle)
        claims.declare_manifest(self, profile, job_handle)
        if review is not None:
            try:  # Best effort, like the estimate before a prepare: no estimate is stated as none.
                estimate = self.estimate(manifest)
            except (ValueError, OSError, KeyError, TypeError):
                estimate = None
            review.update(profile=name, profile_data=profile, message=message.decode('utf-8'), estimate=estimate,
                          workspace=str(self.root), job_handle=job_handle,
                          diff=self.git('diff', '--no-ext-diff', '--no-textconv', '--binary', 'HEAD',
                                        facts['tree']).decode('utf-8', 'replace'))
        return early_approval.sign(self, facts)

    def landing_facts(self, packet, earlier=None):
        """What the landing notice and the integration guard need once the packets are gone.

        `approvals` also keeps, for every approved commit of the branch (the runtime commit of a continuity pair
        included, through `earlier`), the diff the lead developer approved and the sealed selection, so that the approval
        can be carried to a moved base. A record that cannot be built is named and never stops the lifecycle: such
        a commit simply has nothing to carry."""
        early = packet.get('early_approval') or {}
        try:
            prior = early_approval.read_landing(self)
        except (ValueError, OSError, KeyError, TypeError):
            prior = None
        entries, failure = [], None
        for item in (earlier, packet):
            if item is not None:
                try:
                    entries.append(early_approval.approval_entry(self, item))
                except (ValueError, OSError, KeyError, TypeError) as error:
                    failure = str(error)
        go = None
        if early.get('applied') is True:
            go = early_approval.unsign(self, early['go'])['go']
        data = packet.get('profile_data') or {}
        message = base64.b64decode(packet['message_b64']).decode('utf-8')
        return {'version': 1, 'result_sha': packet['result_sha'], 'parent': packet['parent'], 'tree': packet['tree'],
                'branch': packet['branch'], 'transaction': packet['id'], 'job_handle': packet.get('job_handle'),
                'interpreter': packet.get('interpreter'),
                'subject': message.split('\n', 1)[0], 'manifest': packet['manifest'],
                'classification': data.get('classification') or {},
                'changes': review_render.file_changes(packet['manifest'], packet.get('diff', '')),
                'checks': [{'id': e['id'], 'exit_code': e.get('exit_code')} for e in packet['evidence']],
                'approval_path': 'early' if go else 'sealed',
                'go': {k: go[k] for k in ('conversation_id', 'user_message_id', 'channel', 'response')} if go else None,
                'approvals': early_approval.merge_approvals((prior or {}).get('approvals'), entries),
                **({'approvals_error': failure} if failure else {})}

    def landing(self):
        """The landing notice for the last finished transaction in this checkout, rendered by the tool."""
        data = early_approval.read_landing(self)
        require(data is not None, 'No finished transaction to report; run finish first')
        merged = subprocess.run(['git', '-C', str(self.root), 'merge-base', '--is-ancestor', data['result_sha'],
                                 'refs/heads/main'], capture_output=True).returncode == 0
        tip = self.git('rev-parse', 'refs/heads/main').decode().strip() if merged else None
        return review_render.render_landing(data, tip)

    def prepare(self, manifest, message_file, profile_name, expected_head, expected_branch, delegation,
                validate_only=False, worker_context=None, session_id=None, job_handle=None, early_go=None):
        require(delegation.strip(), 'Explicit selective-staging delegation reference required')
        require(session_id is None or (isinstance(session_id, str) and 0 < len(session_id) <= 256
                and session_id == session_id.strip() and all(ord(c) >= 32 for c in session_id)),
                'Session ID must be a declared nonempty identifier or null')
        if job_handle is not None:  # Declared by the worker and shown in the review; never checked against local records.
            local_jobs.job_reference(job_handle)
        pin = self.transaction_pin(early_go)  # Refuses before anything is read or staged when the interpreter changed since the preview.
        self.quiet_operation()
        self.preparation_gate(manifest)
        require(not (self.store / TRANSACTION).exists(),
                'Existing transaction; inspect it. Do not start another until obligations are resolved')
        require(not self.inventory(), 'Pre-existing staged content; tool will not adopt or clear it')
        require(self.head() == expected_head and self.branch() == expected_branch, 'Unexpected HEAD/branch')
        policy, profiles = self.data()
        manifest = self.paths(manifest, policy)
        message_file, message = self.read_message(message_file)
        profile_name, profile, runtime = self.derive_selection(manifest, message.decode('utf-8'), policy, profiles,
                                                               worker_context, profile_name, expected_branch)
        checks = self.check_commands(profile, policy, runtime)
        before = self.snapshot(manifest, policy)
        estimate = self.sealed_estimate(policy, checks)
        packet = {'version': 1, 'id': secrets.token_hex(16), 'repo': self.identity(), 'state': 'preparing',
                  'interpreter': pin, 'parent': expected_head, 'branch': expected_branch, 'manifest': manifest,
                  'delegation': delegation, 'session_id': session_id, 'job_handle': job_handle, 'policy': policy, 'profile': profile_name,
                  'profile_data': profile, 'runtime': runtime,
                  'message_file': str(message_file), 'message_b64': base64.b64encode(message).decode(),
                  'message_sha256': digest(message), 'before_status': self.status()}
        if early_go is not None:
            packet['early_approval'] = early_approval.start(self, early_go, expected_head, expected_branch)
        packet['ref_policy'] = REF_POLICY
        packet['validation_ref_context'] = self.ref_snapshot(include_main=False)
        packet['configuration'] = self.configuration()
        if estimate is not None:
            packet['timing_estimate'] = estimate
        if self.common != self.gitdir:
            packet['reconciliation_context'] = self.reconciliation_context()
        packet['residual'] = self.residual(manifest)
        self.ensure_key()
        self.save(packet)
        try:
            artifact_classification.stage_manifest(self.git, self.root, manifest)
            require(self.inventory() == manifest, 'Staged inventory does not equal manifest (unchanged entry or extra staging)')
            packet.update(tree=self.git('write-tree').decode().strip(),
                          index=digest(self.git('ls-files', '--stage', '-z')),
                          working=before, prepared_status=self.status())
            packet['stat'] = self.git('diff', '--cached', '--stat', '--no-renames').decode('utf-8')
            packet['diff'] = self.git('diff', '--cached', '--no-ext-diff', '--no-textconv', '--binary').decode('utf-8', 'replace')
            self.save(packet)
            return self.validate_packet(packet, not validate_only)
        except Exception as error:
            packet.update(state='prepare-failed', error=str(error), actual_inventory=self.inventory())
            self.save(packet)
            raise ValueError(str(error) + '\nNo rollback performed. Inspect status and transaction; manually review staged paths before recovery.') from error

    def inspect(self):
        packet = self.load()
        if packet['state'] in ('prepared', 'approved', 'validated'):
            self.assert_prepared(packet)
        return packet

    def present(self):
        """Agent presents this entire review, retaining its identity for the user reply."""
        packet = self.load()
        require(packet['state'] == 'prepared', 'Presentation requires a prepared transaction')
        self.assert_prepared(packet)
        sha = digest(canonical(packet))
        try:  # A rendering fault must never block a decision; the packet below is still complete.
            review = {'review': review_render.render(packet, sha, DECISION_CONTROLS, DECISION_TOKENS)}
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            review = {'review_error': str(error)}
        return {'transaction_id': packet['id'], 'prepared_sha256': sha,
                'packet': packet, **review,
                'full_exact_message': repr(base64.b64decode(packet['message_b64']).decode('utf-8')),
                # Vendor-neutral host request: adapters render these controls or, failing that, the tokens.
                'approval_request': {
                    'transaction_id': packet['id'], 'prepared_sha256': sha,
                    'controls': [{'label': label, 'value': value} for label, value in DECISION_CONTROLS],
                    'text_tokens': [{'token': token, 'decision': value} for token, value in DECISION_TOKENS]}}

    def consent_receipt(self, prepared, consent):
        """Validate an agent-recorded lead developer decision; never interpret prose or authenticate authorship.

        The decision is an exact control label/value or canonical token bound to the presented seal,
        with the same meaning whichever declared channel delivered it.
        The execution contract must verify the source against the active task.
        Repository text, tool outputs, quotations and worker messages are not user decisions.
        The local HMAC authenticates integrity only, not the asserted conversation source.
        """
        require(isinstance(consent, dict) and set(consent) == {
            'source', 'conversation_id', 'user_message_id', 'channel', 'response', 'decision',
            'transaction_id', 'prepared_sha256'}, 'Invalid lead developer decision input')
        require(consent['source'] == 'active-task-user', 'Lead developer/user-authored explicit decision required')
        require(all(isinstance(consent[k], str) and consent[k].strip() for k in
                    ('conversation_id', 'user_message_id', 'response')), 'Missing active conversation/user reference')
        require(consent['channel'] in DECISION_CHANNELS, 'Unknown lead developer decision channel')
        # Reject is an exact lookup, ignoring only surrounding whitespace. An approval is the raw reply
        # passed to the one approval-word matcher, which owns the word list and its guards. A selected or
        # typed Approve, APPROVE or approve is the same decision on every channel.
        responses = {key: value for pairs in (DECISION_CONTROLS, DECISION_TOKENS)
                     for label, value in pairs for key in (label, value)}
        decision = responses.get(consent['response'].strip()) or approval_words.meaning('sealed', consent['response'])
        require(decision is not None,
                'Unrecognized lead developer decision; use the Approve/Reject control or an approval word, or reply REJECT')
        require(consent['decision'] == decision, 'Declared decision does not match the lead developer response')
        sha = digest(canonical(prepared))
        require(consent['transaction_id'] == prepared['id'] and consent['prepared_sha256'] == sha,
                'Decision does not match the currently presented sealed packet; fresh decision required')
        return {'prepared_sha256': sha, 'kind': 'approver-decision-v1', 'decision': dict(consent)}

    def assert_approval(self, prepared, approval):
        sha = digest(canonical(prepared))
        legacy = {'prepared_sha256': sha, 'confirmation': 'APPROVE ' + prepared['id'] + ' ' + sha}
        if isinstance(approval, dict) and approval.get('kind') == early_approval.RECEIPT_KIND:
            early_approval.assert_receipt(self, prepared, approval)
        elif isinstance(approval, dict) and approval.get('kind') == 'approver-decision-v1':
            receipt = self.consent_receipt(prepared, approval.get('decision'))
            require(approval == receipt and receipt['decision']['decision'] == 'approve',
                    'Invalid exact approval receipt')
        else:
            # Superseded natural-language receipts (approver-consent-v1) are not accepted.
            require(approval == legacy, 'Invalid exact approval receipt')

    def approve_consent(self, consent):
        packet = self.assert_pinned(self.load())
        require(packet['state'] in ('prepared', 'approved'), 'Decision requires a sealed prepared transaction')
        self.assert_prepared(packet)
        prepared = {k: v for k, v in packet.items() if k != 'approval'}
        prepared['state'] = 'prepared'
        receipt = self.consent_receipt(prepared, consent)
        if receipt['decision']['decision'] == 'reject':
            if packet['state'] == 'approved':
                self.save(prepared)  # Rejection withdraws a recorded approval; only a fresh approve restores it.
            raise ValueError('Lead developer rejected the sealed transaction; no commit is authorized')
        if packet['state'] == 'approved':
            require(packet.get('approval') == receipt, 'Different approval already recorded; inspect before retry')
            return packet  # An identical retry does not rewrite authenticated evidence.
        self.assert_prepared(packet)
        packet.update(approval=receipt, state='approved')
        self.save(packet)
        return packet

    def approve(self, confirmation):
        packet = self.assert_pinned(self.load())
        require(packet['state'] == 'prepared', 'Approval requires a prepared transaction')
        self.assert_prepared_repository(packet)
        print(json.dumps(packet, indent=2, ensure_ascii=True))
        print('\nFULL EXACT MESSAGE (UTF-8 bytes represented below; no normalization):')
        print(repr(base64.b64decode(packet['message_b64']).decode('utf-8')))
        token = 'APPROVE ' + packet['id'] + ' ' + digest(canonical(packet))
        require(confirmation('Type exactly: ' + token + '\n> ') == token, 'Lead developer approval not supplied')
        self.assert_prepared_repository(packet)
        packet['approval'] = {'prepared_sha256': digest(canonical(packet)), 'confirmation': token}
        packet['state'] = 'approved'
        self.save(packet)
        return packet

    def commit(self):
        packet = self.assert_pinned(self.load())
        require(packet['state'] == 'approved' and 'approval' in packet, 'No sealed lead developer approval')
        prepared = dict(packet)
        approval = prepared.pop('approval')
        prepared['state'] = 'prepared'
        self.assert_approval(prepared, approval)
        self.assert_prepared(packet)
        if approval.get('kind') == early_approval.RECEIPT_KIND:
            early_approval.assert_switch_on(self, prepared)  # Read again from main's tip before the commit exists.
        packet['state'] = 'committing'
        self.save(packet)
        # stdin plus verbatim cleanup preserves every approved byte; no message-file rewrite.
        result = subprocess.run(['git', '-C', str(self.root), 'commit', '--cleanup=verbatim',
                                 '--no-gpg-sign', '--file=-'], input=base64.b64decode(packet['message_b64']),
                                capture_output=True)
        packet['commit_result'] = {'exit_code': result.returncode,
                                   'stdout': result.stdout.decode('utf-8', 'replace'),
                                   'stderr': result.stderr.decode('utf-8', 'replace')}
        packet['state'] = 'commit-attempted'
        self.save(packet)
        require(result.returncode == 0, 'Commit failed; no cleanup/retry. Inspect sealed result and run verify')
        early_approval.mark_committed(self, packet)
        return self.verify()

    def committed_checkpoint(self):
        """Required committed identity, even when scratch or working evidence is missing."""
        import re
        require((self.root / 'docs/PROGRESS.md').is_file(),
                'Missing continuity evidence: docs/PROGRESS.md')
        progress = self.git('show', 'HEAD:docs/PROGRESS.md').decode('utf-8')
        rows = [line for line in progress.splitlines()
                if line.startswith('| implementation_checkpoint |')]
        require(len(rows) == 1, 'Committed implementation_checkpoint requires exactly one row')
        cells = rows[0].split('|')
        require(len(cells) == 4 and not cells[0] and not cells[3] and
                re.fullmatch(r'[a-f0-9]{40}', cells[2].strip()),
                'Committed implementation_checkpoint must be a full Git SHA')
        return cells[2].strip()

    def durable_continuity(self):
        # A working-copy PASS cannot promote facts that the commit does not contain.
        require((self.root / 'docs/PROGRESS.md').is_file(),
                'Missing continuity evidence: docs/PROGRESS.md')
        require(not self.git('diff', '--name-only', 'HEAD', '--', 'docs/PROGRESS.md'),
                'Continuity Progress must be committed before completion')
        from governance.tools.continuity import validate_continuity as continuity
        _, facts = continuity.verify_repository(self.root)
        if 'implementation_checkpoint' not in facts:
            return  # No runtime boundary is declared: there is no checkpoint log to match.
        if not artifact_classification.devlog_settings(self.root)['enabled']:
            return  # The project keeps no devlog: the pointer in the progress record needs no entry beside it.
        checkpoint = facts['implementation_checkpoint']
        matching = [name for name in continuity.validate_docs(self.root)
                    if f'Repository checkpoint: `{checkpoint}`' in continuity.read(self.root, name)]
        require(len(matching) == 1, 'Durable continuity requires one matching log')
        name = matching[0]
        require(self.git('show', 'HEAD:' + name).decode('utf-8').replace('\r\n', '\n') ==
                continuity.read(self.root, name), 'Continuity log must be committed before completion')
        index = self.git('show', 'HEAD:' + posixpath.dirname(name) + '/README.md').decode('utf-8')
        require('](' + Path(name).name + ')' in index,
                'Continuity log must be indexed in the committed record')

    def completed_continuity(self, result):
        require(result['exit_code'] == 0, 'Post-commit continuity failed; blocked')
        self.durable_continuity()
        return 'PASS'

    def runtime_pending(self, packet, result):
        """Extra independent checks because the normal validator stops at stale runtime HEAD."""
        from governance.tools.continuity import validate_continuity as continuity
        require(result['exit_code'] != 0 and result['stdout'].strip() == '' and
                result['stderr'].strip() == 'FAIL: Stale implementation checkpoint: runtime history has advanced',
                'Post-commit continuity has an unexpected failure; blocked')
        continuity.validate_docs(self.root)
        contract = json.loads(continuity.read(self.root, continuity.CONTRACT))
        facts = continuity.extract_facts(self.root, contract)
        require(facts['implementation_checkpoint'] != packet['result_sha'], 'Unexpected continuity checkpoint')
        if continuity.FROZEN_FACT in contract['facts']:
            continuity.frozen_invariants(self.root, packet['result_sha'])
        report = continuity.completion_evidence_source(contract)
        if report:
            anchored = self.git('show', packet['result_sha'] + ':' + report.split('#')[0]).decode('utf-8')
            require(continuity.source_text(self.root, report).strip() in anchored,
                    'Runtime commit does not anchor completion evidence')
        return 'checkpoint committed; continuity follow-up pending'

    def verify(self):
        packet = self.assert_pinned(self.load())
        self.quiet_operation()
        require(self.branch() == packet['branch'] and self.configuration() == packet['configuration'],
                'Post-commit branch/configuration changed')
        require(packet['state'] in ('committing', 'commit-attempted', 'verified', 'follow-up-pending'),
                'No commit attempt to verify')
        self.commit_identity(packet)
        if packet['state'] in ('verified', 'follow-up-pending'):
            require(self.head() == packet['result_sha'] and not self.inventory(), 'Post-verification HEAD/index changed')
            require(self.snapshot(packet['manifest'], packet['policy']) == packet['working'], 'Post-verification content changed')
            require(self.residual(packet['manifest']) == self.residual(packet['manifest'], packet['prepared_status']), 'Post-verification unrelated state changed')
            atomic(self.store / HANDOFF, canonical(packet))
            return packet
        require(packet['state'] in ('committing', 'commit-attempted'), 'No commit attempt to verify')
        sha = self.head()
        raw = self.git('cat-file', 'commit', sha)
        headers, message = raw.split(b'\n\n', 1)
        parents = [line[7:].decode() for line in headers.splitlines() if line.startswith(b'parent ')]
        tree = next(line[5:].decode() for line in headers.splitlines() if line.startswith(b'tree '))
        require(parents == [packet['parent']] and tree == packet['tree'] and
                message == base64.b64decode(packet['message_b64']), 'Post-commit parent/tree/exact message mismatch')
        require(self.branch() == packet['branch'] and not self.inventory(), 'Post-commit branch/index mismatch')
        require(self.snapshot(packet['manifest'], packet['policy']) == packet['working'], 'Post-commit working content changed')
        require(self.residual(packet['manifest']) == self.residual(packet['manifest'], packet['prepared_status']), 'Post-commit unrelated status changed')
        packet['result_sha'] = sha
        post = self.checks([{'id': 'continuity', 'argv': packet['policy']['commands']['continuity']}])[0]
        packet['post_continuity'] = post
        packet['post_status'] = self.status()
        packet['follow_up'] = 'Separate fresh-worker assessment of historical/current records; no authorization to edit or commit'
        try:
            require(self.head() == sha and self.branch() == packet['branch'] and not self.inventory(),
                    'Git state changed during post-commit validation')
            require(self.configuration() == packet['configuration'] and
                    self.snapshot(packet['manifest'], packet['policy']) == packet['working'] and
                    self.residual(packet['manifest']) == self.residual(packet['manifest'], packet['prepared_status']),
                    'Validated state changed during post-commit validation')
            if packet['runtime']:
                require(all(c['exit_code'] == 0 for c in packet['evidence']), 'Missing passing pre-commit evidence')
                packet['continuity'] = self.runtime_pending(packet, post)
                packet['state'] = 'follow-up-pending'
            else:
                packet['continuity'] = self.completed_continuity(post)
                packet['state'] = 'verified'
        except Exception as error:
            packet.update(state='verification-blocked', error=str(error))
            self.save(packet)
            raise
        self.save(packet)
        # Identical packet produces identical handoff bytes. No prose or devlog mutation.
        atomic(self.store / HANDOFF, canonical(packet))
        return packet
