"""Read-only: print the effective classification of repository paths.

Usage: python -B governance/tools/show_classification.py <path>... [--checks] [-v] [--repo <root>]

It needs no transaction, takes no lock and writes nothing; its Git calls read the tables committed at
HEAD (the framework's and, when there is one, the project's). For each path it prints the resolved row of governance/ARTIFACT-CLASSIFICATION.md and where each cell came
from, then the effective approval sensitivity: the higher of the HEAD row and the working row, as the early
approval counts it, saying when the two differ or the HEAD table cannot be read. --checks adds the
checks a change to the path would select and why; -v adds the matching row (and its parent row) exactly
as written, with their line numbers. A path no row covers prints as blocked.
"""
import argparse
from pathlib import Path
import subprocess
import sys

from governance.tools.continuity import artifact_classification as ac
import repo_paths as rp


def require(ok, message):
    if not ok:
        raise ValueError(message)


def normalize(root, raw):
    """Accept a repository-relative path in either slash style, or an absolute path inside the repository."""
    text = raw.replace('\\', '/')
    candidate = Path(text)
    if candidate.is_absolute():
        try:
            text = candidate.resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            raise ValueError('Path is outside the repository: ' + raw) from None
    while text.startswith('./'):
        text = text[2:]
    return text


def head_reader(root):
    """A reader of the files committed at HEAD: a path -> its text, or None when HEAD does not hold it."""
    def read(relative):
        present = subprocess.run(['git', '-C', str(root), 'cat-file', '-e', 'HEAD:' + relative], capture_output=True)
        if present.returncode != 0:
            return None
        result = subprocess.run(['git', '-C', str(root), 'show', 'HEAD:' + relative], capture_output=True)
        require(result.returncode == 0, 'Git cannot read ' + relative + ' at HEAD')
        return result.stdout.decode('utf-8')
    return read


def head_table(root, profiles):
    """The tables as committed at HEAD (the framework's and the project's, when HEAD has one), parsed by the shared
    loader; None when Git or the table cannot be read."""
    try:
        return ac.table_from(head_reader(root), profiles)
    except (ValueError, UnicodeError):
        return None


def explain_sensitivity(table, head, path):
    """The effective approval sensitivity line: the level and why it is not simply the working row's."""
    grades = table.sensitivity(path, head)
    if head is None:
        note = 'the HEAD table cannot be read, so the path counts as high'
    elif grades['head'] == 'none':
        note = 'no row covers it at HEAD, so it counts as high'
    elif grades['head'] != grades['candidate']:
        note = 'HEAD row %s, working row %s: the higher counts' % (grades['head'], grades['candidate'])
    else:
        note = 'HEAD and working rows agree'
    return '  %-22s%-26s(%s)' % ('effective sensitivity', grades['level'], note)


def explain_checks(resolved, policy, profiles):
    """Which checks a change to this path would select, and why (read-only explanation)."""
    if resolved.profile == ac.BLOCKED:
        return ['  checks     none: the path is blocked and cannot be prepared']
    envelope = profiles['profiles'][resolved.profile]['checks']
    invariants = resolved.invariants
    mapped = invariants is not None and ac.SYMBOL_MAP not in invariants
    narrow = resolved.profile in ac.NARROW_PROFILES
    from_profile = [c['id'] for c in envelope if not (mapped and narrow and c['id'] in ac.REPLACEABLE)]
    groups, pending, seen = [], list(invariants or []), set()
    while pending:
        name = pending.pop()
        if name in seen or name == ac.SYMBOL_MAP:
            continue
        seen.add(name)
        entry = profiles['impact']['invariants'][name]
        groups.extend(entry['groups'])
        pending.extend(entry.get('requires', []))
    lines = ['  checks     from profile ' + resolved.profile + ': ' + ', '.join(from_profile)]
    if invariants is None:
        lines.append('             the path is unmapped, so the whole profile envelope runs')
    elif ac.SYMBOL_MAP in invariants:
        lines.append('             the changed code symbols decide the rest; an unmapped symbol runs the whole envelope')
    named = [i for i in (invariants or []) if i != ac.SYMBOL_MAP]
    if named:
        extra = sorted(set(groups) - set(from_profile))
        lines.append('             from invariants ' + ', '.join(named) + ': ' + (', '.join(extra) or 'nothing more'))
    return lines


def written(row):
    return '| `' + row.path + '` | ' + ' | '.join(row.cells[c] for c in ac.COLUMNS[1:]) + ' |'


def show(root, paths, checks=False, verbose=False):
    root = Path(root)
    policy = ac.load_policy(root)
    profiles = ac.load_profiles(root)
    table = ac.load_table(root, profiles)
    require(table is not None, 'The policy declares no classification table')
    head = head_table(root, profiles)
    lines = []
    for raw in paths:
        path = normalize(root, raw)
        lines.append('Effective classification of ' + path)
        resolved = table.resolve(path)
        if resolved is None:
            lines.append('  blocked    no classification row covers this path (classification fails closed)')
            lines.append('')
            continue
        for column in ac.COLUMNS[1:]:
            source = resolved.sources[column]
            owner = 'own row' if resolved.row.kind == 'file' else 'row ' + resolved.row.path
            lines.append('  %-22s%-26s(%s)' % (column, resolved[column],
                                                owner if source == 'row' else 'inherited from ' + source))
        lines.append(explain_sensitivity(table, head, path))
        if verbose:
            label = 'own row    ' if resolved.row.kind == 'file' else 'matching row'
            lines.append('  %s line %d: %s' % (label, resolved.row.line, written(resolved.row)))
            if resolved.parent is not None:
                lines.append('  parent row line %d: %s' % (resolved.parent.line, written(resolved.parent)))
        if checks:
            lines.extend(explain_checks(resolved, policy, profiles))
        lines.append('')
    return '\n'.join(lines).rstrip('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('paths', nargs='+', help='repository-relative paths (either slash style)')
    parser.add_argument('--checks', action='store_true', help='also print the checks a change would select')
    parser.add_argument('-v', '--verbose', action='store_true', help='also print the matching row and its parent row')
    parser.add_argument('--repo', type=Path, default=rp.ROOT,
                        help='repository root (default: the checkout this script belongs to)')
    args = parser.parse_args(argv)
    try:
        print(show(args.repo, args.paths, args.checks, args.verbose))
        return 0
    except (ValueError, OSError, KeyError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
