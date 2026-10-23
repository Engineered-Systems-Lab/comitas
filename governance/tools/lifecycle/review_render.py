"""Deterministic Markdown rendering of a sealed checkpoint review, built only from the sealed packet.

One layout for every transaction, so no worker has to remember an order or a field. The agent shows this text
verbatim and adds only its own `Review focus`, which is judgment and cannot be generated. The text is a view of
the packet: it is not sealed, grants nothing and never changes the digest. Standard library only.
"""
import base64
import re

from governance.tools.lifecycle import approval_words
from governance.tools.lifecycle import early_approval
from governance.tools.support import timing_record

NONE = '-'


def cell(value):
    """Make a value safe inside a Markdown table cell."""
    return str(value).replace('|', '\\|').replace('\r', ' ').replace('\n', ' ')


def code(value):
    return '`' + str(value).replace('`', "'") + '`'


def fence(text, info=''):
    """A code fence longer than any backtick run inside the text."""
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    ticks = '`' * max(3, longest + 1)
    return f'{ticks}{info}\n{text.rstrip(chr(10))}\n{ticks}'


def table(header, rows):
    lines = ['| ' + ' | '.join(header) + ' |', '| ' + ' | '.join('---' for _ in header) + ' |']
    lines += ['| ' + ' | '.join(cell(c) for c in row) + ' |' for row in rows]
    return '\n'.join(lines)


def file_changes(manifest, diff):
    """Per manifest path: how it changed and by how many lines, read from the sealed diff."""
    result = {}
    for path in manifest:
        header = f'diff --git a/{path} b/{path}\n'
        start = diff.find(header)
        if start < 0:
            result[path] = {'change': 'changed', 'added': None, 'removed': None}
            continue
        end = diff.find('\ndiff --git ', start + len(header))
        block = diff[start:end if end >= 0 else len(diff)]
        header = block.split('\n@@', 1)[0]  # only the header decides: a text file may quote these phrases
        if 'GIT binary patch' in header or '\nBinary files ' in header:
            result[path] = {'change': 'binary', 'added': None, 'removed': None}
            continue
        change = 'new' if '\nnew file mode ' in block else 'deleted' if '\ndeleted file mode ' in block else 'edit'
        body = block.split('\n@@', 1)[1] if '\n@@' in block else ''
        lines = body.split('\n')[1:] if body else []
        result[path] = {'change': change,
                        'added': sum(1 for l in lines if l.startswith('+')),
                        'removed': sum(1 for l in lines if l.startswith('-'))}
    return result


def change_text(info):
    if info['added'] is None:
        return info['change']
    parts = ([f'+{info["added"]}'] if info['added'] else []) + ([f'-{info["removed"]}'] if info['removed'] else [])
    size = ' '.join(parts) or '0'
    return size if info['change'] == 'edit' else f'{info["change"]} {size}'


def flags(path, data):
    found = []
    view = (data.get('artifact_classification') or {}).get(path) or {}
    if 'runtime' in view.get('boundary', []):
        found.append('runtime')
    if 'frozen' in view.get('boundary', []):
        found.append('frozen')
    if view.get('bound'):
        found.append('bound')
    if view.get('approval_sensitivity') in ('medium', 'high'):
        found.append('approval-' + view['approval_sensitivity'])
    levels = ('low', 'medium', 'high')
    before, after = view.get('head_sensitivity'), view.get('candidate_sensitivity')
    if before in levels and after in levels and before != after:  # The higher of the two judges the early approval.
        found.append('sensitivity-raised' if levels.index(after) > levels.index(before) else 'sensitivity-lowering-ignored')
    if view.get('link_retarget'):
        found.append('link-only')
    if (data.get('impact') or {}).get(path, {}).get('mode') == 'broad':
        found.append('broad')
    return ', '.join(found) or NONE


def outside_manifest(packet):
    status = packet.get('prepared_status')
    if status is None:
        return None
    manifest = set(packet['manifest'])
    return sorted(entry for entry in status.split('\0') if entry and entry[3:] not in manifest)


def render(packet, digest, controls=(('Approve', 'approve'), ('Reject', 'reject')),
           tokens=(('APPROVE', 'approve'), ('REJECT', 'reject'))):
    data = packet.get('profile_data') or {}
    repo = packet.get('repo') or {}
    metadata = data.get('commit_metadata') or {}
    branch = str(packet.get('branch', NONE))
    out = ['## Sealed review', '']
    job = packet.get('job_handle')
    out.append(table(['Item', 'Value'], [
        ['Job', NONE if job is None else code(job)],
        ['Worktree', code(repo.get('root', NONE))],
        ['Branch', code(branch[len('refs/heads/'):] if branch.startswith('refs/heads/') else branch)],
        ['Parent', code(str(packet.get('parent', NONE))[:7])],
        ['Tree', code(str(packet.get('tree', NONE))[:7])],
        ['Worker', metadata.get('worker', NONE)],
        ['Transaction', code(packet.get('id', NONE))],
        ['State', packet.get('state', NONE)],
        ['Digest', code(digest)],
    ]))

    manifest = packet['manifest']
    changes = file_changes(manifest, packet.get('diff', ''))
    classification = data.get('classification') or {}
    views = data.get('artifact_classification') or {}
    categories = data.get('categories') or sorted(set(classification.values()))
    added = sum(c['added'] or 0 for c in changes.values())
    removed = sum(c['removed'] or 0 for c in changes.values())
    out += ['', f'### Manifest: {len(manifest)} paths, {" + ".join(categories) or NONE}'
                f'{" (mixed)" if len(categories) > 1 else ""}, profile {code(packet.get("profile", NONE))}, '
                f'+{added} -{removed} lines', '']
    out.append(table(['Path', 'Class', 'Subclass', 'Flags', 'Change'], [
        [code(path), classification.get(path, NONE),
         ', '.join((views.get(path) or {}).get('subclass', [])) or NONE, flags(path, data), change_text(changes[path])]
        for path in manifest]))

    evidence = packet.get('evidence') or []
    action = {run['id']: run['action'] for run in packet.get('validation_runs') or []}
    action['staged-whitespace'] = 'executed'
    estimate = packet.get('timing_estimate')
    observations = packet.get('timing_observations')
    flagged = {o['subject'].split(':', 1)[-1]: o['kind'] for o in observations or [] if isinstance(observations, list)}
    expected = (estimate or {}).get('checks') or {}
    rows = []
    for item in evidence:
        known = expected.get(item['id']) or {}
        mean = known.get('mean')
        rows.append([item['id'], 'pass' if item.get('exit_code') == 0 else 'FAIL',
                     f'{item.get("duration_seconds", 0):.1f}', NONE if mean is None else
                     f'{mean:.1f} (n={known["n"]}{", thin" if known.get("basis") == "thin" else ""})',
                     action.get(item['id'], 'executed'), flagged.get(item['id'], NONE)])
    passed = sum(1 for item in evidence if item.get('exit_code') == 0)
    took = sum(item.get('duration_seconds', 0) for item in evidence if action.get(item['id'], 'executed') == 'executed')
    reused = sum(1 for item in evidence if action.get(item['id']) == 'reused')
    summary = f'{passed} of {len(evidence)} checks passed, {reused} reused. Took {timing_record.span(took)}.'
    if estimate is None and observations is None:
        summary += ' The timing record is off, so there was no estimate.'
    elif estimate is None:
        summary += ' This packet was sealed before estimates were captured, so there is none to compare.'
    elif 'unavailable' in estimate:
        summary += ' The estimate was unavailable: ' + estimate['unavailable']
    else:
        summary += ' Estimate when sealing began: ' + estimate['summary']
    if isinstance(observations, dict) and 'unavailable' in observations:
        summary += ' Departure flags were unavailable: ' + observations['unavailable']
    out += ['', '### Checks', '', summary, '']
    out.append(table(['Check', 'Result', 'Seconds', 'Expected', 'Run', 'Flag'], rows))

    out += ['', '### Limits', '', data.get('limitations') or NONE]
    outside = outside_manifest(packet)
    out += ['', '### Outside the manifest', '']
    if outside is None:
        out.append('Not recorded in this packet.')
    elif not outside:
        out.append('Clean: nothing else is modified or untracked.')
    else:
        out.append('Not part of this commit:')
        out += [f'- {code(entry)}' for entry in outside]

    message = base64.b64decode(packet['message_b64']).decode('utf-8')
    out += ['', '### Exact commit message', '', fence(message),
            '', f'SHA-256 {code(packet.get("message_sha256", NONE))}, {len(message.encode("utf-8"))} bytes. '
                'Escaped form, which shows trailing spaces and line endings:', '', fence(repr(message))]
    early = packet.get('early_approval')
    if early:
        out += ['', '### Early approval', '']
        if early.get('applied') is False:
            out.append('The go at the preview did not approve this candidate, so only the decision below counts: ' +
                       '; '.join(early.get('reasons') or ['no reason recorded']) + '.')
        else:
            out.append('A go at the preview names this candidate and has not been applied yet.')
    reject = next((token.lower() for token, value in tokens if value == 'reject'), 'reject')
    words = ', '.join('**' + word + '**' for word in approval_words.DISPLAY[:-1]) + \
            ' or **' + approval_words.DISPLAY[-1] + '**'
    out += ['', '### Decision', '',
            'Choose ' + ' or '.join(label for label, _ in controls) + ', or reply ' + words +
            ' to approve, or **' + reject + '** to reject. The decision is bound to the digest above.']
    return '\n'.join(out) + '\n'


def sensitivity_text(view):
    """The effective approval sensitivity of one path, with both rows when the candidate's own table differs from HEAD."""
    if not view.get('resolved'):
        return 'no row'
    level = view.get('approval_sensitivity', NONE)
    before, after = view.get('head_sensitivity'), view.get('candidate_sensitivity')
    return level if before == after else '%s (HEAD %s, candidate %s)' % (level, before, after)


def render_preview(facts):
    """The preview as one Markdown block, built only from the preview record and the facts derived beside it.

    `facts` holds `body` (the preview record's body), `profile`, `profile_data` (the derived selection, as sealed in a
    packet), `diff` (HEAD to the candidate tree), `message`, `estimate` (or None), `workspace` and `job_handle`. The
    worker shows this text verbatim and adds only its own `Review focus`. Like the sealed review it is a view: it is
    not sealed, grants nothing and never changes the preview record.
    """
    body, data = facts['body'], facts.get('profile_data') or {}
    manifest = body['manifest']
    branch = str(body['branch'])
    job = facts.get('job_handle')
    switch = body.get('switch') or {}
    out = ['## Preview', '']
    out.append(table(['Item', 'Value'], [
        ['Job', NONE if job is None else code(job)],
        ['Workspace', code(facts.get('workspace', NONE))],
        ['Branch', code(branch[len('refs/heads/'):] if branch.startswith('refs/heads/') else branch)],
        ['Parent', code(str(body['parent'])[:7])],
        ['Tree', code(str(body['tree'])[:7])],
        ['Preview', code(body['id'])],
        ['Early approval reach', code(switch.get('reach', 'off'))],
    ]))
    out += ['', 'Nothing is staged or sealed yet and no suite has run. You can read the change in the workspace folder '
                'in IntelliJ or another IDE.']

    changes = file_changes(manifest, facts.get('diff', ''))
    classification = data.get('classification') or {}
    views = data.get('artifact_classification') or {}
    categories = data.get('categories') or sorted(set(classification.values()))
    added = sum(c['added'] or 0 for c in changes.values())
    removed = sum(c['removed'] or 0 for c in changes.values())
    out += ['', f'### Manifest: {len(manifest)} paths, {" + ".join(categories) or NONE}'
                f'{" (mixed)" if len(categories) > 1 else ""}, profile {code(facts.get("profile", NONE))}, '
                f'+{added} -{removed} lines', '']
    out.append(table(['Path', 'Class', 'Subclass', 'Sensitivity', 'Flags', 'Change'], [
        [code(path), classification.get(path, NONE), ', '.join((views.get(path) or {}).get('subclass', [])) or NONE,
         sensitivity_text(views.get(path) or {}), flags(path, data), change_text(changes[path])] for path in manifest]))

    estimate = facts.get('estimate')
    expected = (estimate or {}).get('checks') or {}
    if isinstance(expected, list):  # `estimate` lists its checks by subject; a sealed packet maps them by id
        expected = {item['subject'].split(':', 1)[-1]: item for item in expected}
    rows = []
    for name in ['staged-whitespace'] + list(body['check_ids']):
        known = expected.get(name) or {}
        mean = known.get('mean')
        rows.append([name, NONE if mean is None else
                     f'{mean:.1f} (n={known["n"]}{", thin" if known.get("basis") == "thin" else ""})'])
    out += ['', '### Derived checks', '',
            'These run when the candidate is sealed, and the worker cannot reduce the list.' +
            (' Estimate: ' + estimate['summary'] if estimate else ' No estimate: the timing record is off or unavailable.'), '']
    out.append(table(['Check', 'Expected seconds'], rows))
    if data.get('runtime_checks'):
        out += ['', 'Runtime gates: ' + ', '.join(code(gate) for gate in data['runtime_checks']) + '.']
    out += ['', 'Limits: ' + (data.get('limitations') or NONE)]

    message = facts['message']
    out += ['', '### Draft commit message', '', fence(message),
            '', f'SHA-256 {code(body.get("message_sha256", NONE))}, {len(message.encode("utf-8"))} bytes. '
                'Escaped form, which shows trailing spaces and line endings:', '', fence(repr(message))]

    out += ['', '### Early approval', '']
    if body.get('eligible'):
        out.append(f'Eligible: every path is within the early approval reach {code(switch.get("reach", "off"))} '
                   '(each judged by the higher of its row at HEAD and in the candidate\'s own table) and the switch is on.')
    else:
        out.append('Not eligible:')
        out += [f'- {reason}' for reason in body.get('reasons') or ['no reason recorded']]
    out += ['', '### Review focus', '', 'The worker replaces this line with its own Review focus and adds nothing else.']

    words = ', '.join('**' + word + '**' for word in approval_words.DISPLAY[:-1]) + \
            ' or **' + approval_words.DISPLAY[-1] + '**'
    out += ['', '### Go file values', '',
            'The go file names this preview by these two values; copy them, compute nothing.', '',
            fence('preview_id: %s\npreview_sha256: %s' % (body['id'], early_approval.digest(early_approval.canonical(body)))),
            '', '### Go', '']
    if body.get('eligible'):
        out.append(f'Reply {words} to approve this exact candidate (tree {code(str(body["tree"])[:7])}) if every check '
                   'passes; any difference or failure returns to the sealed review and its Approve.')
    else:
        out.append(f'Reply {words} to stage and seal. This manifest is not eligible, so the go only stages and seals; '
                   'the sealed review and its Approve decide.')
    return '\n'.join(out) + '\n'


def render_carry(audit):
    """The carry notice: what moved, what was proven and what the lead developer may still want to look at. No click follows."""
    commits = audit['commits']
    ran = [c for item in commits for c in item.get('checks') or []]
    passed = sum(1 for c in ran if c.get('exit_code') == 0)
    upstream = audit['upstream']
    out = ['## Carry notice', '',
           'An approval you already gave was carried to a newer main. The diff you approved is unchanged, and every selected '
           'check ran again on the carried tree. Nothing asks for a click; this is the record.', '']
    out.append(table(['Item', 'Value'], [
        ['Job', NONE if audit.get('job_handle') is None else code(audit['job_handle'])],
        ['Branch', code(audit['owner'])],
        ['Tip', code(audit['old_tip'][:7]) + ' to ' + code(audit['new_tip'][:7])],
        ['Base', code(audit['old_base'][:7]) + ' to ' + code(audit['new_base'][:7])],
        ['Upstream range', '%s commit(s), %s path(s)' % (len(upstream['commits']), len(upstream['paths']))],
        ['Carry number', audit['carry_number']],
        ['Switch', code(audit['key']['name']) + ' is true on main ' + code(audit['key']['read_at_main'][:7])],
        ['Checks', '%d of %d passed on the carried tree' % (passed, len(ran))],
    ]))
    out += ['', '### Commits', '']
    out.append(table(['Old', 'New', 'Parent', 'Tree', 'Patch id', 'Approved by'], [
        [code(c['old'][:7]), code(c['new'][:7]), code(c['old_parent'][:7]) + ' to ' + code(c['new_parent'][:7]),
         code(c['old_tree'][:7]) + ' to ' + code(c['new_tree'][:7]), code((c['patch_id'] or NONE)[:12]),
         'the sealed Approve' if c.get('approval_path') != 'early' else 'a go at the preview'] for c in commits]))
    for c in commits:
        if c.get('id_map'):
            out += ['', 'Commit %s cites the commits carried before it, so these ids were replaced in its records: %s.' % (
                code(c['new'][:7]), ', '.join(code(old[:7]) + ' to ' + code(new[:7]) for old, new in c['id_map'].items()))]
    out += ['', '### Files both the candidate and the upstream range touched', '']
    out.append(', '.join(code(p) for p in audit['overlap']) if audit['overlap'] else
               'None. A shared file would be a notice and not a gate; it is no proof of independence.')
    out += ['', 'The proofs (merge tree, patch ids, id map, selection digest, check results) are in the authenticated landing record.']
    return '\n'.join(out) + '\n'


def render_go_carry(audit):
    """The go-carry notice: what moved, what was proven and what the lead developer may still want to look at. No click follows."""
    upstream, selection = audit['upstream'], audit['selection']
    out = ['## Go carry notice', '',
           'The go you gave at a preview was carried to a newer main. The diff you previewed is unchanged, the message is the '
           'same, the selection derived on the new main reproduces the previewed facts, and every selected check runs again '
           'on the new tree when the candidate is sealed. Nothing asks for a click; this is the record.', '']
    out.append(table(['Item', 'Value'], [
        ['Job', NONE if audit.get('job_handle') is None else code(audit['job_handle'])],
        ['Mission', code(audit['owner'])],
        ['Carried from', 'a staged transaction, unstaged again' if audit['state'] == 'staged' else 'a preview'],
        ['Parent', code(audit['old_parent'][:7]) + ' to ' + code(audit['new_parent'][:7])],
        ['Tree', code(audit['old_tree'][:7]) + ' to ' + code(audit['new_tree'][:7])],
        ['Upstream range', '%s commit(s), %s path(s)' % (len(upstream['commits']), len(upstream['paths']))],
        ['Move number', audit['move_number']],
        ['Switch', code(audit['key']['name']) + ' is true on main ' + code(audit['key']['read_at_main'][:7])],
        ['Patch id', code((audit['patch_id'] or NONE)[:12]) + ' (the previewed diff: ' + code((audit['original_patch_id'] or NONE)[:12]) + ')'],
        ['Early approval reach', code(selection['reach']) + (', still eligible' if selection['eligible'] else ', not eligible')],
    ]))
    out += ['', '### Files both the candidate and the upstream range touched', '']
    out.append(', '.join(code(p) for p in audit['overlap']) if audit['overlap'] else
               'None. A shared file would be a notice and not a gate; it is no proof of independence.')
    out += ['', 'The proofs (merge tree, patch ids, selection facts) are in the authenticated go record.']
    return '\n'.join(out) + '\n'


def render_landing(data, main_tip=None):
    """The notice the lead developer gets after a transaction lands, built only from the finished transaction's facts."""
    early = data.get('approval_path') == 'early'
    carried = data.get('carried_from')
    go = data.get('go') or {}
    job = data.get('job_handle')
    out = ['## Landing notice', '']
    out.append(table(['Item', 'Value'], [
        ['Job', NONE if job is None else code(job)],
        ['Commit', code(data['result_sha'])],
        ['Subject', data.get('subject') or NONE],
        ['Parent', code(str(data['parent'])[:7])],
        ['Tree', code(str(data['tree'])[:7])],
        ['Branch', code(str(data['branch']).removeprefix('refs/heads/'))],
        ['Approved by', 'your approval of the original commit, carried onto a newer main (see the carry notice below)' if carried
         else ('your go at the preview ("' + str(go.get('response')) + '", message ' +
               str(go.get('user_message_id')) + ')') if early else 'the sealed Approve'],
        ['Integrated', ('yes, main is now ' + code(main_tip[:7])) if main_tip else 'not yet'],
    ]))
    checks = data.get('checks') or []
    passed = sum(1 for c in checks if c.get('exit_code') == 0)
    out += ['', f'### Checks: {passed} of {len(checks)} passed', '',
            ', '.join(c['id'] for c in checks) or NONE, '', f'### Manifest: {len(data["manifest"])} paths', '']
    changes = data.get('changes') or {}
    out.append(table(['Path', 'Class', 'Change'], [
        [code(path), (data.get('classification') or {}).get(path, NONE),
         change_text(changes[path]) if path in changes else NONE] for path in data['manifest']]))
    if carried:
        out += ['', render_carry(carried).rstrip('\n')]
    out += ['', '### Undo', '', 'Nothing was pushed. To undo this change on main, run `git revert ' +
            data['result_sha'][:7] + '` from the main checkout.']
    return '\n'.join(out) + '\n'
