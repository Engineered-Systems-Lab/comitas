"""The previews of a mission, kept in its review folder: the last one in a fixed-name file, and every one in a record of its own.

`checkpoint.py preview` writes its record wherever the worker chooses (`--output`) and keeps no copy, so nothing could map
a mission to its preview. This module writes one small authenticated record, `last-preview.json`, in the mission's
review folder: the preview's id, tree, parent, branch, time and where its record and readable block were written. A new
preview replaces it, `preview-go` adds the go to it, and it goes with the review folder when the mission is removed
([G025 review folder lifecycle extension](../../DECISIONS.md#g025-review-folder-lifecycle-extension-2026-10-18)).

Every preview also leaves `history-<preview id>.json` beside it, in the same authenticated form: the preview's id, tree, parent,
branch and time, and its place in the mission's order (`seq`, 1 for the first). `last-preview.json` stays the pointer to the
newest, so the lookup and its tests do not change; the history lets a reader (the diff viewer) compare the rounds of a mission.
A history record is never replaced or removed by a later preview, goes with the review folder, and is read back only through
`history`, which verifies each file and says what it could not read instead of guessing. A preview made before the history
existed has no such record: it is simply absent from the list.

The record is a convenience for the read-only lookup (`local_jobs.py where`) and grants nothing: it is signed with the
receipt key of the mission's checkout like every preview record, so that an edited record or one from another checkout is
refused, and writing it never stops or changes a preview. A preview made before this record existed has none, and the
lookup says so instead of guessing.

Every write here holds the mission's records lock, `preview-records.lock` in the review folder, made by an exclusive
create: the preview across its pointer and its history record, the go across its read and write of the pointer, and the
review step (`review_step.py run`) across its last look at the last preview and the save of the findings. So a preview
made during a review lands either before that look, and the findings are not saved, or after the save. A preview or a go
waits a few seconds for the lock and then writes nothing and says why; the review step does not wait. The lock is never
broken by a tool: one left by a command that was killed stays until the lead developer's word (H3). Readers take no
lock, because every file is replaced atomically. Standard library and the early approval helpers only.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import time

from governance.tools.lifecycle import early_approval
from governance.tools.support import local_state, mission_layout
from governance.tools.worktrees import claims

FILE = 'last-preview.json'
VERSION = 1
KIND = 'last-preview'
HISTORY_KIND = 'preview-history'
HISTORY_PREFIX = 'history-'
HISTORY_NAME = re.compile(HISTORY_PREFIX + r'([0-9a-f]{32})\.json')
BRANCH = re.compile(r'refs/heads/tx/(' + mission_layout.MISSION_ID + ')')
LOCK = 'preview-records.lock'
WAIT_SECONDS = 30  # how long a preview or a go waits for the lock; the review step holds it for one lookup and one save
POLL_SECONDS = 0.1


@contextmanager
def records_lock(folder, wait=0, sleep=time.sleep, clock=time.monotonic):
    """Hold the records lock of the mission whose review folder is `folder`; ValueError when it is still held after `wait`
    seconds. The lock is made by an exclusive create and removed on the way out, and never removed when it is not ours."""
    path = Path(folder) / LOCK
    deadline = clock() + wait
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if clock() >= deadline:
                raise ValueError('the records lock %s is held by another preview, go or review save, or was left by one '
                                 'that was stopped (remove it only with the lead developer\'s word, H3)' % path) from None
            sleep(POLL_SECONDS)
    try:
        os.close(fd)
        yield
    finally:
        path.unlink()


def mission_of(branch):
    """The mission id of a `refs/heads/tx/<mission-id>` branch; None for any other branch."""
    found = BRANCH.fullmatch(branch) if isinstance(branch, str) else None
    return found.group(1) if found else None


def record_path(tool, branch):
    """(path of the record, None), or (None, why not): a branch that is not a mission's, or a review folder that is not there.

    The folder is made by the worker with the review inputs, so it is never created here."""
    owner = mission_of(branch)
    if owner is None:
        return None, 'the branch %s is not a mission branch (tx/<mission-id>)' % branch
    try:
        folder = mission_layout.review_folder(tool.common, owner)
    except (ValueError, local_state.LocalStateError) as error:
        return None, str(error)
    if local_state.indirect(folder) or not folder.is_dir():
        return None, 'the review folder %s does not exist as a plain folder' % folder
    path = folder / FILE
    if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
        return None, '%s is not a plain file' % path
    return path, None


def history_path(folder, preview_id):
    return Path(folder) / (HISTORY_PREFIX + preview_id + '.json')


def history(tool, folder):
    """(entries, problems) of the preview history in `folder`: the verified records in the mission's order, and one line for each file
    that could not be used (edited, from another checkout, misnamed, unreadable or nested too deeply to be parsed). Nothing is
    written or guessed, and one bad file never costs the others."""
    entries, problems = [], []
    folder = Path(folder)
    if local_state.indirect(folder) or not folder.is_dir():
        return entries, ['the review folder %s is not a plain folder' % folder]
    for path in sorted(folder.iterdir()):
        named = HISTORY_NAME.fullmatch(path.name)
        if not path.name.startswith(HISTORY_PREFIX) or not path.name.endswith('.json'):
            continue
        try:
            early_approval.require(named is not None, 'the name does not follow the pattern ' + HISTORY_PREFIX + '<preview id>.json')
            early_approval.require(path.is_file() and not path.is_symlink() and not path.is_junction(), 'not a plain file')
            body = early_approval.unsign(tool, json.loads(path.read_bytes()))
            early_approval.require(isinstance(body, dict) and body.get('kind') == HISTORY_KIND and body.get('preview_id') == named.group(1)
                                   and isinstance(body.get('seq'), int) and body['seq'] >= 1, 'not a history record of this preview')
            entries.append(body)
        except (ValueError, OSError, TypeError) as error:
            problems.append('%s: %s' % (path.name, error))
        except RecursionError:  # json.loads of a deeply nested file
            problems.append('%s: nested too deeply to be parsed' % path.name)
    entries.sort(key=lambda body: body['seq'])
    return entries, problems


def add_history(tool, folder, body, job_handle):
    """Record the preview `body` in the history; a preview already in it keeps its place. Raises ValueError or OSError.

    A new preview comes after the highest verified number plus one place for each refused history file (whose number cannot be
    read) written no earlier than the newest verified one, so a refused round after it never gives its number to the new
    preview, even when another file is missing. A refused file older than the newest verified one already lies below that
    number and is not counted, so it opens no new gap at later previews. A file edited after the newest verified one counts
    once, which leaves one unused number that a reader shows as unavailable."""
    path = history_path(folder, body['id'])
    entries, _ = history(tool, folder)
    known = [entry for entry in entries if entry['preview_id'] == body['id']]
    verified = {HISTORY_PREFIX + entry['preview_id'] + '.json' for entry in entries}
    files = [entry for entry in Path(folder).iterdir() if HISTORY_NAME.fullmatch(entry.name) and entry.name != path.name]
    newest = max([entry.stat().st_mtime for entry in files if entry.name in verified], default=None)
    refused = [entry for entry in files if entry.name not in verified and (newest is None or entry.stat().st_mtime >= newest)]
    seq = known[0]['seq'] if known else 1 + max([entry['seq'] for entry in entries] + [len(entries)]) + len(refused)
    data = {'version': VERSION, 'kind': HISTORY_KIND, 'seq': seq, 'owner': mission_of(body['branch']), 'branch': body['branch'],
            'preview_id': body['id'], 'parent': body['parent'], 'tree': body['tree'], 'eligible': body['eligible'],
            'job_handle': job_handle, 'recorded_at': claims.now()}
    early_approval.atomic(path, early_approval.canonical(early_approval.sign(tool, data)))


def load(tool, path):
    """The authenticated body of the record at `path`; ValueError when it is missing, edited or not this checkout's."""
    early_approval.require(path.is_file() and not path.is_symlink(), 'No preview record at ' + str(path))
    return early_approval.unsign(tool, json.loads(path.read_bytes()))


def write(tool, result, output, readable, job_handle):
    """Record `result` (the signed preview envelope) as the mission's last preview. Returns None, or a note saying why not.

    The preview is also added to the history of the mission's previews, both under the records lock. Best effort by design: a
    failure here never changes what the preview did, and the note is for the worker."""
    body = result['body']
    try:
        path, why = record_path(tool, body['branch'])
        if path is None:
            return 'no last-preview record written: ' + why
        taken = [str(Path(name).resolve()) for name in (output, readable) if Path(name).resolve() == path.resolve()]
        if taken:
            return ('no last-preview record written: the preview output is %s, the reserved name of the record, and it was '
                    'left as the preview wrote it; use another --output name' % FILE)
        if Path(output).resolve() == (path.parent / LOCK).resolve():
            return lock_named(output)
        data = {'version': VERSION, 'kind': KIND, 'owner': mission_of(body['branch']), 'branch': body['branch'],
                'preview_id': body['id'], 'parent': body['parent'], 'tree': body['tree'], 'eligible': body['eligible'],
                'job_handle': job_handle, 'recorded_at': claims.now(),
                'output': str(Path(output).resolve()), 'readable': str(Path(readable).resolve()), 'go': None}
        with records_lock(path.parent, WAIT_SECONDS):
            early_approval.atomic(path, early_approval.canonical(early_approval.sign(tool, data)))
            return history_note(tool, path.parent, body, output, readable, job_handle)
    except (ValueError, OSError, KeyError, TypeError) as error:
        return 'no last-preview record written: ' + str(error)


def lock_named(output):
    """The note for an output file written under the name of the records lock: it now holds the lock, so it must be moved."""
    return ('no record written: the output %s has the reserved name of the records lock, %s, so it now holds the lock and '
            'every later preview, go and review save of the mission is refused until it is moved away; it is your own output, '
            'not a lock of another command, so move it and use another --output name' % (Path(output).resolve(), LOCK))


def history_note(tool, folder, body, output, readable, job_handle):
    """Add the preview to the history, under the records lock its caller holds. Returns None, or a note saying why not."""
    try:
        taken = [str(Path(name).resolve()) for name in (output, readable) if Path(name).resolve() == history_path(folder, body['id']).resolve()]
        if taken:
            return 'no history record written: the preview output is the reserved name of the history record of this preview'
        add_history(tool, folder, body, job_handle)
        return None
    except (ValueError, OSError, KeyError, TypeError) as error:
        return 'no history record written: ' + str(error)


def attach_go(tool, preview, go_result, go_output):
    """Add the go to the record of the preview it names. Returns None, or a note saying why not.

    Only the record of that same preview takes it, so a go never lands on a newer preview."""
    try:
        path, why = record_path(tool, preview['branch'])
        if path is None:
            return 'the go is not added to a last-preview record: ' + why
        if Path(go_output).resolve() == path.resolve():
            return 'the go is not added to the last-preview record: the go output is %s, the reserved name of the record' % FILE
        if Path(go_output).resolve() == (path.parent / LOCK).resolve():
            return 'the go is not added to the last-preview record: ' + lock_named(go_output)
        with records_lock(path.parent, WAIT_SECONDS):  # so a newer preview's record is never written over with this one
            if not path.exists():
                return 'the go is not added to a last-preview record: this preview has none (it was made before the record existed)'
            data = load(tool, path)
            if data.get('preview_id') != preview['id']:
                return 'the go is not added to the last-preview record: it is for another preview'
            data['go'] = {'recorded_at': claims.now(), 'file': str(Path(go_output).resolve()),
                          'go_id': early_approval.go_id(go_result['body'])}
            early_approval.atomic(path, early_approval.canonical(early_approval.sign(tool, data)))
            return None
    except (ValueError, OSError, KeyError, TypeError) as error:
        return 'the go is not added to a last-preview record: ' + str(error)
