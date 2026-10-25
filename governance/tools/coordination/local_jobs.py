"""Local coordination records, not authorization or checkpoint evidence (Python 3.13+)."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from graphlib import CycleError, TopologicalSorter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

STATES = ('PENDING', 'WIP', 'AWAITING_RESPONSE', 'BLOCKED', 'COMPLETE', 'CANCELLED')
TERMINAL = ('COMPLETE', 'CANCELLED')
RELATIVE = Path('.local/coordination')
# The one list of work kinds: a record's optional `kind` must be a key here, and `kinds` prints it.
KINDS = {
    'fix': 'a defect or silent failure',
    'feature': 'new capability',
    'cleanup': 'tidying or restructuring that adds no behavior',
    'design': 'a design or investigation before anything is built',
    'docs': 'documentation or explanatory notes',
    'record': 'a historical record such as a devlog entry',
    'decision': 'mainly waiting on a lead developer decision',
}
DESCRIPTION_LIMIT = 160
IDENTITY_LIMIT = 120
RELEASED = 'RELEASED by '
CLAIM_SOURCE = 'claim-coordinator command in governance/tools/local_jobs.py'
DISPOSITION_CHECKS = ('commit', 'path', 'job', 'unchecked')
EXCERPT_LIMIT = 100
# What a Job can wait for the lead developer to give, and the vendors whose session id the page may turn into a link.
AWAITING = ('preview', 'approval')
LINKED_VENDORS = ('claude', 'codex')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
# The brief takes the COORDINATION block from this document, found by its first line; the tool keeps no copy of it.
COORDINATION_DOCUMENT = Path('governance/LOCAL-COORDINATION.md')
SCOPE_POINTER = ('The scope lives in your Job record, which you read above; this prompt holds no plan of its own. '
                 'Read every entry of the record: it holds the scope, the riders and any routed handoff.')
COORDINATION_TITLE = 'COORDINATION (governance/LOCAL-COORDINATION.md, Execution status)'
BRIEF_READING = ('AGENTS.md', 'governance/AGENT-ORIENTATION.md', 'governance/ROLE-WORKER.md',
                 'governance/CONSTRAINTS.md', 'governance/CHECKPOINT-WORKFLOW.md',
                 'governance/CHECKPOINT-TOOL.md', 'governance/WORKTREE-WORKFLOW.md',
                 'governance/LOCAL-COORDINATION.md', 'governance/ARTIFACT-CLASSIFICATION.md')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()


def roots(repo):
    """Find the primary checkout by common Git directory, never cwd, branch name or a fixed path."""
    root = Path(git(repo, 'rev-parse', '--show-toplevel')).resolve()
    common = Path(git(root, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    records = git(root, 'worktree', 'list', '--porcelain', '-z').split('\0\0')
    for record in records:
        fields = dict(field.split(' ', 1) if ' ' in field else (field, '')
                      for field in record.split('\0') if field)
        if 'worktree' in fields:
            candidate = Path(fields['worktree']).resolve()
            private = Path(git(candidate, 'rev-parse', '--path-format=absolute', '--git-dir')).resolve()
            if private == common:
                return root, candidate
    raise ValueError('No primary checkout found; bare repositories are unsupported')


def safe_directory(root, create=False):
    root = Path(root).resolve()
    current = root
    for part in RELATIVE.parts:
        current = current / part
        require(not current.is_symlink() and not current.is_junction(), 'Indirect local storage: ' + str(current))
        require(not current.exists() or current.is_dir(), 'Local storage is not a directory')
    if create:
        require(git(root, 'check-ignore', '--', (RELATIVE / 'probe.json').as_posix()),
                'Local coordination storage must be ignored')
        current.mkdir(parents=True, exist_ok=True)
    return current


@contextmanager
def locked(*directories):
    """Short writer lock, never auto-break a lock left by an interrupted writer."""
    held = []
    try:
        for directory in sorted(set(directories), key=str):
            path = directory / '.writer.lock'
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                raise ValueError('Writer active or interrupted; inspect lock: ' + str(path)) from None
            os.close(fd)
            held.append(path)
        yield
    finally:
        for path in reversed(held):
            path.unlink()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key: ' + key)
        result[key] = value
    return result


def timestamp(value):
    require(isinstance(value, str), 'Timestamp must be text')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(parsed.utcoffset() is not None, 'Timestamp requires a timezone')


def handle(value):
    require(isinstance(value, str) and re.fullmatch(r'[a-z][a-z0-9]*(?:_[a-z0-9]+)*', value)
            and len(value) <= 64 and value != 'coordinator', 'Invalid or reserved job handle')


def job_reference(value):
    """A plain handle, or `epic--job`: two valid handles joined by exactly two hyphens.

    A handle never contains a hyphen, so the split is exact. Only the checkpoint declaration uses this.
    """
    require(isinstance(value, str), 'Invalid or reserved job handle')
    parts = value.split('--')
    require(len(parts) <= 2, 'Invalid or reserved job handle')
    for part in parts:
        handle(part)


def awaiting(value):
    """The optional `awaiting_lead` field: what the Job waits for and, when known, the worker's session."""
    require(isinstance(value, dict) and 'for' in value and set(value) <= {'for', 'session'},
            'awaiting_lead needs for and may carry session')
    require(isinstance(value['for'], str) and value['for'] in AWAITING, 'Unknown awaiting_lead for')
    if 'session' in value:
        session = value['session']
        require(isinstance(session, dict) and set(session) == {'vendor', 'id'}
                and all(isinstance(v, str) for v in session.values()), 'Session needs vendor and id')
        require(re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', session['vendor']), 'Invalid session vendor')
        pattern = UUID if session['vendor'] in LINKED_VENDORS else r'[A-Za-z0-9][A-Za-z0-9._:-]{0,127}'
        require(re.fullmatch(pattern, session['id'], re.IGNORECASE if session['vendor'] in LINKED_VENDORS else 0),
                'Invalid session id for the vendor')


def disposition(value):
    """The optional `disposition` of a pending entry: where its material went, how it was checked and when.

    `check` says how a recorded reference was verified: `commit` (an ancestor of main), `path` (it exists on main),
    `job` (a record exists) or `unchecked` (nothing could verify it and the caller said so).
    """
    require(isinstance(value, dict) and isinstance(value.get('kind'), str), 'A disposition needs a kind')
    shapes = {'routed': {'kind', 'ref', 'at'}, 'recorded': {'kind', 'ref', 'check', 'at'},
              'discarded': {'kind', 'reason', 'at'}}
    require(value['kind'] in shapes and set(value) == shapes[value['kind']], 'Unknown or malformed disposition')
    timestamp(value['at'])
    if value['kind'] == 'routed':
        handle(value['ref'])
    elif value['kind'] == 'recorded':
        require(isinstance(value['ref'], str) and value['ref'] and value['ref'] == value['ref'].strip(),
                'A recorded disposition needs a trimmed reference')
        require(isinstance(value['check'], str) and value['check'] in DISPOSITION_CHECKS, 'Unknown recorded check')
    else:
        require(isinstance(value['reason'], str) and value['reason'].strip(), 'A discard needs a reason')


def validate(data):
    require(isinstance(data, dict) and type(data.get('version')) is int and data['version'] == 1,
            'Expected version 1 JSON object')
    common = {'version', 'worker', 'updated_at'}
    require(isinstance(data.get('worker'), str) and data['worker'].strip(), 'Worker identity required')
    timestamp(data.get('updated_at'))
    if 'queue' in data:
        require(common | {'queue', 'context'} <= set(data) <=
                common | {'queue', 'context', 'priorities', 'epics', 'dependencies'},
                'Unexpected coordinator fields')
        require(isinstance(data['queue'], list), 'Queue must be a list')
        for item in data['queue']:
            handle(item)
        require(len(set(data['queue'])) == len(data['queue']), 'Duplicate queue handle')
        priorities = data.get('priorities', {})
        require(isinstance(priorities, dict), 'Priorities must be an object')
        require(set(priorities) <= set(data['queue']), 'Priority handle is not queued')
        require(all(p is None or (type(p) is int and p >= 1) for p in priorities.values()),
                'Priority must be a positive integer or null')
        epics = data.get('epics', {})
        require(isinstance(epics, dict), 'Epics must be an object')
        members = []
        for name, epic in epics.items():
            handle(name)
            require(name in data['queue'], 'Epic must be queued')
            require(isinstance(epic, dict) and set(epic) == {'jobs'} and
                    isinstance(epic['jobs'], list) and epic['jobs'], 'Epic requires nonempty jobs')
            for job in epic['jobs']:
                handle(job)
                require(job not in epics and job not in data['queue'],
                        'Epic member cannot be an Epic or standalone queue entry')
                members.append(job)
        require(len(set(members)) == len(members), 'Duplicate Epic membership')
        dependencies = data.get('dependencies', {})
        require(isinstance(dependencies, dict), 'Dependencies must be an object')
        for job, prerequisites in dependencies.items():
            handle(job)
            require(job not in epics and isinstance(prerequisites, list),
                    'Dependencies belong to Jobs')
            for prerequisite in prerequisites:
                handle(prerequisite)
                require(prerequisite not in epics, 'Dependency must name a Job, not an Epic')
            require(len(set(prerequisites)) == len(prerequisites), 'Duplicate dependency')
        try:
            tuple(TopologicalSorter(dependencies).static_order())
        except CycleError:
            raise ValueError('Dependency cycle') from None
        entries = data['context']
    else:
        required = common | {'handle', 'status', 'activity', 'step', 'waiting', 'pending', 'commits', 'records'}
        require(required <= set(data) <= required | {'description', 'kind', 'created_at', 'awaiting_lead', 'disposed'},
                'Unexpected job fields')
        if 'awaiting_lead' in data:
            awaiting(data['awaiting_lead'])
            require(data['status'] not in TERMINAL, 'A terminal job cannot wait for the lead developer')
        if 'created_at' in data:
            timestamp(data['created_at'])
        handle(data['handle'])
        if 'description' in data:
            text = data['description']
            require(isinstance(text, str) and text and text == text.strip() and text.isprintable()
                    and len(text) <= DESCRIPTION_LIMIT,
                    'Description must be one trimmed line of at most %d characters' % DESCRIPTION_LIMIT)
        require('kind' not in data or (isinstance(data['kind'], str) and data['kind'] in KINDS), 'Unknown work kind')
        require(data['status'] in STATES, 'Unknown job status')
        require(data['activity'] in ('UNKNOWN', 'ACTIVE', 'PAUSED', 'STOPPED'), 'Unknown reported activity')
        require(isinstance(data['step'], str) and data['step'].strip(), 'Current step required')
        require(data['waiting'] is None or isinstance(data['waiting'], str), 'Waiting detail must be text or null')
        if data['status'] in ('AWAITING_RESPONSE', 'BLOCKED'):
            require(bool(data['waiting']), 'Waiting/blocker detail required')
        require(isinstance(data['commits'], list) and all(isinstance(c, str) and
                re.fullmatch(r'[0-9a-f]{40}', c) for c in data['commits']), 'Full commit IDs required')
        require(isinstance(data['records'], list) and all(isinstance(r, str) and r.strip()
                for r in data['records']), 'Record references must be text')
        entries = data['pending']
        if data['status'] in TERMINAL:
            require(entries == [], 'Transfer or record pending material before terminal status')
        if data['status'] == 'COMPLETE':
            require(bool(data['commits']), 'Completed job requires integration commit IDs')
    require(isinstance(entries, list), 'Context/pending material must be a list')
    for entry in entries:
        base = {'kind', 'text', 'source'}
        # Only a Job's pending entry may carry a disposition; coordinator context never does.
        require(isinstance(entry, dict) and (set(entry) == base or ('queue' not in data and set(entry) == base | {'disposition'}))
                and all(isinstance(entry[key], str) and entry[key].strip() for key in base),
                'Material requires kind, text and source; distinguish quotes, intent and inference')
        if 'disposition' in entry:
            disposition(entry['disposition'])
    if 'disposed' in data:
        require('queue' not in data and isinstance(data['disposed'], list), 'disposed is a list on a Job record')
        for item in data['disposed']:
            require(isinstance(item, dict) and set(item) == {'entry', 'kind', 'excerpt', 'disposition'}
                    and type(item['entry']) is int and item['entry'] >= 1
                    and all(isinstance(item[key], str) and item[key].strip() for key in ('kind', 'excerpt')),
                    'Disposed entries need entry, kind, excerpt and disposition')
            disposition(item['disposition'])
    return data


def snapshot(path):
    require(path.is_file() and not path.is_symlink() and not path.is_junction(), 'Missing or indirect record: ' + str(path))
    raw = path.read_bytes()
    data = validate(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique))
    expected = 'coordinator' if 'queue' in data else data['handle']
    require(path.stem == expected, 'Filename and record identity differ')
    return {'record': data, 'sha256': hashlib.sha256(raw).hexdigest()}


def read(path):
    return snapshot(path)['record']


def record_name(value):
    if value != 'coordinator':
        handle(value)


def pointer(directory, name):
    record_name(name)
    current = snapshot(directory / (name + '.json'))
    return 'primary:' + name + '@' + current['sha256']


def resolve_pointer(directory, value):
    match = re.fullmatch(r'primary:([a-z][a-z0-9_]*?)@([0-9a-f]{64})', value)
    require(match is not None, 'Expected primary:<record>@<SHA-256> pointer')
    name, expected = match.groups()
    record_name(name)
    current = snapshot(directory / (name + '.json'))
    return dict(current, pointer='primary:' + name + '@' + current['sha256'],
                signaled_sha256=expected, matches_signal=current['sha256'] == expected)


def write(path, data):
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(name, path)
    finally:
        if Path(name).exists():
            Path(name).unlink()


def put(directory, data, expected):
    validate(data)
    name = 'coordinator' if 'queue' in data else data['handle']
    path = directory / (name + '.json')
    with locked(directory):
        require(not path.is_symlink() and not path.is_junction(), 'Indirect record')
        if 'queue' in data:
            for epic in data.get('epics', {}):
                target = directory / (epic + '.json')
                require(not target.exists() and not target.is_symlink() and not target.is_junction(),
                        'Epic handle conflicts with Job record')
        else:
            coordinator = directory / 'coordinator.json'
            if coordinator.exists():
                require(name not in read(coordinator).get('epics', {}),
                        'Job handle conflicts with Epic')
        if path.exists():
            read(path)
            require(expected == hashlib.sha256(path.read_bytes()).hexdigest(), 'Record changed; read again before updating')
        else:
            require(expected is None, 'Expected record is missing')
        write(path, data)
    return str(path)


def transfer(source, destination):
    """Move job files only; coordinator context is owned by the primary checkout."""
    require(source != destination, 'Already in primary checkout')
    with locked(source, destination):
        paths = sorted(source.glob('*.json'))
        coordinator_path = destination / 'coordinator.json'
        epics = read(coordinator_path).get('epics', {}) if coordinator_path.exists() else {}
        for path in paths:
            require(path.name != 'coordinator.json', 'Coordinator belongs in primary checkout')
            read(path)
            require(path.stem not in epics, 'Transferred Job handle conflicts with Epic')
            target = destination / path.name
            require(not target.exists() and not target.is_symlink() and not target.is_junction(),
                    'Destination conflict; reconcile explicitly: ' + path.name)
        moved = []
        for path in paths:
            path.rename(destination / path.name)
            moved.append(path.name)
        return moved


def integrated(data, primary):
    """Mechanical completion only; semantic recording and authority remain assertions."""
    if data is None or data['status'] != 'COMPLETE':
        return False
    try:
        for commit in data['commits']:
            git(primary, 'merge-base', '--is-ancestor', commit, 'refs/heads/main')
    except subprocess.CalledProcessError:
        return False
    return True


def queued(directory):
    """Group all queued Jobs; derive dependency readiness without changing Job status."""
    coordinator = read(directory / 'coordinator.json')
    require('queue' in coordinator, 'Coordinator record required')
    priorities = coordinator.get('priorities', {})
    epics = coordinator.get('epics', {})
    dependencies = coordinator.get('dependencies', {})
    records = {}
    for path in sorted(directory.glob('*.json')):
        if path.name != 'coordinator.json':
            data = read(path)
            require(data['handle'] not in epics, 'Epic handle conflicts with Job record')
            records[data['handle']] = data
    satisfied = {name for name, data in records.items() if integrated(data, directory.parents[1])}
    members = {job for epic in epics.values() for job in epic['jobs']}
    scheduled = (set(coordinator['queue']) - set(epics)) | members
    graph = TopologicalSorter({name: dependencies.get(name, []) for name in
                               scheduled | set(dependencies)})
    graph.prepare()
    order = []
    while graph.is_active():
        layer = sorted(graph.get_ready())
        order.extend(layer)
        graph.done(*layer)
    rank = {name: index for index, name in enumerate(order)}

    def row(name):
        data = records.get(name)
        path = directory / (name + '.json')
        unmet = [dep for dep in dependencies.get(name, []) if dep not in satisfied]
        return {'handle': name, 'status': data['status'] if data else None,
                'activity': data['activity'] if data else None,
                'worker': data['worker'] if data else None,
                'step': data['step'] if data else None,
                'description': data.get('description') if data else None,
                'created_at': data.get('created_at') if data else None,
                'work_kind': data.get('kind') if data else None,
                'waiting': data['waiting'] if data else None,
                'awaiting_lead': data.get('awaiting_lead') if data else None,
                'pending_count': len(data['pending']) if data else None,
                'record': str(path) if data else None,
                'dependencies': dependencies.get(name, []),
                'unsatisfied_dependencies': unmet,
                'dependency_ready': not unmet,
                'missing_dependencies': [dep for dep in unmet if dep not in records]}

    def standing(job):
        """(not in flight, blocked by prerequisites): False sorts first. In flight is WIP or waiting for the lead developer."""
        return (not (job['status'] == 'WIP' or bool(job['awaiting_lead'])), not job['dependency_ready'])

    # The order of execution: in flight first, then priority (unset last), ready before blocked, then handle. An Epic
    # takes the standing of its best member, so its band sits where its first ready member would. The Job rows and
    # the Epic members keep their dependency order; none of this adds a dependency or grants any authority.
    entries = []
    for name in coordinator['queue']:
        if name in epics:
            jobs = [row(job) for job in sorted(epics[name]['jobs'], key=lambda h: rank[h])]
            entry = {'handle': name, 'kind': 'epic', 'priority': priorities.get(name), 'jobs': jobs}
            entries.append((min(standing(job) for job in jobs), entry))
        else:
            entry = dict(row(name), kind='job', priority=priorities.get(name))
            entries.append((standing(entry), entry))
    result = [entry for (flight, blocked), entry in sorted(
        entries, key=lambda item: (item[0][0], item[1]['priority'] is None, item[1]['priority'] or 0,
                                   item[0][1], item[1]['handle']))]
    unqueued = [row(name) for name in sorted(records.keys() - scheduled)
                if records[name]['status'] not in TERMINAL]
    return {'queue': result, 'unqueued': unqueued}


def renamed(queue, old, new):
    """Coordinator record with `old` replaced by `new` everywhere it is named, keeping every position."""
    def swap(name):
        return new if name == old else name
    queue = json.loads(json.dumps(queue))
    queue['queue'] = [swap(name) for name in queue['queue']]
    if 'priorities' in queue:
        queue['priorities'] = {swap(name): value for name, value in queue['priorities'].items()}
    for epic in queue.get('epics', {}).values():
        epic['jobs'] = [swap(name) for name in epic['jobs']]
    if 'dependencies' in queue:
        queue['dependencies'] = {swap(name): [swap(dep) for dep in deps]
                                 for name, deps in queue['dependencies'].items()}
    return queue


def mentioned(queue, name):
    return (name in queue['queue'] or name in queue.get('priorities', {}) or name in queue.get('dependencies', {})
            or any(name in epic['jobs'] for epic in queue.get('epics', {}).values())
            or any(name in deps for deps in queue.get('dependencies', {}).values()))


def rename(directory, old, new):
    """Rename a live Job in one guarded step: its record and file, queue entry, priority, Epic and dependencies.

    Everything is checked and validated before the first write. Prose mentions of the old handle in other
    records and in committed text are left as written. An interrupted rename can leave both record files; the
    coordinator then still names the old handle, so remove the new file after inspection and run it again.
    """
    handle(old)
    handle(new)
    require(old != new, 'New handle equals the old handle')
    old_path, new_path = directory / (old + '.json'), directory / (new + '.json')
    coordinator_path = directory / 'coordinator.json'
    with locked(directory):
        data = read(old_path)
        require(data['status'] not in TERMINAL, 'Terminal notes are pruned, not renamed')
        require(not new_path.exists() and not new_path.is_symlink() and not new_path.is_junction(),
                'New handle already has a record')
        queue = read(coordinator_path) if coordinator_path.exists() else None
        now = datetime.now(timezone.utc).isoformat()
        if queue:
            require(old not in queue.get('epics', {}), 'Epic handles cannot be renamed')
            require(not mentioned(queue, new), 'New handle is already in the queue, an Epic or a dependency')
            queue = renamed(queue, old, new)
            queue['updated_at'] = now
            validate(queue)
        data['handle'] = new
        data['updated_at'] = now
        data['pending'].append({'kind': 'handoff', 'source': 'rename command in governance/tools/local_jobs.py',
                                'text': 'Renamed from %s to %s on %s. Prose mentions of %s in other records and '
                                        'in committed text are unchanged.' % (old, new, now[:10], old)})
        validate(data)
        write(new_path, data)
        try:
            if queue:
                write(coordinator_path, queue)
        except BaseException:
            new_path.unlink()
            raise
        old_path.unlink()
        return {'renamed': {'from': old, 'to': new}, 'record': str(new_path), 'coordinator_updated': bool(queue)}


def prune(directory, primary):
    """Delete terminal job notes only; the worker attests durable-record completeness."""
    with locked(directory):
        records = [(p, read(p)) for p in sorted(directory.glob('*.json')) if p.name != 'coordinator.json']
        completed = [(p, d) for p, d in records if d['status'] in TERMINAL]
        for _, data in completed:
            for commit in data['commits']:
                git(primary, 'merge-base', '--is-ancestor', commit, 'refs/heads/main')
        queue_path = directory / 'coordinator.json'
        queue = read(queue_path) if queue_path.exists() else None
        handles = {d['handle'] for _, d in completed}
        if directory != safe_directory(primary):
            shared_path = safe_directory(primary) / 'coordinator.json'
            shared = read(shared_path) if shared_path.exists() else {}
            referenced = set(shared.get('queue', []))
            referenced.update(job for epic in shared.get('epics', {}).values() for job in epic['jobs'])
            referenced.update(shared.get('dependencies', {}))
            referenced.update(dep for deps in shared.get('dependencies', {}).values() for dep in deps)
            require(not handles & referenced, 'Transfer scheduled terminal notes to primary before pruning')
        if queue:
            dependencies = queue.get('dependencies', {})
            cancelled = {d['handle'] for _, d in completed if d['status'] == 'CANCELLED'}
            require(not any(cancelled & set(deps) for job, deps in dependencies.items() if job not in handles),
                    'Cancelled prerequisite is not satisfied; resolve dependencies before pruning')
            satisfied = {d['handle'] for _, d in completed if d['status'] == 'COMPLETE'}
            if 'dependencies' in queue:
                queue['dependencies'] = {job: [dep for dep in deps if dep not in satisfied]
                                         for job, deps in dependencies.items() if job not in handles}
            removed_epics = set()
            for name, epic in list(queue.get('epics', {}).items()):
                epic['jobs'] = [job for job in epic['jobs'] if job not in handles]
                if not epic['jobs']:
                    removed_epics.add(name)
                    del queue['epics'][name]
            removed = handles | removed_epics
            queue['queue'] = [h for h in queue['queue'] if h not in removed]
            if 'priorities' in queue:
                queue['priorities'] = {h: p for h, p in queue['priorities'].items() if h not in removed}
            queue['updated_at'] = datetime.now(timezone.utc).isoformat()
            validate(queue)
            write(queue_path, queue)
        for path, _ in completed:
            path.unlink()
        return sorted(handles)


LOCK_TRIES = 6
LOCK_WAIT = 0.25
MISSION_ID = r'[a-z0-9][a-z0-9-]{0,63}'
STEPS = ('every commit is on main', 'main working tree is clean', 'transaction worktree and branch are gone',
         'execution status is not frozen by this Job', 'record commits match')


def text_file(path):
    """UTF-8 text from a file, with or without a byte-order mark and with LF line ends."""
    return Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').strip()


def guarded(directory, change):
    """Run `change` under the writer lock, waiting briefly while another writer holds it, never breaking the lock.

    The record is read inside the lock, so the digest it is written over cannot go stale: that is the expected-hash
    retry the one-off close scripts spelled out by hand.
    """
    for attempt in range(LOCK_TRIES):
        try:
            with locked(directory):
                return change()
        except ValueError as error:
            if 'Writer active' not in str(error) or attempt == LOCK_TRIES - 1:
                raise
            time.sleep(LOCK_WAIT)


def stamped(data):
    data['updated_at'] = datetime.now(timezone.utc).isoformat()
    return data


def note(directory, name, kind, source, text):
    """Append one pending entry to a live Job record, atomically."""
    handle(name)
    require(isinstance(kind, str) and re.fullmatch(r'[a-z][a-z0-9_]*', kind), 'Entry kind must be one lowercase word')
    path = directory / (name + '.json')

    def change():
        data = read(path)
        require(data['status'] not in TERMINAL, 'A terminal job takes no new pending material')
        data['pending'].append({'kind': kind, 'text': text, 'source': source})
        write(path, validate(stamped(data)))
        return {'noted': name, 'kind': kind, 'pending_count': len(data['pending'])}
    return guarded(directory, change)


def coordination_block(primary):
    """The COORDINATION block as the document holds it now: the fenced block whose first line is the title."""
    path = Path(primary) / COORDINATION_DOCUMENT
    require(path.is_file() and not path.is_symlink() and not path.is_junction(), 'Missing coordination document: ' + str(path))
    lines = path.read_bytes().decode('utf-8-sig').replace('\r\n', '\n').split('\n')
    starts = [i for i in range(len(lines) - 1)
              if re.fullmatch(r'```\w*', lines[i]) and lines[i + 1] == COORDINATION_TITLE]
    require(len(starts) == 1, 'COORDINATION block not found exactly once in %s (%d found); no brief printed'
            % (COORDINATION_DOCUMENT.as_posix(), len(starts)))
    block = []
    for line in lines[starts[0] + 1:]:
        if line == '```':
            return '\n'.join(block)
        block.append(line)
    raise ValueError('COORDINATION block is not closed by a fence in %s; no brief printed' % COORDINATION_DOCUMENT.as_posix())


def extra_text(path):
    """The Job-specific scope from a UTF-8 file, with LF line ends and no blank lines around it."""
    source = Path(path)
    require(source.is_file(), 'Extra file not found: ' + str(path))
    text = source.read_bytes().decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').strip('\n')
    require(text.strip(), 'Extra file is empty: ' + str(path))
    return text


def brief(primary, name, stagger=False, extra_file=None):
    """The standard worker brief as plain text with LF line ends. It reads only: no record is written, nobody is contacted."""
    handle(name)
    where = Path(primary).as_posix()
    require("'" not in where and '\n' not in where, 'Primary checkout path cannot be quoted: ' + where)
    block = coordination_block(primary)
    for placeholder in ('<primary-checkout>', '<your handle>'):
        require(placeholder in block, 'COORDINATION block lacks %s; no brief printed' % placeholder)
    block = block.replace('<primary-checkout>', where).replace('<primary>', where).replace('<your handle>', name)
    scope = SCOPE_POINTER
    if extra_file:
        scope += '\n\nNOTE FROM THE COORDINATOR (where it differs from the record, the record wins):\n' + extra_text(extra_file)
    launcher = "python -B governance/tools/local_jobs.py --repo '%s' --primary " % where
    lines = ['BRIEF FOR JOB ' + name,
             'Printed by local_jobs.py brief from the live governance/LOCAL-COORDINATION.md in the primary checkout.',
             '', 'READ FIRST']
    if not (safe_directory(primary) / (name + '.json')).is_file():
        lines += ['- NOTE: no record exists for %s yet (a brief may precede its record); the record commands below '
                  'fail until the Coordinator creates it.' % name]
    lines += ['- Your Job record (scope, riders and any routed handoff; read EVERY entry). Print its pointer, then read '
              'what the pointer names:',
              '  ' + launcher + 'pointer ' + name,
              '  ' + launcher + "read '<the pointer line printed above>'",
              '- Read these in full before any worktree, checkpoint, staging, approval, finish or integration step, '
              'and the specification, contract or conventions that own the artifact you change:']
    lines += ['  ' + item for item in BRIEF_READING]
    lines += ['  In LOCAL-COORDINATION.md read "Execution status" (with the COORDINATION block) and "Updates, handoff and disposal".',
              '- `python` in a command line stands for any Python 3.13 or later. If a bare `python` fails with '
              "\"SRE module mismatch\" it is LibreOffice's Python: run the tool with the path of a Python 3.13 or later instead.",
              "- The lead developer's terminal tab is PowerShell: every command line you give them says PowerShell or Git Bash.",
              '', block, '',
              'IF MAIN MOVES',
              '- If your base moves before your prepare and you have only a preview, expect remove + create + a new '
              'preview + a fresh go from the lead developer, NOT reconcile (reconcile needs a sealed packet). A second '
              '`preview --output` to the same path does not overwrite the old file: use a new name. Report it to the '
              'Coordinator and the lead developer before doing anything.',
              '', 'RULES THAT STAY',
              '- Approval is typed or selected by the lead developer in YOUR OWN session on the presented seal; nobody relays '
              'it, not even the Coordinator. Never push; the lead developer pushes. Do not touch any other branch or worktree.',
              '- Report follow-up to the Coordinator; do not create successor workers or Jobs. Keep your Job record '
              'current by its handle (the `note` command, or `put` with --expected-sha256). Report anything beyond '
              'scope instead of adding it.',
              '- While the diff viewer extension is installed (its folder under governance/extensions exists), every message in which you present a preview or a sealed '
              'review ENDS with exactly one line, placed last, immediately before the go or Approve question: '
              '`Diff page: <file:// link>`, or `Diff page: not available (<reason>)` when rendering failed; never '
              'omitted, never elsewhere in the message.',
              '- Do NOT close or complete your own Job: the Coordinator closes it with `dispose` and `complete` after '
              'reading every pending entry.',
              "- Stale lock files, killing a process, or touching branches and worktrees other than your own: only with "
              "the lead developer's word.",
              '- At the end: integrate, remove your owned worktree and branch and any superseded ones, report, and leave '
              'Job closure to the Coordinator. A mission of yours that a landed mission of the same Job replaced is removed '
              'with `worktree.py remove --superseded-by <landed full commit>`, without asking the lead developer.', '']
    if stagger:
        lines += ['STAGGERED LANDING',
                  '- Another landing runs ahead of yours. Build and preview in parallel with it, and start your PREPARE '
                  'only after the Coordinator says the other landing is done. One landing at a time.', '']
    lines += ['SCOPE', scope, '',
              'This brief reads only: it writes no record, contacts no worker and gives no approval or authority.']
    return '\n'.join(lines) + '\n'


def run_git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True)


def full_commit(primary, value):
    require(isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{7,40}', value), 'Commit must be a hex id: ' + str(value))
    found = run_git(primary, 'rev-parse', '--verify', '-q', value.lower() + '^{commit}')
    require(found.returncode == 0, 'Unknown commit: ' + value)
    return found.stdout.decode().strip()


def listed_worktrees(primary):
    found = []
    for record in git(primary, 'worktree', 'list', '--porcelain', '-z').split('\0\0'):
        fields = dict(f.split(' ', 1) if ' ' in f else (f, '') for f in record.split('\0') if f)
        if 'worktree' in fields and 'bare' not in fields:
            found.append(fields)
    return found


def execution_snapshot(primary):
    """(level, windows, landings): the execution status and the recorded landings, read the way worktree.py reads them.

    Imported late: worktree.py imports this module. Both only read here, and neither creates a folder or a database.
    """
    from governance.tools.worktrees import claims
    from governance.tools.worktrees import guard as worktree
    status = worktree.execution_status(primary)
    common = Path(git(primary, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    declared = claims.read_state(common)
    return status['level'], status['windows'] + status['claims'], declared.get('landed', [])


def same_job(value, name):
    return isinstance(value, str) and value.split('--')[-1] == name


def claim_handles(primary):
    """{mission id: Job handles of its claims and windows in the claims database, open or closed; None stands for a
    claim without a handle}. A database that does not exist yet names no mission; one that cannot be read refuses,
    because then nobody can tell whose a branch is."""
    from governance.tools.worktrees import claims
    common = Path(git(primary, 'rev-parse', '--path-format=absolute', '--git-common-dir')).resolve()
    try:
        con = claims.open_database(common, False)
    except claims.ClaimsError as error:
        if error.state == 'missing':
            return {}
        raise ValueError('The claims database cannot be read (%s: %s); no one can tell whose branches these are' % (
            error.state, error.message))
    try:
        found = {}
        for row in con.execute('SELECT owner, job_handle FROM claims WHERE owner IS NOT NULL').fetchall():
            found.setdefault(row['owner'], set()).add(row['job_handle'])
        return found
    except Exception as error:
        failure = claims.classify(error)
        raise ValueError('The claims database cannot be read (%s: %s); no one can tell whose branches these are' % (
            failure.state, failure.message))
    finally:
        con.close()


def transaction_owners(primary, name, commits, named, status, claimed=None):
    """Mission ids that belong to this Job: named by the caller, by its claims (the claims database, open or closed)
    and windows, or by its landing event. A mission whose claims name this Job and no other counts; one whose claims
    name another Job, name two Jobs or name none is never this Job's by claims. Only when none of those names a
    mission is a `tx/` branch that points at one of its commits taken for it, and never a branch whose claims,
    windows or landing event name another Job or leave its owner unclear."""
    level, windows, landings = status
    claimed = claim_handles(primary) if claimed is None else claimed
    owners = set(named)
    owners.update(w['owner'] for w in windows if w.get('owner') and same_job(w.get('job_handle'), name))
    owners.update(owner for owner, handles in claimed.items() if handles and all(same_job(h, name) for h in handles))
    for event in landings:
        if event.get('owner') and same_job(event['detail'].get('job_handle'), name):
            owners.add(event['owner'])
    if owners:
        return sorted(owners)
    others = {w['owner'] for w in windows if w.get('owner') and w.get('job_handle') and not same_job(w['job_handle'], name)}
    others.update(e['owner'] for e in landings
                  if e.get('owner') and e['detail'].get('job_handle') and not same_job(e['detail']['job_handle'], name))
    others.update(owner for owner, handles in claimed.items() if not all(same_job(h, name) for h in handles))
    heads = run_git(primary, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads/tx/').stdout.decode()
    for line in heads.splitlines():
        ref, tip = line.split(' ')
        mission = ref.removeprefix('refs/heads/tx/')
        if tip in commits and mission not in others:
            owners.add(mission)
    return sorted(owners)


def leftover_folders(primary, owners):
    """Workspace and review folders of those missions that are still on disk, by the one container layout."""
    from governance.tools.continuity.artifact_classification import CONTAINER, REVIEW
    container = Path(primary).resolve().parent / CONTAINER / Path(primary).resolve().name
    return [folder for owner in owners
            for folder in (container / owner, container / REVIEW / owner) if folder.exists()]


def folder_key(value):
    return os.path.normcase(str(Path(value).resolve()))


def landing_check(primary, name, record, commits, leftovers, owners, status=None):
    """The one fixed landing check. Returns (steps passed, full commit ids, owners, leftover folders); raises on the
    first failure naming the step, and writes nothing."""
    passed = []

    def fail(step, detail):
        raise ValueError('Landing check failed at step %d of %d (%s): %s%s' % (
            step, len(STEPS), STEPS[step - 1], detail, ' [passed: %s]' % ', '.join(passed) if passed else ''))
    require(commits, 'Name at least one --commit')
    full = []
    for value in commits:
        commit = full_commit(primary, value)
        if commit not in full:
            full.append(commit)
    for commit in full:
        if run_git(primary, 'merge-base', '--is-ancestor', commit, 'refs/heads/main').returncode != 0:
            fail(1, 'commit %s is not an ancestor of main' % commit)
    passed.append('1')
    dirty = run_git(primary, 'status', '--porcelain').stdout.decode().strip()
    if dirty:
        fail(2, 'main working tree has changes: ' + ', '.join(line.strip() for line in dirty.splitlines()[:5]))
    passed.append('2')
    for owner in owners:
        require(re.fullmatch(MISSION_ID, owner), 'Invalid --owner mission id: ' + owner)
    status = status or execution_snapshot(primary)
    found = transaction_owners(primary, name, full, owners, status)
    for owner in found:
        if run_git(primary, 'rev-parse', '--verify', '-q', 'refs/heads/tx/' + owner).returncode == 0:
            fail(3, 'branch tx/%s still exists' % owner)
        for row in listed_worktrees(primary):
            if row.get('branch') == 'refs/heads/tx/' + owner or row.get('locked') == 'transaction:' + owner:
                fail(3, 'worktree %s of mission %s is still registered' % (row['worktree'], owner))
    named = {folder_key(folder) for folder in leftovers}
    unnamed = [str(folder) for folder in leftover_folders(primary, found) if folder_key(folder) not in named]
    if unnamed:
        fail(3, 'folder(s) left behind: %s; name them with --leftovers to record them in the Coordinator record' % ', '.join(unnamed))
    passed.append('3')
    level, windows, landings = status
    if level == 'UNKNOWN':
        fail(4, 'the execution status could not be read')
    held = [w for w in windows if w.get('scope') and (w.get('owner') in found or same_job(w.get('job_handle'), name))]
    if held:
        fail(4, 'frozen by %s since %s' % (held[0].get('owner') or name, held[0].get('since')))
    passed.append('4')
    if record['commits'] and set(record['commits']) != set(full):
        fail(5, 'the record names %s, the order names %s' % (', '.join(record['commits']), ', '.join(full)))
    passed.append('5')
    return passed, full, found


def now():
    return datetime.now(timezone.utc).isoformat()


class Batch:
    """Job records read and changed together under the writer lock, then written together.

    Every record is validated before the first write, and a failed write puts the earlier files back, so a change
    that touches several Jobs leaves no partial state. `last` is written after the others: the Job being changed.
    """

    def __init__(self, directory):
        self.directory, self.originals, self.records = directory, {}, {}

    def load(self, name):
        handle(name)
        path = self.directory / (name + '.json')
        require(path.is_file(), 'No record for Job: ' + name)
        return self.loaded(name, path)

    def coordinator(self):
        """The Coordinator record, which owns follow-up that belongs to no Job; it names no project's Job."""
        path = self.directory / 'coordinator.json'
        require(path.is_file(), 'No Coordinator record in %s; create it with put before recording leftovers' % self.directory)
        return self.loaded('coordinator', path)

    def loaded(self, name, path):
        if name not in self.records:
            self.originals[path] = path.read_bytes()
            self.records[name] = read(path)
        return self.records[name]

    def live(self, name):
        data = self.load(name)
        require(data['status'] not in TERMINAL, 'Job is terminal: ' + name)
        return data

    def commit(self, last):
        for data in self.records.values():
            validate(stamped(data))
        written = []
        try:
            for name in [n for n in self.records if n != last] + [last]:
                path = self.directory / (name + '.json')
                write(path, self.records[name])
                written.append(path)
        except BaseException:
            for done in written:
                restore = done.with_name('.restore-' + done.name)
                restore.write_bytes(self.originals[done])
                os.replace(restore, done)
            raise


def route_entry(batch, name, position, other):
    """Copy pending entry `position` of Job `name` to the live Job `other` and mark the original as routed."""
    require(other != name, 'Cannot route to the same Job')
    destination = batch.live(other)
    data = batch.records[name]
    entry = data['pending'][position - 1]
    copy = {key: value for key, value in entry.items() if key != 'disposition'}
    copy['source'] += '; routed from ' + name
    destination['pending'].append(copy)
    if other not in data['records']:
        data['records'].append(other)
    entry['disposition'] = {'kind': 'routed', 'ref': other, 'at': now()}


def check_reference(directory, primary, value, unchecked=False):
    """(check, reference) for where pending material now lives, verified as far as the tool can.

    A commit must be an ancestor of main, a path must exist on main (an anchor after `#` is not checked), a handle
    must have a record. A reference none of these can verify is refused unless the caller says `unchecked`, and is
    then stored as unchecked. A commit that exists but is not on main is refused whatever the flag says.
    """
    value = value.strip()
    require(value, 'A recorded reference cannot be empty')
    if re.fullmatch(r'[0-9a-fA-F]{7,40}', value):
        found = run_git(primary, 'rev-parse', '--verify', '-q', value.lower() + '^{commit}')
        if found.returncode == 0:
            commit = found.stdout.decode().strip()
            require(run_git(primary, 'merge-base', '--is-ancestor', commit, 'refs/heads/main').returncode == 0,
                    'Commit %s is not an ancestor of main' % commit)
            return 'commit', commit
    try:
        record_name(value)
        record = directory / (value + '.json')
        if record.is_file() and not record.is_symlink() and not record.is_junction():
            return 'job', value
    except ValueError:
        pass
    target = value.split('#', 1)[0].replace('\\', '/').strip('/')
    if target and ':' not in target and '..' not in target.split('/') \
            and run_git(primary, 'cat-file', '-e', 'refs/heads/main:' + target).returncode == 0:
        return 'path', value
    require(unchecked, 'Cannot verify %r as a commit on main, a path on main or a Job or record handle; '
            'pass --unchecked to store it as unchecked' % value)
    return 'unchecked', value


def excerpt(text):
    line = ' '.join(text.split())
    return line if len(line) <= EXCERPT_LIMIT else line[:EXCERPT_LIMIT - 3] + '...'


def reason_text(value):
    """A discard reason typed on the command line, or the path of a UTF-8 file that holds it (as `note` reads its source)."""
    try:
        if Path(value).is_file():
            return text_file(value)
    except (OSError, ValueError):
        pass
    return value.strip()


def dispose(directory, primary, name, position, route=None, recorded_in=None, discard=None, unchecked=False):
    """Give pending entry `position` (from 1) of a live Job its disposition: routed, recorded in or discarded.

    Exactly one of `route`, `recorded_in` and `discard` is given. The entry stays in `pending` with its disposition,
    so numbers stay stable and every count stays as it was; `complete` clears the list into `disposed`.
    """
    handle(name)
    require(sum(value is not None for value in (route, recorded_in, discard)) == 1,
            'Name exactly one of --route, --recorded-in and --discard')
    require(not unchecked or recorded_in is not None, '--unchecked belongs to --recorded-in')

    def change():
        batch = Batch(directory)
        data = batch.live(name)
        require(1 <= position <= len(data['pending']), 'No such pending entry: %d' % position)
        entry = data['pending'][position - 1]
        require('disposition' not in entry,
                'Entry %d already has a disposition (%s)' % (position, entry.get('disposition', {}).get('kind')))
        if route is not None:
            route_entry(batch, name, position, route)
        elif recorded_in is not None:
            check, ref = check_reference(directory, primary, recorded_in, unchecked)
            entry['disposition'] = {'kind': 'recorded', 'ref': ref, 'check': check, 'at': now()}
        else:
            reason = reason_text(discard)
            require(reason, 'A discard needs a reason')
            entry['disposition'] = {'kind': 'discarded', 'reason': reason, 'at': now()}
        batch.commit(name)
        return {'disposed': name, 'entry': position, 'disposition': entry['disposition'],
                'undisposed': [index for index, other in enumerate(data['pending'], 1) if 'disposition' not in other]}
    return guarded(directory, change)


def complete(directory, primary, name, commits, prune_after=False, leftovers=(), routes=None, owners=()):
    """Verify the landing, then close a Job: COMPLETE with full commit ids, no pending, activity STOPPED.

    Every pending entry must have a disposition (see `dispose`); `routes` ({1-based position: handle}) gives the
    entries that have none the routed disposition in the same step. There is no blanket attestation: an entry
    without a disposition refuses the close and nothing is written. The dispositions stay on the closed record as
    `disposed` until it is pruned. Every file is validated before the first write and a failed write puts the
    earlier ones back, so no partial state.
    """
    routes = routes or {}
    handle(name)
    path = directory / (name + '.json')
    before = read(path)
    require(before['status'] not in TERMINAL, 'The job is already terminal')
    passed, full, found = landing_check(primary, name, before, commits, leftovers, owners)
    result = {'completed': name, 'commits': full, 'landing_check': dict(zip(STEPS, ['passed'] * len(passed))),
              'transaction_owners': found, 'routed': [], 'disposed': [], 'unchecked': [], 'leftovers': [], 'pruned': []}

    def change():
        batch = Batch(directory)
        data = batch.load(name)
        require(data == before, 'Record changed during the landing check; read again')
        require(all(1 <= position <= len(data['pending']) for position in routes), 'No such pending entry to route')
        for position, other in sorted(routes.items()):
            require('disposition' not in data['pending'][position - 1],
                    'Entry %d already has a disposition; --route is for entries without one' % position)
            route_entry(batch, name, position, other)
            result['routed'].append({'entry': position, 'to': other})
        undisposed = [(index, entry['kind']) for index, entry in enumerate(data['pending'], 1) if 'disposition' not in entry]
        require(not undisposed, 'Pending entries without a disposition (%s); give each one with the dispose command '
                '(--route, --recorded-in or --discard), or route it here with --route <n>=<handle>' % ', '.join(
                    '%d %s' % item for item in undisposed))
        if leftovers:
            batch.coordinator()['context'].append({
                'kind': 'finding', 'source': 'complete command in governance/tools/local_jobs.py, Job ' + name,
                'text': 'Folder(s) left behind after %s landed as %s: %s. The landing check named them; inspect and '
                        'dispose of them.' % (name, ', '.join(c[:12] for c in full), ', '.join(map(str, leftovers)))})
            result['leftovers'] = [str(folder) for folder in leftovers]
        trail = [{'entry': index, 'kind': entry['kind'], 'excerpt': excerpt(entry['text']),
                  'disposition': entry['disposition']} for index, entry in enumerate(data['pending'], 1)]
        result['disposed'] = [{'entry': item['entry'], 'kind': item['kind'], 'disposition': item['disposition']['kind'],
                               'check': item['disposition'].get('check')} for item in trail]
        result['unchecked'] = [item['entry'] for item in trail if item['disposition'].get('check') == 'unchecked']
        data.update(status='COMPLETE', activity='STOPPED', waiting=None, commits=full, pending=[], disposed=trail,
                    step='Complete: landed as ' + ', '.join(c[:12] for c in full))
        data.pop('awaiting_lead', None)
        batch.commit(name)
    guarded(directory, change)
    if prune_after:
        result['pruned'] = prune(directory, primary)
    return result


def claimant(identity):
    """The text a new Coordinator gives for itself: one trimmed printable line, and not the release marker."""
    require(isinstance(identity, str) and identity and identity == identity.strip() and identity.isprintable()
            and len(identity) <= IDENTITY_LIMIT,
            'The identity must be one trimmed printable line of at most %d characters' % IDENTITY_LIMIT)
    require(not identity.startswith(RELEASED), 'The identity cannot start with the release marker %r' % RELEASED)
    return identity


def claim_coordinator(directory, identity, take_over=False, reason=None):
    """Claim the Coordinator role: create the Coordinator record, or take it over with the lead developer's word.

    The marker is the record's own `worker` (the holder) and an attributed `context` entry (the time of the claim), as the
    Coordinator role file describes for a release and a takeover; no new field exists. With no record, the record is made
    with an empty queue. With a record, the command changes nothing and names the holder unless `take_over` is given with
    a reason (the lead developer's designation); the previous holder is then recorded as superseded, with the time and
    the reason. The identity, the reason and the designation are the caller's assertions: nothing authenticates them.
    """
    claimant(identity)
    require(not take_over or (isinstance(reason, str) and reason.strip()),
            "--take-over needs --reason: the lead developer's designation, in a line")
    require(take_over or reason is None, '--reason belongs to --take-over')
    path = directory / 'coordinator.json'

    def change():
        require(not path.is_symlink() and not path.is_junction(), 'Indirect record')
        stamp = now()
        if not path.exists():
            require(not take_over, 'There is no Coordinator record to take over; claim without --take-over')
            text = 'Claimed by %s at %s. The queue was empty.' % (identity, stamp)
            data = {'version': 1, 'worker': identity, 'updated_at': stamp, 'queue': [],
                    'context': [{'kind': 'handoff', 'source': CLAIM_SOURCE, 'text': text}]}
            write(path, validate(data))
            return {'claimed': identity, 'since': stamp, 'previous': None, 'record': str(path)}
        data = read(path)
        previous = data['worker']
        claims = [entry['text'] for entry in data['context'] if entry['source'].startswith(CLAIM_SOURCE)]
        require(take_over, 'The Coordinator role is held by %r (record last updated %s; %s). Nothing was changed. Take it '
                "over only with the lead developer's word, after confirming the holder stopped: run claim-coordinator "
                "again with --take-over and --reason." % (previous, data['updated_at'],
                                                          ('latest claim: ' + claims[-1]) if claims else 'no claim entry'))
        text = ('Takeover at %s: %s takes over from %s, which is superseded. Reason, as given by the caller: %s'
                % (stamp, identity, previous, reason.strip()))
        data['context'].append({'kind': 'handoff', 'source': CLAIM_SOURCE + ' --take-over', 'text': text})
        data['worker'] = identity
        data['updated_at'] = stamp
        write(path, validate(data))
        return {'claimed': identity, 'since': stamp, 'previous': previous, 'superseded': True, 'record': str(path)}
    return guarded(directory, change)


def main():
    require(sys.version_info >= (3, 13), 'Requires Python 3.13+')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', default='.')
    parser.add_argument('--primary', action='store_true', help='Use primary checkout storage')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('paths')
    sub.add_parser('list')
    signal = sub.add_parser('pointer', help='Print a primary record pointer; never send a message')
    signal.add_argument('name')
    receive = sub.add_parser('read', help='Read current primary record and compare signaled digest')
    receive.add_argument('pointer')
    sub.add_parser('queue', help='Read primary queue sorted by explicit priority, unset last')
    update = sub.add_parser('put')
    update.add_argument('--input', required=True)
    update.add_argument('--expected-sha256')
    sub.add_parser('transfer')
    claim = sub.add_parser('claim-coordinator', help='Claim the Coordinator role: create the record, or take it over')
    claim.add_argument('--identity', required=True, help='Who the Coordinator is, in one line')
    claim.add_argument('--take-over', action='store_true',
                       help="Take over a record that has a holder; only with the lead developer's word")
    claim.add_argument('--reason', help="With --take-over: the lead developer's designation, in a line")
    sub.add_parser('prune')
    sub.add_parser('kinds', help='Print the closed list of work kinds with their meanings')
    move = sub.add_parser('rename', help='Rename a live Job in primary storage, record and queue together')
    move.add_argument('old')
    move.add_argument('new')
    add = sub.add_parser('note', help='Append one pending entry to a Job; the text comes from a UTF-8 file')
    add.add_argument('name')
    add.add_argument('--kind', required=True)
    add.add_argument('--source', required=True, help='The source text, or the path of a UTF-8 file that holds it')
    add.add_argument('--text-file', required=True)
    close = sub.add_parser('complete', help='Verify the landing, then mark a Job COMPLETE (primary storage)')
    close.add_argument('name')
    close.add_argument('--commit', action='append', default=[], help='Integrated commit; repeat for several')
    close.add_argument('--prune', action='store_true', help='Then prune terminal Jobs, as the prune command does')
    close.add_argument('--leftovers', action='append', default=[],
                       help='A folder left behind by the removal; recorded in the Coordinator record context instead of refusing')
    close.add_argument('--owner', action='append', default=[], help='Mission id, when no claim or landing names it')
    close.add_argument('--route', action='append', default=[], metavar='N=HANDLE',
                       help='Route pending entry N (from 1), which has no disposition yet, to another live Job')
    close.add_argument('--recorded', action='store_true', help=argparse.SUPPRESS)
    guide = sub.add_parser('brief', help='Print the standard worker brief for a Job; reads only, writes no record')
    guide.add_argument('name')
    guide.add_argument('--stagger', action='store_true', help='Add the staggered-landing paragraph')
    guide.add_argument('--extra-file', help='UTF-8 file with a short note, printed under the pointer to the Job record (the record wins)')
    place = sub.add_parser('dispose', help='Give one pending entry of a Job its checked disposition (primary storage)')
    place.add_argument('name')
    place.add_argument('entry', type=int, help='The pending entry, counted from 1')
    way = place.add_mutually_exclusive_group(required=True)
    way.add_argument('--route', metavar='HANDLE', help='Move the entry to another live Job and record where')
    way.add_argument('--recorded-in', metavar='REF',
                     help='Where the material now lives: a commit on main, a path on main or a Job or record handle')
    way.add_argument('--discard', metavar='REASON', help='Drop the entry for this reason, or the path of a UTF-8 file that holds it')
    place.add_argument('--unchecked', action='store_true',
                       help='With --recorded-in, accept a reference nothing can verify and store it as unchecked')
    args = parser.parse_args()
    require(not getattr(args, 'recorded', False), '--recorded was removed: give each pending entry a disposition with '
            'the dispose command, or route it with --route <n>=<handle>')
    if args.command == 'kinds':
        print(json.dumps(KINDS, indent=2))
        return
    root, primary = roots(args.repo)
    if args.command == 'paths':
        print(json.dumps({'local': str(root / RELATIVE), 'primary': str(primary / RELATIVE)}, indent=2))
        return
    if args.command == 'brief':
        text = brief(primary, args.name, args.stagger, args.extra_file)
        sys.stdout.buffer.write(text.encode('utf-8'))  # bytes, so Windows never turns the LF line ends into CRLF
        sys.stdout.buffer.flush()
        return
    directory = safe_directory(primary if args.primary or args.command in ('pointer', 'read', 'queue', 'rename', 'complete', 'dispose', 'claim-coordinator') else root,
                               create=args.command in ('put', 'transfer', 'claim-coordinator'))
    if args.command == 'pointer':
        print(pointer(directory, args.name))
        return
    if args.command == 'read':
        result = resolve_pointer(directory, args.pointer)
    elif args.command == 'queue':
        result = queued(safe_directory(primary))
    elif args.command == 'put':
        data = json.loads(Path(args.input).read_text(encoding='utf-8-sig'), object_pairs_hook=unique)
        if 'queue' in data:
            require(root == primary or args.primary, 'Coordinator belongs in primary checkout')
        result = put(directory, data, args.expected_sha256)
    elif args.command == 'rename':
        result = rename(directory, args.old, args.new)
    elif args.command == 'claim-coordinator':
        result = claim_coordinator(directory, args.identity, args.take_over, args.reason)
    elif args.command == 'note':
        source = Path(args.source)
        result = note(directory, args.name, args.kind, text_file(source) if source.is_file() else args.source.strip(),
                      text_file(args.text_file))
    elif args.command == 'complete':
        routes = {}
        for item in args.route:
            position, _, other = item.partition('=')
            require(position.isdigit() and other and int(position) not in routes, 'Route is N=HANDLE, one per entry: ' + item)
            routes[int(position)] = other
        result = complete(directory, primary, args.name, args.commit, args.prune, args.leftovers, routes, args.owner)
    elif args.command == 'dispose':
        result = dispose(directory, primary, args.name, args.entry, args.route, args.recorded_in, args.discard,
                         args.unchecked)
    elif args.command == 'transfer':
        require(not args.primary, 'Transfer runs from source checkout')
        result = transfer(directory, safe_directory(primary, create=True))
    elif args.command == 'prune':
        result = prune(directory, primary) if directory.exists() else []
    else:
        result = [snapshot(p)
                  for p in sorted(directory.glob('*.json'))] if directory.exists() else []
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('FAIL: ' + str(error))
