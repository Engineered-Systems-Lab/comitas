"""Helpers the worktree lifecycle and the carry of an approval share: the checkpoint driver files, disposable-folder
removal, the identity environment, the read-only Git reader of the feature switches and the one lifecycle lock."""
from checkpoint import Checkpoint, require
from governance.tools.support import local_state
from governance.tools.worktrees import claims
from governance.tools.continuity.artifact_classification import TOOLS
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


def checkpoint_driver():
    """The files of the checkpoint driver (its facade and components) and one digest of them all."""
    module = sys.modules[Checkpoint.__module__]
    return [Path(path).resolve() for path in module.driver_paths()], module.driver_digest()


def tools_root(path):
    """The checkout that holds a tooling file, whatever the depth of its package under the tools folder."""
    tools = Path(TOOLS).parts
    for folder in Path(path).parents:
        if folder.parts[-len(tools):] == tools:
            return folder.parents[len(tools) - 1]
    raise ValueError('Not a tooling file: ' + str(path))


def remove_tree(path):
    """Delete a disposable folder, including the read-only files Git leaves in it on Windows."""
    def writable(function, name, error):
        os.chmod(name, stat.S_IWRITE)
        function(name)
    shutil.rmtree(path, onexc=writable)


def identity_environment(source, sha):
    """The author and committer of a commit as environment, so a carried commit keeps them and is deterministic."""
    fields = source.git('log', '-1', '--date=raw', '--format=%an%x00%ae%x00%ad%x00%cn%x00%ce%x00%cd',
                        sha).decode('utf-8').rstrip('\n').split('\0')
    require(len(fields) == 6, 'Cannot read the identity of commit ' + sha)
    return dict(os.environ, GIT_AUTHOR_NAME=fields[0], GIT_AUTHOR_EMAIL=fields[1], GIT_AUTHOR_DATE=fields[2],
                GIT_COMMITTER_NAME=fields[3], GIT_COMMITTER_EMAIL=fields[4], GIT_COMMITTER_DATE=fields[5])


class ReadOnlyGit:
    """Just enough of a checkpoint tool for the feature reader: read-only Git calls in one checkout."""

    def __init__(self, root):
        self.root = Path(root)

    def git(self, *args):
        result = subprocess.run(['git', '-C', str(self.root), *args], capture_output=True)
        require(result.returncode == 0, 'Git failed: ' + ' '.join(args))
        return result.stdout


@contextmanager
def lifecycle_lock(tool, operation='lifecycle', owner=None, job_handle=None):
    # All cooperating creation/integration/removal operations share one short lock. The window is declared best effort.
    # The lock lives in the primary checkout's .local/locks folder, created on demand and only when .local is ignored.
    # It is created with the atomic exclusive create below, so two operations can never both hold it.
    local_state.prepare(tool.common, local_state.LOCKS)
    path = local_state.lifecycle_lock(tool.common)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('Worktree lifecycle operation already active; inspect before recovery')
    try:
        os.close(fd)
        with claims.lifecycle_window(tool.common, operation, owner, str(tool.root), job_handle):
            yield
    finally:
        path.unlink()
