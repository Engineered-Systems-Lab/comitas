"""Where a mission's folders are: the one place the checkpoint tool and the Job lookup read the container layout from.

The layout is the one of the worktree workflow (G025 worktree container extension): the container is
`<parent>/_worktrees/<repo>` beside the main checkout, a mission's workspace is `<container>/<mission-id>` and its
review folder is `<container>/_review/<mission-id>`. The names `_worktrees` and `_review` are anchors of the artifact
classification parser. The functions only compute paths: they read no Git state and create nothing. The lifecycle guard
keeps its own `layout`, which a test pins to this one. Standard library and the anchors only.
"""
from pathlib import Path
import re

from governance.tools.continuity.artifact_classification import CONTAINER, REVIEW
from governance.tools.support import local_state

MISSION_ID = r'[a-z0-9][a-z0-9-]{0,63}'


def layout(main_root, owner):
    """The container, workspace and review folder of a mission, derived from the main checkout's folder alone."""
    if not isinstance(owner, str) or not re.fullmatch(MISSION_ID, owner):
        raise ValueError('Use a unique lowercase mission ID')
    main_root = Path(main_root).resolve()
    container = main_root.parent / CONTAINER / main_root.name
    return {'container': str(container), 'worktree': str(container / owner), 'review': str(container / REVIEW / owner)}


def review_folder(common, owner):
    """The review folder of a mission, from the common Git directory of any checkout of its repository."""
    return Path(layout(local_state.primary_root(common), owner)['review'])
