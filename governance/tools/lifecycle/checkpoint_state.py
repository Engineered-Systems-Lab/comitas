"""The repository, its checkpoint store and the packet file: Git and file I/O, identity, refs and snapshots.

One of the four components of the checkpoint tool; governance/tools/checkpoint.py composes them.
"""
from governance.tools import runtime_guard
from governance.tools.continuity import artifact_classification
from governance.tools.lifecycle import early_approval
from governance.tools.support import local_state
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import subprocess
import time


POLICY = artifact_classification.POLICY

PROFILES = artifact_classification.PROFILES

TABLE = artifact_classification.TABLE

# The project's optional files beside the framework's (table, policy, profiles): bound like them, absent or not.
PROJECT_TABLE = artifact_classification.PROJECT_TABLE

PROJECT_POLICY = artifact_classification.PROJECT_POLICY

PROJECT_PROFILES = artifact_classification.PROJECT_PROFILES

TRANSACTION = artifact_classification.TRANSACTION

PENDING = artifact_classification.PENDING

HANDOFF = artifact_classification.HANDOFF

CLEANUP = artifact_classification.CLEANUP

RECEIPT_KEY = artifact_classification.RECEIPT_KEY

OPERATION_LOCK = artifact_classification.OPERATION_LOCK

EARLY_USES = artifact_classification.EARLY_USES

# One definition of what a transaction depends on. Each call site names only its own extras
# (the worktree rows, the configuration) and compares through ref_projection.
REF_POLICY_V1 = 'codex-turn-diffs-same-tree-v1'

REF_POLICY_V2 = 'protected-set-v2'

REF_POLICY = REF_POLICY_V2  # Written into every new packet; older packets keep the policy they were sealed with.

TRANSIENT_REF_PREFIXES = ('refs/codex/turn-diffs/captures/',
                          'refs/codex/turn-diffs/checkpoints/')

# v2 tolerates only these. Every other ref (the owner's own branch, main, tags, replace, notes, unknown) stays exact.
TOLERATED_REF_PREFIXES = ('refs/heads/tx/', 'refs/heads/claude/', 'refs/remotes/', 'refs/codex/')

TOLERATED_REF_NAMES = ('refs/stash',)

def ref_tolerated(name, owner):
    """True when a change to this ref cannot affect what a transaction depends on (v2)."""
    if name == owner or name == 'refs/heads/main':
        return False
    if name.startswith('refs/heads/tx/') and not owner:
        return False  # No owner named: every transaction branch stays exact.
    return name in TOLERATED_REF_NAMES or name.startswith(TOLERATED_REF_PREFIXES)

def own_worktree_row(worktrees, owner):
    """The owner's registration row alone; without an owner every row stays exact."""
    if not owner:
        return worktrees
    return '\0\0'.join(row for row in worktrees.split('\0\0') if 'branch ' + owner in row.split('\0'))

def ref_projection(context):
    """The comparison form of a context under the policy it carries."""
    policy = context.get('ref_policy')
    rest = {k: v for k, v in context.items() if k not in ('ref_policy', 'owner', 'refs', 'transient_refs')}
    if policy == REF_POLICY_V2:
        owner = context.get('owner')
        rest.update(ref_policy=policy, refs=[r for r in context['refs'] if not ref_tolerated(r.split()[0], owner)])
        if 'worktrees' in rest:
            rest['worktrees'] = own_worktree_row(rest['worktrees'], owner)
        return rest
    if policy is None:
        return dict(rest, refs=context['refs'])
    transient = context['transient_refs']
    require(policy == REF_POLICY_V1 and
            all(r in context['refs'] and any(r.split()[0].startswith(p)
                for p in TRANSIENT_REF_PREFIXES) for r in transient), 'Invalid transient ref classification')
    targets = sorted((next(p for p in TRANSIENT_REF_PREFIXES if row.split()[0].startswith(p)),
                      row.split()[1]) for row in transient)
    return dict(rest, ref_policy=policy, refs=[r for r in context['refs'] if r not in transient],
                transient_refs=targets)

def restamp(snapshot, template):
    """Give a current snapshot the policy and owner of an older context; a snapshot always holds every ref."""
    out = {k: v for k, v in snapshot.items() if k not in ('ref_policy', 'owner')}
    out.update({k: template[k] for k in ('ref_policy', 'owner') if k in template})
    return out

def check_ref_context(expected, actual, message, audits):
    """Compare under the expected context's own policy: legacy exact, v1 same-tree renames, v2 protected set."""
    require(isinstance(expected, dict), message)
    actual = restamp(actual, expected)
    policy = expected.get('ref_policy')
    if policy == REF_POLICY_V1:
        before = dict(row.split() for row in expected['transient_refs'])
        after = dict(row.split() for row in actual['transient_refs'])
        require(ref_projection(expected) == ref_projection(actual) and
                all(before[n] == after[n] for n in before.keys() & after.keys()), message)
        if before != after:
            audit = {'phase': message, 'policy': policy,
                     'before': expected['transient_refs'], 'after': actual['transient_refs']}
            if audit not in audits:
                audits.append(audit)
        return
    require(ref_projection(expected) == ref_projection(actual), message)
    if policy == REF_POLICY_V2:
        owner = expected.get('owner')
        before, after = ({r.split()[0]: r.split()[1] for r in ctx['refs'] if ref_tolerated(r.split()[0], owner)}
                         for ctx in (expected, actual))
        if before != after:
            audit = {'phase': message, 'policy': policy,
                     'added': sorted(after.keys() - before.keys()), 'removed': sorted(before.keys() - after.keys()),
                     'moved': sorted(n for n in before.keys() & after.keys() if before[n] != after[n])}
            if audit not in audits:
                audits.append(audit)

def require(ok, message):
    if not ok:
        raise ValueError(message)

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')

def digest(data):
    return hashlib.sha256(data).hexdigest()

def atomic(path, data):
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8) + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()

def exclusive_evidence(path, data):
    """Resume identical evidence only; never replace an existing audit artifact."""
    require(not path.is_symlink(), 'Symlink recovery evidence prohibited')
    try:
        with path.open('xb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        require(path.read_bytes() == data, 'Recovery evidence collision/tampering: ' + path.name)

def run(argv, root):
    started = time.monotonic()
    try:
        result = subprocess.run(argv, cwd=root, capture_output=True, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {'argv': argv, 'exit_code': -1, 'stdout': '', 'stderr': str(error),
                'duration_seconds': round(time.monotonic() - started, 6)}
    return {'argv': argv, 'exit_code': result.returncode,
            'stdout': result.stdout.decode('utf-8', 'replace'),
            'stderr': result.stderr.decode('utf-8', 'replace'),
            'duration_seconds': round(time.monotonic() - started, 6)}

def validate_git_environment(root):
    """Allow only the exact reviewed worker overrides, without normalizing trust paths."""
    overrides = {k: v for k, v in os.environ.items() if k.upper().startswith('GIT_')}
    if 'GIT_PAGER' in overrides:
        require(overrides.pop('GIT_PAGER') == 'cat', 'Unsupported GIT_PAGER override')
    # The subtree entry deliberately trusts descendants, never a parent or global wildcard.
    expected = {'GIT_CONFIG_COUNT': '2',
                'GIT_CONFIG_KEY_0': 'safe.directory', 'GIT_CONFIG_VALUE_0': str(root),
                'GIT_CONFIG_KEY_1': 'safe.directory', 'GIT_CONFIG_VALUE_1': str(root) + '/*'}
    require(not overrides or overrides == expected,
            'Unsupported GIT_* overrides: expected only the exact repository safe.directory pair')


class RepoState:
    def __init__(self, root, runner=run):
        self.root = Path(root).resolve()
        self.runner = runner
        validate_git_environment(self.root)
        actual = Path(self.git('rev-parse', '--show-toplevel').decode().strip()).resolve()
        require(actual == self.root, 'Supply the exact repository root')
        self.gitdir = Path(self.git('rev-parse', '--absolute-git-dir').decode().strip()).resolve()
        common = Path(self.git('rev-parse', '--git-common-dir').decode().strip())
        common = (self.root / common).resolve()
        self.common = common
        # The store dies with the worktree: it is the `governance/` folder of this checkout's own Git directory.
        self.store = local_state.store_folder(self.gitdir)
        require(not local_state.indirect(self.store), 'Symlink transaction store prohibited')
        self.store.mkdir(exist_ok=True)
        require(not local_state.indirect(self.store), 'Symlink transaction store prohibited')

    def git(self, *args, data=None):
        validate_git_environment(self.root)
        result = subprocess.run(['git', '-C', str(self.root), '--literal-pathspecs', *args],
                                input=data, capture_output=True)
        require(result.returncode == 0, 'Git failed: ' + ' '.join(args) + '\n' +
                result.stderr.decode('utf-8', 'replace'))
        return result.stdout

    @contextmanager
    def lock(self, _lifecycle=False):
        if not _lifecycle:
            require(not local_state.lifecycle_lock(self.common).exists(),
                    'Worktree lifecycle operation active; retry after it completes')
        path = self.store / OPERATION_LOCK
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError('Operation lock exists; inspect any running process before manual lock recovery')
        try:
            os.close(fd)
            if not _lifecycle:
                require(not local_state.lifecycle_lock(self.common).exists(),
                        'Worktree lifecycle operation active; retry after it completes')
            yield
        finally:
            path.unlink()

    def key(self):
        path = self.store / RECEIPT_KEY
        require(path.is_file() and not path.is_symlink(),
                'Missing local receipt key in ' + str(self.store) + '; the store was damaged or deleted, so approval '
                'cannot be recovered and no key is made again silently')
        key = path.read_bytes()
        require(len(key) == 32, 'Invalid local receipt key')
        return key

    def ensure_key(self):
        """Make the receipt key at the first use of a store. A store that holds records but no key lost its key: stop."""
        keypath = self.store / RECEIPT_KEY
        if not keypath.exists():
            kept = sorted(p.name for p in self.store.iterdir() if p.name != OPERATION_LOCK)
            require(not kept, 'The receipt key is missing from ' + str(self.store) + ' but the store holds ' +
                    ', '.join(kept) + '; the key was deleted or the store was damaged. No new key is made, because it '
                    'could not authenticate those records: stop and report it to the lead developer')
            fd = os.open(keypath, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(secrets.token_bytes(32))
            os.chmod(keypath, 0o600)

    def validation_ok(self, packet):
        return all(c['exit_code'] == 0 for c in packet['evidence'])

    def transaction_pin(self, early_go=None):
        """The interpreter fingerprint a transaction is pinned to, taken at its first step.

        A transaction that began with a preview keeps the preview's pin, so a prepare on another interpreter refuses at
        its start; a go that cannot be read pins nothing of its own and the prepare pins the running interpreter."""
        pinned = None
        if early_go is not None:
            try:
                pinned = early_approval.unsign(self, early_go)['preview'].get('interpreter')
            except (ValueError, KeyError, TypeError, OSError):
                pinned = None
        runtime_guard.assert_pin(pinned)
        return pinned if pinned is not None else runtime_guard.fingerprint()

    def assert_pinned(self, packet):
        """Refuse at the start of a step when the running interpreter is not the one the transaction began with.

        A packet sealed before the pin existed carries none and is not compared."""
        runtime_guard.assert_pin(packet.get('interpreter'))
        return packet

    def save(self, packet):
        body = canonical(packet)
        envelope = {'packet': packet, 'mac': hmac.new(self.key(), body, hashlib.sha256).hexdigest()}
        atomic(self.store / TRANSACTION, canonical(envelope))

    def load(self):
        envelope = json.loads((self.store / TRANSACTION).read_bytes())
        packet = envelope['packet']
        require(hmac.compare_digest(envelope['mac'], hmac.new(self.key(), canonical(packet),
                                                             hashlib.sha256).hexdigest()),
                'Transaction seal mismatch; do not edit packets or approval receipts')
        require(packet['repo'] == self.identity(), 'Transaction repository identity mismatch')
        return packet

    def identity(self):
        value = {'root': str(self.root), 'gitdir': str(self.gitdir)}
        if self.common != self.gitdir:
            value.update(common=str(self.common), owner=self.worktree_owner())
        return value

    def worktree_owner(self):
        import re
        lock = self.gitdir / 'locked'
        require(lock.is_file() and not lock.is_symlink(), 'Linked worktree requires a mission ownership lock')
        owner = lock.read_text(encoding='utf-8').strip()
        require(re.fullmatch(r'transaction:[a-z0-9][a-z0-9-]{0,63}', owner) and
                self.branch() == 'refs/heads/tx/' + owner.removeprefix('transaction:'),
                'Linked worktree ownership/branch mismatch')
        return owner

    def head(self):
        return self.git('rev-parse', 'HEAD').decode().strip()

    def branch(self):
        return self.git('symbolic-ref', '--quiet', 'HEAD').decode().strip()

    def inventory(self):
        return sorted(p.decode('utf-8') for p in self.git('diff', '--cached', '--name-only',
                                                       '--ita-visible-in-index', '--no-renames', '-z').split(b'\0') if p)

    def configuration(self):
        return digest(self.git('config', '--list', '--show-origin', '-z'))

    def reconciliation_context(self):
        rows = self.git('worktree', 'list', '--porcelain', '-z').decode().split('\0\0')
        rows = ['\0'.join(f for f in row.split('\0') if not f.startswith('HEAD '))
                if 'branch refs/heads/main' in row.split('\0') else row for row in rows]
        return dict(self.ref_snapshot(include_main=False), worktrees='\0\0'.join(rows))

    def ref_snapshot(self, include_main=True, owner=None):
        """Every ref plus the owner branch; policies decide in ref_projection what must stay exact."""
        if owner is None:
            try:
                owner = self.branch()
            except Exception:
                owner = ''
        refs, transient = [], []
        for row in self.git('for-each-ref', '--format=%(refname) %(objectname) %(objecttype) %(symref)').decode().splitlines():
            name, oid, kind, *symbolic = row.split()
            if not include_main and name == 'refs/heads/main':
                continue
            value = name + ' ' + oid
            refs.append(value)
            if kind == 'tree' and not symbolic and any(name.startswith(p) for p in TRANSIENT_REF_PREFIXES):
                transient.append(value)
        return {'ref_policy': REF_POLICY, 'owner': owner, 'refs': refs, 'transient_refs': transient}

    def status(self):
        return self.git('status', '--porcelain=v1', '-z', '--no-renames', '--untracked-files=normal').decode('utf-8')

    def residual(self, manifest, status=None):
        status = self.status() if status is None else status
        return sorted(p for p in status.split('\0') if p and p[3:] not in manifest)

    def quiet_operation(self):
        if self.common != self.gitdir:
            self.worktree_owner()
        branch = 'branch ' + self.branch()
        require(self.git('worktree', 'list', '--porcelain', '-z').decode().split('\0').count(branch) == 1,
                'Multiple worktrees claim the same branch')
        for name in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply', 'index.lock'):
            require(not (self.gitdir / name).exists(), 'Concurrent/incomplete Git operation: ' + name)
        hooks = self.git('config', '--get', 'core.hooksPath') if self.configured('core.hooksPath') else b''
        hookdir = (self.root / hooks.decode().strip()).resolve() if hooks else self.common / 'hooks'
        require(not any((hookdir / n).exists() for n in
                        ('pre-commit', 'prepare-commit-msg', 'commit-msg', 'post-commit')),
                'Active commit hooks are unsupported; review them outside this transaction')

    def configured(self, name):
        return subprocess.run(['git', '-C', str(self.root), 'config', '--get', name],
                              capture_output=True).returncode == 0

    def snapshot(self, manifest, policy):
        names = set(p for p in self.git('ls-files', '-z').decode('utf-8').split('\0') if p)
        names.update(manifest)
        names.update([POLICY, PROFILES, TABLE, *artifact_classification.OVERLAY_FILES, *artifact_classification.CHECKPOINT_DRIVER])
        names.update(self.bound_paths(policy))
        exclusions = ['--exclude=' + p + '/' for p in policy['prohibited_components']]
        names.update(p for p in self.git('ls-files', '--others', '--exclude-standard',
                                        *exclusions, '-z').decode('utf-8').split('\0') if p)
        result = {}
        for name in sorted(names):
            require(not any(p.casefold() in policy['prohibited_components'] for p in PurePosixPath(name).parts),
                    'Tracked transient path unsupported: ' + name)
            path = self.root / name
            require(not path.is_symlink() and path.resolve().is_relative_to(self.root),
                    'Symlink/outside validated content: ' + name)
            result[name] = digest(path.read_bytes()) if path.is_file() else None
        return result

    def load_path(self, path):
        require(not path.is_symlink(), 'Symlink packet prohibited')
        envelope = json.loads(path.read_bytes())
        packet = envelope['packet']
        require(hmac.compare_digest(envelope['mac'], hmac.new(self.key(), canonical(packet),
                    hashlib.sha256).hexdigest()), 'Transaction seal mismatch')
        require(packet['repo'] == self.identity(), 'Transaction repository identity mismatch')
        return packet

    def read_message(self, message_file):
        message_file = Path(message_file).resolve()
        message = message_file.read_bytes()
        message.decode('utf-8')
        require(message.strip() and b'\0' not in message, 'Message must be nonempty UTF-8 without NUL')
        require(not any(c < 32 and c not in (9, 10, 13) for c in message), 'Message contains control bytes')
        return message_file, message
