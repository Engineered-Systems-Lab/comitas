"""Offline command-recovery checks; Git and Python 3.9+ standard library only."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

# The classification parser is the single reader of the table. It sits beside this checker; the repository root is the import root (this file is also loaded alone, isolated, by the guard).
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from governance.tools.continuity import artifact_classification  # noqa: E402

# Its own root, on purpose: the integration guard loads this file isolated and byte-verified, together with the parser
# only, so it must not import another repository module (repo_paths included).
ROOT = Path(__file__).resolve().parents[3]
CONTRACT = artifact_classification.CONTRACT
FROZEN_FACT = 'frozen_contracts_and_fixtures_unchanged'
FROZEN_MANIFEST = artifact_classification.FROZEN_MANIFEST


def reconstruction_contract(root):
    """The reconstruction contract of this root: the framework's part with the project's added, when it has one."""
    return artifact_classification.load_contract(root)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def classification(root):
    """The classification table of this root (the framework's with the project's added, when it has one), or None for a
    layout that predates it."""
    policy_path = Path(root) / artifact_classification.POLICY
    profiles_path = Path(root) / artifact_classification.PROFILES
    if not (policy_path.is_file() and profiles_path.is_file()):
        return None
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    if policy.get('version', 0) < 4:
        return None
    return artifact_classification.load_table(root, artifact_classification.load_profiles(root))


def scope(root):
    """(frozen, runtime, required) paths, from the root's classification table."""
    table = classification(root)
    require(table is not None, 'The repository has no artifact classification table')
    return (table.boundary_paths('frozen'), table.boundary_paths('runtime'), table.reachable_paths())


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    require(result.returncode == 0, 'Git evidence unavailable: ' + ' '.join(args))
    return result.stdout


def safe_path(root, relative):
    path = (root / relative).resolve()
    require(path.is_relative_to(root.resolve()), f'Outside repository: {relative}')
    parts = path.relative_to(root.resolve()).parts
    require(not any(p in {'.git', 'work', 'tmp', '.cache', 'target', '__pycache__'} for p in parts),
            f'Transient/private evidence prohibited: {relative}')
    require(path.is_file(), f'Missing recovery evidence: {relative}')
    return path


def read(root, relative):
    return safe_path(root, relative).read_text(encoding='utf-8')


def headings(text):
    """GitHub-style slugs for the ATX headings used in repository prose."""
    result, counts = [], {}
    for match in re.finditer(r'^(#{1,6}) (.+?)\s*$', text, re.M):
        slug = re.sub(r'[^\w\- ]', '', match[2].lower()).replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.append((slug + (f'-{count}' if count else ''), len(match[1]), match.start(), match.end()))
    return result


def source_text(root, source):
    """Evidence text of a source: a repository file, or one heading section of it."""
    safe_path(root, source.partition('#')[0])
    return section_text(root, source)


def section_text(root, source):
    """Text of a file or of one heading section, with no evidence guard: link integrity only."""
    relative, _, fragment = source.partition('#')
    text = read(root, relative)
    if not fragment:
        return text
    hs = headings(text)
    matching = [h for h in hs if h[0] == fragment]
    require(len(matching) == 1, f'Missing/ambiguous heading: {source}')
    _, level, start, end = matching[0]
    stop = next((h[2] for h in hs if h[2] > start and h[1] <= level), len(text))
    return text[end:stop]


def local_links(root, relative):
    text = read(root, relative)
    links = []
    for target in re.findall(r'(?<!!)\[[^\]\n]+\]\(([^)\s]+)\)', text):
        if urlsplit(target).scheme:
            continue
        target = unquote(target)
        path, sep, fragment = target.partition('#')
        normalized = (Path(relative).parent / path).as_posix() if path else relative
        resolved = safe_path(root, normalized).relative_to(root.resolve()).as_posix()
        if sep:
            section_text(root, resolved + '#' + fragment)  # a link is not evidence, so a link target is only checked for existence
        links.append(resolved)
    return links


def governance_layout(root):
    # Explicit contract identity preserves revision-specific recovery tests.
    read(root, CONTRACT)  # the framework's part must exist as evidence
    bootstrap = reconstruction_contract(root)['bootstrap']
    require(bootstrap in ('docs/COMMAND-RECOVERY-PROMPT-v1.md',
                          'governance/COMMAND-RECOVERY-PROMPT-v1.md'),
            'Bootstrap/version mismatch')
    return bootstrap.startswith('governance/')


def unclassified_paths(root, table):
    """Tracked files that no row of the table covers; Git is the only list of files."""
    tracked = git(root, 'ls-files', '-z').decode('utf-8').split('\0')
    return sorted(path for path in tracked if path and table.resolve(path) is None)


def validate_coverage(root, table):
    uncovered = unclassified_paths(root, table)
    require(not uncovered, f'{len(uncovered)} tracked path(s) have no row in {artifact_classification.TABLE}; '
            'add a row for each:\n  ' + '\n  '.join(uncovered))


def missing_row_files(root, table):
    """The live exact-file rows whose file is absent. A row under `project-governance/` classifies a file the project may
    leave out (an optional overlay or setting), so it is not one."""
    folder = artifact_classification.PROJECT_GOVERNANCE + '/'
    return [row.path for row in table.rows
            if row.kind == 'file' and not row.path.startswith(folder) and not (root / row.path).is_file()]


def validate_docs(root):
    current = governance_layout(root)
    table = classification(root)
    required = scope(root)[2]
    for path in required:
        read(root, path)
    if table is not None:
        # Rows are never dirt: a live file row names a file that exists; a deleted file's row lives under Historical paths.
        missing = missing_row_files(root, table)
        require(not missing, f'Classification row names a missing file; move it to Historical paths: {missing[0] if missing else ""}')
        for path in table.historical:
            require(not (root / path).exists(), f'Historical classification path still exists: {path}')
        validate_coverage(root, table)
    settings = artifact_classification.devlog_settings(root)
    folder, pattern = settings['folder'], settings['date_format']
    keeps_log = settings['enabled']  # False: the project keeps no devlog; its folder and settings are ignored
    labels = artifact_classification.citation_labels(root)  # the repositories whose commits the project cites by a label
    # The project's own homes for its rules and decisions are optional: an absent one means none. A present one is read like
    # any document (its links must resolve), must be reachable from AGENTS.md, and a registry holds only its own prefix.
    homes = [p for p in (artifact_classification.PROJECT_CONSTRAINTS, artifact_classification.PROJECT_DECISIONS,
                         artifact_classification.PROJECT_RATIONALE) if (root / p).is_file()]
    docs = sorted(set(['AGENTS.md', 'README.md'] + homes +
                      [p.relative_to(root).as_posix() for where in (('docs', folder) if keeps_log else ('docs',))
                       for p in (root / where).glob('*.md')] +
                      ([p.relative_to(root).as_posix() for p in (root / 'governance').rglob('*.md')]
                       if current else [])))
    for registry in (artifact_classification.FRAMEWORK_DECISIONS, artifact_classification.PROJECT_DECISIONS):
        if registry in docs:
            artifact_classification.check_registry_prefix(registry, read(root, registry))
    edges = {p: local_links(root, p) for p in docs}
    reachable, pending = set(), ['AGENTS.md']
    while pending:
        p = pending.pop()
        if p not in reachable:
            reachable.add(p)
            pending.extend(edges.get(p, []))
    for p in [*required, *homes]:
        if p.endswith('.md') and p != 'README.md':
            require(p in reachable, f'Not reachable from AGENTS.md: {p}')
    if not keeps_log:
        return []  # No log folder, no index and no entry is required or validated; everything above still is
    index = set(edges[folder + '/README.md'])
    # The conventions own format rules; they are not a dated log entry.
    logs = [p for p in docs if p.startswith(folder + '/')
            and p not in (folder + '/README.md', folder + '/CONVENTIONS.md')]
    for p in logs:
        require(p in index, f'Log absent from index: {p}')
        text = read(root, p)
        for key in ('Date:', 'Subject:', 'Status: Historical / non-normative', 'Provenance:'):
            require(key in text, f'Log metadata {key} missing: {p}')
        require(text.startswith('# ') and text.split('\n', 1)[0][2:].strip(), f'Log title line missing: {p}')
        found = re.search(r'^Date: (.+)$', text, re.M)
        require(found is not None and Path(p).name.startswith(artifact_classification.date_prefix(found[1])),
                f'Log date: {p}')
        try:
            dt = datetime.strptime(found[1], pattern)
        except ValueError:
            dt = None
        require(dt is not None and dt.strftime(pattern) == found[1], f'Invalid date: {p}')
        cited = artifact_classification.split_citations(text, labels)
        require(not cited.malformed, f'Log has a label without a valid id: {p}')
        refs = [token for _, token in cited.local if len(token) == 40]
        require(refs or cited.external, f'Log has no checkpoint: {p}')
        for ref in refs:  # a commit of a declared repository is only checked for its form, by the reader
            git(root, 'cat-file', '-e', ref + '^{commit}')
    return logs


def digest(data):
    """SHA-256 of the bytes with CRLF read as LF, so a checkout's line-ending rules never change a frozen entry."""
    return hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()


def covers(row, path):
    """Whether a frozen row (a file, a folder or a `folder/*.ext` filter, as boundary_paths gives it) covers a path."""
    if '*' in row:
        folder, _, pattern = row.rpartition('/')
        return path.startswith(folder + '/') and path.endswith(pattern[1:])
    return path == row or path.startswith(row + '/')


def frozen_files(root, rows):
    """The files in the working tree that the frozen rows cover, as sorted repository-relative paths."""
    found = set()
    for row in rows:
        folder, _, pattern = row.rpartition('/')
        if '*' in row:
            found.update(p.relative_to(root).as_posix() for p in (root / folder).rglob(pattern) if p.is_file())
        elif (root / row).is_file():
            found.add(row)
        elif (root / row).is_dir():
            found.update(p.relative_to(root).as_posix() for p in (root / row).rglob('*') if p.is_file())
    return sorted(found)


def read_frozen_manifest(root):
    """{path: sha256} from the frozen manifest of this root, or None when the project has none."""
    path = Path(root) / FROZEN_MANIFEST
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as error:  # includes UnicodeError and json.JSONDecodeError
        raise ValueError(f'Frozen manifest unreadable: {FROZEN_MANIFEST}: {error}') from None
    require(isinstance(data, dict) and set(data) == {'version', 'files'} and data['version'] == 1
            and isinstance(data['files'], dict),
            f'Frozen manifest must hold exactly "version" (1) and "files": {FROZEN_MANIFEST}')
    for name, value in data['files'].items():
        require(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value) and name and not name.startswith('/')
                and '\\' not in name and '..' not in name.split('/'),
                f'Frozen manifest entry is not a repository path with a SHA-256: {name}')
    return data['files']


def write_frozen_manifest(root, files):
    text = json.dumps({'version': 1, 'files': dict(sorted(files.items()))}, indent=2) + '\n'
    (Path(root) / FROZEN_MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    (Path(root) / FROZEN_MANIFEST).write_bytes(text.encode('utf-8'))  # bytes: LF only, whatever the platform


def frozen_invariants(root, checkpoint=None):
    """The frozen files are exactly the files of the manifest, with the content it records, in the working tree and in the
    index. The manifest is written when files are frozen (`--freeze`), so the check never asks Git for history; the
    checkpoint argument is accepted for callers that still pass it and plays no part."""
    root = Path(root)
    frozen = scope(root)[0]
    manifest = read_frozen_manifest(root) or {}
    current = frozen_files(root, frozen)
    missing = sorted(set(current) - set(manifest))
    require(not missing, f'Frozen file inventory changed: no manifest entry for {", ".join(missing)}; '
            f'record them with python -B governance/tools/validate_continuity.py --freeze')
    gone = sorted(set(manifest) - set(current))
    require(not gone, f'Frozen file inventory changed: manifest entry without a frozen file: {", ".join(gone)}')
    for relative in current:
        require(digest(safe_path(root, relative).read_bytes()) == manifest[relative], f'Frozen content changed: {relative}')
    staged = [p for p in git(root, 'ls-files', '-z').decode('utf-8').split('\0')
              if p and any(covers(row, p) for row in frozen)]
    require(sorted(staged) == current, 'Frozen index differs from the manifest: ' + ', '.join(
        sorted(set(staged) ^ set(current))))
    for relative in staged:
        require(digest(git(root, 'show', ':' + relative)) == manifest[relative], f'Frozen index differs from the manifest: {relative}')
    return True


def frozen_in_force(root):
    """Whether the frozen comparison applies: the classification table has a frozen row. It does not depend on the
    reconstruction contract."""
    return bool(scope(root)[0])


def freeze(root, refreeze=()):
    """Record the frozen files in the manifest. Files with no entry get one from their current content, whatever their
    history: freezing only adds a restriction. An existing entry changes or goes only when its path is named in `refreeze`
    (its file is then read again, or the entry is removed when the file is no longer frozen). Returns (added, changed,
    removed, untouched) path lists; `untouched` holds existing entries that disagree with the working tree and were not named."""
    root = Path(root)
    frozen = scope(root)[0]
    manifest = read_frozen_manifest(root) or {}
    current = frozen_files(root, frozen)
    named = set(refreeze)
    unknown = sorted(named - set(manifest) - set(current))
    require(not unknown, f'--refreeze names a path that is neither frozen nor in the manifest: {", ".join(unknown)}')
    added, changed, removed = [], [], []
    for relative in current:
        value = digest(safe_path(root, relative).read_bytes())
        if relative not in manifest:
            manifest[relative] = value
            added.append(relative)
        elif relative in named and manifest[relative] != value:
            manifest[relative] = value
            changed.append(relative)
    for relative in sorted(set(manifest) - set(current)):
        if relative in named:
            del manifest[relative]
            removed.append(relative)
    untouched = [r for r in sorted(manifest) if r not in named and (r not in current or
                 digest(safe_path(root, r).read_bytes()) != manifest[r])]
    if added or changed or removed or read_frozen_manifest(root) is None:
        write_frozen_manifest(root, manifest)
    return added, changed, removed, untouched


def extract_facts(root, contract):
    result = {}
    for name, spec in contract['facts'].items():
        if spec['source'] == 'git:frozen-invariants':
            continue
        matches = re.findall(spec['pattern'], source_text(root, spec['source']), re.M)
        require(len(matches) == (2 if spec['type'] == 'sum' else 1), f'Evidence cardinality: {name}')
        result[name] = (sum(map(int, matches)) if spec['type'] == 'sum' else
                        int(matches[0]) if spec['type'] == 'integer' else matches[0].strip())
    return result


def verify_repository(root=ROOT):
    """Verify the recovery chain of a repository.

    The runtime-checkpoint and completion-evidence checks apply only to what the adopter's reconstruction contract
    declares: a project with no runtime boundary has no `implementation_checkpoint` fact and is verified by its
    documents, links, logs and the facts it does declare. The frozen comparison with the manifest does not wait for the
    contract: it runs whenever the classification table has a frozen row (`frozen_in_force`).
    """
    root = root.resolve()
    logs = validate_docs(root)
    read(root, CONTRACT)
    contract = reconstruction_contract(root)
    require(contract['version'] == 1, 'Unsupported contract version')
    facts = extract_facts(root, contract)
    if 'implementation_checkpoint' not in facts:
        require(FROZEN_FACT not in contract['facts'], 'Frozen invariants need an implementation_checkpoint fact')
        if frozen_in_force(root):
            frozen_invariants(root)
        return contract, facts
    checkpoint = facts['implementation_checkpoint']
    require(re.fullmatch(r'[a-f0-9]{40}', checkpoint), 'Checkpoint must be a full Git SHA')
    git(root, 'merge-base', '--is-ancestor', checkpoint, 'HEAD')
    latest = git(root, 'log', '-1', '--format=%H', 'HEAD', '--', *scope(root)[1]).decode().strip()
    require(checkpoint == latest, 'Stale implementation checkpoint: runtime history has advanced')
    settings = artifact_classification.devlog_settings(root)
    if settings['enabled']:  # A project that keeps no devlog needs no entry beside the pointer
        matching = [p for p in logs if f'Repository checkpoint: `{checkpoint}`' in read(root, p)]
        require(len(matching) == 1, 'Implementation checkpoint needs exactly one indexed log')
        timestamp = git(root, 'show', '-s', '--format=%cI', checkpoint).decode().strip()
        prefix = artifact_classification.date_prefix(datetime.fromisoformat(timestamp).astimezone(timezone.utc).strftime(
            settings['date_format']))
        require(Path(matching[0]).name.startswith(prefix), 'Checkpoint log timestamp mismatch')
    # The historical execution report, when the contract names one, must remain the evidence anchored by this commit.
    completion_source = completion_evidence_source(contract)
    if completion_source:
        report = completion_source.split('#')[0]
        committed = git(root, 'show', checkpoint + ':' + report).decode('utf-8')
        completion = source_text(root, completion_source)
        require(completion.strip() in committed, 'Completion evidence no longer matches checkpoint report')
    if FROZEN_FACT in contract['facts']:
        facts[FROZEN_FACT] = frozen_invariants(root)
    elif frozen_in_force(root):
        frozen_invariants(root)
    return contract, facts


def completion_evidence_source(contract):
    """The evidence source the contract names as the completion report, or None when it names none."""
    name = contract.get('completion_evidence_fact')
    return contract['facts'][name]['source'] if name else None


def validate_reconstruction(root, artifact, contract, expected):
    require(isinstance(artifact, dict) and set(artifact) == {'contract_version', 'facts', 'sections'},
            'Reconstruction top-level keys')
    require(type(artifact['contract_version']) is int and artifact['contract_version'] == contract['version'],
            'Reconstruction version')
    facts = artifact['facts']
    require(isinstance(facts, dict) and set(facts) == set(expected), 'Reconstruction fact keys')
    for name, value in expected.items():
        item = facts[name]
        require(isinstance(item, dict) and set(item) == {'value', 'evidence'}, f'Fact shape: {name}')
        require(type(item['value']) is type(value) and item['value'] == value, f'Incorrect fact/type: {name}')
        evidence = item['evidence']
        require(isinstance(evidence, list) and contract['facts'][name]['source'] in evidence,
                f'Missing authoritative evidence: {name}')
        for ref in evidence:
            require(isinstance(ref, str), f'Evidence must be a string: {name}')
            if ref != 'git:frozen-invariants':
                source_text(root, ref)
    sections = artifact['sections']
    require(isinstance(sections, dict) and set(sections) == set(contract['required_sections']), 'Narrative section keys')
    all_evidence = set()
    for name, item in sections.items():
        require(isinstance(item, dict) and set(item) == {'text', 'evidence'}, f'Section shape: {name}')
        require(isinstance(item['text'], str) and item['text'].strip(), f'Empty narrative: {name}')
        require(isinstance(item['evidence'], list) and item['evidence'], f'Empty section evidence: {name}')
        for ref in item['evidence']:
            require(isinstance(ref, str), f'Section evidence must be a string: {name}')
            source_text(root, ref)
            all_evidence.add(ref.split('#')[0])
    require(set(contract['governance_evidence']) <= all_evidence, 'Missing governance citations')


def pass_line(root, facts):
    """The PASS line names only the checks that ran: the checkpoint and evidence checks need the checkpoint fact, the
    frozen comparison needs a frozen row."""
    ran = ['recovery documents', 'links', 'logs']
    if 'implementation_checkpoint' in facts:
        ran.append('checkpoint/evidence')
    if frozen_in_force(root):
        ran.append('frozen invariants')
    return 'PASS: ' + ', '.join(ran[:-1]) + (' and ' if len(ran) > 1 else '') + ran[-1] + '.'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reconstruction', type=Path)
    parser.add_argument('--freeze', action='store_true',
                        help='write a manifest entry for every frozen file that has none, from its current content; '
                             'checks nothing else and never changes an existing entry')
    parser.add_argument('--refreeze', action='append', default=[], metavar='PATH',
                        help='with --freeze: also re-read the named frozen file into its entry, or remove the entry of a '
                             'path that is no longer frozen (repeat the option for each path)')
    args = parser.parse_args()
    if args.refreeze and not args.freeze:
        parser.error('--refreeze goes with --freeze')
    try:
        if args.freeze:
            added, changed, removed, untouched = freeze(ROOT, args.refreeze)
            for label, paths in (('added', added), ('changed', changed), ('removed', removed)):
                for path in paths:
                    print(f'{label}: {path}')
            for path in untouched:
                print(f'left as it was (name it with --refreeze to change it): {path}')
            print(f'Frozen manifest {FROZEN_MANIFEST}: {len(added)} added, {len(changed)} changed, {len(removed)} removed.')
            return 0
        contract, facts = verify_repository()
        print(pass_line(ROOT, facts))
        if args.reconstruction:
            artifact = json.loads(args.reconstruction.read_text(encoding='utf-8-sig'))
            validate_reconstruction(ROOT, artifact, contract, facts)
            print('PASS: reconstruction v1 typed facts, citations and narrative structure (no LLM judgment).')
    except (ValueError, OSError, KeyError, TypeError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
