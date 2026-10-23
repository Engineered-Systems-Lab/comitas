"""Read-only Mission Control extraction. Python 3.13+, Git, standard library."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
from governance.tools.lifecycle.commit_metadata import reconstruct
from governance.tools.continuity import artifact_classification
from governance.tools.lifecycle import commit_metadata

TYPES = ('devlog', 'commits', 'decisions', 'documents', 'rationale')
# The commit dataset reconstructs class and subclass from these; changing any of them rebuilds it. The last three are the
# project's optional counterparts, read together with the framework's like every other tool does.
COMMIT_INPUTS = (artifact_classification.POLICY, artifact_classification.PROFILES, artifact_classification.TABLE,
                 *artifact_classification.OVERLAY_FILES)
# The project's homes for its decisions and their rationale: declared by its inputs part, and an absent one means none.
OPTIONAL_HOMES = (artifact_classification.PROJECT_DECISIONS, artifact_classification.PROJECT_RATIONALE)
SHA = re.compile(r'[0-9a-f]{40}\Z')
# A decision id is a prefix and three digits: G for the framework's registry, D for a project's own registry.
ID = r'[GD]\d{3}'
# A dated extension heading of the Decisions document: `### G0xx <name> (date)`, optionally
# `(date, Status)` as the registry's own level-two headings write it.
EXTENSION_HEADING = re.compile('(' + ID + r'\b[^()]*?) \((\d{4}-\d{2}-\d{2})(?:, ([^()]+))?\)')
# An extension states no status of its own unless its heading does; the document holds accepted entries.
EXTENSION_STATUS = 'Accepted'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + '\n').encode('utf-8')


def path_name(value):
    require(isinstance(value, str) and value and '\\' not in value and
            not value.startswith('/') and ':' not in value and
            all(p not in ('', '.', '..') for p in value.split('/')),
            'Expected exact repo-relative POSIX path: ' + str(value))
    require(not any(p in {'.git', 'tmp', 'work', '__pycache__', 'target', '.cache',
                          'generated', 'node_modules', '.venv', 'secrets', '.secrets',
                          '.aws', '.ssh', '.env', '.envrc'} or p.startswith('.env.')
                    for p in value.split('/')),
            'Private/transient input: ' + value)
    return value


def git(root, *args):
    environment = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_OPTIONAL_LOCKS='0', GIT_NO_REPLACE_OBJECTS='1')
    result = subprocess.run(['git', '--no-pager', '--no-replace-objects',
                             '-c', 'safe.directory=' + str(Path(root).resolve()),
                             '-c', 'log.showSignature=false', '-C', str(root), *args],
                            capture_output=True, env=environment)
    require(result.returncode == 0, 'Git input unavailable: ' + ' '.join(args))
    return result.stdout


def prose(text):
    """Remove fenced/inline code and comments before recognizing prose links."""
    text = re.sub(r'<!--.*?-->', lambda m: '\n' * m[0].count('\n'), text, flags=re.S)
    text = re.sub(r'^(`{3,}|~{3,}).*?^\1[^\n]*$',
                  lambda m: '\n' * m[0].count('\n'), text, flags=re.M | re.S)
    return re.sub(r'(`+).*?\1', '', text)


def headings(text):
    result, counts = [], {}
    # Keep offsets in the complete source; code examples are not headings.
    masked = re.sub(r'^(`{3,}|~{3,}).*?^\1[^\n]*$',
                    lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]),
                    text, flags=re.M | re.S)
    for match in re.finditer(r'^(#{1,6}) (.+?)\s*$', masked, re.M):
        title = match[2]
        slug = re.sub(r'[^\w\- ]', '', title.lower()).replace(' ', '-')
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        result.append((slug + (f'-{count}' if count else ''), len(match[1]),
                       match.start(), title))
    return result


def references(text):
    """The repository's inline, image, reference and autolink Markdown forms."""
    text = prose(text)
    definitions = {m[1].casefold(): m[2] for m in re.finditer(
        r'^\s*\[([^\]]+)\]:\s*<?([^\s>]+)>?', text, re.M)}
    refs = []
    inline = list(re.finditer(r'!?\[([^\]\n]+)\]\(<?([^\s)>]+)>?(?:\s+"[^"]*")?\)', text))
    for m in inline:
        refs.append((m[1], m[2]))
    remainder = text
    for m in reversed(inline):
        remainder = remainder[:m.start()] + ' ' * len(m[0]) + remainder[m.end():]
    require(not re.search(r'\[[^\]\n]+\]\(', remainder),
            'Unsupported inline Markdown link syntax; resolve explicitly')
    for m in re.finditer(r'!?\[([^\]\n]+)\]\[([^\]\n]*)\]', text):
        key = (m[2] or m[1]).casefold()
        require(key in definitions, 'Undefined Markdown reference: ' + key)
        refs.append((m[1], definitions[key]))
    for m in re.finditer(r'(?<!!)(?<!\])\[([^\]\n]+)\](?![\[( :])', text):
        if m[1].casefold() in definitions:
            refs.append((m[1], definitions[m[1].casefold()]))
    refs.extend((m[1], m[1]) for m in re.finditer(r'<(https?://[^>\s]+)>', text))
    return sorted(set(refs))


class Selection:
    def __init__(self, root, revision, proposal=None):
        self.root = Path(root).resolve()
        require(SHA.fullmatch(revision), 'Revision must be a full Git commit SHA')
        require(git(self.root, 'cat-file', '-t', revision).strip() == b'commit',
                'Revision is not a commit')
        self.revision = revision
        require(git(self.root, 'rev-parse', '--is-shallow-repository').strip() == b'false',
                'Incomplete/shallow Git history')
        grafts = Path(git(self.root, 'rev-parse', '--git-path', 'info/grafts').decode().strip())
        require(not (grafts if grafts.is_absolute() else self.root / grafts).exists(),
                'Ambient Git grafts are not declared inputs')
        self.tree = {}
        for entry in git(self.root, 'ls-tree', '-rz', revision).split(b'\0'):
            if entry:
                meta, name = entry.split(b'\t', 1)
                mode, kind, oid = meta.decode('ascii').split()
                self.tree[name.decode('utf-8')] = (mode, kind, oid)
        self.overlay = {}
        for name, expected in sorted((proposal or {}).items()):
            path_name(name)
            if expected is None:
                require(name in self.tree, 'Proposal deletion is absent: ' + name)
                self.overlay[name] = None
                continue
            require(re.fullmatch(r'[0-9a-f]{64}', expected or ''), 'Invalid proposal SHA-256')
            file = self.root / name
            require(file.resolve().is_relative_to(self.root) and not file.is_symlink(),
                    'Proposal symlink/outside root: ' + name)
            require(file.is_file() and file.relative_to(self.root).as_posix() == name,
                    'Missing proposal file: ' + name)
            # Windows lookup is case insensitive; verify each component explicitly.
            parent = self.root
            for part in PurePosixPath(name).parts:
                require(part in {p.name for p in parent.iterdir()}, 'Wrong proposal path case: ' + name)
                parent = parent / part
                require(not parent.is_symlink(), 'Proposal symlink: ' + name)
            data = file.read_bytes()
            require(digest(data) == expected, 'Proposal content identity mismatch: ' + name)
            self.overlay[name] = data
        self.cache = {}
        self.sources = {}
        self.anchors = {}

    def names(self):
        return sorted((set(self.tree) | set(self.overlay)) -
                      {p for p, data in self.overlay.items() if data is None})

    def read(self, name):
        path_name(name)
        if name in self.overlay:
            require(self.overlay[name] is not None, 'Deleted input/link target: ' + name)
            return self.overlay[name]
        require(name in self.tree, 'Missing input/link target (exact case): ' + name)
        mode, kind, oid = self.tree[name]
        require(mode in ('100644', '100755') and kind == 'blob', 'Non-file input/link target: ' + name)
        if name not in self.cache:
            self.cache[name] = git(self.root, 'cat-file', 'blob', oid)
        return self.cache[name]

    def has(self, name):
        return self.overlay[name] is not None if name in self.overlay else name in self.tree

    def optional_text(self, name):
        """The text of an input that the selected revision may not have, such as a project file, or None."""
        return self.text(name) if self.has(name) else None

    def text(self, name):
        data = self.read(name)
        require(not data.startswith(b'\xef\xbb\xbf'), 'Input BOM: ' + name)
        return data.decode('utf-8').replace('\r\n', '\n')

    def source(self, name, fragment=None):
        if name in self.sources:
            value = dict(self.sources[name])
            if fragment:
                require(fragment in self.anchors[name], 'Broken fragment: ' + name + '#' + fragment)
                value['fragment'] = fragment
            return value
        data = self.read(name)
        if name in self.overlay:
            value = {'path': name, 'selection': 'proposal', 'contentSha256': digest(data)}
        else:
            # Last content-owning commit, never a global source-tip stamp.
            revision = git(self.root, 'log', '-1', '--format=%H', self.revision, '--', name).decode().strip()
            require(SHA.fullmatch(revision), 'No source revision: ' + name)
            require(git(self.root, 'rev-parse', revision + ':' + name).decode().strip() == self.tree[name][2],
                    'Source traceability does not resolve: ' + name)
            value = {'path': name, 'selection': 'committed', 'revision': revision,
                     'blob': self.tree[name][2]}
        self.sources[name] = dict(value)
        self.anchors[name] = {h[0] for h in headings(self.text(name))} if name.endswith('.md') else set()
        if fragment:
            require(fragment in self.anchors[name],
                    'Broken fragment: ' + name + '#' + fragment)
            value['fragment'] = fragment
        return value


class Generator:
    def __init__(self, selection, inventory):
        self.selection = selection
        self.inventory = inventory
        require(set(inventory) == {'version', 'datasets', 'commitHistory', 'linkPolicy'},
                'Unknown inventory/dependency knowledge; use reviewed inventory')
        require(inventory['version'] == 1 and inventory['commitHistory'] == 'reachable-from-declared-revision'
                and inventory['linkPolicy'] == 'explicit-markdown-and-checkpoint', 'Unsupported inventory')
        require(set(inventory['datasets']) == set(TYPES) - {'commits'}, 'Incomplete dataset inventory')
        self.paths = {}
        self.disabled = set()
        for kind, spec in inventory['datasets'].items():
            # Only the devlog may be switched off: a project that keeps no devlog has an empty dataset that says so.
            require({'paths', 'markdownDirectories'} <= set(spec) <=
                    {'paths', 'markdownDirectories', 'excludePaths'} | ({'enabled'} if kind == 'devlog' else set()),
                    'Unknown input selector')
            require(isinstance(spec.get('enabled', True), bool), 'Invalid input selector: enabled must be true or false')
            if spec.get('enabled', True) is False:
                require(not spec['paths'] and not spec['markdownDirectories'], 'A disabled dataset declares no inputs')
                self.disabled.add(kind)
            names = set(map(path_name, spec['paths']))
            # The project's own decisions and rationale are optional: a revision without them has none.
            names.difference_update(p for p in OPTIONAL_HOMES if p in names and not selection.has(p))
            for directory in spec['markdownDirectories']:
                path_name(directory)
                names.update(p for p in selection.names() if p.startswith(directory + '/') and p.endswith('.md'))
            names.difference_update(map(path_name, spec.get('excludePaths', [])))
            for name in names:
                selection.read(name)
            self.paths[kind] = sorted(names)
        owners = {}
        for kind, names in self.paths.items():
            for name in names:
                require(name not in owners, 'Ambiguous artifact ownership: ' + name)
                owners[name] = kind
        self.owners = owners
        self.dependencies = {kind: set(names) for kind, names in self.paths.items()}
        self.dependencies['commits'] = set(COMMIT_INPUTS)
        self.pair = None
        self.aliases = {}

    def inputs(self, kind):
        # Both families consume the explicit association inventory, even when a
        # decision currently has no rationale. New associations must invalidate it.
        if kind in ('decisions', 'rationale'):
            return sorted(set(self.paths['decisions'] + self.paths['rationale']))
        if kind == 'commits':
            return list(COMMIT_INPUTS)
        return self.paths.get(kind, [])

    def links(self, kind, name, text, source):
        result = []
        for label, href in references(text):
            url = urlsplit(href)
            evidence = dict(source)
            if url.scheme:
                require(url.scheme in ('https', 'http', 'mailto'), 'Unsupported link scheme: ' + href)
                result.append({'relation': 'reference', 'label': label, 'target': {'url': href},
                               'evidence': evidence})
                continue
            require(not url.netloc and not url.query, 'Unsupported local link: ' + href)
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), unquote(url.path))) if url.path else name
            path_name(target)
            self.dependencies[kind].add(target)
            target_source = self.selection.source(target, unquote(url.fragment) or None)
            result.append({'relation': 'reference', 'label': label, 'target': target_source, 'evidence': evidence})
        # Only the explicit checkpoint metadata supports this additional directed edge.
        for line in text.splitlines():
            if line.startswith('Repository checkpoint:'):
                cited = artifact_classification.split_citations(
                    line, artifact_classification.citation_labels(self.selection.root))
                require(not cited.external and not cited.malformed,
                        'A repository checkpoint is a commit of this repository, not a labeled one: ' + line)
                for sha in [token for _, token in cited.local if len(token) == 40]:
                    require(git(self.selection.root, 'cat-file', '-t', sha).strip() == b'commit',
                            'Broken checkpoint commit: ' + sha)
                    result.append({'relation': 'checkpoint', 'target': {'commit': sha}, 'evidence': dict(source)})
        return sorted(result, key=lambda value: canonical(value))

    def record(self, kind, name, text, fragment=None, identity=None):
        source = self.selection.source(name, fragment)
        hs = headings(text)
        title = hs[0][3] if hs else PurePosixPath(name).name
        item = {'id': identity or kind + ':' + name + ('#' + fragment if fragment else ''),
                'type': kind, 'title': title, 'categories': [], 'topics': [], 'keywords': [],
                'summary': next((line for line in text.splitlines() if line.strip() and
                                 not line.startswith(('#', '|', '<!--'))), title),
                'contentMarkdown': text, 'contentFormat': 'markdown', 'source': source,
                'links': self.links(kind, name, text, source)}
        for key in ('Status', 'Date', 'Subject'):
            match = re.search(r'^' + key + r': (.+)$', text, re.M)
            if match:
                item[key.lower()] = match[1]
        return item

    def decision_rows(self):
        """The decision records of every registry: the framework's (prefix G) and, when the revision has one, the project's
        (prefix D). A registry takes only its own prefix; a decision of the other is refused with one message."""
        names = self.paths['decisions']
        require(0 < len(names) <= 2 and len({artifact_classification.registry_prefix(name) for name in names}) == len(names),
                'Decisions takes one framework registry and at most one project registry')
        result, self.aliases = [], {}
        for name in names:
            result.extend(self.registry_records(name))
        require(result, 'No decision registry rows')
        require(not set(self.aliases) & {item['decisionId'] for item in result},
                'Extension alias is also a decision identity')
        return result

    def registry_records(self, name):
        text = self.selection.text(name)
        artifact_classification.check_registry_prefix(name, text)
        hs = headings(text)
        result = []
        linked = {}
        for match in re.finditer(r'^\| (' + ID + r'[^|]*) \| ([^|]+) \| ([^|]+) \| (.+) \|$', text, re.M):
            label, date, status, statement = (v.strip() for v in match.groups())
            for _, href in references(statement):
                if href.startswith('#'):
                    linked.setdefault(href[1:], set()).add(label)
            sections = []
            for index, (slug, level, start, title) in enumerate(hs):
                if level == 2 and re.match(re.escape(label) + r'(?:\s|$)', title):
                    stop = next((h[2] for h in hs[index + 1:] if h[1] <= level), len(text))
                    sections.append(text[start:stop])
            content = '| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n' + match[0] + '\n\n' + '\n'.join(sections)
            item = self.record('decisions', name, content, identity='decision:' + label)
            item.update(title=label, decisionId=label, date=date, status=status,
                        summary=statement, source=dict(item['source'], lineStart=text[:match.start()].count('\n') + 1))
            result.append(item)
        # A dated extension heading is a decision identity too. A registry row wins where it names
        # the same thing: by the heading's own label, or by linking the heading's anchor. Such a
        # heading becomes an alias of that row and creates no second record.
        registry = {item['decisionId'] for item in result}
        seen = set()
        for index, (slug, level, start, title) in enumerate(hs):
            match = EXTENSION_HEADING.fullmatch(title) if level == 3 else None
            if not match:
                continue
            label, date, status = match[1].strip(), match[2], (match[3] or '').strip()
            require(label not in seen, 'Duplicate extension heading: ' + label)
            seen.add(label)
            if label in registry:
                continue
            owners = linked.get(slug, set())
            require(len(owners) <= 1, 'Extension heading linked by several registry rows: ' + label)
            if owners:
                self.aliases[label] = next(iter(owners))
                continue
            stop = next((h[2] for h in hs[index + 1:] if h[1] <= level), len(text))
            section = text[start:stop]
            paragraph = next((' '.join(p.split()) for p in section.partition('\n')[2].split('\n\n') if p.strip()), '')
            require(paragraph, 'Extension heading without text: ' + label)
            sentence = re.match(r'.+?[.!?](?=\s|$)', paragraph)
            item = self.record('decisions', name, section, fragment=slug, identity='decision:' + label)
            item.update(title=label, decisionId=label, date=date, status=status or EXTENSION_STATUS,
                        summary=sentence[0] if sentence else paragraph,
                        source=dict(item['source'], lineStart=text[:start].count('\n') + 1))
            result.append(item)
        return result

    def decision_labels(self, annotation, names):
        # names maps every accepted spelling to its decision identity: the identities themselves
        # and the dated extension headings that a registry row stands for.
        known = set(names.values())
        labels = set()
        remaining = annotation
        # Exact labels (including extensions) win over their component IDs.
        for label in sorted(names, key=lambda value: (-len(value), value)):
            if re.fullmatch(ID, label):
                continue
            pattern = r'(?<!\w)' + re.escape(label) + r'(?!\w)'
            if re.search(pattern, remaining):
                labels.add(names[label])
                # An explicit compound identity keeps its own record, and its
                # constituent decisions are also explicit association endpoints.
                if '/' in label:
                    labels.update(re.findall(r'\b' + ID + r'\b', label))
                remaining = re.sub(pattern, '', remaining)
        for match in list(re.finditer(r'\b([GD])(\d{3})-\1(\d{3})\b', remaining)):
            require(int(match[2]) <= int(match[3]), 'Reversed rationale decision range')
            labels.update(f'{match[1]}{number:03d}' for number in range(int(match[2]), int(match[3]) + 1))
        remaining = re.sub(r'\b([GD])\d{3}-\1\d{3}\b', '', remaining)
        labels.update(re.findall(r'\b' + ID + r'\b', remaining))
        require(labels <= known, 'Unknown rationale decision identity: ' + ', '.join(sorted(labels - known)))
        return sorted(labels)

    def decision_rationale(self):
        if self.pair is not None:
            return self.pair
        decisions = self.decision_rows()
        known = {item['decisionId']: item for item in decisions}
        rationales = []
        for kind in ('decisions', 'rationale'):
            self.dependencies[kind].update(self.inputs(kind))
        for name in self.paths['rationale']:
            text = self.selection.text(name)
            hs = headings(text)
            sections = [(slug, start, title, text[start:next(
                (h[2] for h in hs[index + 1:] if h[1] <= level), len(text))])
                for index, (slug, level, start, title) in enumerate(hs) if level == 2]
            if not sections:
                rationales.append(self.record('rationale', name, text))
                continue
            context = text[:sections[0][1]]
            audit = next((section for section in sections if section[2] == 'Evidence audit'), None)
            audit_context = audit[3].split('| Decision |', 1)[0] if audit else ''
            rows = {}
            if audit:
                for match in re.finditer(r'^\| (' + ID + r'[^|]*) \| ([^|]+) \| (.+) \|$', audit[3], re.M):
                    spelled = match[1].strip()
                    label = self.aliases.get(spelled, spelled)
                    require(label in known, 'Unknown rationale audit decision: ' + spelled +
                            ' (neither a registry row nor a dated extension heading of ' +
                            ' or '.join(self.paths['decisions']) + ')')
                    require(label not in rows, 'Duplicate rationale audit decision: ' + spelled)
                    rows[label] = (match[0], match[2].strip(), match[3].strip())
            entries, covered = [], set()
            for section in sections:
                if section is audit:
                    continue
                # Only the owning heading annotation supplies coverage. Incidental
                # mentions in evidence/history prose do not create associations.
                labels = self.decision_labels(section[2].partition(':')[2],
                                              {**{label: label for label in known}, **self.aliases})
                covered.update(labels)
                entries.append((section, labels))
            # Findings without a substantive section are still real rationale
            # evidence, including source-authored gaps marked missing.
            entries.extend((None, [label]) for label in sorted(set(rows) - covered))
            for section, labels in entries:
                anchor = section[0] if section else audit[0]
                body = context + audit_context
                findings = {label: rows[label] for label in labels if label in rows}
                if findings:
                    body += '| Decision | Rationale status | Finding / reading location |\n| --- | --- | --- |\n' + '\n'.join(value[0] for value in findings.values()) + '\n\n'
                body += section[3] if section else f'## {labels[0]} rationale finding\n\n{rows[labels[0]][2]}\n'
                identity = 'rationale:' + labels[0] if len(labels) == 1 else None
                item = self.record('rationale', name, body, anchor, identity)
                # Inherited document context cites the document, while local
                # section references retain the actual owning section fragment.
                item['links'] = self.links('rationale', name, context + audit_context,
                                           self.selection.source(name))
                if section:
                    item['links'].extend(self.links('rationale', name, section[3],
                                                   self.selection.source(name, anchor)))
                if findings:
                    item['links'].extend(self.links('rationale', name,
                        '\n'.join(value[0] for value in findings.values()),
                        self.selection.source(name, audit[0])))
                item.update(title=section[2] if section else labels[0] + ' · ' + rows[labels[0]][2],
                            status='Explanatory / non-normative', decisionIds=labels,
                            rationaleStatuses={label: value[1] for label, value in findings.items()})
                if len(labels) == 1:
                    item['decisionId'] = labels[0]
                item['summary'] = section[2] if section else rows[labels[0]][2]
                if not section:
                    line = rows[labels[0]][0]
                    item['source']['lineStart'] = text[:text.index(line)].count('\n') + 1
                for label in labels:
                    decision = known[label]
                    # Both directions cite the explicit rationale association,
                    # never an inferred inverse of an arbitrary Markdown link.
                    evidence = dict(item['source'])
                    item['links'].append({'relation': 'explains', 'label': label,
                                          'target': {**decision['source'], 'recordId': decision['id']},
                                          'evidence': evidence})
                    decision['links'].append({'relation': 'rationale', 'label': item['title'],
                                             'target': {**item['source'], 'recordId': item['id']},
                                             'evidence': evidence})
                rationales.append(item)
        for item in decisions + rationales:
            item['links'].sort(key=canonical)
        self.pair = {'decisions': decisions, 'rationale': rationales}
        return self.pair

    def commits(self):
        # Traverse only the declared commit graph, then impose our own total order.
        result = []
        # The framework's files plus the project's, when the revision has them: the loaders the tools use, over this selection.
        read = self.selection.optional_text
        policy = artifact_classification.policy_from(read)
        profiles = artifact_classification.profiles_from(read)
        table = None
        if policy['version'] >= 4:
            table = artifact_classification.table_from(read, profiles)
        for sha in sorted(git(self.selection.root, 'rev-list', self.selection.revision).decode().splitlines()):
            raw = git(self.selection.root, 'cat-file', 'commit', sha)
            headers, message = raw.split(b'\n\n', 1)
            lines = headers.decode('utf-8').splitlines()
            metadata = {}
            for role in ('author', 'committer'):
                line = next(v[len(role) + 1:] for v in lines if v.startswith(role + ' '))
                match = re.fullmatch(r'(.+) <([^<>]*)> (-?\d+) ([+-]\d{4})', line)
                require(match, 'Unsupported Git metadata: ' + sha)
                metadata[role] = {'name': match[1], 'email': match[2], 'epochSeconds': int(match[3]), 'offset': match[4]}
            content = message.decode('utf-8')
            parents = sorted(v[7:] for v in lines if v.startswith('parent '))
            paths = git(self.selection.root, 'diff-tree', '--root', '--no-commit-id',
                        '--name-only', '-r', '-z', sha).decode('utf-8').split('\0')
            paths = [path for path in paths if path] if len(parents) <= 1 else []
            commit_metadata = reconstruct(content, paths, policy, profiles, table)
            result.append({'id': 'commit:' + sha, 'type': 'commits', 'sha': sha,
                           'title': content.splitlines()[0] if content else sha,
                           'summary': content.splitlines()[0] if content else sha,
                           'categories': [], 'topics': [], 'keywords': [], 'contentText': content,
                           'contentFormat': 'text', 'source': {'commit': sha}, 'links': [],
                           'parents': parents, **metadata, **commit_metadata})
        return result

    def build(self, kind):
        if kind == 'commits':
            items = self.commits()
        elif kind in ('decisions', 'rationale'):
            items = self.decision_rationale()[kind]
        else:
            items = []
            for name in self.paths[kind]:
                text = self.selection.text(name)
                item = self.record(kind, name, text)
                if kind == 'devlog':
                    require(item.get('status') == 'Historical / non-normative', 'Log status mismatch: ' + name)
                items.append(item)
        items.sort(key=lambda item: item['id'])
        require(len({item['id'] for item in items}) == len(items), 'Duplicate record identity')
        flag = {'enabled': False} if kind in self.disabled else {}
        return canonical({'schemaVersion': 1, 'type': kind, 'authority': 'derived/non-authoritative', 'items': items, **flag})


def behavior_digest():
    """Digest of the code that decides the output; dependency state is valid only for the digest that built it."""
    return digest(Path(__file__).read_bytes().replace(b'\r\n', b'\n') +
                  Path(commit_metadata.__file__).read_bytes().replace(b'\r\n', b'\n') +
                  Path(artifact_classification.__file__).read_bytes().replace(b'\r\n', b'\n'))


def inventory_digest(inventory):
    return digest(canonical(inventory))


def generate(root, revision, inventory, output, mode='full', changed=(), proposal=None):
    selection = Selection(root, revision, proposal)
    generator = Generator(selection, inventory)
    output = Path(output)
    require(not output.is_symlink(), 'Symlink output directory')
    behavior = behavior_digest()
    config = inventory_digest(inventory)
    state_path = output / 'state.json'
    state = None
    if mode == 'normal':
        require(state_path.is_file(), 'Missing dependency state; use explicit full mode')
        state = json.loads(state_path.read_bytes())
        require(state.get('version') == 1 and state.get('behavior') == behavior and state.get('inventory') == config,
                'Unknown generator/inventory state; use explicit full mode')
        require(set(state.get('datasets', {})) == set(TYPES), 'Incomplete dependency state; use full mode')
        baseline = Selection(root, state['revision'])
        # Detect omitted changes, not just trust an operator-supplied list.
        old = {p: ('git', *v) for p, v in baseline.tree.items()}
        for p, sha in state['proposal'].items():
            path_name(p)
            if sha is None:
                old.pop(p, None)
            else:
                old[p] = ('proposal', sha)
        new = {p: ('git', *v) for p, v in selection.tree.items()}
        for p, raw in selection.overlay.items():
            if raw is None:
                new.pop(p, None)
            else:
                new[p] = ('proposal', digest(raw))
        names = set(old) | set(new)
        known = set().union(*(set(v['dependencies']) for v in state['datasets'].values()),
                            *map(set, generator.paths.values()))
        actual = {p for p in names & known if old.get(p) != new.get(p)}
        declared = {path_name(p) for p in changed}
        require(actual <= declared, 'Incomplete change set: ' + ', '.join(sorted(actual - declared)))
        require(declared <= names, 'Unknown changed path; use explicit full mode')
        require(declared <= known, 'Unknown dependency/impact; use explicit full mode: ' +
                ', '.join(sorted(declared - known)))
        for kind, prior in state['datasets'].items():
            file = output / (kind + '.json')
            require(file.is_file() and digest(file.read_bytes()) == prior['sha256'],
                    'Dataset/state mismatch; use explicit full mode: ' + kind)
            require(isinstance(prior.get('dependencies'), list), 'Missing dependencies; use full mode')
            records = json.loads(file.read_bytes())['items']
            derived = set(prior['inputs']) | {link['target']['path'] for item in records
                                               for link in item['links'] if 'path' in link['target']}
            require(derived == set(prior['dependencies']), 'Incomplete dependencies; use full mode')
        affected = {kind for kind in TYPES if
                    (set(generator.inputs(kind)) != set(state['datasets'][kind]['inputs']) or
                     declared & set(state['datasets'][kind]['dependencies']))}
        if revision != state['revision']:
            affected.add('commits')
    else:
        require(mode in ('full', 'verify'), 'Unknown generation mode')
        affected = set(TYPES)
    # Extract affected families only. Validate every resulting link and traceability
    # against the selection, including records in preserved datasets.
    data = {kind: generator.build(kind) if kind in affected else (output / (kind + '.json')).read_bytes()
            for kind in TYPES}
    record_index = {item['id']: item for raw in data.values() for item in json.loads(raw)['items']}
    base_classes = commit_metadata.registry(json.loads(
        selection.text(artifact_classification.PROFILES)))['classes']
    for kind, raw in data.items():
        envelope = json.loads(raw)
        require(canonical(envelope) == raw and envelope['type'] == kind, 'Noncanonical dataset')
        records = envelope['items']
        require([i['id'] for i in records] == sorted({i['id'] for i in records}),
                'Duplicate/unordered identities')
        for item in records:
            if kind == 'commits':
                require('class' not in item and isinstance(item['classes'], list) and
                        all(isinstance(v, str) for v in item['classes']) and
                        set(item['classes']) <= set(base_classes) and
                        item['classes'] == [v for v in base_classes if v in item['classes']],
                        'Invalid commit classes')
                require(isinstance(item['subclasses'], list) and
                        all(isinstance(v, str) for v in item['subclasses']) and
                        len(item['subclasses']) == len(set(item['subclasses'])), 'Invalid commit subclasses')
                require(item['worker'] is None or isinstance(item['worker'], str), 'Invalid commit worker')
                require(isinstance(item['inferredFields'], list) and
                        len(item['inferredFields']) == len(set(item['inferredFields'])) and
                        set(item['inferredFields']) <= {'classes', 'subclasses', 'worker'}, 'Invalid inferred fields')
            source = item['source']
            if 'path' in source:
                current = selection.source(source['path'], source.get('fragment'))
                require(all(current.get(k) == v for k, v in source.items() if k != 'lineStart'),
                        'Stale source traceability; use full mode: ' + item['id'])
            for link in item['links']:
                target = link['target']
                if 'path' in target:
                    identity = {key: value for key, value in target.items() if key not in ('recordId', 'lineStart')}
                    require(selection.source(target['path'], target.get('fragment')) == identity,
                            'Stale link dependency; use full mode: ' + item['id'])
                    if 'recordId' in target:
                        resolved = record_index.get(target['recordId'])
                        require(resolved is not None and resolved['source'] ==
                                {key: value for key, value in target.items() if key != 'recordId'},
                                'Broken record target: ' + target['recordId'])
                    evidence = link['evidence']
                    require(selection.source(evidence['path'], evidence.get('fragment')) ==
                            {key: value for key, value in evidence.items() if key != 'lineStart'},
                            'Stale link evidence: ' + item['id'])
                elif 'commit' in target:
                    require(git(root, 'cat-file', '-t', target['commit']).strip() == b'commit', 'Broken commit target')
    if mode == 'verify':
        require(all((output / (kind + '.json')).is_file() and
                    (output / (kind + '.json')).read_bytes() == raw for kind, raw in data.items()),
                'Generated datasets differ from full reconstruction')
        return {'verified': list(TYPES)}
    next_state = {'version': 1, 'behavior': behavior, 'inventory': config, 'revision': revision,
                  'proposal': {p: None if raw is None else digest(raw) for p, raw in selection.overlay.items()},
                  'datasets': {kind: {'sha256': digest(raw),
                                     'inputs': generator.inputs(kind),
                                     'dependencies': sorted(generator.dependencies[kind]) if kind in affected else
                                     state['datasets'][kind]['dependencies']}
                               for kind, raw in data.items()}}
    # All errors precede writes. Ignore files are disposable; a partial write is
    # detected by state hashes on the next normal invocation, requiring full recovery.
    output.mkdir(parents=True, exist_ok=True)
    require(not state_path.is_symlink() and all(not (output / (kind + '.json')).is_symlink()
                                                for kind in TYPES), 'Symlink output file')
    for kind in sorted(affected):
        file = output / (kind + '.json')
        require(not file.is_symlink(), 'Symlink output file')
        file.write_bytes(data[kind])
    state_path.write_bytes(canonical(next_state))
    return {'updated': sorted(affected), 'sha256': {kind: digest(raw) for kind, raw in data.items()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('normal', 'full', 'verify'))
    parser.add_argument('--repo', required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--revision')
    source.add_argument('--selection', type=Path, help='Declared revision/proposal JSON selection')
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--proposal', type=Path, help='Explicit path -> raw-byte SHA-256 (null means deletion)')
    parser.add_argument('--changed', action='append', default=[], help='Exact changed POSIX input path; repeatable')
    args = parser.parse_args()
    require(sys.version_info >= (3, 13), 'Python 3.13+ required')
    try:
        proposal = json.loads(args.proposal.read_bytes()) if args.proposal else None
        if args.selection:
            require(args.proposal is None, 'Selection already declares proposal content')
            selection = json.loads(args.selection.read_bytes())
            require(set(selection) == {'revision', 'proposal'}, 'Invalid declared selection')
            args.revision, proposal = selection['revision'], selection['proposal']
        print(json.dumps(generate(args.repo, args.revision, json.loads(args.inventory.read_bytes()),
                                  args.output, args.mode, args.changed, proposal), sort_keys=True))
    except (ValueError, OSError, KeyError, UnicodeError) as error:
        parser.exit(1, str(error) + '\n')


if __name__ == '__main__':
    import runtime_guard  # the core's guard, on the path set up above
    runtime_guard.enter('generate', extension_mode=runtime_guard.LOCAL)  # first: version check, GIT_* scrub, lifecycle gate
    main()
