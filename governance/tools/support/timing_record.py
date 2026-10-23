"""Machine-local record of how long timed work takes: estimate before a seal, flag departures after one.

The record is an append-only JSON-lines file in the `.local/timings/` folder of the primary checkout (see local_state),
so every worktree shares it and no tracked file ever holds it. It is advisory: it never blocks a transaction and never grants anything. Durations
belong to the hardware that produced them, so each entry carries a machine token and other machines' entries
are only ever a rough, labelled guide. Standard library only.
"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import sys
import time

from governance.tools.continuity import artifact_classification as ac
from governance.tools.support import local_state

SCHEMA = 1
ENTRY_KEYS = {'v', 'at', 'subject', 'metric', 'value', 'ok', 'run', 'machine', 'context'}
SUBJECT = re.compile(r'[a-z][a-z0-9-]*:[A-Za-z0-9._-]+')
DEFAULTS = {'mode': 'local', 'window': 20, 'min_samples': 3, 'slow_factor': 1.5, 'slow_margin_seconds': 10,
            'fast_fraction': 0.4, 'fast_min_mean_seconds': 20}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def machine():
    """The hardware and runtime a duration belongs to. Equal tokens mean comparable numbers."""
    return f'{sys.platform}/cpu{os.cpu_count()}/py{platform.python_version()}'


def settings(policy):
    """The validated `timing_record` configuration, or None when it is absent or switched off."""
    given = policy.get('timing_record')
    if given is None:
        return None
    require(isinstance(given, dict) and set(given) <= set(DEFAULTS), 'Unknown timing_record setting')
    cfg = {**DEFAULTS, **given}
    require(cfg['mode'] in ('local', 'off'), 'timing_record mode must be local or off')
    if cfg['mode'] == 'off':
        return None
    for name in DEFAULTS:
        if name != 'mode':
            require(type(cfg[name]) in (int, float) and cfg[name] > 0 and math.isfinite(cfg[name]),
                    f'timing_record {name} must be a positive number')
    require(type(cfg['window']) is int and type(cfg['min_samples']) is int and 2 <= cfg['min_samples'] <= cfg['window'],
            'timing_record needs integer 2 <= min_samples <= window')
    require(cfg['fast_fraction'] < 1 < cfg['slow_factor'], 'timing_record needs fast_fraction < 1 < slow_factor')
    return cfg


def entry(subject, seconds, ok, run, token=None, context=None, now=None):
    """One timed observation in the generic, benchmark-ready shape."""
    value = {'v': SCHEMA, 'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() if now is None else now)),
             'subject': subject, 'metric': 'seconds', 'value': round(float(seconds), 6), 'ok': bool(ok), 'run': run,
             'machine': token or machine(), 'context': context or {}}
    return check_entry(value)


def check_entry(value):
    require(isinstance(value, dict) and set(value) == ENTRY_KEYS, 'Timing entry has the wrong fields')
    require(value['v'] == SCHEMA and value['metric'] == 'seconds', 'Unsupported timing entry version or metric')
    require(isinstance(value['subject'], str) and SUBJECT.fullmatch(value['subject']), 'Invalid timing subject')
    require(type(value['value']) in (int, float) and math.isfinite(value['value']) and value['value'] >= 0,
            'Invalid timing value')
    require(type(value['ok']) is bool and isinstance(value['run'], str) and value['run'] and
            isinstance(value['machine'], str) and value['machine'] and isinstance(value['context'], dict) and
            isinstance(value['at'], str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value['at']),
            'Invalid timing entry')
    return value


def parse(text):
    """Strictly parse JSON lines; a malformed line is an error, never silently skipped."""
    result = []
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip():
            try:
                result.append(check_entry(json.loads(line)))
            except (ValueError, TypeError) as error:
                raise ValueError(f'timing record line {number}: {error}') from error
    return result


class Record:
    """The timing record of the repository whose Git common directory is `common`."""

    def __init__(self, common):
        self.common = Path(common)

    @property
    def path(self):
        """Resolved on use, so a repository without a primary checkout fails where the record is read or written."""
        return local_state.timings_file(self.common)

    def entries(self):
        return parse(self.path.read_text(encoding='utf-8')) if self.path.exists() else []

    def append(self, entries):
        """Append whole lines in one write so concurrent worktrees never interleave a line."""
        if entries:
            local_state.prepare(self.common, local_state.TIMINGS)  # creates the folder; refuses when .local/ is not ignored
            data = ''.join(json.dumps(check_entry(e), sort_keys=True, ensure_ascii=True) + '\n' for e in entries)
            with open(self.path, 'a', encoding='ascii', newline='\n') as stream:
                stream.write(data)

    def merge(self, entries):
        """Add entries not already present (same run and subject); idempotent. Returns how many were added."""
        known = {(e['run'], e['subject']) for e in self.entries()}
        fresh = []
        for e in entries:
            if (e['run'], e['subject']) not in known:
                known.add((e['run'], e['subject']))
                fresh.append(e)
        self.append(fresh)
        return len(fresh)


def summarize(entries, subject, token, cfg):
    """Statistics for one subject from its recent successful runs, with an honest basis."""
    window = cfg['window']
    good = [e for e in entries if e['subject'] == subject and e['ok']]
    local = [e['value'] for e in good if e['machine'] == token][-window:]
    foreign = [e['value'] for e in good if e['machine'] != token][-window:]
    if len(local) >= cfg['min_samples']:
        basis, values = 'measured', local
    elif len(local) + len(foreign) >= cfg['min_samples']:
        basis, values = 'imported', local + foreign
    else:
        basis, values = ('thin', local + foreign) if local or foreign else ('none', [])
    return {'subject': subject, 'basis': basis, 'n': len(values), 'local': len(local), 'foreign': len(foreign),
            'mean': statistics.fmean(values) if values else None,
            'stdev': statistics.stdev(values) if len(values) > 1 else None,
            'min': min(values) if values else None, 'max': max(values) if values else None}


def estimate(entries, subjects, token, cfg):
    """Expected time for a set of subjects. No history means no estimate, never zero."""
    rows = [summarize(entries, s, token, cfg) for s in subjects]
    known = [r for r in rows if r['n']]
    gaps = [r['subject'] for r in rows if r['basis'] in ('none', 'thin')]
    if not known:
        basis = 'none'
    elif gaps:
        basis = 'partial'
    elif any(r['basis'] == 'imported' for r in rows):
        basis = 'imported'
    else:
        basis = 'measured'
    expected = sum(r['mean'] for r in known) if known else None
    high = sum(r['mean'] + 2 * (r['stdev'] or 0) for r in known) if known else None
    return {'basis': basis, 'expected_seconds': expected, 'high_seconds': high, 'gaps': gaps, 'checks': rows,
            'min_samples': cfg['min_samples']}


def span(seconds):
    seconds = int(round(seconds))
    return f'{seconds // 60}m {seconds % 60:02d}s' if seconds >= 60 else f'{seconds}s'


def describe(result):
    """One plain sentence an agent can repeat before a seal."""
    basis, expected, high = result['basis'], result['expected_seconds'], result['high_seconds']
    if basis == 'none':
        return 'No timing history on this machine for these checks, so there is no estimate.'
    gaps = ', '.join(g.split(':', 1)[1] for g in result['gaps'])
    if basis == 'partial':
        return (f'At least {span(expected)}; no usable history (fewer than {result["min_samples"]} runs) for: {gaps}. '
                'The total is a lower bound, not an estimate.')
    kind = ('measured on this machine' if basis == 'measured' else
            'from history imported from other hardware, so only a rough guide')
    return f'About {span(expected)} (up to {span(high)}), {kind}. Assumes every check runs fresh.'


def observations(entries, results, token, cfg):
    """Advisory flags for results (subject, seconds, ok) that depart from their own history."""
    flags = []
    for subject, seconds, ok in results:
        row = summarize(entries, subject, token, cfg)
        if not ok or row['basis'] != 'measured':
            continue
        mean = row['mean']
        if seconds >= cfg['slow_factor'] * mean and seconds - mean >= cfg['slow_margin_seconds']:
            kind = 'slower'
        elif mean >= cfg['fast_min_mean_seconds'] and seconds < cfg['fast_fraction'] * mean:
            kind = 'faster'
        else:
            continue
        flags.append({'subject': subject, 'kind': kind, 'seconds': round(seconds, 3), 'mean': round(mean, 3),
                      'runs': row['n']})
    return flags


def export_text(entries, label, token=None, now=None):
    require(isinstance(label, str) and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}', label), 'Use a lowercase label')
    header = {'export': SCHEMA, 'label': label, 'machine': token or machine(),
              'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() if now is None else now))}
    lines = [json.dumps(header, sort_keys=True)] + [json.dumps(e, sort_keys=True, ensure_ascii=True) for e in entries]
    return '\n'.join(lines) + '\n'


def import_entries(text):
    lines = text.splitlines()
    require(lines, 'Empty timing export')
    header = json.loads(lines[0])
    require(isinstance(header, dict) and header.get('export') == SCHEMA, 'Not a timing export')
    return parse('\n'.join(lines[1:]))


def load_settings(repo):
    policy = ac.load_policy(repo)
    cfg = settings(policy)
    require(cfg is not None, 'timing_record is not enabled in ' + ac.POLICY)
    return cfg


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path('.'))
    sub = parser.add_subparsers(dest='action', required=True)
    stats = sub.add_parser('stats', help='per-subject statistics from this machine')
    stats.add_argument('--json', action='store_true')
    export = sub.add_parser('export', help='write the record to a file you choose; nothing is staged or committed')
    export.add_argument('--output', required=True, type=Path)
    export.add_argument('--label', required=True)
    load = sub.add_parser('import', help='seed the record from an export; idempotent')
    load.add_argument('--input', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        cfg = load_settings(args.repo)
        record = Record(local_state.common_directory(args.repo))
        if args.action == 'stats':
            entries, token = record.entries(), machine()
            rows = [summarize(entries, s, token, cfg) for s in sorted({e['subject'] for e in entries})]
            if args.json:
                print(json.dumps({'machine': token, 'subjects': rows}, indent=2))
            else:
                print(f'machine {token}; window {cfg["window"]}, minimum {cfg["min_samples"]} runs')
                for r in rows:
                    spread = '' if r['stdev'] is None else f' sd {r["stdev"]:.1f}s'
                    mean = 'no successful run' if r['mean'] is None else f'mean {r["mean"]:.1f}s{spread}'
                    print(f'{r["subject"]:42} {r["basis"]:9} n={r["n"]:<3} {mean}')
        elif args.action == 'export':
            require(not args.output.exists(), 'Refusing to overwrite an existing export')
            args.output.write_text(export_text(record.entries(), args.label), encoding='ascii', newline='\n')
            print(f'exported {len(record.entries())} entries to {args.output}')
        else:
            added = record.merge(import_entries(args.input.read_text(encoding='utf-8')))
            print(f'imported {added} new entries')
        return 0
    except (ValueError, OSError, KeyError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
