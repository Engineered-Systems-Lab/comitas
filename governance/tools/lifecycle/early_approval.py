"""Early conditional approval (G018 extension): a go at an eligible preview approves the exact candidate it names.

Pure helpers for the checkpoint tool (Python 3.13+, standard library only). A preview record names one candidate:
parent, tree, manifest, message bytes and the derived check list. A go recorded against that record approves the
candidate only if the sealed result reproduces every one of those facts, every check passes, no manifest path is
above the committed reach in the artifact classification table, and the committed reach is not off. Anything else
returns to the sealed review. Like the sealed approval, the go is the agent's asserted reference to a lead developer reply:
the local receipt authenticates integrity and exact binding, never who typed it.
"""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib
import zlib

from governance.tools import runtime_guard
from governance.tools.lifecycle import approval_words
from governance.tools.continuity import artifact_classification

FEATURES = artifact_classification.FEATURES
EARLY_USES = artifact_classification.EARLY_USES
LANDING = artifact_classification.LANDING
KEY = 'early_approval_reach'
CARRY_KEY = 'diff_bound_approval'
REACHES = tuple(artifact_classification.REACH_ALLOWS)
OFF = 'off'
PARALLEL_KEY = 'parallel_tests'
WORKERS_KEY = 'test_workers'
MAIN_REF = 'refs/heads/main'
RECEIPT_KIND = 'approver-preview-go-v1'
ATTEMPT_CAP = 3
CHANNELS = ('interactive-control', 'text-token')
GO_INPUT = {'source', 'conversation_id', 'user_message_id', 'channel', 'response', 'preview_id', 'preview_sha256'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sign(tool, body):
    """An authenticated envelope; the receipt key stays in the owning checkout's scratch."""
    return {'body': body, 'mac': hmac.new(tool.key(), canonical(body), hashlib.sha256).hexdigest()}


def unsign(tool, envelope):
    require(isinstance(envelope, dict) and set(envelope) == {'body', 'mac'}, 'Malformed authenticated record')
    expected = hmac.new(tool.key(), canonical(envelope['body']), hashlib.sha256).hexdigest()
    require(isinstance(envelope['mac'], str) and hmac.compare_digest(envelope['mac'], expected),
            'Authenticated record mismatch; do not edit preview or go records')
    return envelope['body']


def read_features(tool):
    """(data, reason) from the feature file on main's current tip; data is None when it cannot be read or parsed."""
    try:
        raw = tool.git('show', MAIN_REF + ':' + FEATURES)
        return tomllib.loads(raw.decode('utf-8')), 'read'
    except (ValueError, UnicodeError, OSError):  # includes an unreadable file, a missing file and tomllib.TOMLDecodeError
        return None, 'the feature file on main is missing, unreadable or invalid'


def switch(tool):
    """(reach, reason) from main's current tip: `low` or `medium`, else `off`.

    Only the exact strings `low` and `medium` turn the early approval on; `off`, `high`, a missing key, any other
    value or type, and a missing, unreadable or invalid file are all off, and off means the sealed Approve."""
    data, why = read_features(tool)
    if data is None:
        return OFF, why
    value = data.get(KEY)
    if isinstance(value, str) and value in REACHES:
        return value, 'reach ' + value
    return OFF, KEY + ' is not "low" or "medium"'


def carry_switch(tool):
    """(on, reason) from main's current tip: only the boolean true turns the carry of an approval on.

    A missing key, any other value or type, and a missing, unreadable or invalid file are all off, and off means
    that a moved base takes a fresh Approve, exactly as before the carry existed."""
    data, why = read_features(tool)
    if data is None:
        return False, why
    if data.get(CARRY_KEY) is True:
        return True, CARRY_KEY + ' is true'
    return False, CARRY_KEY + ' is not the boolean true'


def assert_carry_on(tool):
    on, why = carry_switch(tool)
    require(on, 'The carry switch is off: ' + why + '; the approval does not carry, take a fresh Approve')


def test_settings(tool):
    """How the test runner runs, read like the switch above from main's tip: {parallel, workers, reason}.

    `parallel_tests` must be the boolean true for parallel mode; a missing key, any other value or an unreadable or
    invalid file means sequential. `test_workers` is a whole number, zero (one worker per core, also the value of a
    missing key) or a positive cap; any other value fails safe to sequential."""
    data, why = read_features(tool)
    if data is None:
        return {'parallel': False, 'workers': 0, 'reason': why}
    workers = data.get(WORKERS_KEY, 0)
    if type(workers) is not int or workers < 0:
        return {'parallel': False, 'workers': 0, 'reason': WORKERS_KEY + ' is not a whole number of zero or more'}
    if data.get(PARALLEL_KEY) is not True:
        return {'parallel': False, 'workers': workers, 'reason': PARALLEL_KEY + ' is not the boolean true'}
    return {'parallel': True, 'workers': workers, 'reason': PARALLEL_KEY + ' is true'}


def check_list(profile):
    """The derived check list: exact ids and argument arrays, plus the runtime gates, order-independent."""
    return {'checks': sorted([c['id'], list(c['argv'])] for c in profile['checks']),
            'runtime_checks': sorted(profile.get('runtime_checks', []))}


def checks_digest(profile):
    return digest(canonical(check_list(profile)))


def candidate_tree(tool, manifest):
    """The tree the staged candidate will have, computed in a private temporary index without staging anything."""
    with tempfile.TemporaryDirectory(prefix='candidate-index-') as folder:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(folder) / 'index'))

        def git(*args):
            result = subprocess.run(['git', '-C', str(tool.root), '--literal-pathspecs', *args], env=env,
                                    capture_output=True)
            require(result.returncode == 0, 'Git failed: ' + ' '.join(args) + '\n' +
                    result.stderr.decode('utf-8', 'replace'))
            return result.stdout
        git('read-tree', 'HEAD')
        artifact_classification.stage_manifest(git, tool.root, manifest)
        return git('write-tree').decode().strip()


ABOVE, NO_ROW, UNCLASSIFIED, HIGH = 'above', 'no-row', 'unclassified', 'high'
KNOWN_LEVELS = ('low', 'medium', 'high')
NO_ROW_HINT = 'counted high; a folder or pattern row on main, added once, makes later files here eligible'


def refusal_case(resolved, blocked, head_level, candidate_level, level, allowed):
    """Why one path is not admitted, as (case, effective sensitivity), or None when it is admitted.

    The cases are the ones that occur: `above` (a row at HEAD, graded above the reach but not high), `no-row` (a path with
    no row at HEAD whose row exists only in the candidate, counted high because a change may not grade itself),
    `unclassified` (no usable row, an unknown value or a blocked profile) and `high` (graded high, never admitted at any
    reach). Anything that is not clearly one of the first, second and fourth is unclassified, so the test stays fail-closed."""
    if resolved is True and not blocked and head_level in allowed and level in allowed:
        return None
    if resolved is not True or blocked or head_level is None:
        return UNCLASSIFIED, level
    if head_level == 'none':
        return (NO_ROW, level) if candidate_level in KNOWN_LEVELS else (UNCLASSIFIED, level)
    if head_level not in KNOWN_LEVELS or level not in KNOWN_LEVELS:
        return UNCLASSIFIED, level
    return (HIGH if level == 'high' else ABOVE), level


def case_reasons(reach, found):
    """One line per non-empty case from {path: (case, level)}, each with its paths."""
    reasons = []
    above = {}
    for path, (case, level) in found.items():
        if case == ABOVE:
            above.setdefault(level, []).append(path)
    for level in sorted(above, key=KNOWN_LEVELS.index):
        reasons.append('paths above the early approval reach (%s), effective sensitivity %s: %s'
                       % (reach, level, ', '.join(sorted(above[level]))))
    lines = ((NO_ROW, 'paths with no row at HEAD (%s)' % NO_ROW_HINT),
             (HIGH, 'paths graded high, which no reach admits'),
             (UNCLASSIFIED, 'paths unclassified or blocked (no usable row, an unknown value or a blocked profile)'))
    for case, label in lines:
        paths = sorted(p for p, (c, _) in found.items() if c == case)
        if paths:
            reasons.append('%s: %s' % (label, ', '.join(paths)))
    return reasons


def reasons_not_eligible(table, head, manifest, reach, why):
    """Why a manifest cannot carry an early approval; empty means eligible. Unknown values fail closed.

    Each path counts as the higher of its row in `head`, the table at HEAD, and its row in `table`, the candidate's.
    A manifest on the runtime continuity boundary is eligible like any other: it keeps the follow-up of its records.
    The paths the reach refuses are named by case, one reason per case (`refusal_case`); the paths are the same ones."""
    reasons = []
    if reach == OFF:
        reasons.append('the early conditional approval switch is off: ' + why)
    refused = table.ineligible_paths(manifest, reach, head)
    if refused:
        found = {}
        for path in refused:
            grades = table.sensitivity(path, head)
            rows = [None if t is None else t.resolve(path) for t in (head, table)]
            blocked = any(r is not None and r.profile == artifact_classification.BLOCKED for r in rows)
            found[path] = refusal_case(True, blocked, None if head is None else grades['head'], grades['candidate'],
                                       grades['level'], ())
        reasons += case_reasons(reach, found)
    return reasons


def sealed_reasons(packet, reach):
    """The eligibility reasons recomputed from the sealed classification view; any missing fact is a reason.

    A sensitivity the reach does not allow, `high` at any reach, a missing or unknown value and an unknown reach are
    all reasons, and so is an entry that does not record the HEAD row's sensitivity beside the effective one. They are
    named by the same cases as the preview's, one reason per case."""
    view = (packet.get('profile_data') or {}).get('artifact_classification') or {}
    allowed = artifact_classification.REACH_ALLOWS.get(reach, ())
    found = {}
    for path in packet['manifest']:
        entry = view.get(path) or {}
        case = refusal_case(entry.get('resolved'), entry.get('profile') == 'blocked', entry.get('head_sensitivity'),
                            entry.get('candidate_sensitivity'), entry.get('approval_sensitivity'), allowed)
        if case:
            found[path] = case
    return case_reasons(reach, found)


def preview_facts(tool, manifest, message, profile, table, head, expected_head, expected_branch):
    reach, why = switch(tool)
    reasons = reasons_not_eligible(table, head, manifest, reach, why)
    return {'version': 1, 'id': digest(os.urandom(32))[:32], 'repo': tool.identity(),
            'interpreter': runtime_guard.fingerprint(), 'branch': expected_branch,
            'parent': expected_head, 'tree': candidate_tree(tool, manifest), 'manifest': manifest,
            'message_sha256': digest(message), 'checks_sha256': checks_digest(profile),
            'check_ids': [c['id'] for c in profile['checks']], 'switch': {'on': reach != OFF, 'reach': reach, 'reason': why},
            'eligible': not reasons, 'reasons': reasons,
            'sensitive_paths': table.sensitive_paths(manifest, head)}


def record_go(tool, preview_envelope, consent):
    """Validate the agent-recorded go against its preview record and return the authenticated go envelope."""
    body = unsign(tool, preview_envelope)
    require(body.get('version') == 1 and body.get('repo') == tool.identity(), 'Preview belongs to another checkout')
    runtime_guard.assert_pin(body.get('interpreter'))  # The transaction began at this preview: refuse at the start of the step.
    require(isinstance(consent, dict) and set(consent) == GO_INPUT, 'Invalid lead developer go input')
    require(consent['source'] == 'active-task-user', 'Lead developer/user-authored explicit go required')
    require(all(isinstance(consent[k], str) and consent[k].strip() for k in
                ('conversation_id', 'user_message_id', 'response', 'preview_id')), 'Missing active conversation reference')
    require(consent['channel'] in CHANNELS, 'Unknown lead developer go channel')
    require(approval_words.meaning('preview', consent['response']) == 'stage-and-seal',
            'The reply is not an approval word; show the normal preview flow instead')
    require(consent['preview_id'] == body['id'] and consent['preview_sha256'] == digest(canonical(body)),
            'The go does not name the currently presented preview; copy preview_id and preview_sha256 from the '
            'rendered preview block')
    require(body['eligible'], 'The preview was not eligible for early approval: ' + '; '.join(body['reasons']))
    reach, why = switch(tool)
    require(reach != OFF, 'The early conditional approval switch is off: ' + why)
    allowed = artifact_classification.REACH_ALLOWS[reach]
    sensitive = body.get('sensitive_paths')
    require(isinstance(sensitive, dict) and all(level in allowed for level in sensitive.values()),
            'The preview names paths above the early approval reach (%s); show a new preview' % reach)
    require(tool.head() == body['parent'] and tool.branch() == body['branch'],
            'HEAD or branch moved since the preview; show a new preview')
    require(candidate_tree(tool, body['manifest']) == body['tree'],
            'The working content changed since the preview; show a new preview')
    return sign(tool, {'version': 1, 'go': dict(consent), 'preview': body})


def uses(tool):
    """Attempts per go, authenticated and kept in scratch so an abandon and a new prepare still count them."""
    path = tool.store / EARLY_USES
    if not path.exists():
        return {}
    require(not path.is_symlink(), 'Symlink early-approval record prohibited')
    return unsign(tool, json.loads(path.read_bytes()))['uses']


def atomic(path, data):
    temporary = path.with_name(path.name + '.' + digest(os.urandom(8))[:16] + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def write_new(path, data):
    """Write a review input that must not already exist."""
    with path.open('xb') as stream:
        stream.write(data)


def save_uses(tool, value):
    atomic(tool.store / EARLY_USES, canonical(sign(tool, {'uses': value})))


def start(tool, envelope, expected_head, expected_branch):
    """The packet's early-approval section for a prepare. A go that cannot be used never stops the prepare."""
    try:
        body = unsign(tool, envelope)
        require(body.get('version') == 1 and body['preview']['repo'] == tool.identity(), 'The go belongs to another checkout')
        require(body['preview']['parent'] == expected_head and body['preview']['branch'] == expected_branch,
                'The go names another parent or branch')
        go_id = digest(canonical(body))
        record = uses(tool).get(go_id, {'attempts': 0, 'committed': False})
        require(not record['committed'], 'The go was already used by a commit')
        record['attempts'] += 1
        require(record['attempts'] <= ATTEMPT_CAP, 'The go reached its cap of %d prepare attempts' % ATTEMPT_CAP)
    except (ValueError, KeyError, TypeError) as error:
        return {'applied': False, 'reasons': ['the go cannot be used: ' + str(error)]}
    value = uses(tool)
    value[go_id] = record
    save_uses(tool, value)
    return {'go_id': go_id, 'go': envelope, 'attempt': record['attempts'], 'applied': None, 'reasons': []}


def problems(tool, packet, reach, why):
    """Every reason the sealed packet does not carry the go at the committed reach; empty means it applies."""
    early = packet['early_approval']
    try:
        body = unsign(tool, early['go'])
        preview, go = body['preview'], body['go']
    except (ValueError, KeyError, TypeError) as error:
        return ['the go record is not authentic: ' + str(error)]
    found = []
    expected = (('parent', packet['parent']), ('tree', packet['tree']), ('manifest', packet['manifest']),
                ('message_sha256', packet['message_sha256']), ('branch', packet['branch']),
                ('checks_sha256', checks_digest(packet['profile_data'])), ('repo', packet['repo']))
    for name, actual in expected:
        if preview.get(name) != actual:
            found.append('the sealed %s differs from the previewed one' % name)
    if go.get('preview_id') != preview.get('id') or early.get('go_id') != digest(canonical(body)):
        found.append('the go does not name this preview')
    if reach == OFF:
        found.append('the early conditional approval switch is off: ' + why)
    else:
        found += sealed_reasons(packet, reach)
    if not all(c.get('exit_code') == 0 for c in packet['evidence']):
        found.append('a check failed')
    if early.get('attempt', ATTEMPT_CAP + 1) > ATTEMPT_CAP:
        found.append('the go reached its attempt cap')
    return found


def decide(tool, packet):
    """At the end of sealing: carry the go into an approval receipt, or record why it does not apply."""
    early = packet.get('early_approval')
    if not early or 'go' not in early:
        return
    reach, why = switch(tool)
    reasons = problems(tool, packet, reach, why)
    early['applied'], early['reasons'], early['reach'] = not reasons, reasons, reach
    if reasons:
        return
    prepared = {k: v for k, v in packet.items() if k != 'approval'}
    prepared['state'] = 'prepared'
    packet['approval'] = {'kind': RECEIPT_KIND, 'prepared_sha256': digest(canonical(prepared)), 'go': early['go']}
    packet['state'] = 'approved'


def assert_receipt(tool, prepared, approval):
    """Validate a carried go against the prepared packet it was recorded for, without reading the switch.

    The reach it was decided at is part of the sealed packet; a packet without one carries nothing."""
    require(isinstance(approval, dict) and set(approval) == {'kind', 'prepared_sha256', 'go'}
            and approval['kind'] == RECEIPT_KIND, 'Invalid early approval receipt')
    require(approval['prepared_sha256'] == digest(canonical(prepared)), 'Early approval does not match the sealed packet')
    early = prepared.get('early_approval') or {}
    require(early.get('applied') is True and early.get('go') == approval['go'], 'Early approval was not applied to this packet')
    require(early.get('reach') in REACHES, 'Early approval has no recorded reach')
    body = unsign(tool, approval['go'])
    preview = body['preview']
    require(preview['parent'] == prepared['parent'] and preview['tree'] == prepared['tree'] and
            preview['manifest'] == prepared['manifest'] and preview['message_sha256'] == prepared['message_sha256'] and
            preview['checks_sha256'] == checks_digest(prepared['profile_data']) and not sealed_reasons(prepared, early['reach']) and
            all(c.get('exit_code') == 0 for c in prepared['evidence']),
            'The sealed candidate differs from the previewed one')


def assert_switch_on(tool, prepared=None):
    """The reach read from main's tip must not be off and, given the sealed packet, must still cover its paths."""
    reach, why = switch(tool)
    require(reach != OFF, 'The early conditional approval switch is off: ' + why + '; the seal must be redone')
    if prepared is not None:
        require(not sealed_reasons(prepared, reach),
                'The early approval reach is now %s and no longer covers the sealed paths; the seal must be redone' % reach)


def mark_committed(tool, packet):
    early = packet.get('early_approval') or {}
    if early.get('go_id'):
        value = uses(tool)
        value.setdefault(early['go_id'], {'attempts': early.get('attempt', 1), 'committed': False})['committed'] = True
        save_uses(tool, value)


def write_landing(tool, packet, data):
    """Keep the landing facts after `finish` clears the packets, so integration and the notice can read them."""
    atomic(tool.store / LANDING, canonical(sign(tool, data)))


def read_landing(tool):
    path = tool.store / LANDING
    if not path.exists():
        return None
    require(not path.is_symlink(), 'Symlink landing record prohibited')
    return unsign(tool, json.loads(path.read_bytes()))


def assert_landing_pin(source):
    """Refuse, at the start of an integration or a carry, when the interpreter is not the one the transaction finished on.

    The landing record is written by `finish` and keeps the pin of the transaction; a worktree without one, or a record
    made before the pin existed, carries nothing to compare."""
    landing = read_landing(source)
    if landing:
        runtime_guard.assert_pin(landing.get('interpreter'))


def assert_integration(source, main, expected_tip):
    """Read the switches from main's tip again before an early-approved or carried tip lands.

    An early approval needs the reach not to read off, and a carried approval needs `diff_bound_approval` to read
    true, for the tip and for every approved commit of the branch that main does not hold yet."""
    landing = read_landing(source)
    if not landing or landing.get('result_sha') != expected_tip:
        return
    paths = {landing.get('approval_path')}
    try:
        pending = set(source.git('rev-list', MAIN_REF + '..' + expected_tip).decode().split())
    except ValueError:
        pending = {expected_tip}
    for entry in landing.get('approvals') or []:
        if isinstance(entry, dict) and entry.get('result_sha') in pending:
            paths.update((entry.get('approval_path'), (entry.get('original') or {}).get('approval_path')))
    if 'early' in paths:
        assert_switch_on(main)
    if 'carried' in paths:
        assert_carry_on(main)


# ---- Diff-bound approval: what a commit's approval binds to, and how a carried commit is compared with it ----

def raw_change(tool, parent, sha):
    """The change a commit makes, without context: the one text the approval binds to besides message and selection."""
    return tool.git('diff', '--no-ext-diff', '--no-textconv', '--no-renames', '--binary', '-U0', parent, sha)


def stripped(raw):
    """The change without hunk headers and blob ids, so that line numbers and the base's blobs do not matter."""
    return b''.join(line for line in raw.splitlines(keepends=True) if not line.startswith((b'@@ ', b'index ')))


def patch_id(raw):
    """`git patch-id --stable` of a change; empty when Git has no hunk to hash (a mode change, a binary file)."""
    result = subprocess.run(['git', 'patch-id', '--stable'], input=raw, capture_output=True)
    require(result.returncode == 0, 'git patch-id failed: ' + result.stderr.decode('utf-8', 'replace'))
    return result.stdout.decode('ascii', 'replace').split(' ', 1)[0].strip()


def substitute(raw, id_map):
    """Replace each old id by its new one, in full and in every shorter form of at least 7 hex digits.

    Only whole hex runs match, so the id of another object that merely starts alike is left alone. Anything the
    map does not account for stays different, and the diff gate then refuses."""
    for old, new in sorted(id_map.items()):
        if old == new:
            continue
        for length in range(len(old), 6, -1):
            pattern = re.compile(rb'(?<![0-9a-fA-F])' + re.escape(old[:length].encode('ascii')) + rb'(?![0-9a-fA-F])')
            raw = pattern.sub(new[:length].encode('ascii'), raw)
    return raw


def pack(raw):
    return base64.b64encode(zlib.compress(raw, 9)).decode('ascii')


def unpack(text):
    return zlib.decompress(base64.b64decode(text))


def selection_digest(profile_name, profile_data, runtime, policy):
    """The sealed selection: class, profile, checks, sensitivity view, runtime flag and the policy it came from."""
    return digest(canonical([profile_name, profile_data, runtime, policy]))


def approval_entry(tool, packet):
    """What a finished, approved commit keeps so that its approval can be carried to a moved base later.

    It binds the diff the lead developer saw (kept whole, in compressed form, so every later carry is checked against the
    original and never against a chain of copies), the exact message, the sealed selection and the manifest."""
    sha, parent = packet['result_sha'], packet['parent']
    raw = raw_change(tool, parent, sha)
    approval = packet.get('approval') or {}
    path = 'early' if (packet.get('early_approval') or {}).get('applied') is True else 'sealed'
    original = {'result_sha': sha, 'parent': parent, 'tree': packet['tree'], 'message_sha256': packet['message_sha256'],
                'approval_path': path, 'approval_kind': approval.get('kind'), 'seal_sha256': approval.get('prepared_sha256'),
                'patch_id': patch_id(raw), 'change_sha256': digest(stripped(raw)), 'change': pack(raw)}
    return {'result_sha': sha, 'parent': parent, 'tree': packet['tree'], 'message_sha256': packet['message_sha256'],
            'branch': packet['branch'], 'manifest': packet['manifest'], 'runtime': packet['runtime'],
            'profile': packet['profile'],
            'selection_sha256': selection_digest(packet['profile'], packet['profile_data'], packet['runtime'],
                                                 packet['policy']),
            'check_ids': [c['id'] for c in packet['profile_data']['checks']],
            'worker_context': (packet['profile_data'] or {}).get('worker_context'),
            'approval_path': path, 'original': original, 'carries': []}


# ---- Diff-bound approval, slice 2: the go of an uncommitted candidate follows a moved base ----

GO_FACTS = ('branch', 'manifest', 'message_sha256', 'checks_sha256', 'check_ids', 'sensitive_paths')


def go_id(body):
    return digest(canonical(body))


def go_pin(body):
    """The interpreter fingerprint of the first preview of a go, also for a go that was moved before; None when it has none."""
    rebound = body.get('rebound')
    preview = rebound['original']['preview'] if rebound else body['preview']
    return preview.get('interpreter')


def go_original(tool, body):
    """The candidate the lead developer previewed, kept whole, so that every later move is compared with it and never with a copy.

    The first move builds it from the objects of the preview (its diff, compressed, and its facts); a go that was moved
    before carries it, and it is returned unchanged."""
    rebound = body.get('rebound')
    if rebound:
        return rebound['original']
    preview = body['preview']
    raw = raw_change(tool, preview['parent'], preview['tree'])
    return {'preview': preview, 'go': body['go'], 'patch_id': patch_id(raw), 'change_sha256': digest(stripped(raw)),
            'change': pack(raw)}


def go_fact_differences(original, facts):
    """The names of the facts of the original preview that the facts derived on the new main do not reproduce."""
    return [name for name in GO_FACTS if original['preview'].get(name) != facts.get(name)]


def rebound_go(tool, body, original, facts, audit):
    """The authenticated go for the moved candidate: the lead developer's reply, re-bound to the facts derived on the new main.

    The reply is copied as given; only the preview it names changes. The original preview and every move stay in the
    body, so the go is single-use for the same reasons as before and the record shows where it came from."""
    consent = dict(body['go'], preview_id=facts['id'], preview_sha256=digest(canonical(facts)))
    moves = [*(body.get('rebound') or {}).get('moves', []), audit]
    return sign(tool, {'version': 1, 'go': consent, 'preview': facts, 'rebound': {'original': original, 'moves': moves}})


def spend_go(tool, old_id, new_id):
    """The old go is used up by the move and its attempts carry over to the new one, so the cap of three stays."""
    value = uses(tool)
    record = value.get(old_id, {'attempts': 0, 'committed': False})
    value[old_id] = dict(record, committed=True, rebound_to=new_id)
    value[new_id] = {'attempts': record['attempts'], 'committed': False}
    save_uses(tool, value)


def merge_approvals(existing, new):
    """The landing record's approvals: earlier entries stay, a new entry for the same commit never replaces one."""
    merged = [e for e in (existing or []) if isinstance(e, dict)]
    known = {e.get('result_sha') for e in merged}
    return merged + [e for e in new if e is not None and e['result_sha'] not in known]
