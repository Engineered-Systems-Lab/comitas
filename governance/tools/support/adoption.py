"""The adoption helper: run once in a new repository, after `git init` and `git add -A`, before the first commit.

A repository made from Comitas fails two checks that Comitas itself passes, and this helper repairs both
before they are committed:

1. A shell script must be committed as mode 100755. On Windows `core.filemode` is false, so `git add` stages every script
   as 100644; the helper sets the executable bit of each staged `*.sh` in the index (`git update-index --chmod=+x`).
   It never edits `core.filemode` or any Git configuration.
2. The framework cites its own commits as `template commit`; the label names the framework repository by role
   (see governance/PROJECT-GOVERNANCE.md, "The template label"). A copy has none of that history, so the project
   declares the label `template` in `project-governance/config/citations.json`: the helper writes that file, or adds
   the label to the one that exists, and stages it.

It refuses where HEAD exists, so it can never run in Comitas or in a project that is already going. Before the first
commit it is idempotent: a second run changes nothing. It writes no Git configuration and nothing outside this repository."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from governance.tools.continuity import artifact_classification as ac

ROOT = Path(__file__).resolve().parents[3]
LABEL = 'template'
REGULAR = '100644'
KEYS = {'version', 'external_repositories'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def git(root, *args, check=True):
    """Run Git in `root` with the inherited GIT_* variables removed (the launcher does it too) and return the result."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
    done = subprocess.run(['git', '-C', str(root), *args], capture_output=True, env=env)
    require(not check or done.returncode == 0,
            'git %s failed: %s' % (' '.join(args), done.stderr.decode('utf-8', 'replace').strip()))
    return done


def indexed(root):
    """(mode, path) for every entry of the index."""
    out = git(root, 'ls-files', '-s', '-z').stdout.decode('utf-8')
    rows = []
    for entry in filter(None, out.split('\0')):
        meta, path = entry.split('\t', 1)
        rows.append((meta.split()[0], path))
    return rows


def refuse_unless_first_commit(root):
    top = git(root, 'rev-parse', '--show-toplevel', check=False)
    require(top.returncode == 0 and os.path.samefile(top.stdout.decode('utf-8').strip(), root),
            '%s is not the top folder of a Git repository: run git init -b main there, then git add -A, then this helper'
            % root)
    head = git(root, 'rev-parse', '--verify', '-q', 'HEAD', check=False)
    require(head.returncode != 0,
            'HEAD exists (%s): the helper runs once, before the first commit, and never in a repository that already has one '
            '(a repository made with "Use this template" on GitHub needs no adoption step)'
            % head.stdout.decode('utf-8').strip()[:12])


def executable_scripts(root, rows):
    """Set the executable bit of every staged regular `*.sh` that lacks it; return the paths changed."""
    wrong = [path for mode, path in rows if ac.is_script(path) and mode == REGULAR]
    for start in range(0, len(wrong), 50):
        git(root, 'update-index', '--chmod=+x', '--', *wrong[start:start + 50])
    return wrong


def citation_settings(root):
    """(file, new text or None): the citation settings with the label declared; None when nothing needs to change."""
    path = root / ac.CITATIONS
    require(not path.is_symlink() and not path.is_junction(), 'Indirect file: ' + ac.CITATIONS)
    if not path.exists():
        data = {'version': 1, 'external_repositories': [LABEL]}
    else:
        try:
            data = json.loads(path.read_text(encoding='utf-8-sig'))
        except ValueError as error:
            raise ValueError('%s does not parse (%s); fix or remove it, then run the helper again' % (ac.CITATIONS, error))
        require(isinstance(data, dict) and set(data) <= KEYS and data.get('version', 1) == 1,
                '%s may hold only "version" (1) and "external_repositories"; fix it, then run the helper again' % ac.CITATIONS)
        labels = data.get('external_repositories', [])
        require(isinstance(labels, list) and all(isinstance(label, str) for label in labels),
                '%s: "external_repositories" must be a list of labels; fix it, then run the helper again' % ac.CITATIONS)
        if LABEL in labels:
            return path, None
        data['external_repositories'] = labels + [LABEL]
    return path, json.dumps(data, indent=2) + '\n'


def adopt(root):
    """Do the two repairs in the index of the repository at `root`; return a summary of what was done and what was found."""
    root = Path(root).resolve()
    refuse_unless_first_commit(root)
    rows = indexed(root)
    require(rows, 'Nothing is staged: run git add -A first, then this helper')
    changed = executable_scripts(root, rows)
    path, text = citation_settings(root)
    if text is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('utf-8'))  # bytes, so Windows never turns the LF line ends into CRLF
    git(root, 'add', '--', ac.CITATIONS)
    scripts = [p for _, p in rows if p.endswith('.sh')]
    return {'executable': changed, 'scripts': len(scripts), 'citations': ac.CITATIONS, 'label_written': text is not None}


def report(summary):
    lines = []
    if summary['executable']:
        lines.append('Set the executable bit (100755) in the index of %d shell script(s): %s'
                     % (len(summary['executable']), ', '.join(summary['executable'])))
    else:
        lines.append('Shell scripts: %d staged, none needed the executable bit.' % summary['scripts'])
    if summary['label_written']:
        lines.append('Declared the label %r in %s and staged it.' % (LABEL, summary['citations']))
    else:
        lines.append('The label %r is already declared in %s; staged it.' % (LABEL, summary['citations']))
    if not summary['executable'] and not summary['label_written']:
        lines.append('Nothing needed to change: the helper has already been run.')
    lines.append('Next: make the first commit, then initialize ' + ac.COMMIT_HISTORY
                 + ' following governance/PROJECT-GOVERNANCE.md#commit-history; do not run this helper after the first commit.')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run once in a new repository, after git init and git add -A and before the '
                                                 'first commit: set the executable bit of the staged shell scripts in the '
                                                 'index and declare the label "template" in the citation settings.')
    parser.parse_args(argv)
    try:
        print(report(adopt(ROOT)))
    except (ValueError, OSError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
