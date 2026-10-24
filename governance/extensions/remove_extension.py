"""Remove an optional extension (or all of them) from a copy of the framework, leaving the core consistent.

    python -B governance/extensions/remove_extension.py <name> [--root DIR]
    python -B governance/extensions/remove_extension.py --all [--root DIR]

The rule behind it: the core never depends on an extension, so removing one is a list of deletions, never a refactor.
Each extension describes what it owns in `governance/extensions/<name>/extension.json`; this script reads that file and
does the mechanical part of the removal list in `governance/extensions/README.md`:

- deletes the extension's folder, its test folder and the project files it names;
- deletes the rows of the artifact classification table, the golden entries, the policy commands and the profile
  invariants that belong to it, and the commit subclasses it declared, and strips its invariant and subclass names from
  the rows of the core that carry them;
- turns every Markdown link that points into the removed folders into plain text, so no link breaks;
- fails, naming each line, when a row, an entry or a name of the extension is still in a configuration file.

With `--all` it also removes the extension layer itself: `governance/extensions/` and `governance/tests/extensions/`.
It does not edit prose. It prints the lines of the documents that still name the extension, for the maintainer to edit.
Python 3.13+, standard library only; it reads and writes files only, never runs Git.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys

EXTENSIONS = 'governance/extensions'
EXTENSION_TESTS = 'governance/tests/extensions'
TABLE = 'governance/ARTIFACT-CLASSIFICATION.md'
GOLDEN = 'governance/config/artifact-classification-golden.json'
POLICY = 'governance/config/checkpoint-policy.json'
PROFILES = 'governance/config/checkpoint-profiles.json'
CONFIGURATION = (TABLE, GOLDEN, POLICY, PROFILES)
SKIPPED_FOLDERS = {'.git', 'tmp', 'node_modules', '__pycache__', '.local'}
LINK = re.compile(r'\[([^\]]*)\]\(([^)\s]+)\)')


def fail(message):
    raise SystemExit('FAIL: ' + message)


def read(root, relative):
    return (root / relative).read_text(encoding='utf-8')


def write(root, relative, text):
    (root / relative).write_text(text, encoding='utf-8', newline='\n')


def load_manifest(root, name):
    path = root / EXTENSIONS / name / 'extension.json'
    if not path.is_file():
        fail('no extension named ' + name + ' (' + str(path.relative_to(root).as_posix()) + ' is missing)')
    data = json.loads(path.read_text(encoding='utf-8'))
    keys = {'version', 'name', 'folder', 'tests', 'policy_commands', 'invariants', 'subclasses', 'project_files',
            'markers', 'prose'}
    if not isinstance(data, dict) or not keys <= set(data) <= keys | {'group'} or data['version'] != 1 or data['name'] != name:
        fail(path.name + ' of ' + name + ' must be a version 1 object with exactly the keys ' + ', '.join(sorted(keys))
             + ' and, optionally, group')
    group = data.get('group', {'label': '-', 'paths': []})
    if not isinstance(group, dict) or set(group) != {'label', 'paths'} or not isinstance(group['label'], str) \
            or not isinstance(group['paths'], list) or not all(isinstance(item, str) for item in group['paths']):
        fail(name + ': group must be {"label": text, "paths": [paths]}')
    for key in keys - {'version', 'name', 'folder', 'tests'}:
        if not isinstance(data[key], list) or not all(isinstance(item, str) for item in data[key]):
            fail(name + ': ' + key + ' must be a list of strings')
    for key in ('folder', 'tests'):
        if not isinstance(data[key], str) or not data[key]:
            fail(name + ': ' + key + ' must be a path')
    return data


def strip_tokens(cell, tokens):
    """A list cell without the tokens; a list that becomes empty is the explicit no-value hyphen."""
    items = [item.strip() for item in cell.split(',') if item.strip() and item.strip() != '-']
    kept = [item for item in items if item not in tokens]
    return ', '.join(kept) if kept else '-'


def edit_table(text, prefixes, invariants, subclasses):
    """Drop the rows under the prefixes; strip the extension's invariant and subclass names from the rows that remain."""
    lines = []
    for line in text.split('\n'):
        if line.startswith('| `'):
            cells = [cell.strip() for cell in line.strip()[1:-1].split('|')]
            path = cells[0].strip('`')
            if any(path == prefix.rstrip('/') or path.startswith(prefix.rstrip('/') + '/') or
                   path.startswith(prefix.rstrip('/') + '*') for prefix in prefixes):
                continue
            changed = False
            # The table has nine cells (Invariants sixth, Subclass seventh); the historical table has three (Subclass third).
            for index, tokens in (((5, invariants), (6, subclasses)) if len(cells) == 9 else
                                  ((2, subclasses),) if len(cells) == 3 else ()):
                if cells[index] not in ('', '-') and set(item.strip() for item in cells[index].split(',')) & set(tokens):
                    cells[index] = strip_tokens(cells[index], tokens)
                    changed = True
            if changed:
                line = '| ' + ' | '.join(cells) + ' |'
        lines.append(line)
    return '\n'.join(lines)


def edit_golden(data, prefixes, invariants, subclasses):
    paths = {}
    for path, entry in data['paths'].items():
        if any(path.startswith(prefix.rstrip('/') + '/') for prefix in prefixes):
            continue
        if isinstance(entry.get('invariants'), list):
            kept = [item for item in entry['invariants'] if item not in invariants]
            entry['invariants'] = kept or None
        if isinstance(entry.get('subclass'), list):
            entry['subclass'] = [item for item in entry['subclass'] if item not in subclasses]
        paths[path] = entry
    data['paths'] = paths
    return data


def edit_policy(data, commands):
    for command in commands:
        data['commands'].pop(command, None)
    if 'required_checks' in data:
        data['required_checks'] = [check for check in data['required_checks'] if check not in commands]
    return data


def edit_profiles(data, commands, invariants, subclasses):
    table = data['impact']['invariants']
    for name in invariants:
        table.pop(name, None)
    for entry in table.values():
        for key, removed in (('groups', set(commands)), ('requires', set(invariants))):
            if key in entry:
                entry[key] = [item for item in entry[key] if item not in removed]
    metadata = data['commit_metadata']
    metadata['subclasses'] = [entry for entry in metadata['subclasses'] if entry['value'] not in subclasses]
    return data


def unlink_documents(root, prefixes):
    """Replace every Markdown link into a removed folder with its text, in every Markdown file that stays."""
    count = 0
    for path in sorted(root.rglob('*.md')):
        relative = path.relative_to(root)
        if SKIPPED_FOLDERS & set(relative.parts) or any(relative.as_posix().startswith(p.rstrip('/') + '/') for p in prefixes):
            continue
        text = path.read_text(encoding='utf-8')

        def replace(match):
            nonlocal count
            target = match.group(2).split('#', 1)[0]
            if not target or re.match(r'[a-z][a-z0-9+.-]*:', target):
                return match.group(0)
            resolved = PurePosixPath(*relative.parent.parts, target)
            parts = []
            for part in resolved.parts:
                if part == '..':
                    if parts:
                        parts.pop()
                elif part != '.':
                    parts.append(part)
            normal = '/'.join(parts)
            if any(normal == p.rstrip('/') or normal.startswith(p.rstrip('/') + '/') for p in prefixes):
                count += 1
                return match.group(1)
            return match.group(0)

        updated = LINK.sub(replace, text)
        if updated != text:
            path.write_text(updated, encoding='utf-8', newline='\n')
    return count


def leftovers(root, markers):
    """The lines of the configuration files that still carry a marker of a removed extension."""
    found = []
    for relative in CONFIGURATION:
        for number, line in enumerate(read(root, relative).split('\n'), 1):
            if any(marker in line for marker in markers):
                found.append(relative + ':' + str(number) + ': ' + line.strip()[:120])
    return found


def mentions(root, words):
    """Lines of Markdown files that still name a removed extension in prose (for the maintainer to edit by hand)."""
    found = []
    for path in sorted(root.rglob('*.md')):
        relative = path.relative_to(root)
        if SKIPPED_FOLDERS & set(relative.parts):
            continue
        for number, line in enumerate(path.read_text(encoding='utf-8').split('\n'), 1):
            if any(word in line for word in words):
                found.append(relative.as_posix() + ':' + str(number))
    return found


def remove(root, names, everything):
    manifests = [load_manifest(root, name) for name in names]
    prefixes = [item for manifest in manifests for item in (manifest['folder'], manifest['tests'])]
    if everything:
        prefixes += [EXTENSIONS, EXTENSION_TESTS]
    commands = [c for m in manifests for c in m['policy_commands']]
    invariants = [i for m in manifests for i in m['invariants']]
    subclasses = [s for m in manifests for s in m['subclasses']]
    markers = [x for m in manifests for x in m['markers']] + prefixes
    prose = [x for m in manifests for x in m['prose']]
    write(root, TABLE, edit_table(read(root, TABLE), prefixes, invariants, subclasses))
    write(root, GOLDEN, json.dumps(edit_golden(json.loads(read(root, GOLDEN)), prefixes, invariants, subclasses),
                                   indent=2, ensure_ascii=False) + '\n')
    write(root, POLICY, json.dumps(edit_policy(json.loads(read(root, POLICY)), commands), indent=2, ensure_ascii=False) + '\n')
    write(root, PROFILES, json.dumps(edit_profiles(json.loads(read(root, PROFILES)), commands, invariants, subclasses),
                                     indent=2, ensure_ascii=False) + '\n')
    links = unlink_documents(root, prefixes)
    for manifest in manifests:
        for relative in manifest['project_files']:
            (root / relative).unlink(missing_ok=True)
            parent = (root / relative).parent
            while parent != root and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
    for prefix in prefixes:
        shutil.rmtree(root / prefix, ignore_errors=False) if (root / prefix).is_dir() else None
    left = leftovers(root, markers)
    if left:
        fail('rows or names of the removed extension remain in the configuration:\n  ' + '\n  '.join(left))
    print('removed: ' + ', '.join(names) + (' and the extension layer' if everything else ''))
    print('links turned into text: ' + str(links))
    by_hand = mentions(root, prose + [m for m in markers if '/' not in m])
    if by_hand:
        print('prose that still names it (edit by hand): ' + ', '.join(by_hand))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('name', nargs='?', help='the extension to remove')
    parser.add_argument('--all', action='store_true', help='remove every extension and the extension layer')
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2], help='the repository root')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.all == bool(args.name):
        parser.error('give one extension name, or --all')
    if args.all:
        names = sorted(path.parent.name for path in (root / EXTENSIONS).glob('*/extension.json'))
    else:
        names = [args.name]
    remove(root, names, args.all)
    return 0


if __name__ == '__main__':
    sys.exit(main())
