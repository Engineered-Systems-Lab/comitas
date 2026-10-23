"""The one place that names the repository root and the repository paths that tests and tools repeat.

Import a name from here instead of writing the literal again or finding the root from a file location
(`Path(__file__).resolve().parents[N]`). The folder anchors TOOLS, TESTS and CONFIG, and the configuration files, are owned by
`artifact_classification`: this module re-exports those very objects and never redefines them. The development log folder is
a setting (`project-governance/config/devlog.json`): read it through `artifact_classification.devlog_settings`; only the default
folder is named here.

The checkpoint driver files and the worktree guard files do not import this module: they are hashed together, marked high
and pinned, and keep their own references. Neither does `validate_continuity.py`, which the integration guard loads isolated
and byte-verified with the parser alone, nor `runtime_guard.py`, which runs before any repository module is trusted.
Importing this module needs `governance/tools` on `sys.path`, as for every other tool.
"""
from pathlib import Path

from governance.tools.continuity import artifact_classification as ac
from governance.tools.support import local_state

# The repository root: this file is governance/tools/repo_paths.py. The only copy of this depth outside the tools that
# must run before they can import anything (the launcher and the extensions' own path set-up, one depth each).
ROOT = Path(__file__).resolve().parents[2]

# Folder anchors and configuration files, re-exported from their owner.
TOOLS = ac.TOOLS
TESTS = ac.TESTS
CONFIG = ac.CONFIG
TABLE = ac.TABLE
POLICY = ac.POLICY
PROFILES = ac.PROFILES
CONTRACT = ac.CONTRACT
FEATURES = ac.FEATURES
DEVLOG_SETTINGS = ac.DEVLOG
GOLDEN = CONFIG + '/artifact-classification-golden.json'

# The project's own side, optional: the folder an upgrade never touches and the files it may hold, named like the framework's:
# its table, policy, profiles and half of the reconstruction contract, and its homes for its rules and decisions.
# The feature switches and the development log settings (FEATURES, DEVLOG_SETTINGS above) live there, with no framework copy.
PROJECT_GOVERNANCE = ac.PROJECT_GOVERNANCE
PROJECT_CONFIG = ac.PROJECT_CONFIG
PROJECT_TABLE = ac.PROJECT_TABLE
PROJECT_POLICY = ac.PROJECT_POLICY
PROJECT_PROFILES = ac.PROJECT_PROFILES
PROJECT_CONSTRAINTS = ac.PROJECT_CONSTRAINTS
PROJECT_DECISIONS = ac.PROJECT_DECISIONS
PROJECT_RATIONALE = ac.PROJECT_RATIONALE
PROJECT_CONTRACT = ac.PROJECT_CONTRACT
PROJECT_GOLDEN = PROJECT_CONFIG + '/artifact-classification-golden.json'

# Documents: repository-relative POSIX paths, as the classification table writes them.
AGENTS = 'AGENTS.md'
README = 'README.md'
ARCHITECTURE = 'docs/ARCHITECTURE.md'
PLAN = 'docs/PLAN.md'
PROGRESS = 'docs/PROGRESS.md'
CONSTRAINTS = 'governance/CONSTRAINTS.md'
DECISIONS = 'governance/DECISIONS.md'
RATIONALE = 'governance/RATIONALE.md'
DEVLOG_INDEX = ac.DEVLOG_DEFAULTS['folder'] + '/README.md'  # the default folder; the real one is a setting

# Tool files that several tests and tools name. The driver and guard files come from the tuples artifact_classification
# owns, so each is spelled once.
_driven = {Path(name).stem: name for name in ac.CHECKPOINT_DRIVER + ac.WORKTREE_GUARD}
CHECKPOINT = _driven['checkpoint']
CHECKPOINT_STATE = _driven['checkpoint_state']
CHECKPOINT_SELECTION = _driven['checkpoint_selection']
CHECKPOINT_EVIDENCE = _driven['checkpoint_evidence']
WORKTREE = _driven['worktree']
VALIDATE_CONTINUITY = ac.CONTINUITY_CHECKER
CLAIMS = TOOLS + '/worktrees/claims.py'
RUN_TESTS = TOOLS + '/run_tests.py'
LOCAL_STATE = TOOLS + '/support/local_state.py'

# The machine-local state shared by every worktree lives under the primary checkout's `.local/`. local_state owns the
# locations and the functions that find them; only its names are re-exported here.
LOCAL = local_state.LOCAL
TIMINGS_FILE = local_state.TIMINGS_FILE
LIFECYCLE_LOCK = local_state.LIFECYCLE_LOCK
