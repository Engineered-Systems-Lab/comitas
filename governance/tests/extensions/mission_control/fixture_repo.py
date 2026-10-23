"""A small disposable repository for the terminal's checks, so they never depend on this project's own records.

The terminal's Node tests need the five generated datasets, and the template ships with no decisions history, no
log entries and no commit metadata worth testing against. This builds a synthetic repository with every record
type the tests exercise (a registry decision, a joint rationale, documents with a recorded status, log entries in
both date formats, and commits with and without explicit metadata) and a declared-input inventory for it. The commit
metadata inputs (policy, profiles and classification table) are copied from this repository, as the generator
reads them from the repository it describes.
"""
import json
from pathlib import Path
import subprocess

FILES = {
    'governance/DECISIONS.md': (
        '# Decisions\n\n| ID | Date | Status | Decision |\n| --- | --- | --- | --- |\n'
        '| G001 | 2026-09-30 | Accepted | The oldest fixture decision. |\n'
        '| G016 | 2026-10-01 | Accepted | Fixture decision, first part of a joint explanation. |\n'
        '| G017 | 2026-10-01 | Accepted | Fixture decision, second part of a joint explanation. |\n'
        '| G018 | 2026-10-02 | Accepted; design frozen | Fixture decision with a recorded status. |\n'),
    'governance/RATIONALE.md': (
        '# Rationale\n\nStatus: Explanatory / non-normative\n\nHistorical context, not approval.\n\n'
        '## Evidence audit\n\nMissing means not documented.\n\n'
        '| Decision | Rationale status | Finding / reading location |\n| --- | --- | --- |\n'
        '| G016 | explicit | See the joint explanation. |\n| G017 | explicit | See the joint explanation. |\n'
        '| G001 | missing | No reason recorded. |\n| G018 | missing | No reason recorded. |\n\n'
        '## Joint explanation: G016, G017\n\nOne explanation of two decisions, never split.\n\n'
        '## Unnumbered context\n\nNo decision ID is assigned here.\n'),
    'docs/guide.md': '# Guide\n\nStatus: Prepared; not yet accepted.\n\n[Target](target.md#target)\n',
    'docs/target.md': '# Target\n\nStatus: Accepted\n\nComplete target content.\n',
    'docs/PROGRESS.md': '# Progress\n\nStatus: Template fixture.\n',
    'governance/extensions/mission_control/README.md': '# Mission Control generator v1\n\nStatus: Fixture stand-in for the generator README.\n',
    'devlog/2026-10-02T0843Z-event.md': (
        '# Operational log\n\nStatus: Historical / non-normative\nDate: 2026-10-02T08:43Z\nSubject: An operational event\n\n'
        'Contemporary uncertainty preserved.\n'),
    'devlog/2026-10-03T0001Z-second-event.md': (
        '# Second entry\n\nStatus: Historical / non-normative\nDate: 2026-10-03T00:01Z\nSubject: A second event\n\n'
        'The log folder holds one entry per file.\n'),
}

INVENTORY = {
    'version': 1, 'commitHistory': 'reachable-from-declared-revision', 'linkPolicy': 'explicit-markdown-and-checkpoint',
    'datasets': {
        'decisions': {'paths': ['governance/DECISIONS.md'], 'markdownDirectories': []},
        'rationale': {'paths': ['governance/RATIONALE.md'], 'markdownDirectories': []},
        'documents': {'paths': ['docs/PROGRESS.md', 'docs/guide.md', 'docs/target.md',
                                'governance/extensions/mission_control/README.md'], 'markdownDirectories': []},
        'devlog': {'paths': [], 'markdownDirectories': ['devlog']},
    },
}

# (path, message): commits that follow the base commit; the metadata trailers are explicit where the message has them.
COMMITS = [
    ('governance/extensions/mission_control/generate.py', 'Fixture tooling change\n\nClass: tooling\nSubclass: mission-control\nWorker: Synthetic worker'),
    ('docs/guide.md', 'Fixture guide change without metadata'),
]


def git(root, *args):
    done = subprocess.run(['git', '-C', str(root), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                           *args], capture_output=True, text=True)
    if done.returncode:
        raise RuntimeError('git ' + ' '.join(args) + ': ' + done.stderr)
    return done.stdout.strip()


def build(root, repository):
    """Create the fixture repository at `root`; return (head sha, inventory dict). `repository` is this checkout."""
    root, repository = Path(root), Path(repository)
    root.mkdir(parents=True, exist_ok=True)
    git(root, 'init', '-q', '-b', 'fixture')
    inputs = ['governance/config/checkpoint-policy.json', 'governance/config/checkpoint-profiles.json', 'governance/ARTIFACT-CLASSIFICATION.md',
              'project-governance/ARTIFACT-CLASSIFICATION.md', 'project-governance/config/checkpoint-policy.json',
              'project-governance/config/checkpoint-profiles.json']
    files = dict(FILES)
    files['governance/extensions/mission_control/generate.py'] = '# fixture stand-in for a tooling path\n'
    for name in inputs:
        if (repository / name).is_file():  # the project's files are optional
            files[name] = (repository / name).read_text(encoding='utf-8')
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')
    git(root, 'add', '--all')
    git(root, 'commit', '-q', '-m', 'Fixture base')
    for name, message in COMMITS:
        path = root / name
        path.write_text(path.read_text(encoding='utf-8') + '\nChanged by the fixture.\n', encoding='utf-8', newline='\n')
        git(root, 'add', '--', name)
        git(root, 'commit', '-q', '-m', message)
    return git(root, 'rev-parse', 'HEAD'), json.loads(json.dumps(INVENTORY))
