"""The last preview of a mission, kept in a fixed-name file of its review folder, so that a Job handle can lead to it.

`checkpoint.py preview` writes its record wherever the worker chooses (`--output`) and keeps no copy, so nothing could map
a mission to its preview. This module writes one small authenticated record, `last-preview.json`, in the mission's
review folder: the preview's id, tree, parent, branch, time and where its record and readable block were written. A new
preview replaces it, `preview-go` adds the go to it, and it goes with the review folder when the mission is removed
([G025 review folder lifecycle extension](../../DECISIONS.md#g025-review-folder-lifecycle-extension-2026-10-18)).

The record is a convenience for the read-only lookup (`local_jobs.py where`) and grants nothing: it is signed with the
receipt key of the mission's checkout like every preview record, so that an edited record or one from another checkout is
refused, and writing it never stops or changes a preview. A preview made before this record existed has none, and the
lookup says so instead of guessing. Standard library and the early approval helpers only.
"""
import json
from pathlib import Path
import re

from governance.tools.lifecycle import early_approval
from governance.tools.support import local_state, mission_layout
from governance.tools.worktrees import claims

FILE = 'last-preview.json'
VERSION = 1
KIND = 'last-preview'
BRANCH = re.compile(r'refs/heads/tx/(' + mission_layout.MISSION_ID + ')')


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


def load(tool, path):
    """The authenticated body of the record at `path`; ValueError when it is missing, edited or not this checkout's."""
    early_approval.require(path.is_file() and not path.is_symlink(), 'No preview record at ' + str(path))
    return early_approval.unsign(tool, json.loads(path.read_bytes()))


def write(tool, result, output, readable, job_handle):
    """Record `result` (the signed preview envelope) as the mission's last preview. Returns None, or a note saying why not.

    Best effort by design: a failure here never changes what the preview did, and the note is for the worker."""
    body = result['body']
    try:
        path, why = record_path(tool, body['branch'])
        if path is None:
            return 'no last-preview record written: ' + why
        taken = [str(Path(name).resolve()) for name in (output, readable) if Path(name).resolve() == path.resolve()]
        if taken:
            return ('no last-preview record written: the preview output is %s, the reserved name of the record, and it was '
                    'left as the preview wrote it; use another --output name' % FILE)
        data = {'version': VERSION, 'kind': KIND, 'owner': mission_of(body['branch']), 'branch': body['branch'],
                'preview_id': body['id'], 'parent': body['parent'], 'tree': body['tree'], 'eligible': body['eligible'],
                'job_handle': job_handle, 'recorded_at': claims.now(),
                'output': str(Path(output).resolve()), 'readable': str(Path(readable).resolve()), 'go': None}
        early_approval.atomic(path, early_approval.canonical(early_approval.sign(tool, data)))
        return None
    except (ValueError, OSError, KeyError, TypeError) as error:
        return 'no last-preview record written: ' + str(error)


def attach_go(tool, preview, go_result, go_output):
    """Add the go to the record of the preview it names. Returns None, or a note saying why not.

    Only the record of that same preview takes it, so a go never lands on a newer preview."""
    try:
        path, why = record_path(tool, preview['branch'])
        if path is None:
            return 'the go is not added to a last-preview record: ' + why
        if Path(go_output).resolve() == path.resolve():
            return 'the go is not added to the last-preview record: the go output is %s, the reserved name of the record' % FILE
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
