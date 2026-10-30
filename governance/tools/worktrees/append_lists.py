"""Append-only lists (G060): paths whose pure appends do not conflict between missions.

A path declared in `governance/config/append-lists.json` (and the project's own file) is a list that missions only add
to. When the carry or the go carry meets a textual conflict on such paths only, this module proves from the two diffs that
each side only inserted lines inside a declared region, builds the merged file itself and returns it. The proof is a
property of the diffs and never a claim; the diff, message, selection and check gates of the carry still decide. The
declaration is read from main, never from the candidate. Standard library only."""
import json
import os
import re
import subprocess
import tempfile

FRAMEWORK = 'governance/config/append-lists.json'
PROJECT = 'project-governance/config/append-lists.json'
ORDERS = ('landing', 'newest-first')
TOKEN = 'append-only'   # marks a path claim whose change is a proved append; it is not a subclass
HUNK = re.compile(rb'^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')
LINES = re.compile(rb'[^\n]*\n|[^\n]+')


class NotAppend(ValueError):
    """The change is not a pure append inside a declared region, so the ordinary conflict stands."""


def split(data):
    return LINES.findall(data)


def parse(raw, source):
    """The declared lists of one file as {path: {'regions': [...], 'order': ...}}; ValueError names the first fault."""
    try:
        document = json.loads(raw.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError('%s is not valid JSON: %s' % (source, error))
    if not isinstance(document, dict) or document.get('version') != 1 or set(document) != {'version', 'lists'} \
            or not isinstance(document['lists'], list):
        raise ValueError('%s must hold exactly "version": 1 and a "lists" array' % source)
    found = {}
    for entry in document['lists']:
        if not isinstance(entry, dict) or set(entry) != {'path', 'regions', 'order'}:
            raise ValueError('%s: each list needs exactly path, regions and order' % source)
        path, regions, order = entry['path'], entry['regions'], entry['order']
        if not isinstance(path, str) or not path or path.startswith('/') or '\\' in path or '..' in path.split('/'):
            raise ValueError('%s: a list path must be a relative path with forward slashes' % source)
        if path in found:
            raise ValueError('%s: %s is declared twice' % (source, path))
        if order not in ORDERS:
            raise ValueError('%s: order must be one of %s' % (source, ', '.join(ORDERS)))
        if not isinstance(regions, list) or not regions:
            raise ValueError('%s: %s needs at least one region' % (source, path))
        for region in regions:
            if not isinstance(region, dict):
                raise ValueError('%s: a region must be an object' % source)
            if region == {'eof': True}:
                continue
            if not set(region) <= {'start', 'end', 'skip'} or not region:
                raise ValueError('%s: a region is {"eof": true} or holds start, end and skip' % source)
            for key in ('start', 'end'):
                if key in region:
                    if not isinstance(region[key], str):
                        raise ValueError('%s: region %s must be a regular expression string' % (source, key))
                    try:
                        re.compile(region[key])
                    except re.error as error:
                        raise ValueError('%s: region %s is not a valid expression: %s' % (source, key, error))
            if not isinstance(region.get('skip', 0), int) or isinstance(region.get('skip', 0), bool) or region.get('skip', 0) < 0:
                raise ValueError('%s: skip must be a non-negative integer' % source)
            if 'skip' in region and 'start' not in region:
                raise ValueError('%s: skip needs a start' % source)
        found[path] = {'regions': regions, 'order': order}
    return found


def declarations(read):
    """The framework's and the project's lists, as read through read(path) -> bytes or None. A project path that repeats a
    framework path is a fault. Any fault raises ValueError, and callers then declare nothing."""
    lists = {}
    for path in (FRAMEWORK, PROJECT):
        raw = read(path)
        if raw is None:
            continue
        for name, spec in parse(raw, path).items():
            if name in lists:
                raise ValueError('%s repeats %s from the framework file' % (path, name))
            lists[name] = spec
    return lists


def allowed(lines, regions):
    """Spans (first, last) of insertion positions; position p means after the first p lines of the base."""
    texts = [line.rstrip(b'\r\n').decode('utf-8', 'replace') for line in lines]
    spans = []
    for region in regions:
        if region.get('eof'):
            spans.append((len(lines), len(lines)))
            continue
        first = 0
        if 'start' in region:
            index = next((i for i, t in enumerate(texts) if re.search(region['start'], t)), None)
            if index is None:
                continue
            first = index + 1 + region.get('skip', 0)
        last = len(lines)
        if 'end' in region:
            index = next((i for i in range(first, len(texts)) if re.search(region['end'], texts[i])), None)
            if index is not None:
                last = index
        if first <= last:
            spans.append((first, last))
    return spans


def insertions(base, side):
    """{position: added lines} of side against base when every hunk is a pure insertion; NotAppend otherwise."""
    with tempfile.TemporaryDirectory(prefix='append-') as folder:
        paths = []
        for name, data in (('a', base), ('b', side)):
            paths.append(os.path.join(folder, name))
            with open(paths[-1], 'wb') as handle:
                handle.write(data)
        result = subprocess.run(['git', '--no-pager', 'diff', '--no-index', '--no-ext-diff', '--no-textconv', '--no-renames',
                                 '-U0', '--', 'a', 'b'], cwd=folder, capture_output=True)
    if result.returncode not in (0, 1):
        raise NotAppend('git diff failed: ' + result.stderr.decode('utf-8', 'replace'))
    found, position = {}, None
    for line in result.stdout.splitlines(keepends=True):
        header = HUNK.match(line)
        if header:
            removed = 1 if header.group(2) is None else int(header.group(2))
            if removed:
                raise NotAppend('it removes or changes an existing line')
            position = int(header.group(1))
            found[position] = []
        elif position is not None:
            if line.startswith(b'\\'):
                raise NotAppend('a file without a final newline is not an append')
            if line.startswith(b'+'):
                found[position].append(line[1:])
            else:
                raise NotAppend('it removes or changes an existing line')
    return found


def check(base, side, spec):
    """The insertions of side against base, proved to lie inside the declared regions."""
    found = insertions(base, side)
    spans = allowed(split(base), spec['regions'])
    for position in found:
        if not any(first <= position <= last for first, last in spans):
            raise NotAppend('an insertion lies outside the declared region')
    return found


def proved_append(base, side, spec):
    """True when side only adds lines to base, inside the declared regions, and adds at least one."""
    try:
        return bool(check(base, side, spec))
    except NotAppend:
        return False


def flat(found):
    return b''.join(line for position in sorted(found) for line in found[position])


def merge(base, ours, theirs, spec):
    """base with the insertions of both sides, whole blocks, at the same position in the declared order.

    The lines that come out must be the lines the candidate approved: Git aligns repeated lines (blank lines, repeated
    text) as it likes, so the merge is accepted only when the diff of the merged file against ours adds exactly the
    candidate's lines. When the declared order would not, the other order is tried; when neither does, the conflict stands."""
    mine, other = check(base, ours, spec), check(base, theirs, spec)
    lines = split(base)
    approved = flat(other)
    orders = [(mine, other), (other, mine)]
    if spec['order'] != 'landing':
        orders.reverse()
    for first, second in orders:
        out = []
        for position in range(len(lines) + 1):
            for side in (first, second):
                out.extend(side.get(position, []))
            if position < len(lines):
                out.append(lines[position])
        merged = b''.join(out)
        try:
            if flat(insertions(ours, merged)) == approved:
                return merged
        except NotAppend:
            continue
    raise NotAppend('the merged file would not add the lines that were approved')


ESCAPES = {'a': 7, 'b': 8, 't': 9, 'n': 10, 'v': 11, 'f': 12, 'r': 13, '"': 34, chr(92): 92}


def unquote(name):
    """The real path of a name Git printed with its default C-style quoting: octal bytes for non-ASCII, and escapes."""
    if not (len(name) >= 2 and name.startswith('"') and name.endswith('"')):
        return name
    body, out, index = name[1:-1], bytearray(), 0
    while index < len(body):
        char = body[index]
        if char != chr(92) or index + 1 >= len(body):
            out.extend(char.encode('utf-8'))
            index += 1
        elif body[index + 1] in ESCAPES:
            out.append(ESCAPES[body[index + 1]])
            index += 2
        elif re.fullmatch(r'[0-7]{3}', body[index + 1:index + 4]):
            out.append(int(body[index + 1:index + 4], 8))
            index += 4
        else:
            return name
    return out.decode('utf-8', 'replace')


def conflicts(output):
    """Names listed by `git merge-tree --write-tree --name-only` and whether every conflict is a content conflict."""
    lines = output.decode('utf-8', 'replace').splitlines()
    names, index = [], 1
    while index < len(lines) and lines[index]:
        names.append(unquote(lines[index]))
        index += 1
    messages = [l for l in lines[index + 1:] if l.startswith('CONFLICT')]
    return names, all(m.startswith('CONFLICT (content)') for m in messages)


def resolve(git, output, base, ours, theirs, lists, environ=None):
    """The tree of a merge whose conflicts are all appends to declared lists, or None when any is not.

    git(*args, data=None) runs Git in the mission's repository; base, ours and theirs are commits. The tree git wrote for the
    conflicted merge gets each conflicted file replaced by the merged bytes, in a throwaway index."""
    names, content = conflicts(output)
    if not names or not content or any(n not in lists for n in names):
        return None, []
    merged = {}
    for name in names:
        try:
            blobs = [git('cat-file', 'blob', commit + ':' + name) for commit in (base, ours, theirs)]
            merged[name] = merge(blobs[0], blobs[1], blobs[2], lists[name])
        except NotAppend:
            return None, []
        except Exception:
            return None, []
    tree = output.decode('utf-8', 'replace').splitlines()[0].strip()
    with tempfile.TemporaryDirectory(prefix='append-index-') as folder:
        env = dict(environ if environ is not None else os.environ, GIT_INDEX_FILE=os.path.join(folder, 'index'))
        git('read-tree', tree, env=env)
        for name, data in sorted(merged.items()):
            row = git('ls-tree', '-z', tree, '--', name).decode('utf-8', 'replace').strip('\0')
            mode = row.split(' ', 1)[0] if row else '100644'
            if mode not in ('100644', '100755'):
                return None, []
            blob = git('hash-object', '-w', '--stdin', data=data).decode().strip()
            git('update-index', '--cacheinfo', mode + ',' + blob + ',' + name, env=env)
        return git('write-tree', env=env).decode().strip(), sorted(merged)


def flagged(lists, paths, head, working):
    """The paths whose change from head(path) to working(path) is a proved append to a declared list."""
    found = []
    for path in paths:
        spec = lists.get(path)
        if spec is None:
            continue
        before, after = head(path), working(path)
        if before is not None and after is not None and proved_append(before, after, spec):
            found.append(path)
    return found
