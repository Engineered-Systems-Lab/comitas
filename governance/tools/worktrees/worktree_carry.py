"""The carry of an approval to a moved main (G018 diff-bound approval extension): the carry of a committed
approval and the carry of a go, each under its own gates. A mixin of the worktree lifecycle guard; governance/tools/worktree.py
composes it and drives it from the command line."""
from governance.tools.continuity.artifact_classification import EARLY_USES, LANDING
from governance.tools.support import local_state
from checkpoint import Checkpoint, require, digest, canonical, TRANSACTION, RECEIPT_KEY, OPERATION_LOCK, ABANDONABLE, check_ref_context
from governance.tools import runtime_guard
from governance.tools.worktrees import claims
from governance.tools.lifecycle import early_approval
import json
import os
from pathlib import Path
import re
from governance.tools.lifecycle import review_render
import subprocess
import tempfile
from governance.tools.worktrees.worktree_support import ReadOnlyGit, checkpoint_driver, identity_environment, lifecycle_lock, remove_tree


class CarryRefused(ValueError):
    """A gate of the carry failed. Nothing was moved, and the old rule applies: a fresh Approve."""

    def __init__(self, gate, detail, commit=None):
        self.gate, self.commit = gate, commit
        super().__init__('Carry refused at the %s gate%s: %s. Nothing was moved; a fresh Approve on a new reconciliation '
                         'mission is needed.' % (gate, ' (commit ' + commit[:7] + ')' if commit else '', detail))


class GoCarryRefused(CarryRefused):
    """A gate of the go carry failed. Nothing was moved, and today's rule applies: a fresh preview and a fresh go."""

    def __init__(self, gate, detail):
        ValueError.__init__(self, 'The go was not carried at the %s gate: %s. Nothing was moved; show a fresh preview and take '
                            'a fresh go (or the sealed Approve).' % (gate, detail))
        self.gate, self.commit = gate, None


def carry_note(root, owner, worktree):
    """What a moved base means for one mission, said only as far as cheap, read-only facts show it.

    The note names the one case in which `worktree.py carry` can be tried: the carry switch reads true on main and the
    mission's branch is committed and has an approval record with a sealed selection. It evaluates no gate that needs
    work (the merge, the diff, the selection, the checks), so it never promises a carry."""
    on, _ = early_approval.carry_switch(ReadOnlyGit(root))
    if not on:
        return 'its approval cannot be carried (diff_bound_approval is off): prepare again and take a fresh Approve'
    try:
        body = json.loads((local_state.checkout_store(worktree) / LANDING).read_bytes())['body']
        head = subprocess.run(['git', '-C', str(worktree), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
        known = [e for e in body.get('approvals') or [] if isinstance(e, dict) and e.get('result_sha') == head]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return ('it has no committed, approved candidate to carry yet; an uncommitted candidate with a go at its preview '
                'can try `worktree.py carry-go`')
    if body.get('result_sha') != head:
        return 'its tip is not its last finished, approved commit, so there is nothing to carry'
    if not known or not known[0].get('selection_sha256'):
        return 'the record gate would refuse: its commit has no approval record with a sealed selection (approved before the carry existed)'
    return ('`worktree.py carry` can try its approval of %s (diff_bound_approval is on; the merge, diff, selection and '
            'check gates decide)' % head[:7])


class Carry:
    # ---- The carry of an approval across a moved base (diff-bound approval, slice 1) -----------------

    def carry(self, path, owner, expected_main, expected_tip, job_handle=None):
        """Carry the approval of a finished, committed branch onto the current main, or change nothing.

        The lead developer's approval binds to the diff they saw. When main moved and the branch is not a descendant of it, the
        guard refuses a fast-forward; this operation proves, commit by commit and without touching the owned branch,
        that the same diff still stands on the new main: the merge is clean, the carried diff equals the approved diff
        (modulo the ids of the predecessors carried before it), the message is unchanged, the selection derived on the
        new main equals the sealed one and every selected check passes again on the carried tree. Only then does a short
        locked step move the owned branch and worktree to the carried commits and write the authenticated audit.
        Any gate that fails raises CarryRefused naming it, and the old rule applies: a fresh Approve.
        """
        require(all(re.fullmatch(r'[0-9a-f]{40}', s) for s in (expected_main, expected_tip)),
                'The carry requires full main and tip SHAs')
        source = self.source(path, owner)
        early_approval.assert_landing_pin(source)  # At the start: before anything is read or proved.
        self.assert_carry_switch()
        with source.lock(_lifecycle=True):
            self.hooks()
            for t, sha in ((self.main, expected_main), (source, expected_tip)):
                self.idle(t)
                self.exact(t, sha)
            shared = self.shared_state(source.branch())
            staging = Path(tempfile.mkdtemp(prefix='carry-'))
            try:
                proof = self.carry_proof(source, expected_main, expected_tip, staging, job_handle)
                with lifecycle_lock(self.main, 'carry', owner, job_handle), self.main.lock(_lifecycle=True):
                    return self.carry_move(source, owner, path, shared, proof, job_handle)
            finally:
                remove_tree(staging)

    def assert_carry_switch(self):
        on, why = early_approval.carry_switch(self.main)
        if not on:
            raise CarryRefused('switch', why + ' on main; the carry is off')

    def carry_proof(self, source, main_sha, tip, staging, job_handle):
        """Every gate, in a disposable clone and in unreferenced objects; the owned branch is not touched."""
        def git(*args, **kw):
            return source.git(*args, **kw)
        require(subprocess.run(['git', '-C', str(source.root), 'merge-base', '--is-ancestor', main_sha, tip]).returncode != 0,
                'Main is already an ancestor of the source tip; there is nothing to carry, integrate it')
        base = git('merge-base', main_sha, tip).decode().strip()
        chain = [row.split() for row in git('rev-list', '--reverse', '--parents', base + '..' + tip).decode().splitlines()]
        if not chain or any(len(row) != 2 for row in chain) or chain[0][1] != base or \
                any(chain[i][1] != chain[i - 1][0] for i in range(1, len(chain))):
            raise CarryRefused('record', 'the branch is not a straight line of single-parent commits from its merge base')
        landing = early_approval.read_landing(source)
        if landing is None:
            raise CarryRefused('record', 'the worktree has no landing record; no finished, approved transaction is recorded')
        landing_bytes = digest((source.store / LANDING).read_bytes())
        approvals = {e.get('result_sha'): e for e in landing.get('approvals') or [] if isinstance(e, dict)}
        upstream = git('rev-list', '--reverse', base + '..' + main_sha).decode().split()
        upstream_paths = sorted(p for p in git('diff', '--name-only', '--no-renames', '-z', base, main_sha).decode().split('\0') if p)
        entries = []
        for sha, parent in chain:
            entry = approvals.get(sha)
            message = git('cat-file', 'commit', sha).split(b'\n\n', 1)[1]
            tree = git('rev-parse', sha + '^{tree}').decode().strip()
            if entry is None or not entry.get('selection_sha256') or not isinstance(entry.get('original'), dict) \
                    or not entry['original'].get('change'):
                raise CarryRefused('record', 'no approval record with a sealed selection and the approved diff; it was '
                                   'approved before the carry existed or not by this tool', sha)
            if entry.get('parent') != parent or entry.get('tree') != tree or entry.get('message_sha256') != digest(message) \
                    or entry.get('branch') != source.branch():
                raise CarryRefused('record', 'the approval record does not describe this commit (parent, tree, message or branch)', sha)
            entries.append(entry)
        results = []   # per commit: the carried commit and its proofs
        by_old = {}    # current id -> carried id of the predecessors, for rewriting what the records cite
        by_original = {}   # originally approved id -> carried id, for comparing with the approved diff
        previous_new = main_sha
        for number, ((sha, parent), entry) in enumerate(zip(chain, entries), 1):
            result = self.carry_one(source, sha, parent, entry, previous_new, by_old, by_original, number)
            results.append(result)
            original = entry['original']
            for key, mapping in ((sha, by_old), (original['result_sha'], by_original)):
                mapping[key] = result['new']
            for key, mapping in ((parent, by_old), (original['parent'], by_original)):
                mapping[key] = result['new_parent']
            for key, mapping in ((entry['tree'], by_old), (original['tree'], by_original)):
                mapping[key] = result['new_tree']
            previous_new = result['new']
        # The selection gate for every commit comes before the first long check run.
        for result, entry in zip(results, entries):
            self.carry_selection(source, result, entry, main_sha, staging)
        for result, entry in zip(results, entries):
            self.carry_checks(source, result, entry, job_handle)
        touched = sorted({p for entry in entries for p in entry['manifest']})
        overlap = sorted(set(touched) & set(upstream_paths))
        return {'base': base, 'main': main_sha, 'tip': tip, 'new_tip': results[-1]['new'], 'commits': results,
                'entries': entries, 'upstream': {'from': base, 'to': main_sha, 'commits': upstream, 'paths': upstream_paths},
                'overlap': overlap, 'landing': landing, 'landing_sha256': landing_bytes}

    def carry_one(self, source, sha, parent, entry, new_parent, by_old, by_original, number):
        """The carried commit and the merge, diff and message gates for one approved commit."""
        merge = subprocess.run(['git', '-C', str(source.root), 'merge-tree', '--write-tree', '--name-only',
                                '--merge-base=' + parent, new_parent, sha], capture_output=True)
        lines = merge.stdout.decode('utf-8', 'replace').splitlines()
        require(merge.returncode in (0, 1), 'git merge-tree failed: ' + merge.stderr.decode('utf-8', 'replace'))
        if merge.returncode != 0:
            conflicts = [l for l in lines[1:] if l and not l.startswith(('Auto-merging', 'CONFLICT'))]
            raise CarryRefused('merge-tree', 'the commit does not merge cleanly onto the new base' +
                               ((': ' + ', '.join(conflicts[:5])) if conflicts else ''), sha)
        merged = lines[0].strip()
        rewrite = {k: v for k, v in by_old.items() if k != v}
        tree = merged
        if rewrite:  # the records of a continuity commit cite the commit before it by id, tree and parent
            tree = self.rewrite_tree(source, merged, parent, sha, rewrite)
        env = identity_environment(source, sha)
        message = source.git('cat-file', 'commit', sha).split(b'\n\n', 1)[1]
        made = subprocess.run(['git', '-C', str(source.root), 'commit-tree', tree, '-p', new_parent, '-F', '-'],
                              input=message, env=env, capture_output=True)
        require(made.returncode == 0, 'Cannot create the carried commit: ' + made.stderr.decode('utf-8', 'replace'))
        new = made.stdout.decode().strip()
        raw_commit = source.git('cat-file', 'commit', new)
        headers, new_message = raw_commit.split(b'\n\n', 1)
        if new_message != message or digest(new_message) != entry['original']['message_sha256']:
            raise CarryRefused('message', 'the carried message differs from the approved message or its trailers', sha)
        require([x[7:].decode() for x in headers.splitlines() if x.startswith(b'parent ')] == [new_parent] and
                b'tree ' + tree.encode() in headers.splitlines(), 'The carried commit does not have the proven parent and tree')
        carried = early_approval.raw_change(source, new_parent, new)
        original = entry['original']
        approved = early_approval.substitute(early_approval.unpack(original['change']),
                                             {k: v for k, v in by_original.items() if k != v})
        carried_id, approved_id = early_approval.patch_id(carried), early_approval.patch_id(approved)
        have, want = early_approval.stripped(carried), early_approval.stripped(approved)
        if have != want or carried_id != approved_id:
            first = next((a for a, b in zip(have.splitlines(), want.splitlines()) if a != b), b'')
            raise CarryRefused('diff', 'the carried diff differs from the approved diff (patch id %s against %s, %d against %d '
                               'bytes, first difference: %s)' % (carried_id[:12] or '-', approved_id[:12] or '-', len(have),
                                                                len(want), first[:80].decode('utf-8', 'replace') or 'at the end'), sha)
        names = sorted(n for n in source.git('diff', '--name-only', '--no-renames', '-z', new_parent, new).decode().split('\0') if n)
        if names != entry['manifest']:
            raise CarryRefused('diff', 'the carried commit changes other paths than the approved manifest', sha)
        return {'number': number, 'old': sha, 'old_parent': parent, 'old_tree': entry['tree'], 'new': new,
                'new_parent': new_parent, 'new_tree': tree, 'merge_tree': merged, 'patch_id': carried_id,
                'approved_patch_id': approved_id, 'change_sha256': digest(have), 'id_map': dict(sorted(rewrite.items())),
                'original_seal_sha256': original.get('seal_sha256'), 'original_commit': original['result_sha'],
                'approval_path': entry['approval_path'], 'selection_sha256': entry['selection_sha256'], 'checks': []}

    def rewrite_tree(self, source, tree, parent, sha, rewrite):
        """The merged tree with the predecessors' ids replaced in the files this commit changed, and only there."""
        changed = [n for n in source.git('diff', '--name-only', '--no-renames', '-z', parent, sha).decode().split('\0') if n]
        with tempfile.TemporaryDirectory(prefix='rewrite-', dir=source.gitdir) as folder:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(folder) / 'index'))

            def git(*args, data=None):
                result = subprocess.run(['git', '-C', str(source.root), '--literal-pathspecs', *args], env=env, input=data,
                                        capture_output=True)
                require(result.returncode == 0, 'Cannot rewrite the carried tree: ' + result.stderr.decode('utf-8', 'replace'))
                return result.stdout
            git('read-tree', tree)
            for name in changed:
                row = git('ls-tree', '-z', tree, '--', name).decode('utf-8', 'replace').strip('\0')
                if not row:
                    continue
                mode, kind, rest = row.split(' ', 2)
                blob = rest.split('\t', 1)[0]
                if kind != 'blob' or mode not in ('100644', '100755'):
                    continue
                data = git('cat-file', 'blob', blob)
                updated = early_approval.substitute(data, rewrite)
                if updated != data:
                    new_blob = git('hash-object', '-w', '--stdin', data=updated).decode().strip()
                    git('update-index', '--cacheinfo', mode + ',' + new_blob + ',' + name)
            return git('write-tree').decode().strip()

    def clone_at(self, source, result, main_sha, staging):
        """A disposable repository at the carried commit's parent with the carried content in its working tree."""
        folder = staging / ('commit-%d' % result['number'])
        branch = source.branch().removeprefix('refs/heads/')
        def git(*args, root=None):
            done = subprocess.run(['git', *(['-C', str(root)] if root else []), *args], capture_output=True)
            require(done.returncode == 0, 'Cannot prepare the carry clone: ' + done.stderr.decode('utf-8', 'replace'))
            return done.stdout
        git('clone', '--shared', '--no-checkout', '--quiet', str(source.root), str(folder))
        git('checkout', '--quiet', '--force', '-B', branch, result['new_parent'], root=folder)
        git('update-ref', 'refs/heads/main', main_sha, root=folder)
        git('read-tree', '-m', '-u', result['new_parent'], result['new'], root=folder)
        git('read-tree', result['new_parent'], root=folder)
        return folder

    def carry_selection(self, source, result, entry, main_sha, staging):
        """The selection derived for the carried candidate on the new base must equal the sealed one."""
        folder = self.clone_at(source, result, main_sha, staging)
        result['clone'] = str(folder)
        tool = Checkpoint(folder, runner=source.runner)
        policy, profiles = tool.data()
        if tool.head_table(profiles) is None:  # fail closed: nothing may be judged without the table at the new parent
            raise CarryRefused('selection', 'the classification table at the new parent cannot be read', result['old'])
        message = source.git('cat-file', 'commit', result['new']).split(b'\n\n', 1)[1].decode('utf-8')
        try:
            name, profile, runtime = tool.derive_selection(entry['manifest'], message, policy, profiles,
                                                           entry.get('worker_context'), 'auto', entry['branch'])
        except (ValueError, KeyError, TypeError, OSError) as error:
            raise CarryRefused('selection', 'the selection cannot be derived on the new base: ' + str(error), result['old']) from error
        found = early_approval.selection_digest(name, profile, runtime, policy)
        if found != entry['selection_sha256']:
            raise CarryRefused('selection', 'the selection derived on the new base (class, profile, checks, sensitivity or '
                               'policy) differs from the sealed one (%s against %s)' % (found[:12], entry['selection_sha256'][:12]),
                               result['old'])
        result['derived_selection_sha256'] = found

    def carry_checks(self, source, result, entry, job_handle):
        """Every selected check runs again on the carried tree, through the checkpoint tool itself, in the clone."""
        folder = Path(result.pop('clone'))
        tool = Checkpoint(folder, runner=source.runner)
        message = folder.parent / ('message-%d.txt' % result['number'])
        message.write_bytes(source.git('cat-file', 'commit', result['new']).split(b'\n\n', 1)[1])
        try:
            packet = tool.prepare(entry['manifest'], message, 'auto', result['new_parent'], entry['branch'],
                                  'Diff-bound carry of an approved candidate', worker_context=entry.get('worker_context'),
                                  job_handle=job_handle)
        except ValueError as error:
            failed = []
            try:
                failed = [e['id'] for e in tool.load().get('evidence', []) if e.get('exit_code') != 0]
            except (ValueError, OSError, KeyError, TypeError):
                pass
            raise CarryRefused('checks', 'a selected check failed on the carried tree' +
                               ((': ' + ', '.join(failed)) if failed else ': ' + str(error).splitlines()[0]), result['old']) from error
        require(packet['state'] == 'prepared' and
                early_approval.selection_digest(packet['profile'], packet['profile_data'], packet['runtime'], packet['policy'])
                == entry['selection_sha256'], 'The carried tree sealed a different selection than the approved one')
        result['checks'] = [{'id': e['id'], 'exit_code': e.get('exit_code'),
                             'seconds': e.get('duration_seconds')} for e in packet['evidence']]

    def carry_move(self, source, owner, path, shared, proof, job_handle):
        """The short locked step: recheck everything the proof read, record the audit, then move the owned branch."""
        main_sha, tip, new_tip = proof['main'], proof['tip'], proof['new_tip']
        ref_audits = []
        self.idle(self.main)
        self.exact(self.main, main_sha)
        self.idle(source)
        self.exact(source, tip)
        self.hooks()
        self.source(path, owner)
        self.assert_carry_switch()
        check_ref_context(shared, self.shared_state(source.branch()),
                          'Shared refs/stash/configuration changed during the carry proof', ref_audits)
        require(digest((source.store / LANDING).read_bytes()) == proof['landing_sha256'], 'The landing record changed during the proof')
        for result in proof['commits']:
            require(source.git('rev-parse', result['new'] + '^{tree}').decode().strip() == result['new_tree'],
                    'A carried commit is missing or changed')
        audit = {'kind': 'diff-bound-carry-v1', 'at': claims.now(), 'owner': owner, 'job_handle': job_handle,
                 'key': {'name': early_approval.CARRY_KEY, 'value': True, 'read_at_main': main_sha},
                 'old_tip': tip, 'new_tip': new_tip, 'old_base': proof['base'], 'new_base': main_sha,
                 'carry_number': 1 + max(len(e.get('carries') or []) for e in proof['entries']),
                 'upstream': proof['upstream'], 'overlap': proof['overlap'],
                 'commits': [{k: v for k, v in r.items() if k != 'number'} for r in proof['commits']],
                 'tooling': {'guard_sha256': self.guard_digest(),
                             'checkpoint_sha256': checkpoint_driver()[1]}}
        carried = []
        for result, entry in zip(proof['commits'], proof['entries']):
            carried.append(dict(entry, result_sha=result['new'], parent=result['new_parent'], tree=result['new_tree'],
                                approval_path='carried',
                                carries=[*entry.get('carries', []), {'from': result['old'], 'to': result['new'],
                                                                     'at': audit['at'], 'new_base': main_sha}]))
        last = proof['commits'][-1]
        landing = dict(proof['landing'], result_sha=new_tip, parent=last['new_parent'], tree=last['new_tree'],
                       approval_path='carried', original_approval_path=proof['landing'].get('approval_path'),
                       checks=[{'id': c['id'], 'exit_code': c['exit_code']} for c in last['checks']],
                       carried_from=audit, approvals=early_approval.merge_approvals(proof['landing'].get('approvals'), carried))
        # The record comes first, and the old entries stay in it: a crash leaves a state that a second carry finishes,
        # because the carried commits are deterministic. A failure that returns here restores the old record.
        before = (source.store / LANDING).read_bytes()
        early_approval.write_landing(source, None, landing)
        try:
            source.git('read-tree', '-m', '-u', tip, new_tip)
            try:
                source.git('update-ref', source.branch(), new_tip, tip)
            except ValueError:
                source.git('read-tree', '-m', '-u', new_tip, tip)
                raise
        except ValueError:
            early_approval.atomic(source.store / LANDING, before)
            raise
        self.exact(source, new_tip)
        self.idle(source)
        self.idle(self.main)
        self.exact(self.main, main_sha)
        return {'state': 'carried', 'old_tip': tip, 'new_tip': new_tip, 'main': main_sha, 'audit': audit,
                'transient_ref_audits': ref_audits,
                'notice': review_render.render_carry(audit),
                'next': 'integrate the new tip with the usual guarded command (the switch is read again there)'}

    # ---- The carry of a go across a moved base (diff-bound approval, slice 2) ---------------------------

    def carry_go(self, path, owner, expected_main, go_file, message_file, context_file, output, job_handle=None):
        """Carry the lead developer's go at a preview, or at a staged transaction, to the current main, or change nothing.

        The go is their judgment about a diff. When main moved, this operation proves, in objects and in a disposable
        clone, that the same diff stands on the new main (the merge is clean, the candidate's diff equals the
        previewed one, the message is the previewed one, the selection derived on the new main reproduces the
        previewed facts and the go is still eligible there), and only then moves the worktree to the new main with the
        candidate on it and writes the go re-bound to the new parent and tree. A staged transaction is unstaged first:
        every selected check runs again when it is prepared again with the carried go. Any gate that fails raises
        GoCarryRefused naming it, and today's rule applies: a fresh preview and a fresh go.
        """
        require(re.fullmatch(r'[0-9a-f]{40}', expected_main), 'The go carry requires the full main SHA')
        output = Path(output)
        require(not output.exists() and output.parent.is_dir(), 'The carried go goes to a new file in an existing folder')
        source = self.source(path, owner)
        envelope = json.loads(Path(go_file).read_text(encoding='utf-8-sig'))
        message = Path(message_file).read_bytes()
        context = json.loads(Path(context_file).read_text(encoding='utf-8-sig')) if context_file else None
        with source.lock(_lifecycle=True):
            self.hooks()
            self.idle(self.main)
            self.exact(self.main, expected_main)
            shared = self.shared_state(source.branch())
            staging = Path(tempfile.mkdtemp(prefix='carry-go-'))
            try:
                proof = self.go_proof(source, expected_main, envelope, message, context, staging)
                with lifecycle_lock(self.main, 'carry-go', owner, job_handle), self.main.lock(_lifecycle=True):
                    return self.go_move(source, owner, path, shared, proof, output, job_handle)
            finally:
                remove_tree(staging)

    def go_state(self, source, body):
        """The uncommitted state a go names, or a refusal at the `state` gate: returns the packet, or None for a preview only."""
        def refuse(detail):
            raise GoCarryRefused('state', detail)
        preview = body['preview']
        if preview.get('repo') != source.identity():
            refuse('the go belongs to another checkout')
        source.quiet_operation()
        if source.head() != preview['parent'] or source.branch() != preview['branch']:
            refuse('HEAD or the branch is not the previewed one')
        manifest = preview['manifest']
        packet = None
        if (source.store / TRANSACTION).exists():
            packet = source.load()
            early = packet.get('early_approval') or {}
            if packet['state'] not in ABANDONABLE:
                refuse('the transaction is in state %s; it has begun to commit' % packet['state'])
            if packet['parent'] != preview['parent'] or packet['tree'] != preview['tree'] or packet['manifest'] != manifest \
                    or early.get('go_id') != early_approval.go_id(body):
                refuse('the sealed transaction was not prepared with this go; abandon it or use the go it was prepared with')
            if packet['state'] == 'approved' and (packet.get('approval') or {}).get('kind') != early_approval.RECEIPT_KIND:
                refuse('the transaction holds your sealed Approve, which binds its seal; commit and finish it, then carry the commit')
        allowed = {RECEIPT_KEY, OPERATION_LOCK, EARLY_USES, *([TRANSACTION] if packet else [])}
        if not all(p.name in allowed and p.is_file() and not p.is_symlink() for p in source.store.iterdir()):
            refuse('the checkpoint scratch holds more than this transaction')
        dirty = {e[3:] for e in source.git('status', '--porcelain=v1', '-z', '--no-renames',
                                           '--untracked-files=all').decode('utf-8').split('\0') if e}
        if dirty - set(manifest):
            refuse('unrelated dirty state: ' + ', '.join(sorted(dirty - set(manifest))[:5]))
        if early_approval.candidate_tree(source, manifest) != preview['tree']:
            refuse('the working content is no longer the previewed candidate')
        return packet

    def go_proof(self, source, main_sha, envelope, message, context, staging):
        """Every gate, in unreferenced objects and a disposable clone; the owned worktree is not touched."""
        def refuse(gate, detail):
            raise GoCarryRefused(gate, detail)
        on, why = early_approval.carry_switch(self.main)
        if not on:
            refuse('switch', why + ' on main; the carry is off')
        try:
            body = early_approval.unsign(source, envelope)
            require(body.get('version') == 1 and isinstance(body.get('preview'), dict) and isinstance(body.get('go'), dict),
                    'it is not a go')
            preview = body['preview']
            parent, tree, manifest = preview['parent'], preview['tree'], preview['manifest']
        except (ValueError, KeyError, TypeError, OSError) as error:
            refuse('record', 'the go record is missing, altered or not a go: ' + str(error))
        runtime_guard.assert_pin(early_approval.go_pin(body))  # The pin of the first preview, before any gate is judged.
        packet = self.go_state(source, body)
        original = early_approval.go_original(source, body)
        if subprocess.run(['git', '-C', str(source.root), 'merge-base', '--is-ancestor', parent, main_sha]).returncode != 0:
            refuse('merge-tree', 'main does not descend from the previewed parent')
        require(parent != main_sha, 'Main is still the previewed parent; there is nothing to carry')
        if early_approval.digest(message) != original['preview']['message_sha256']:
            refuse('message', 'the message is not the previewed message, byte for byte')
        identity = dict(os.environ, GIT_AUTHOR_NAME='Go carry', GIT_AUTHOR_EMAIL='go-carry@example.invalid',
                        GIT_COMMITTER_NAME='Go carry', GIT_COMMITTER_EMAIL='go-carry@example.invalid')
        made = subprocess.run(['git', '-C', str(source.root), 'commit-tree', tree, '-p', parent, '-m', 'previewed candidate'],
                              env=identity, capture_output=True)
        require(made.returncode == 0, 'Cannot name the previewed candidate: ' + made.stderr.decode('utf-8', 'replace'))
        merge = subprocess.run(['git', '-C', str(source.root), 'merge-tree', '--write-tree', '--name-only',
                                '--merge-base=' + parent, main_sha, made.stdout.decode().strip()], capture_output=True)
        lines = merge.stdout.decode('utf-8', 'replace').splitlines()
        require(merge.returncode in (0, 1), 'git merge-tree failed: ' + merge.stderr.decode('utf-8', 'replace'))
        if merge.returncode != 0:
            conflicts = [l for l in lines[1:] if l and not l.startswith(('Auto-merging', 'CONFLICT'))]
            refuse('merge-tree', 'the candidate does not merge cleanly onto the new main' +
                   ((': ' + ', '.join(conflicts[:5])) if conflicts else ''))
        merged = lines[0].strip()
        carried = early_approval.raw_change(source, main_sha, merged)
        have, want = early_approval.stripped(carried), early_approval.stripped(early_approval.unpack(original['change']))
        carried_id = early_approval.patch_id(carried)
        if have != want or carried_id != original['patch_id']:
            first = next((a for a, b in zip(have.splitlines(), want.splitlines()) if a != b), b'')
            refuse('diff', 'the candidate\'s diff on the new main differs from the previewed diff (patch id %s against %s, '
                   '%d against %d bytes, first difference: %s)' % (carried_id[:12] or '-', original['patch_id'][:12] or '-',
                                                                   len(have), len(want), first[:80].decode('utf-8', 'replace') or 'at the end'))
        names = sorted(n for n in source.git('diff', '--name-only', '--no-renames', '-z', main_sha, merged).decode().split('\0') if n)
        if names != manifest:
            refuse('diff', 'the merged candidate changes other paths than the previewed manifest')
        folder = self.clone_at(source, {'number': 1, 'new_parent': main_sha, 'new': merged}, main_sha, staging)
        clone = Checkpoint(folder, runner=source.runner)
        try:
            policy, profiles = clone.data()
            if clone.head_table(profiles) is None:  # fail closed: nothing may be judged without the table on the new main
                refuse('selection', 'the classification table on the new main cannot be read')
            _, profile, _ = clone.derive_selection(manifest, message.decode('utf-8'), policy, profiles, context, 'auto',
                                                   preview['branch'])
            facts = early_approval.preview_facts(clone, manifest, message, profile, clone.table(policy, profiles),
                                                 clone.head_table(profiles), main_sha, preview['branch'])
        except GoCarryRefused:
            raise
        except (ValueError, KeyError, TypeError, OSError, UnicodeError) as error:
            refuse('selection', 'the selection cannot be derived on the new main: ' + str(error))
        facts['repo'] = preview['repo']
        differing = early_approval.go_fact_differences(original, facts)
        if differing:
            refuse('selection', 'what the new main derives differs from the previewed %s' % ', '.join(differing))
        if facts['tree'] != merged:
            refuse('diff', 'the candidate built on the new main is not the merged tree')
        if not facts['eligible']:
            refuse('eligibility', 'the go no longer carries on the new main: ' + '; '.join(facts['reasons']))
        upstream = source.git('rev-list', '--reverse', parent + '..' + main_sha).decode().split()
        upstream_paths = sorted(p for p in source.git('diff', '--name-only', '--no-renames', '-z', parent, main_sha)
                                .decode().split('\0') if p)
        return {'main': main_sha, 'body': body, 'original': original, 'facts': facts, 'merged': merged, 'packet': packet,
                'packet_sha256': digest((source.store / TRANSACTION).read_bytes()) if packet else None,
                'patch_id': carried_id, 'change_sha256': digest(have),
                'upstream': {'from': parent, 'to': main_sha, 'commits': upstream, 'paths': upstream_paths},
                'overlap': sorted(set(manifest) & set(upstream_paths))}

    def go_move(self, source, owner, path, shared, proof, output, job_handle):
        """The short locked step: read everything again, move the worktree, then write the carried go and spend the old one."""
        main_sha, body, facts, merged = proof['main'], proof['body'], proof['facts'], proof['merged']
        preview = body['preview']
        ref_audits = []
        self.idle(self.main)
        self.exact(self.main, main_sha)
        self.hooks()
        self.source(path, owner)
        on, why = early_approval.carry_switch(self.main)
        if not on:
            raise GoCarryRefused('switch', why + ' on main; the carry is off')
        check_ref_context(shared, self.shared_state(source.branch()),
                          'Shared refs/stash/configuration changed during the go carry proof', ref_audits)
        self.go_state(source, body)
        require((digest((source.store / TRANSACTION).read_bytes()) if proof['packet'] else None) == proof['packet_sha256'],
                'The transaction changed during the proof')
        require(source.git('rev-parse', merged + '^{tree}').decode().strip() == merged, 'The merged tree is missing')
        if proof['packet']:
            source.abandon('The go was carried to a moved base; prepare again with the carried go')
        self.go_advance(source, preview['manifest'], preview['parent'], main_sha, preview['tree'], merged)
        moves = (body.get('rebound') or {}).get('moves', [])
        audit = {'kind': 'diff-bound-go-carry-v1', 'at': claims.now(), 'owner': owner, 'job_handle': job_handle,
                 'key': {'name': early_approval.CARRY_KEY, 'value': True, 'read_at_main': main_sha},
                 'move_number': len(moves) + 1, 'state': 'staged' if proof['packet'] else 'preview',
                 'old_parent': preview['parent'], 'new_parent': main_sha, 'old_tree': preview['tree'], 'new_tree': merged,
                 'old_preview': preview['id'], 'new_preview': facts['id'], 'original_preview': proof['original']['preview']['id'],
                 'upstream': proof['upstream'], 'overlap': proof['overlap'],
                 'patch_id': proof['patch_id'], 'original_patch_id': proof['original']['patch_id'],
                 'change_sha256': proof['change_sha256'],
                 'selection': {'checks_sha256': facts['checks_sha256'], 'sensitive_paths': facts['sensitive_paths'],
                               'reach': facts['switch']['reach'], 'eligible': facts['eligible']},
                 'tooling': {'guard_sha256': self.guard_digest(),
                             'checkpoint_sha256': checkpoint_driver()[1]}}
        sealed = early_approval.rebound_go(source, body, proof['original'], facts, audit)
        early_approval.write_new(output, canonical(sealed))
        early_approval.spend_go(source, early_approval.go_id(body), early_approval.go_id(sealed['body']))
        self.exact(self.main, main_sha)
        return {'state': 'go-carried', 'old_parent': preview['parent'], 'new_parent': main_sha, 'old_tree': preview['tree'],
                'new_tree': merged, 'was': audit['state'], 'go': str(output), 'audit': audit, 'transient_ref_audits': ref_audits,
                'notice': review_render.render_go_carry(audit),
                'next': 'show the lead developer the notice, then prepare with --early-go ' + str(output) +
                        ' (every selected check runs again on the new tree)'}

    def go_advance(self, source, manifest, old_base, new_base, old_tree, new_tree):
        """Move the worktree from the old base to the new one with the merged candidate on it, or put it back as it was.

        Everything the candidate is lives in `old_tree` (a tree object), so the manifest paths can be set back to the old
        base's bytes, the whole tree moved by Git itself, and the candidate written again from the merged tree. The branch
        moves last, by compare and swap. A failure puts the old base and the old candidate back and raises."""
        branch = source.branch()

        def write_as(tree, names):
            """The working files of `names` as `tree` has them; a path the tree lacks is removed."""
            with tempfile.TemporaryDirectory(prefix='go-carry-', dir=source.gitdir) as folder:
                env = dict(os.environ, GIT_INDEX_FILE=str(Path(folder) / 'index'))

                def git(*args):
                    done = subprocess.run(['git', '-C', str(source.root), '--literal-pathspecs', *args], env=env, capture_output=True)
                    require(done.returncode == 0, 'Git failed: ' + ' '.join(args) + ': ' + done.stderr.decode('utf-8', 'replace'))
                    return done.stdout
                git('read-tree', tree)
                present = [n.decode('utf-8') for n in git('ls-files', '-z', '--', *names).split(b'\0') if n]
                if present:
                    git('checkout-index', '-f', '-q', '--', *present)
                for name in names:
                    if name not in present:
                        (source.root / name).unlink(missing_ok=True)
        moved = False
        try:
            write_as(old_base, manifest)
            source.git('update-index', '-q', '--refresh')  # the files were rewritten behind the index, so its stat data is stale
            source.git('read-tree', '-m', '-u', old_base, new_tree)
            source.git('update-ref', branch, new_base, old_base)
            moved = True
            source.git('read-tree', new_base)
            require(source.head() == new_base and early_approval.candidate_tree(source, manifest) == new_tree,
                    'The moved worktree does not hold the merged candidate')
        except (ValueError, OSError) as error:
            try:
                if moved:
                    source.git('update-ref', branch, old_base, new_base)
                source.git('read-tree', '--reset', '-u', old_base)
                write_as(old_tree, manifest)
            except (ValueError, OSError) as second:
                raise ValueError('The go carry stopped and could not restore the worktree (%s). The candidate is saved as tree %s: '
                                 'inspect before any recovery. First failure: %s' % (second, old_tree, error)) from error
            raise ValueError('The go carry stopped; the worktree was put back as it was: ' + str(error)) from error
