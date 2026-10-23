"""One shared in-memory stand-in for the Git calls the checkpoint tool makes, for tests that need no repository.

A test that only needs the tool to *read* committed content (the classification table at HEAD, a document at HEAD,
the feature file on main) uses `FakeGit` instead of hand-rolling `tool.git = lambda ...`. The fake answers every
read the tool makes with a sane default, so a new read added to the tool does not break an unrelated test, and a
test states only the facts it cares about. A test that needs real Git behavior (an index, a commit, a worktree)
builds a disposable repository the way `governance/tests/lifecycle/test_checkpoint.py` does; it does not fake Git.

    fake = FakeGit({'governance/ARTIFACT-CLASSIFICATION.md': table_text})
    tool = bare_tool(root, fake)                   # a Checkpoint with no repository behind it
    patch.object(tool, 'git', fake)                # or put it on a real tool for the reads only
    fake.calls                                     # every call, as the argument tuple, for assertions
"""
import checkpoint

HEAD_ID = 'a' * 40


class FakeGit:
    """Answers `Checkpoint.git(*args)` from `files` (path -> text or bytes at HEAD) without running Git.

    Reads answered from the tree:
      show <rev>:<path>        the bytes, or ValueError like the tool's failed Git call when the path is absent
      cat-file -e <rev>:<path> empty bytes, or ValueError when the path is absent
      ls-files [-z] ...        the tracked names (`tracked`, by default the keys of `files`)
      rev-parse ...            `head_id`
      symbolic-ref ...         `branch`
    Any other call answers empty bytes, the answer of a read that found nothing. `others` maps a revision name
    (for example `main` or a commit id) to its own files; every revision not named there reads `files`.
    `unavailable` makes every call fail, as a missing or unreadable repository does.
    """

    def __init__(self, files=None, others=None, tracked=None, head_id=HEAD_ID, branch='refs/heads/main',
                 unavailable=False):
        self.files = dict(files or {})
        self.others = dict(others or {})
        self.tracked = tracked
        self.head_id = head_id
        self.branch = branch
        self.unavailable = unavailable
        self.calls = []

    def tree(self, rev):
        return self.others.get(rev, self.files)

    @staticmethod
    def raw(content):
        return content if isinstance(content, bytes) else content.encode('utf-8')

    def blob(self, spec, args):
        rev, _, path = spec.partition(':')
        files = self.tree(rev)
        if path not in files:
            raise ValueError('Git failed: ' + ' '.join(args) + '\nfatal: path ' + repr(path) + ' does not exist')
        return self.raw(files[path])

    def git(self, *args, data=None):
        self.calls.append(args)
        if self.unavailable:
            raise ValueError('Git failed: ' + ' '.join(args) + '\nfatal: not a git repository')
        verb = args[0] if args else ''
        if verb == 'show' and ':' in args[-1]:
            return self.blob(args[-1], args)
        if verb == 'cat-file' and '-e' in args and ':' in args[-1]:
            self.blob(args[-1], args)
            return b''
        if verb == 'ls-files':
            names = sorted(self.files if self.tracked is None else self.tracked)
            return ('\0'.join(names) + ('\0' if names else '')).encode('utf-8') if '-z' in args \
                else ''.join(name + '\n' for name in names).encode('utf-8')
        if verb == 'rev-parse':
            return (self.head_id + '\n').encode()
        if verb == 'symbolic-ref':
            return (self.branch + '\n').encode()
        return b''

    __call__ = git

    def rooted(self, root, *args, data=None):
        """The same answers for a function that takes the repository root first, as `validate_continuity.git` does."""
        return self.git(*args, data=data)

    def reads(self, verb):
        """The calls made with one verb, for a test that pins what the tool asked."""
        return [call for call in self.calls if call and call[0] == verb]


def bare_tool(root, fake):
    """A Checkpoint with a root and the fake's reads and nothing else: no repository, lock or scratch behind it."""
    tool = object.__new__(checkpoint.Checkpoint)
    tool.root = root
    tool.git = fake
    return tool
