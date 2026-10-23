"""Shared commit trailers and the existing checkpoint classification registry."""
import re
import argparse
import copy
import json
import os
from pathlib import Path


def require(ok, message):
    if not ok:
        raise ValueError(message)


def registry(profiles):
    value = profiles['commit_metadata']
    require(value['version'] == 2, 'Unsupported commit metadata registry')
    classes = value['classes']
    require(list(classes) == ['record', 'governance', 'tooling', 'runtime'],
            'Class must reuse semantic base classification vocabulary')
    entries = value['subclasses']
    tokens = [entry['value'] for entry in entries]
    require(len(tokens) == len(set(tokens)), 'Duplicate registry subclass')
    for entry in entries:
        require(re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', entry['value']),
                'Invalid registry subclass token')
        require(entry['classes'] and set(entry['classes']) <= set(classes),
                'Invalid registry subclass classes')
        require(set(entry.get('invariants', [])) <= set(profiles['impact']['invariants']),
                'Subclass must reuse existing impact invariants')
    return value


# The only keys a new checkpoint's final trailer block may hold; Worker already names the agent.
TRAILER_KEYS = ('Class', 'Subclass', 'Worker')


def parse_metadata(message, profiles, required=True, *, legacy_aggregate=False, other_trailers=False):
    """Read a final, blank-line-separated trailer paragraph; never rewrite prose.

    A required block holds only TRAILER_KEYS; `other_trailers` tolerates other keys, as commits made before the
    allowlist did, and is for reading history only."""
    reg = registry(profiles)
    lines = message.splitlines()
    require(lines and lines[0].strip(), 'Commit subject must be nonempty')
    keys = TRAILER_KEYS
    occurrences = [(i, line) for i, line in enumerate(lines)
                   if re.match(r'\s*(?:Class|Subclass|Worker)\s*:', line, re.IGNORECASE)]
    fields = {}
    if occurrences:
        end = len(lines)
        while end and not lines[end - 1].strip():
            end -= 1
        start = end - 1
        while start > 0 and lines[start - 1].strip():
            start -= 1
        require(start >= 2 and not lines[start - 1].strip(),
                'Metadata trailers require a blank line after subject/body')
        require(all(start <= i < end for i, _ in occurrences),
                'Metadata trailer block must be at the end')
        for line in lines[start:end]:
            match = re.fullmatch(r'([A-Za-z][A-Za-z0-9-]*): (\S(?:.*\S)?)', line)
            require(match, 'Malformed final trailer block')
            key, value = match.groups()
            if key.casefold() in {k.casefold() for k in keys}:
                require(key in keys, 'Metadata trailer names are case sensitive')
                require(key not in fields, 'More than one ' + key + ' trailer')
                fields[key] = value
            else:
                require(not required or other_trailers,
                        'Trailer ' + key + ' is not allowed in the final trailer block; the allowed trailers are '
                        + ', '.join(keys))
    if required:
        require('Class' in fields, 'Class trailer required')
        require('Worker' in fields, 'Worker trailer required')
    classes = []
    legacy = not required and legacy_aggregate and fields.get('Class') == 'mixed'
    for key in ('Class', 'Subclass'):
        if key in fields:
            require(re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*(?:, [a-z][a-z0-9]*(?:-[a-z0-9]+)*)*', fields[key]),
                    key + ' requires kebab-case tokens separated by comma+single-space')
    if 'Class' in fields and not legacy:
        declared = fields['Class'].split(', ')
        require(len(declared) == len(set(declared)), 'Duplicate class')
        require(set(declared) <= set(reg['classes']), 'Unknown Class')
        classes = [token for token in reg['classes'] if token in declared]
    subclasses = []
    if 'Subclass' in fields:
        subclasses = fields['Subclass'].split(', ')
        require(len(subclasses) == len(set(subclasses)), 'Duplicate subclass')
        allowed = [e['value'] for e in reg['subclasses']
                   if legacy or set(classes).intersection(e['classes'])]
        require(set(subclasses) <= set(allowed), 'Subclass invalid under selected Class')
        subclasses = [token for token in allowed if token in subclasses]
    worker = fields.get('Worker')
    if worker is not None:
        require(re.fullmatch(r'[^\[\]\s][^\[\]\r\n]*?(?: \[[^\[\]\r\n]+\])?', worker)
                and all(ord(c) >= 32 for c in worker)
                and (not worker.endswith(']') or worker[worker.index('[') + 1:-1].strip()
                     == worker[worker.index('[') + 1:-1] != ''),
                'Malformed Worker identity')
    return {'classes': classes, 'subclasses': subclasses, 'worker': worker, 'inferredFields': []}


WORKER_FIELDS = ('provider', 'product', 'model', 'configuration', 'isAI')


def worker_text(value, field):
    require(value is None or (isinstance(value, str) and value and value == value.strip()
            and '[' not in value and ']' not in value and all(ord(c) >= 32 for c in value)),
            'Invalid worker ' + field)
    return value


def worker_projection(context):
    configuration = ' '.join(value for value in (context['model'], context['configuration']) if value)
    return context['identity'] + (' [' + configuration + ']' if configuration else '')


class WorkerBootstrap:
    """One bounded pass. Adapters supply direct evidence, never search instructions."""
    def __init__(self, transaction):
        require(isinstance(transaction, str) and transaction.startswith('refs/heads/')
                and transaction.strip() == transaction, 'Full transaction branch required')
        self.transaction = transaction
        self.frozen = None

    def resolve(self, runtime=None, launcher=None, environment=None, probe=None):
        if self.frozen is not None:
            return copy.deepcopy(self.frozen)
        values = dict.fromkeys(WORKER_FIELDS)
        sources = dict.fromkeys(WORKER_FIELDS, 'unknown')

        def consume(evidence, source):
            if evidence is None:
                return
            require(isinstance(evidence, dict) and set(evidence) <= set(WORKER_FIELDS),
                    'Worker adapter accepts only provider/product/model/configuration/isAI')
            # Never mix metadata from different providers or execution products.
            for field in ('provider', 'product'):
                if values[field] is not None and evidence.get(field) is not None:
                    require(values[field] == evidence[field], 'Conflicting worker ' + field)
            for field, value in evidence.items():
                if field == 'isAI':
                    require(value is None or type(value) is bool, 'Worker isAI must be boolean')
                else:
                    worker_text(value, field)
                if values[field] is None and value is not None:
                    values[field], sources[field] = value, source

        consume(runtime, 'runtime/session')
        if any(value is None for value in values.values()):
            consume(launcher, 'launcher/config')
        # A single named environment value, no enumeration or filesystem discovery.
        if any(value is None for value in values.values()):
            raw = (os.environ if environment is None else environment).get('WORKER_CONTEXT')
            consume(json.loads(raw) if raw else None, 'environment')
        if probe is not None and values['provider'] is not None and any(
                values[field] is None for field in ('product', 'model', 'configuration')):
            consume(probe(values['provider']), 'provider-probe')  # exactly one call
        identity = ' '.join(value for value in (values['provider'], values['product']) if value)
        if not identity:
            identity = 'Unknown worker'
        # Unknown worker kind stays explicit; projection still preserves exposed text.
        self.frozen = dict(version=1, transaction=self.transaction, identity=identity,
                           sources=sources, **values)
        return copy.deepcopy(self.frozen)


def bootstrap_worker(output, transaction, runtime_file=None, launcher_file=None):
    """Create once outside the checkout; repeat calls read only the frozen document."""
    output = Path(output)
    if output.exists():
        context = json.loads(output.read_text(encoding='utf-8-sig'))
        require(context.get('transaction') == transaction, 'Worker bootstrap belongs to another transaction')
        validate_worker_context({'worker': worker_projection(context)}, context)
        return context
    def read(path):
        return json.loads(Path(path).read_text(encoding='utf-8-sig')) if path else None
    runtime = read(runtime_file)
    complete = isinstance(runtime, dict) and all(runtime.get(field) is not None for field in WORKER_FIELDS)
    context = WorkerBootstrap(transaction).resolve(runtime, None if complete else read(launcher_file))
    validate_worker_context({'worker': worker_projection(context)}, context)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(context, stream, indent=2)
        stream.write('\n')
    return context


def validate_worker_context(metadata, context):
    """Seal the worker's environment assertion; do not guess or enumerate models."""
    rich = isinstance(context, dict) and type(context.get('version')) is int and context['version'] == 1
    if rich:
        require(set(context) == {'version', 'transaction', 'identity', 'sources', *WORKER_FIELDS},
                'Invalid frozen worker context fields')
        require(isinstance(context['transaction'], str) and context['transaction'].startswith('refs/heads/'),
                'Full transaction branch required')
        require(isinstance(context['sources'], dict) and set(context['sources']) == set(WORKER_FIELDS),
                'Worker provenance required')
        for field in WORKER_FIELDS:
            require(context['sources'][field] in ('unknown', 'runtime/session', 'launcher/config',
                                                 'environment', 'provider-probe'), 'Invalid worker source')
            require((context[field] is None) == (context['sources'][field] == 'unknown'),
                    'Worker provenance must describe known/unknown values')
            if field != 'isAI':
                worker_text(context[field], field)
        require(context['isAI'] is None or type(context['isAI']) is bool, 'Worker isAI must be boolean')
        identity = ' '.join(value for value in (context['provider'], context['product']) if value) or 'Unknown worker'
        require(context['identity'] == identity, 'Worker identity must project provider/product')
        require(metadata['worker'] == worker_projection(context), 'Worker must match frozen projection exactly')
        return copy.deepcopy(context)
    require(isinstance(context, dict) and set(context) == {'identity', 'isAI', 'configuration'},
            'Explicit transaction worker context required')
    require(type(context['isAI']) is bool, 'Worker context isAI must be boolean')
    identity = context['identity']
    require(isinstance(identity, str) and identity and identity == identity.strip()
            and '[' not in identity and ']' not in identity
            and all(ord(c) >= 32 for c in identity), 'Invalid worker context identity')
    configuration = context['configuration']
    require(configuration is None or (isinstance(configuration, str) and configuration
            and configuration == configuration.strip() and '[' not in configuration
            and ']' not in configuration and all(ord(c) >= 32 for c in configuration)),
            'Worker configuration must be a known value or null for unavailable/unknown')
    worker = metadata['worker']
    require(worker is not None and worker.split(' [', 1)[0] == identity,
            'Worker must match transaction environment identity')
    if context['isAI'] and configuration is not None:
        require(worker == identity + ' [' + configuration + ']',
                'AI Worker must include the known [model/configuration] exactly')
    return dict(context)


def classify_paths(paths, policy, profiles, table=None):
    """Conservative historical reconstruction from the classification table."""
    require(table is not None, 'Policy version 4 classifies from the table')
    categories = set()
    for path in paths:
        known = table.classify(path)
        if known is None:
            return []
        categories.add(known[0])
    return [token for token in registry(profiles)['classes'] if token in categories]


def reconstruct(message, paths, policy, profiles, table=None):
    # Historical aggregate trailers are evidence of the old model, never new metadata.
    result = parse_metadata(message, profiles, required=False, legacy_aggregate=True)
    if not result['classes']:
        result['classes'] = classify_paths(paths, policy, profiles, table)
        if result['classes']:
            result['inferredFields'].append('classes')
            allowed = [e for e in registry(profiles)['subclasses']
                       if set(result['classes']).intersection(e['classes'])]
            if result['subclasses']:
                require(set(result['subclasses']) <= {e['value'] for e in allowed},
                        'Subclass invalid under reconstructed classes')
            else:
                labelled = {token for path in paths for token in table.classify(path)[1]}
                result['subclasses'] = [e['value'] for e in allowed if e['value'] in labelled]
                if result['subclasses']:
                    result['inferredFields'].append('subclasses')
        else:
            result['subclasses'] = []
    # Git author/committer and style cannot establish the execution environment.
    return result


def refine_selection(metadata, profile, policy, profiles):
    """Add declared subclass impact to path evidence; never subtract selected gates."""
    expected = [token for token in registry(profiles)['classes'] if token in profile['categories']]
    require(metadata['classes'] == expected,
            'Class must match manifest change classification: ' + ', '.join(expected))
    # A subclass is a label that must describe the manifest; it never selects a check.
    selected = set(profile['categories'])
    labelled = {token for entry in profile['artifact_classification'].values() for token in entry['subclass']}
    labels = [e['value'] for e in registry(profiles)['subclasses']
              if e['value'] in labelled and selected.intersection(e['classes'])]
    require(metadata['subclasses'] == labels,
            'Subclass must match the classification of the manifest paths: ' + (', '.join(labels) or 'none'))
    return dict(profile, commit_metadata=metadata, metadata_impact={'invariants': [], 'groups': []})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Freeze bounded worker metadata once per transaction.')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--transaction', required=True)
    parser.add_argument('--runtime', type=Path)
    parser.add_argument('--launcher', type=Path)
    args = parser.parse_args()
    context = bootstrap_worker(args.output, args.transaction, args.runtime, args.launcher)
    print(worker_projection(context))
