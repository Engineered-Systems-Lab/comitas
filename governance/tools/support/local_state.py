"""Where the machine-local state of a repository lives: the one place that spells these locations.

The rule is the Git directory holds only what dies with its worktree; everything else that is machine-local and shared by
every worktree of a repository lives in the `.local/` folder of the PRIMARY checkout, and `.local/` is git-ignored. The
primary checkout is the parent of Git's common directory, found from any checkout or worktree of the repository, so a
clone has a primary of its own. A repository without a primary checkout (a bare repository, or a common directory that
is not named `.git`, as with a submodule or a separate Git directory) has no such place: the functions here refuse with
a clear message and never guess.

The shared locations, all under `<primary>/.local/`:

- `timings/checkpoint-timings.jsonl`, the machine-local timing record;
- `locks/worktree-workflow.lock`, the lifecycle lock that spans every worktree.

The one thing that does die with its worktree is the checkpoint store (the receipt key, the packets, the landing and
go-attempt records and the operation lock). It lives in the checkout's own Git directory, in the subfolder `governance/`
(`<primary>/.git/governance/` for the primary checkout, `<primary>/.git/worktrees/<name>/governance/` for a linked
worktree), so Git deletes it with the worktree, `git clean -x` cannot reach it and no editor indexes it. The name
`governance` collides with nothing Git keeps in a Git directory (HEAD, index, config, hooks, info, logs, objects, refs,
worktrees, locked, gitdir, commondir and the operation markers the checkpoint tool reads).

The claims database keeps its own folder beside these. The runtime guard runs before any repository module is trusted and
under a possibly older Python 3, so it keeps a small copy of the lifecycle lock's location; a test asserts that the copy
agrees with this module. Standard library only.
"""
from pathlib import Path
import subprocess

LOCAL = '.local'
TIMINGS = 'timings'
LOCKS = 'locks'
TIMINGS_FILE = 'checkpoint-timings.jsonl'
LIFECYCLE_LOCK = 'worktree-workflow.lock'
GIT_DIRECTORY = '.git'
STORE = 'governance'


class LocalStateError(ValueError):
    """The machine-local state of this repository cannot be located or is not safe to use."""


def common_directory(repo):
    """Git's common directory for `repo` (a checkout, a worktree or the directory of a repository), absolute and resolved."""
    try:
        result = subprocess.run(['git', '-C', str(repo), 'rev-parse', '--path-format=absolute', '--git-common-dir'],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise LocalStateError('cannot run Git: ' + str(error)) from error
    if result.returncode != 0:
        raise LocalStateError('not a Git repository: ' + str(repo))
    return Path(result.stdout.strip()).resolve()


def primary_root(common):
    """The primary checkout: the parent of the common directory. Refused when the common directory is not named `.git`."""
    common = Path(common).resolve()
    if common.name != GIT_DIRECTORY:
        raise LocalStateError('this repository has no primary checkout (its common Git directory is not a .git folder, '
                              'so it is bare, a submodule or a separate Git directory): ' + str(common))
    return common.parent


def local_folder(common):
    return primary_root(common) / LOCAL


def timings_folder(common):
    return local_folder(common) / TIMINGS


def locks_folder(common):
    return local_folder(common) / LOCKS


def timings_file(common):
    return timings_folder(common) / TIMINGS_FILE


def lifecycle_lock(common):
    """The path of the lifecycle lock. Nothing is created or checked: a missing folder simply means no lock."""
    return locks_folder(common) / LIFECYCLE_LOCK


def store_folder(git_directory):
    """The checkpoint store of the checkout whose Git directory is `git_directory` (Git's absolute git dir for it)."""
    return Path(git_directory) / STORE


def checkout_git_directory(checkout):
    """The Git directory of a checkout, read from its `.git` entry without running Git: the folder itself for a primary
    checkout, the folder a linked worktree's `.git` file names otherwise. None when it cannot be told."""
    entry = Path(checkout) / GIT_DIRECTORY
    try:
        if entry.is_dir():
            return entry.resolve()
        first = entry.read_text(encoding='utf-8').splitlines()[0]
    except (OSError, IndexError, UnicodeError):
        return None
    if not first.startswith('gitdir:'):
        return None
    target = Path(first[len('gitdir:'):].strip())
    return (target if target.is_absolute() else Path(checkout) / target).resolve()


def checkout_store(checkout):
    """The checkpoint store of a checkout, or None when its Git directory cannot be told. Nothing is created."""
    git_directory = checkout_git_directory(checkout)
    return None if git_directory is None else store_folder(git_directory)


def indirect(path):
    return path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir())


def require_ignored(common, *parts):
    """Refuse unless `.local/<parts>` is git-ignored in the primary checkout, so shared state can never be committed."""
    primary = primary_root(common)
    probe = '/'.join((LOCAL,) + parts + ('probe',))
    try:
        result = subprocess.run(['git', '-C', str(primary), 'check-ignore', '--', probe], capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as error:
        raise LocalStateError('cannot run Git: ' + str(error)) from error
    if result.returncode != 0:
        raise LocalStateError('the machine-local folder .local/ is not git-ignored in ' + str(primary) +
                              '; add /.local/ to its .gitignore before the tools write shared state there')


def prepare(common, *parts):
    """Create `.local/<parts>` on demand and return it, refusing a folder that is not ignored or is a link."""
    primary = primary_root(common)
    require_ignored(common, *parts)
    folder = primary / LOCAL
    for part in (None,) + parts:
        folder = folder if part is None else folder / part
        if indirect(folder):
            raise LocalStateError('indirect local storage: ' + str(folder))
    folder.mkdir(parents=True, exist_ok=True)
    if indirect(folder):
        raise LocalStateError('indirect local storage: ' + str(folder))
    return folder
