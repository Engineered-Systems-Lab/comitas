"""The inputs Mission Control reads, assembled from the framework part, the project's optional part and the devlog setting.

Three sources, none of them authoritative for anything else:

- the framework part, `inputs.json` beside this file, ships with the extension and lists only framework paths;
- the project part, `project-governance/config/mission-control-inputs.json`, is optional and lists the project's own
  documents; when it is absent the terminal shows the framework alone;
- the devlog setting, `project-governance/config/devlog.json` read through `artifact_classification.devlog_settings`, names the
  folder of the log. When the log is enabled that folder is the devlog dataset's directory and its conventions and index
  are excluded from the log (they are documents, declared by the part that owns them); when `enabled` is false the
  devlog dataset is empty and says so.

The result is the inventory the generator takes: lists are merged as sets and sorted, so the same sources always give
the same bytes. Python 3.13+, standard library only.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
from governance.tools.continuity import artifact_classification

FRAMEWORK = 'governance/extensions/mission_control/inputs.json'
PROJECT = 'project-governance/config/mission-control-inputs.json'
KINDS = ('devlog', 'decisions', 'documents', 'rationale')
SELECTORS = ('paths', 'markdownDirectories', 'excludePaths')
# The files of the devlog folder that are documents, not log entries.
LOG_DOCUMENTS = ('CONVENTIONS.md', 'README.md')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_json(root, relative):
    return json.loads((Path(root) / relative).read_bytes())


def read_selectors(data, relative):
    """The dataset selectors of one part, checked: only known datasets, known selectors and lists of strings."""
    require(isinstance(data, dict), relative + ': must be a JSON object')
    datasets = data.get('datasets', {})
    require(isinstance(datasets, dict) and set(datasets) <= set(KINDS), relative + ': unknown dataset')
    result = {}
    for kind, spec in datasets.items():
        require(isinstance(spec, dict) and set(spec) <= set(SELECTORS), relative + ': unknown input selector in ' + kind)
        for key, value in spec.items():
            require(isinstance(value, list) and all(isinstance(item, str) for item in value),
                    relative + ': ' + kind + '.' + key + ' must be a list of paths')
        result[kind] = spec
    return result


def load(root):
    """The inventory for the repository at `root`: framework part, project part if any, and the devlog setting."""
    root = Path(root)
    framework = read_json(root, FRAMEWORK)
    require(framework.get('version') == 1, FRAMEWORK + ': unsupported version')
    parts = [read_selectors(framework, FRAMEWORK)]
    if (root / PROJECT).is_file():
        project = read_json(root, PROJECT)
        require(isinstance(project, dict) and set(project) <= {'version', 'datasets'} and project.get('version') == 1,
                PROJECT + ': must be {"version": 1, "datasets": {...}}')
        parts.append(read_selectors(project, PROJECT))
    settings = artifact_classification.devlog_settings(root)
    merged = {}
    for kind in KINDS:
        spec = {key: sorted({path for part in parts for path in part.get(kind, {}).get(key, [])}) for key in SELECTORS}
        if kind == 'devlog':
            if settings['enabled']:
                folder = settings['folder']
                spec['markdownDirectories'] = sorted(set(spec['markdownDirectories']) | {folder})
                spec['excludePaths'] = sorted(set(spec['excludePaths']) | {folder + '/' + name for name in LOG_DOCUMENTS})
            else:
                spec = {'paths': [], 'markdownDirectories': [], 'excludePaths': [], 'enabled': False}
        elif not spec['excludePaths']:
            del spec['excludePaths']
        merged[kind] = spec
    return {'version': 1, 'commitHistory': framework['commitHistory'], 'linkPolicy': framework['linkPolicy'],
            'datasets': merged}
