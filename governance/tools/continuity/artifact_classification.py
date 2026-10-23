"""Strict parser and resolver for governance/ARTIFACT-CLASSIFICATION.md, and the home of the repository anchors.

The table is ASCII only and sorted by plain ordinal comparison of its Path cells. One row per
path: an exact file, a directory prefix (`dir/`) or a filter (`dir/*.ext`). Directory and filter
rows are complete; an exact-file row may leave cells empty to inherit them, cell by cell, from the
most specific enclosing complete row. A single hyphen is a deliberate no-value. Anything the
parser does not understand fails loudly: classification fails closed.
"""
from pathlib import Path
import re

# Repository anchors: where the governance tools find the files and scratch they share. Each is spelled once,
# here, and imported everywhere else, so a layout move edits one line per anchor. This module is a stdlib-only
# leaf that every tool already imports. The machine-local state outside the checkout (the timing record, the lifecycle lock)
# is named in support/local_state.py, not here: nothing here has to run before a module is trusted.
# The three folders of the framework: the tools folder (the governance tools, the same in every project that adopts the
# template), the tests of those tools beside it, and the configuration (what the tools are told about this project, with
# the conformance tests that pin it). Everything the tools read from the configuration is named here.
TOOLS = 'governance/tools'
TESTS = 'governance/tests'
CONFIG = 'governance/config'
POLICY = CONFIG + '/checkpoint-policy.json'
PROFILES = CONFIG + '/checkpoint-profiles.json'
TABLE = 'governance/ARTIFACT-CLASSIFICATION.md'
CONTRACT = CONFIG + '/reconstruction-contract-v1.json'
# The project's own side (optional). `governance/` is the framework and an upgrade replaces it wholesale; the sibling folder
# `project-governance/` belongs to the project and an upgrade never touches it. Its files mirror the framework's, under the
# same names: a project table beside it and a configuration folder with the project's policy, profiles, feature switches and
# development log settings. The golden file of the project is named in repo_paths. The tables, the profiles and the policy
# are an overlay (the framework's file plus the project's, a clash refused); the feature switches and the development log
# settings are one file each, at the project's path only, read as an override of the defaults in the code.
PROJECT_GOVERNANCE = 'project-governance'
PROJECT_CONFIG = PROJECT_GOVERNANCE + '/config'
PROJECT_TABLE = PROJECT_GOVERNANCE + '/ARTIFACT-CLASSIFICATION.md'
PROJECT_POLICY = PROJECT_CONFIG + '/checkpoint-policy.json'
PROJECT_PROFILES = PROJECT_CONFIG + '/checkpoint-profiles.json'
# The project's homes for its own rules and decisions, and its half of the reconstruction contract. Each is optional and
# an absent one means none. They mirror the framework's documents: the rules, the decision registry (prefix D) and the
# rationale that explains it, the framework's being CONSTRAINTS.md, DECISIONS.md (prefix G) and RATIONALE.md.
PROJECT_CONSTRAINTS = PROJECT_GOVERNANCE + '/CONSTRAINTS.md'
PROJECT_DECISIONS = PROJECT_GOVERNANCE + '/DECISIONS.md'
PROJECT_RATIONALE = PROJECT_GOVERNANCE + '/RATIONALE.md'
PROJECT_CONTRACT = PROJECT_CONFIG + '/reconstruction-contract.json'
FRAMEWORK_DECISIONS = 'governance/DECISIONS.md'
# The checksum manifest of the frozen files (G054), optional: {"version": 1, "files": {<path>: <SHA-256 of the bytes with CRLF
# read as LF>}}. The continuity check compares the frozen files with it, never with a commit. Freezing a file adds its entry
# from its current content (`validate_continuity.py --freeze`); only `--refreeze <path>` changes or removes one.
FROZEN_MANIFEST = PROJECT_CONFIG + '/frozen-manifest.json'
# Tool files that other tools name: the continuity checker and the classification parser the guard verifies, and
# the files of the checkpoint driver, whose bytes every approval binds.
CONTINUITY_CHECKER = TOOLS + '/continuity/validate_continuity.py'
CLASSIFICATION_PARSER = TOOLS + '/continuity/artifact_classification.py'
CHECKPOINT_DRIVER = tuple(TOOLS + '/' + name + '.py' for name in (
    'checkpoint', 'lifecycle/checkpoint_state', 'lifecycle/checkpoint_selection', 'lifecycle/checkpoint_evidence',
    'lifecycle/checkpoint_lifecycle'))
WORKTREE_GUARD = tuple(TOOLS + '/' + name + '.py' for name in (
    'worktree', 'worktrees/guard', 'worktrees/worktree_carry', 'worktrees/worktree_support'))
# Folders whose files a content-bound validation result depends on.
TOOLING_PREFIXES = (TOOLS + '/', TESTS + '/', CONFIG + '/', PROJECT_GOVERNANCE + '/')
# The file names inside a checkout's checkpoint store. The store is the `governance/` folder of the checkout's own Git
# directory (support/local_state.py names it), so nothing here has a path.
TRANSACTION = 'transaction.json'
PENDING = 'pending.json'
HANDOFF = 'handoff.json'
CLEANUP = 'cleanup.json'
RECEIPT_KEY = 'receipt.key'
OPERATION_LOCK = 'operation.lock'
# Container of transaction worktrees, one folder level above the main checkout: <container>/<repo>/<mission-id> is a
# mission's workspace and <container>/<repo>/<review>/<mission-id> holds its external review inputs. One anchor, two names.
CONTAINER = '_worktrees'
REVIEW = '_review'
# The committed feature switches (TOML) and the scratch files of the early conditional approval (G018 extension). The
# switches are the project's: one file in the project's configuration folder, with no framework copy. A missing key, any
# other value or a file that does not parse means the feature is off.
FEATURES = PROJECT_CONFIG + '/checkpoint-features.toml'
LANDING = 'landing.json'
EARLY_USES = 'early-approval.json'
# The adopter's settings of the development log: the folder that holds its dated entries and the pattern of their dates
# (a strftime pattern, read in UTC). One small file in the project's configuration folder, with no framework copy. Every key
# is optional and a key that is not set takes its default from DEVLOG_DEFAULTS, so a missing file or one that does not parse
# means the defaults: the folder `devlog` and ISO-8601 to the minute, `2026-10-16T13:03Z`. The key `enabled` (default true)
# says whether the project keeps a development log at all: when it is false the continuity check requires no log entry and
# validates neither the folder nor its index, and the extensions that show the log use it.
DEVLOG = PROJECT_CONFIG + '/devlog.json'
DEVLOG_DEFAULTS = {'folder': 'devlog', 'date_format': '%Y-%m-%dT%H:%MZ', 'enabled': True}
# The repositories whose commits this project cites by a label (G051). One small file in the project's configuration folder,
# with no framework copy: {"version": 1, "external_repositories": [<labels>]}. A commit id written `<label> commit` and the id
# in backticks, with a declared label, names a commit of another repository, so only its form is checked; every other id
# must resolve here. The code default is no label, and a missing file or one that does not parse means the default.
CITATIONS = PROJECT_CONFIG + '/citations.json'
CITATIONS_DEFAULTS = {'external_repositories': []}
LABEL_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]*')
CITED_ID = re.compile(r'[0-9a-f]{7,40}')
# `<label> commit` and one space, then a backticked token, on one line; the label is one word that no letter, digit, dot or
# hyphen precedes. Every such phrase is found, whatever the label, and so is a bare backticked id; the declared labels decide
# what a phrase means.
CITATION = re.compile(r'(?<![A-Za-z0-9._-])(' + LABEL_RE.pattern + r') commit `([^`\n]*)`|`(' + CITED_ID.pattern + r')`')
# Selection semantics shared by the checkpoint selector and the read-only explanation.
NARROW_PROFILES = ('governance', 'tooling', 'record-only')
REPLACEABLE = frozenset({'governance-regressions', 'continuity-regressions'})
COLUMNS = ('Path', 'Class', 'Profile', 'Boundary', 'Bound', 'Invariants', 'Subclass', 'Reachable', 'Approval-sensitivity')
HEADER = '| ' + ' | '.join(COLUMNS) + ' |'
# Paths that no longer exist. They reconstruct the class and subclass of old commits, and a tracked deletion
# in the same transaction is validated by the row it had at HEAD once its row has moved here.
HISTORICAL_COLUMNS = ('Path', 'Class', 'Subclass')
HISTORICAL_HEADER = '| ' + ' | '.join(HISTORICAL_COLUMNS) + ' |'
HISTORICAL_SEPARATOR = '| ' + ' | '.join(['---'] * len(HISTORICAL_COLUMNS)) + ' |'
SEPARATOR = '| ' + ' | '.join(['---'] * len(COLUMNS)) + ' |'
CLASSES = ('record', 'governance', 'tooling', 'runtime')
BOUNDARIES = ('runtime', 'frozen')
BLOCKED = 'blocked'
SYMBOL_MAP = 'symbol-map'
NONE = '-'
INHERIT = ''
# Columns whose cell may be the no-value hyphen. Class and Profile never may.
NONE_COLUMNS = ('Boundary', 'Bound', 'Invariants', 'Subclass', 'Reachable', 'Approval-sensitivity')
LIST_COLUMNS = ('Boundary', 'Invariants', 'Subclass')
# Approval sensitivity (G018 early conditional approval extension): the hyphen is low, `medium` and `high` are written.
# Any other cell reads as `high`, so an unknown value can never widen what an early go may carry. The reach in the
# feature file names the sensitivities a go may carry; `high` is never among them and an unknown reach carries none.
SENSITIVITY_CELLS = {NONE: 'low', 'medium': 'medium', 'high': 'high'}
REACH_ALLOWS = {'low': ('low',), 'medium': ('low', 'medium')}
SENSITIVITY_ORDER = ('low', 'medium', 'high')
SEGMENT = r'[A-Za-z0-9._-]+'
FILE_RE = re.compile(SEGMENT + '(?:/' + SEGMENT + ')*')
DIR_RE = re.compile('(?:' + SEGMENT + '/)+')
FILTER_RE = re.compile('((?:' + SEGMENT + r'/)+)\*\.([a-z0-9]+)')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def date_prefix(value):
    """The file name prefix of a log entry dated `value`: the date with every character a file name cannot be relied on
    to hold removed (only letters, digits, dot, underscore and hyphen stay), then a hyphen."""
    return re.sub(r'[^A-Za-z0-9._-]', '', value) + '-'


def devlog_settings(root):
    """The development log settings of the repository at `root`: {'folder': name, 'date_format': strftime pattern,
    'enabled': bool}.

    The settings are an override of the defaults in DEVLOG_DEFAULTS. The file is optional and every key in it is
    optional: a key that is not set takes its default, and a file that is missing, unreadable or does not parse as a JSON
    object means the defaults. A key that is set must hold a good value (an optional "version" of 1; a boolean "enabled";
    a single-segment folder name; a pattern that names a year and reads back what it writes), and a key this reader does
    not know is refused, or the tools stop: a log nobody can find or date is not a log, and a misspelt "enabled" must not
    silently keep the log on."""
    import json
    from datetime import datetime
    path = Path(root) / DEVLOG
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (ValueError, OSError):  # includes UnicodeError and json.JSONDecodeError
        data = None
    if not isinstance(data, dict):
        return dict(DEVLOG_DEFAULTS)
    require(set(data) <= {'version', 'folder', 'date_format', 'enabled'} and data.get('version', 1) == 1,
            'The log settings may hold only "version" (1), "folder", "date_format" and "enabled"')
    settings = {**DEVLOG_DEFAULTS, **{key: data[key] for key in ('folder', 'date_format', 'enabled') if key in data}}
    require(isinstance(settings['enabled'], bool), 'The log setting "enabled" must be true or false')
    require(isinstance(settings['folder'], str) and re.fullmatch(SEGMENT, settings['folder'])
            and settings['folder'] not in ('.', '..'), 'The log folder must be one plain folder name')
    pattern = settings['date_format']
    require(isinstance(pattern, str) and '%Y' in pattern, 'The log date format must be a strftime pattern with a year (%Y)')
    sample = datetime(2026, 10, 16, 13, 3)
    written = sample.strftime(pattern)
    try:
        read_back = datetime.strptime(written, pattern)
    except ValueError:
        read_back = None
    require(read_back is not None and read_back.strftime(pattern) == written and date_prefix(written) != '-',
            'The log date format must read back what it writes and leave something to name a file by')
    return settings


def citation_labels(root):
    """The labels that the project at `root` declares as other repositories (a tuple), in the order of the file.

    The file is optional and so is its key: a file that is missing, unreadable or not a JSON object means no label. A file
    that is set must hold an optional "version" of 1 and "external_repositories", a list of distinct labels (a letter or a
    digit, then letters, digits, dots, underscores and hyphens), and a key this reader does not know is refused, or the
    tools stop: a misspelt key must not silently turn the check off for every id."""
    import json
    path = Path(root) / CITATIONS
    try:
        data = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    except (ValueError, OSError):  # includes UnicodeError and json.JSONDecodeError
        data = None
    if not isinstance(data, dict):
        return tuple(CITATIONS_DEFAULTS['external_repositories'])
    require(set(data) <= {'version', 'external_repositories'} and data.get('version', 1) == 1,
            'The citation settings may hold only "version" (1) and "external_repositories"')
    labels = data.get('external_repositories', CITATIONS_DEFAULTS['external_repositories'])
    require(isinstance(labels, list) and all(isinstance(label, str) and LABEL_RE.fullmatch(label) for label in labels)
            and len(set(labels)) == len(labels),
            'The citation setting "external_repositories" must be a list of distinct labels of letters, digits, dots, '
            'underscores and hyphens')
    return tuple(labels)


class Citations:
    """The commit ids that a text cites in backticks, split by what must be checked.

    `local`: (label, token) pairs that must resolve in this repository: a bare 7 to 40 character lowercase hex token has the
    label None, and so has a token written after a label the project does not declare (which is then checked like a bare one).
    `external`: (label, token) pairs written after a declared label with a valid id; only their form is checked.
    `malformed`: (label, token) pairs written after a declared label with a token that is not 7 to 40 lowercase hex."""
    def __init__(self, local, external, malformed):
        self.local, self.external, self.malformed = local, external, malformed

    @property
    def count(self):
        """How many ids the text cites, local or labeled."""
        return len(self.local) + len(self.external)


def split_citations(text, labels):
    """Split the backticked commit ids of `text` into local, external and malformed ones; see Citations.

    The one reader of the form `<label> commit` followed by one space and the id in backticks, on the same line, for the
    continuity check, the conformance test of the framework's documents and the Mission Control generator. The word `commit`
    is lowercase and the label is matched exactly as declared."""
    local, external, malformed = [], [], []
    for found in CITATION.finditer(text):
        label, token, bare = found.groups()
        if bare is not None:
            local.append((None, bare))
        elif label in labels:
            (external if CITED_ID.fullmatch(token) else malformed).append((label, token))
        elif CITED_ID.fullmatch(token):
            local.append((label, token))  # a label the project does not declare: checked like a bare id
    return Citations(local, external, malformed)


class Row:
    def __init__(self, line, path, cells):
        self.line = line
        self.path = path
        self.cells = cells  # column -> stripped raw text ('' means inherit)
        self.source = None  # the project table's path for a row that came from it, else None (the framework's)
        require(all(part not in ('.', '..') for part in path.split('/')),
                f'line {line}: relative segments are not allowed: {path}')
        if path.endswith('/'):
            require(DIR_RE.fullmatch(path), f'line {line}: invalid directory path: {path}')
            self.kind, self.prefix, self.suffix = 'dir', path, ''
        elif '*' in path:
            match = FILTER_RE.fullmatch(path)
            require(match, f'line {line}: invalid filter path: {path}')
            self.kind, self.prefix, self.suffix = 'filter', match[1], '.' + match[2]
        else:
            require(FILE_RE.fullmatch(path), f'line {line}: invalid path: {path}')
            self.kind, self.prefix, self.suffix = 'file', '', ''

    @property
    def complete(self):
        return all(self.cells[c] != INHERIT for c in COLUMNS[1:])

    def covers(self, path):
        if self.kind == 'file':
            return path == self.path
        return path.startswith(self.prefix) and path != self.prefix and path.endswith(self.suffix)

    def covers_row(self, other):
        """True if this directory or filter row encloses the other row's whole path space."""
        if self.kind == 'file' or other is self:
            return False
        if other.kind == 'file':
            return self.covers(other.path)
        if not other.prefix.startswith(self.prefix):
            return False
        return self.kind == 'dir' or (other.kind == 'filter' and other.suffix == self.suffix)

    def rank(self):
        if self.kind == 'file':
            return (2, 0, 0)
        return (1, len(self.prefix), 1 if self.kind == 'filter' else 0)


class Resolved:
    """The effective classification of one path and where each cell came from."""

    def __init__(self, path, row, parent, values, sources):
        self.path, self.row, self.parent = path, row, parent
        self.values, self.sources = values, sources

    def __getitem__(self, column):
        return self.values[column]

    @property
    def klass(self):
        return self.values['Class']

    @property
    def profile(self):
        return self.values['Profile']

    @property
    def boundary(self):
        return [] if self.values['Boundary'] == NONE else self.values['Boundary'].split(', ')

    @property
    def bound(self):
        return self.values['Bound'] == 'yes'

    @property
    def reachable(self):
        return self.values['Reachable'] == 'yes'

    @property
    def approval_sensitivity(self):
        """`low` for the hyphen, `medium` or `high` as written; any other cell reads as `high` (fail-safe)."""
        return SENSITIVITY_CELLS.get(self.values['Approval-sensitivity'], 'high')

    @property
    def invariants(self):
        """None means unmapped (broad validation); a list names the mapped invariants."""
        value = self.values['Invariants']
        return None if value == NONE else value.split(', ')

    @property
    def subclasses(self):
        value = self.values['Subclass']
        return [] if value == NONE else value.split(', ')


class Table:
    def __init__(self, rows, vocabulary, historical=()):
        self.rows = rows
        self.vocabulary = vocabulary
        self.by_path = {row.path: row for row in rows}
        self.historical = {path: (klass, subclasses) for path, klass, subclasses in historical}
        self._validate()
        self._validate_historical(historical)

    # ------------------------------------------------------------------ parsing
    @classmethod
    def parse(cls, text, vocabulary):
        """vocabulary: {'profiles': set, 'invariants': set, 'subclasses': set}."""
        require(text.isascii(), 'Classification document must be ASCII only')
        lines = text.replace('\r\n', '\n').split('\n')
        starts = [i for i, line in enumerate(lines) if line.strip() == HEADER]
        require(len(starts) == 1, 'Exactly one classification table with the required header is expected')
        start = starts[0]
        require(start + 1 < len(lines) and lines[start + 1].strip() == SEPARATOR,
                'Classification table separator row is missing or malformed')
        rows = []
        for index in range(start + 2, len(lines)):
            line = lines[index]
            if not line.startswith('|'):
                break
            number = index + 1
            require(line.endswith('|'), f'line {number}: malformed row')
            body = line[1:-1].split('|')
            require(len(body) == len(COLUMNS), f'line {number}: expected {len(COLUMNS)} cells, found {len(body)}')
            cells = [cell.strip() for cell in body]
            path_cell = cells[0]
            require(len(path_cell) >= 3 and path_cell[0] == '`' and path_cell[-1] == '`' and '`' not in path_cell[1:-1],
                    f'line {number}: the Path cell must be a single backtick-quoted path')
            rest = cells[1:]
            require(all('`' not in c and c == c.strip() for c in rest), f'line {number}: stray quoting in a cell')
            rows.append(Row(number, path_cell[1:-1], dict(zip(COLUMNS[1:], rest))))
        require(rows, 'Classification table has no rows')
        return cls(rows, vocabulary, cls._parse_historical(lines))

    @staticmethod
    def _parse_historical(lines):
        starts = [i for i, line in enumerate(lines) if line.strip() == HISTORICAL_HEADER]
        require(len(starts) <= 1, 'At most one historical paths table is expected')
        if not starts:
            return []
        start = starts[0]
        require(start + 1 < len(lines) and lines[start + 1].strip() == HISTORICAL_SEPARATOR,
                'Historical paths table separator row is missing or malformed')
        rows = []
        for index in range(start + 2, len(lines)):
            line = lines[index]
            if not line.startswith('|'):
                break
            number = index + 1
            require(line.endswith('|'), f'line {number}: malformed historical row')
            cells = [cell.strip() for cell in line[1:-1].split('|')]
            require(len(cells) == len(HISTORICAL_COLUMNS),
                    f'line {number}: expected {len(HISTORICAL_COLUMNS)} cells, found {len(cells)}')
            path_cell = cells[0]
            require(len(path_cell) >= 3 and path_cell[0] == '`' and path_cell[-1] == '`' and '`' not in path_cell[1:-1],
                    f'line {number}: the Path cell must be a single backtick-quoted path')
            path = path_cell[1:-1]
            require(FILE_RE.fullmatch(path) and all(part not in ('.', '..') for part in path.split('/')),
                    f'line {number}: a historical path must be one exact file: {path}')
            require('`' not in cells[1] and '`' not in cells[2], f'line {number}: stray quoting in a cell')
            rows.append((path, cells[1], cells[2]))
        require(rows, 'Historical paths table has no rows')
        return rows

    def _validate_historical(self, historical):
        paths = [path for path, _, _ in historical]
        for before, after in zip(paths, paths[1:]):
            require(before < after,
                    f'Historical rows must be sorted by ASCII ordinal Path: {after!r} must come before {before!r}')
        require(len(set(paths)) == len(paths), 'Duplicate historical path')
        for path, klass, subclass in historical:
            require(path not in self.by_path, f'Historical path is also a live row: {path}')
            require(klass in CLASSES, f'Historical path {path}: unknown class {klass!r}')
            self._check_subclass_cell(f'historical path {path}', subclass)

    # --------------------------------------------------------------- validation
    def _validate(self):
        paths = [row.path for row in self.rows]
        duplicates = sorted({p for p in paths if paths.count(p) > 1})
        require(not duplicates, 'Duplicate classification path: ' + ', '.join(duplicates))
        for before, after in zip(paths, paths[1:]):
            require(before < after,
                    f'Rows must be sorted by ASCII ordinal Path: {after!r} must come before {before!r}')
        for row in self.rows:
            where = f'line {row.line} ({row.path})'
            if row.kind != 'file':
                require(row.complete, f'{where}: directory and filter rows must fill every column')
            for column in COLUMNS[1:]:
                self._check_cell(where, column, row.cells[column])
        for row in self.rows:
            where = f'line {row.line} ({row.path})'
            if row.kind == 'file' and not row.complete:
                require(self._enclosing(row) is not None,
                        f'{where}: a row with empty cells needs an enclosing directory or filter row')
        # A child row may never clear `frozen` that its enclosing row sets.
        for row in self.rows:
            enclosing = self._enclosing(row)
            if enclosing is not None:
                outer = self._resolve_row(enclosing, None)
                inner = self._resolve_row(row, enclosing)
                if 'frozen' in outer.boundary:
                    require('frozen' in inner.boundary,
                            f'line {row.line} ({row.path}): a child row may not clear frozen')

    def _check_subclass_cell(self, where, text):
        if text == NONE:
            return
        tokens = text.split(', ')
        require(all(t and t == t.strip() for t in tokens) and tokens == sorted(set(tokens)),
                f'{where}: Subclass must be unique tokens in ascending order separated by ", "')
        require(set(tokens) <= set(self.vocabulary['subclasses']), f'{where}: unknown subclass in {text!r}')

    def _check_cell(self, where, column, text):
        if text == INHERIT:
            return
        vocab = self.vocabulary
        if text == NONE:
            require(column in NONE_COLUMNS, f'{where}: {column} cannot be the no-value hyphen')
            return
        if column in LIST_COLUMNS:
            tokens = text.split(', ')
            require(all(t and t == t.strip() for t in tokens) and tokens == sorted(set(tokens)),
                    f'{where}: {column} must be unique tokens in ascending order separated by ", "')
        if column == 'Class':
            require(text in CLASSES, f'{where}: unknown class {text!r}')
        elif column == 'Profile':
            require(text == BLOCKED or text in vocab['profiles'], f'{where}: unknown profile {text!r}')
        elif column == 'Boundary':
            require(set(tokens) <= set(BOUNDARIES), f'{where}: unknown boundary {text!r}')
        elif column in ('Bound', 'Reachable'):
            require(text == 'yes', f'{where}: {column} must be yes or a hyphen')
        elif column == 'Approval-sensitivity':
            require(text in ('medium', 'high'), f'{where}: {column} must be medium, high or a hyphen')
        elif column == 'Invariants':
            require(set(tokens) <= set(vocab['invariants']) | {SYMBOL_MAP}, f'{where}: unknown invariant in {text!r}')
        elif column == 'Subclass':
            require(set(tokens) <= set(vocab['subclasses']), f'{where}: unknown subclass in {text!r}')

    # --------------------------------------------------------------- resolution
    def _enclosing(self, row):
        candidates = [r for r in self.rows if r.covers_row(row)]
        return max(candidates, key=lambda r: r.rank()) if candidates else None

    def _resolve_row(self, row, parent):
        values, sources = {}, {}
        for column in COLUMNS[1:]:
            cell = row.cells[column]
            if cell != INHERIT:
                values[column], sources[column] = cell, 'row'
            else:
                require(parent is not None, f'{row.path}: cannot inherit {column}')
                values[column], sources[column] = parent.cells[column], parent.path
        return Resolved(row.path, row, parent if row.kind == 'file' else None, values, sources)

    def resolve(self, path):
        """The most specific row wins; None means no row, which the tools treat as blocked."""
        covering = [r for r in self.rows if r.covers(path)]
        if not covering:
            return None
        row = max(covering, key=lambda r: r.rank())
        if row.complete:
            return self._resolve_row(row, None)
        parent = max((r for r in covering if r is not row and r.kind != 'file'), key=lambda r: r.rank(), default=None)
        return self._resolve_row(row, parent)

    # -------------------------------------------------------------- derivations
    def scope(self, path):
        resolved = self.resolve(path)
        return None if resolved is None else (resolved.klass, resolved.profile)

    def exact_paths(self):
        return [row.path for row in self.rows if row.kind == 'file']

    def bound_paths(self):
        return [p for p in self.exact_paths() if self.resolve(p).bound]

    def reachable_paths(self):
        return [p for p in self.exact_paths() if self.resolve(p).reachable]

    def boundary_paths(self, token):
        """Row paths whose resolved boundary carries the token: files exactly, directories without the slash."""
        return [row.path.rstrip('/') for row in self.rows
                if token in self._resolve_row(row, self._enclosing(row)).boundary]

    def classify(self, path):
        """(class, subclasses) of a live or historical path; None when no row knows it."""
        resolved = self.resolve(path)
        if resolved is not None:
            return resolved.klass, resolved.subclasses
        if path in self.historical:
            klass, cell = self.historical[path]
            return klass, self._listing(cell)
        return None

    @staticmethod
    def _listing(cell):
        return [] if cell == NONE else cell.split(', ')

    def sensitivity(self, path, head):
        """What a path counts as for the early approval: the higher of its row at HEAD and its row in this table.

        `head` is the table as committed at HEAD, `self` the candidate's table, so no path is judged by a table its
        own transaction wrote. A modified path takes the higher of the two rows. A deleted path has no live row here
        and keeps its HEAD row. A new path takes the higher of the row that encloses it at HEAD and this table's row,
        so a new file is never declared lower than the area it sits in. A path with no row at HEAD, an unavailable
        HEAD table and a `blocked` profile on either side count as `high`. Returns {head, candidate, level}, each side
        `none` when the table has no row for the path.
        """
        def grade(table, name):
            resolved = None if table is None else table.resolve(name)
            if resolved is None:
                return None
            return 'high' if resolved.profile == BLOCKED else resolved.approval_sensitivity
        before, after = grade(head, path), grade(self, path)
        if before is None:
            level = 'high'
        else:
            level = before if after is None else max(before, after, key=SENSITIVITY_ORDER.index)
        return {'head': before or 'none', 'candidate': after or 'none', 'level': level}

    def ineligible_paths(self, paths, reach, head):
        """The paths an early approval at this reach must refuse: more sensitive than the reach allows, `high` at any
        reach, or with no usable row (fail closed). An unknown reach allows nothing, so every path is refused."""
        allowed = REACH_ALLOWS.get(reach, ())
        return sorted(path for path in paths if self.sensitivity(path, head)['level'] not in allowed)

    def sensitive_paths(self, paths, head):
        """{path: sensitivity} for the paths above `low`, a path with no usable row counting as `high`."""
        found = {}
        for path in paths:
            level = self.sensitivity(path, head)['level']
            if level != 'low':
                found[path] = level
        return found

    def in_boundary(self, path, token):
        resolved = self.resolve(path)
        return resolved is not None and token in resolved.boundary

    def deletion_scope(self, path, head):
        """The scope of a deleted file whose row this table moved to Historical paths, else None (blocked).

        `head` is the table as committed at HEAD. The deletion is validated by the row the file had there, so the
        move can only keep or raise what a change to the file needed: this table must have no live row for the
        path and a Historical row naming the same class, and the HEAD row must be a live, unblocked row without a
        runtime or frozen boundary. Anything else, including a path no table knows, stays blocked.
        """
        if head is None or path not in self.historical or self.resolve(path) is not None:
            return None
        before = head.resolve(path)
        if before is None or before.profile == BLOCKED or before.boundary or before.klass != self.historical[path][0]:
            return None
        return before


def vocabulary_of(profiles):
    return {'profiles': set(profiles['profiles']), 'invariants': set(profiles['impact']['invariants']),
            'subclasses': {e['value'] for e in profiles['commit_metadata']['subclasses']}}


# ----------------------------------------------------------------------------------------------------------------------
# The overlay: the project's optional files beside the framework's, and the union of the two.
#
# `governance/` is the framework and is replaced wholesale by an upgrade; `project-governance/` is the project's and is never
# touched by one. The tools read both and take the union. Each project file is optional: when it is absent the result is
# exactly the framework's. The clash rules keep the two apart and are never resolved silently:
#   - the project's table may hold no path under `governance/` or `project-governance/`; the framework's own table
#     classifies all of `governance/` and holds a `high` row for `project-governance/`, because those files decide
#     what a go may carry, so a project cannot classify its own overlay;
#   - a project profile or command may not reuse a framework name.
# These functions are stdlib-only: the integration guard loads this module on its own.
# ----------------------------------------------------------------------------------------------------------------------
FRAMEWORK_FOLDER = TOOLS.split('/')[0]
RESERVED_FOLDERS = (FRAMEWORK_FOLDER, PROJECT_GOVERNANCE)
# The project's optional files that decide what a path is, which checks it needs and what the commands are. Every approval
# binds them beside the framework's own (a file that is absent is bound as absent).
OVERLAY_FILES = (PROJECT_TABLE, PROJECT_POLICY, PROJECT_PROFILES)
# What a project file may add. Anything else in it is refused, so an overlay never changes a framework setting.
PROFILES_OVERLAY_KEYS = ('version', 'profiles')
POLICY_OVERLAY_KEYS = ('version', 'commands', 'prohibited_components', 'required_checks')
# The reconstruction contract: the framework's part names the bootstrap prompt, the framework files a fresh agent must
# cite and the narrative sections; the project's part adds the facts read from the progress record, its own evidence and,
# for a runtime boundary, the fact that names the completion report.
CONTRACT_OVERLAY_KEYS = ('version', 'facts', 'governance_evidence', 'completion_evidence_fact')


def file_reader(root):
    """A reader of the working tree under `root`: a repository-relative path -> its text, or None when it does not exist."""
    def read(relative):
        path = Path(root) / relative
        return path.read_text(encoding='utf-8') if path.is_file() else None
    return read


def json_object(read, relative):
    """The JSON object in `relative` as `read` returns it, or None when the file does not exist."""
    import json
    text = read(relative)
    if text is None:
        return None
    data = json.loads(text)
    require(isinstance(data, dict), f'{relative}: must be a JSON object')
    return data


def read_json_file(root, relative):
    """The JSON object in `relative` under `root`, or None when the file does not exist."""
    return json_object(file_reader(root), relative)


def check_overlay_keys(relative, project, allowed, framework):
    unknown = sorted(set(project) - set(allowed))
    require(not unknown, f'{relative}: the project may not set {", ".join(unknown)}; it may add only '
                         f'{", ".join(key for key in allowed if key != "version")}')
    require(project.get('version') == framework.get('version'),
            f'{relative}: version must equal the framework file version {framework.get("version")!r}')


def add_names(relative, label, base, extra):
    """`base` plus the names in `extra`; a name that is already in `base` is a clash and is refused."""
    require(isinstance(extra, dict), f'{relative}: {label} must be an object')
    clash = sorted(set(base) & set(extra))
    require(not clash, f'{relative}: {label} clash with the framework: {", ".join(clash)}; rename them')
    return {**base, **extra}


def merge_profiles(framework, project, relative=PROJECT_PROFILES):
    """The framework's profiles file plus the project's profiles. A project profile may not reuse a framework name."""
    check_overlay_keys(relative, project, PROFILES_OVERLAY_KEYS, framework)
    merged = dict(framework)
    merged['profiles'] = add_names(relative, 'profiles', framework['profiles'], project.get('profiles', {}))
    return merged


def merge_policy(framework, project, relative=PROJECT_POLICY):
    """The framework's policy plus the project's commands, prohibited components and required checks. Commands may not
    reuse a framework name; the two lists are only ever extended."""
    check_overlay_keys(relative, project, POLICY_OVERLAY_KEYS, framework)
    merged = dict(framework)
    if 'commands' in project:
        merged['commands'] = add_names(relative, 'commands', framework['commands'], project['commands'])
    for key in ('prohibited_components', 'required_checks'):
        if key in project:
            require(isinstance(project[key], list) and all(isinstance(item, str) for item in project[key]),
                    f'{relative}: {key} must be a list of strings')
            merged[key] = list(framework[key]) + [item for item in project[key] if item not in framework[key]]
    return merged


def merge_contract(framework, project, relative=PROJECT_CONTRACT):
    """The framework's reconstruction contract plus the project's facts, evidence and completion fact. A project fact may not
    reuse a framework fact name, the evidence lists are only extended, and the project may not set a key the framework owns
    (the bootstrap prompt and the required sections) or name a completion fact the framework already names."""
    check_overlay_keys(relative, project, CONTRACT_OVERLAY_KEYS, framework)
    merged = dict(framework)
    if 'facts' in project:
        merged['facts'] = add_names(relative, 'facts', framework.get('facts', {}), project['facts'])
    if 'governance_evidence' in project:
        extra = project['governance_evidence']
        require(isinstance(extra, list) and all(isinstance(item, str) for item in extra),
                f'{relative}: governance_evidence must be a list of strings')
        base = framework.get('governance_evidence', [])
        merged['governance_evidence'] = list(base) + [item for item in extra if item not in base]
    if 'completion_evidence_fact' in project:
        require('completion_evidence_fact' not in framework,
                f'{relative}: completion_evidence_fact clashes with the framework file')
        merged['completion_evidence_fact'] = project['completion_evidence_fact']
    return merged


def contract_from(read):
    """The reconstruction contract as `read` returns the files: the framework's plus the project's, when it has one."""
    framework = json_object(read, CONTRACT)
    require(framework is not None, 'Reconstruction contract is missing: ' + CONTRACT)
    project = json_object(read, PROJECT_CONTRACT)
    return framework if project is None else merge_contract(framework, project)


def load_contract(root):
    """The reconstruction contract of the repository at `root`: the framework's file plus the project's, when it has one."""
    return contract_from(file_reader(root))


def registry_prefix(path):
    """The decision prefix a registry file takes: D for a file of the project folder, G for any other (the framework's)."""
    return 'D' if path.startswith(PROJECT_GOVERNANCE + '/') else 'G'


def check_registry_prefix(path, text):
    """Refuse, with one message, a decision of the wrong prefix in a registry: a D in the framework's file, a G (or any
    other letter) in the project's. A decision is a row of the registry table or a level-two or level-three heading that
    begins with a letter and three digits."""
    wanted = registry_prefix(path)
    found = re.findall(r'^(?:\| |#{2,3} )([A-Z])(\d{3})\b', text, re.M)
    wrong = sorted({letter + digits for letter, digits in found if letter != wanted})
    require(not wrong, f'{path} takes only decisions with the prefix {wanted} (the framework numbers with G, a project with D); '
                       f'it holds {", ".join(wrong)}')


def profiles_from(read):
    """The checkpoint profiles as `read` returns the files: the framework's plus the project's, when it has one."""
    framework = json_object(read, PROFILES)
    require(framework is not None, 'Checkpoint profiles are missing: ' + PROFILES)
    project = json_object(read, PROJECT_PROFILES)
    return framework if project is None else merge_profiles(framework, project)


def policy_from(read):
    """The checkpoint policy as `read` returns the files: the framework's plus the project's, when it has one."""
    framework = json_object(read, POLICY)
    require(framework is not None, 'Checkpoint policy is missing: ' + POLICY)
    project = json_object(read, PROJECT_POLICY)
    return framework if project is None else merge_policy(framework, project)


def load_profiles(root):
    """The checkpoint profiles of the repository at `root`: the framework's file plus the project's, when it has one."""
    return profiles_from(file_reader(root))


def load_policy(root):
    """The checkpoint policy of the repository at `root`: the framework's file plus the project's, when it has one."""
    return policy_from(file_reader(root))


def is_script(path):
    """The one rule for which committed paths are shell scripts, and so are committed at mode 100755."""
    return path.endswith('.sh')


def stage_manifest(git, root, manifest):
    """Stage the manifest's whole files with `git`, a callable that runs one Git command in the repository at `root`.

    `core.filemode` is false on Windows, so a plain add stages a new script at 100644; each regular script that exists
    is staged again as executable. A script that was deleted, or is a link, keeps what the plain add made of it."""
    git('add', '--', *manifest)
    scripts = [path for path in manifest if is_script(path)
               and (Path(root) / path).is_file() and not (Path(root) / path).is_symlink()]
    if scripts:
        git('add', '--chmod=+x', '--', *scripts)


def reserved(path):
    return any(path == folder or path.startswith(folder + '/') for folder in RESERVED_FOLDERS)


def merge_tables(framework, project, vocabulary):
    """The union of the framework's table and the project's, as one Table. The project's table may hold no path under
    `governance/` or `project-governance/`, and the framework's must classify `project-governance/` as `high`."""
    for row in project.rows:
        require(not reserved(row.path),
                f'{PROJECT_TABLE} line {row.line}: the project table may not classify {row.path}; every path under '
                f'{FRAMEWORK_FOLDER}/ and {PROJECT_GOVERNANCE}/ is classified by the framework table')
    for path in project.historical:
        require(not reserved(path), f'{PROJECT_TABLE}: the project table may not classify the historical path {path}')
    guard = framework.resolve(PROJECT_TABLE)
    require(guard is not None and guard.profile != BLOCKED and guard.approval_sensitivity == 'high',
            f'{TABLE} must classify {PROJECT_GOVERNANCE}/ as approval-sensitivity high before a project table can be used')
    for row in project.rows:
        row.source = PROJECT_TABLE
    rows = sorted(framework.rows + project.rows, key=lambda row: row.path)
    historical = sorted([(path, *value) for path, value in framework.historical.items()]
                        + [(path, *value) for path, value in project.historical.items()])
    return Table(rows, vocabulary, historical)


def build_table(framework_text, project_text, framework_vocabulary, project_vocabulary):
    """The framework's table, with the project's added when its text is given (None means the project has none)."""
    framework = Table.parse(framework_text, framework_vocabulary)
    if project_text is None:
        return framework
    project = Table.parse(project_text, project_vocabulary)
    return merge_tables(framework, project, project_vocabulary)


def load_table(root, profiles):
    """Parse the table at its anchor from the working tree, with the project's table added when it has one.

    `profiles` is the profiles file as the caller read it, the framework's alone or the merged one `load_profiles` returns.
    The framework's table is parsed against the profiles the framework defines, so it cannot name a project profile; the
    project's table against the framework's plus the project's own, which `load_profiles` checks for a clash."""
    read = file_reader(root)
    framework_text, project_text = read(TABLE), read(PROJECT_TABLE)
    require(framework_text is not None, 'Artifact classification table is missing: ' + TABLE)
    if project_text is None:
        return build_table(framework_text, None, vocabulary_of(profiles), None)
    project_profiles = json_object(read, PROJECT_PROFILES)
    if project_profiles is None:
        vocabulary = vocabulary_of(profiles)
        return build_table(framework_text, project_text, vocabulary, vocabulary)
    merged = profiles_from(read)  # refuses a profile name that the two files share
    own = project_profiles.get('profiles', {})
    framework = dict(profiles, profiles={name: entry for name, entry in profiles['profiles'].items() if name not in own})
    return build_table(framework_text, project_text, vocabulary_of(framework), vocabulary_of(merged))


def table_from(read, profiles):
    """The union of the tables as `read` returns them, such as the versions committed at HEAD. `profiles` are the
    effective profiles of the caller: they are the vocabulary of both tables, so a HEAD table that names a profile the
    candidate removed does not parse, and the caller counts every path as `high`."""
    framework_text = read(TABLE)
    require(framework_text is not None, 'Artifact classification table is missing: ' + TABLE)
    vocabulary = vocabulary_of(profiles)
    return build_table(framework_text, read(PROJECT_TABLE), vocabulary, vocabulary)


def parse_table(text, profiles):
    """Parse table text read from elsewhere. It is one table; the overlay is `table_from`."""
    return Table.parse(text, vocabulary_of(profiles))


def deleted_view(table, path, before):
    """The sealed classification view of a deleted path: the HEAD row decides, and so does its sensitivity."""
    return {'resolved': True, 'class': before.klass, 'profile': before.profile, 'boundary': [], 'bound': False,
            'invariants': before.invariants, 'subclass': table.classify(path)[1], 'reachable': False,
            'approval_sensitivity': before.approval_sensitivity, 'head_sensitivity': before.approval_sensitivity,
            'candidate_sensitivity': 'none', 'row': path, 'inherited': {}, 'deleted': True}


def view(table, path, head):
    """The effective, resolved classification of one path, for sealing and presentation.

    `approval_sensitivity` is the effective level, the higher of the HEAD row and the candidate's row; both are
    sealed beside it so a review can say when the candidate raised or tried to lower its own sensitivity."""
    resolved = table.resolve(path)
    if resolved is None:
        return {'resolved': False}
    grades = table.sensitivity(path, head)
    return {'resolved': True, 'class': resolved.klass, 'profile': resolved.profile, 'boundary': resolved.boundary,
            'bound': resolved.bound, 'invariants': resolved.invariants, 'subclass': resolved.subclasses,
            'reachable': resolved.reachable, 'approval_sensitivity': grades['level'],
            'head_sensitivity': grades['head'], 'candidate_sensitivity': grades['candidate'],
            'row': resolved.row.path,
            'inherited': {c: s for c, s in resolved.sources.items() if s != 'row'}}
