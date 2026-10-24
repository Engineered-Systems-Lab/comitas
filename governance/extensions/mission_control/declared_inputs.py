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
the same bytes.

The same sources also declare the display groups of the Status briefing, read by `groups`: the `group` of this
extension's own manifest first, then the `group` of the manifest of every other installed extension in folder order,
then the `groups` of the project part, each in its declared order. A group is `{"label": ..., "paths": [exact document
paths]}`. The paths of an extension's group are inputs too, so the documents it names are shown; the project's paths must
already be inputs. This module names no other extension: removing one removes its manifest and with it its group.
Python 3.13+, standard library only.
"""
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
from governance.tools.continuity import artifact_classification

EXTENSIONS = 'governance/extensions'
OWN = Path(__file__).resolve().parent.name  # the folder of this extension, the only one this module knows by name
FRAMEWORK = EXTENSIONS + '/' + OWN + '/inputs.json'
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


def read_group(value, relative):
    """One group, checked: a non-empty label and a non-empty list of exact, relative, forward-slash document paths."""
    require(isinstance(value, dict) and set(value) == {'label', 'paths'},
            relative + ': a group must be {"label": ..., "paths": [...]}')
    label, paths = value['label'], value['paths']
    require(isinstance(label, str) and label and label.strip() == label, relative + ': a group label must be a non-empty text')
    require(isinstance(paths, list) and paths and all(isinstance(path, str) for path in paths),
            relative + ': the group "' + label + '" must list its document paths')
    for path in paths:
        parts = PurePosixPath(path).parts
        require(path and '\\' not in path and ':' not in path and '//' not in path and not path.startswith('/')
                and all(part not in ('.', '..') for part in parts),
                relative + ': the path "' + path + '" in the group "' + label + '" is not an exact repository path')
    return {'label': label, 'paths': list(paths)}


def extension_groups(root):
    """The groups declared by the manifests of the installed extensions: this extension's own first, then the others by folder."""
    names = sorted((path.parent.name for path in (Path(root) / EXTENSIONS).glob('*/extension.json')),
                   key=lambda name: (name != OWN, name))
    found = []
    for name in names:
        relative = EXTENSIONS + '/' + name + '/extension.json'
        manifest = read_json(root, relative)
        if isinstance(manifest, dict) and 'group' in manifest:
            found.append(dict(read_group(manifest['group'], relative), source='the extension ' + name))
    return found


def project_groups(root):
    """The groups of the project part, in declared order; none when the file or the key is absent."""
    if not (Path(root) / PROJECT).is_file():
        return []
    value = read_json(root, PROJECT).get('groups', [])
    require(isinstance(value, list), PROJECT + ': groups must be a list')
    return [dict(read_group(group, PROJECT), source='the project') for group in value]


def is_input(path, inventory):
    """Whether the inventory selects the document at `path`: listed, or inside a declared folder and not excluded."""
    for spec in inventory['datasets'].values():
        folders = [folder.rstrip('/') + '/' for folder in spec['markdownDirectories']]
        if path in spec['paths'] or (path.endswith('.md') and path not in spec.get('excludePaths', [])
                                     and any(path.startswith(folder) for folder in folders)):
            return True
    return False


def check_groups(found, inventory=None):
    """Refuse with one message a label declared twice, a path in two groups and a project path that is not an input."""
    labels, owners = {}, {}
    for group in found:
        label = group['label']
        if label in labels:
            raise ValueError('Mission Control groups: the label "' + label + '" is declared by ' + labels[label] + ' and by '
                             + group['source'] + '; every group needs its own label')
        labels[label] = group['source']
        for path in group['paths']:
            if path in owners:
                raise ValueError('Mission Control groups: the path "' + path + '" is in the group "' + owners[path]
                                 + '" and in the group "' + label + '"; a document belongs to one group')
            owners[path] = label
            if inventory is not None and group['source'] == 'the project' and not is_input(path, inventory):
                raise ValueError('Mission Control groups: the path "' + path + '" of the group "' + label
                                 + '" is not among the inputs of Mission Control')
    return found


def groups(root, inventory=None):
    """The display groups, checked, each {"label", "paths", "source"}; the briefing's fallback group is its own."""
    return check_groups(extension_groups(root) + project_groups(root), inventory)


def load(root):
    """The inventory for the repository at `root`: framework part, extension and project parts if any, and the devlog setting."""
    root = Path(root)
    framework = read_json(root, FRAMEWORK)
    require(framework.get('version') == 1, FRAMEWORK + ': unsupported version')
    parts = [read_selectors(framework, FRAMEWORK)]
    parts.append({'documents': {'paths': sorted({path for group in extension_groups(root) for path in group['paths']})}})
    if (root / PROJECT).is_file():
        project = read_json(root, PROJECT)
        require(isinstance(project, dict) and set(project) <= {'version', 'datasets', 'groups'} and project.get('version') == 1,
                PROJECT + ': must be {"version": 1, "datasets": {...}, "groups": [...]}')
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
    inventory = {'version': 1, 'commitHistory': framework['commitHistory'], 'linkPolicy': framework['linkPolicy'],
                 'datasets': merged}
    groups(root, inventory)  # a refused group stops the refresh with its one message, like a refused input
    return inventory
