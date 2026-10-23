"""Refresh the local Mission Control datasets; run it as `python -B governance/extensions/mission_control/data_launcher.py`."""
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

MC = 'governance/extensions/mission_control'
DATASETS = ('devlog', 'commits', 'decisions', 'documents', 'rationale')
# The refresh is ONE current copy in the primary checkout's ignored .local folder, found by the local job
# helper like the coordination records: `data/` holds the five datasets and is replaced on every refresh, `meta.json`
# says when and from which revision it was generated. No history, no retention, nothing to clean up. The datasets
# are never committed. `data/` also holds the generator's dependency state, which lets the next refresh be incremental.
LOCAL = '.local/mission-control'
LOCAL_DATA = 'data'
LOCAL_META = 'meta.json'
STATE = 'state.json'
# One incremental command carries its changed paths on the command line; a longer list means a full rebuild.
MAX_CHANGED = 200


def canonical_selection(revision):
    return ('{\n  "proposal": {},\n  "revision": "' + revision + '"\n}\n').encode('ascii')


def git(root, *args, accepted=(0,)):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode not in accepted:
        raise ValueError('Git history inspection failed: ' + result.stderr.decode('utf-8', 'replace').strip())
    return result


class Fallback(ValueError):
    """The incremental update cannot run; the reason is shown with the full rebuild that replaces it."""


def generate(root, scratch, selection, mode, changed=(), quiet=False):
    # The inventory is assembled from the framework part, the project's optional part and the devlog setting.
    inventory = scratch / 'inventory.json'
    inventory.write_bytes(mission_module('generate').canonical(mission_module('declared_inputs').load(root)))
    # The generator runs under the interpreter that runs this launcher.
    result = subprocess.run([sys.executable, '-B', MC + '/generate.py', mode, '--repo', '.',
                             '--selection', str(selection), '--inventory', str(inventory),
                             '--output', str(scratch / 'out'),
                             *(arg for path in changed for arg in ('--changed', path))], cwd=root, capture_output=True)
    (scratch / (mode + '.log')).write_bytes(result.stdout + result.stderr)
    if result.returncode:
        text = (result.stdout + result.stderr).decode('utf-8', 'replace')
        lines = [line for line in text.splitlines() if line.strip()]
        if quiet:
            raise Fallback(lines[-1] if lines else 'no output')
        sys.stderr.write(text)
        raise ValueError(f'generator {mode} failed; live data untouched')
    return json.loads(result.stdout.decode('utf-8').splitlines()[-1])


def mission_module(name):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        return importlib.import_module(name)
    finally:
        sys.path.pop(0)


def mission_generator():
    return mission_module('generate')


def incremental_plan(root, snapshot, head):
    """The input paths changed since the last local refresh, or Fallback saying why an incremental update cannot run.

    The local copy must carry its dependency state and still match it, the generator code and the inventory must
    be the ones that built it, and the revision it describes must be an ancestor of HEAD. The changed paths come
    from Git between that revision and HEAD, reduced to the paths the generator tracks: the generator refuses any
    other path and verifies the whole set against the two trees itself.
    """
    generate = mission_generator()
    folder = snapshot['folder']
    damaged = Fallback('the local dependency state is missing or damaged')
    try:
        state = json.loads((folder / STATE).read_bytes())
        datasets = state['datasets']
        dependencies = {path for entry in datasets.values() for path in entry['dependencies']}
        intact = (state['version'] == 1 and state['proposal'] == {} and set(datasets) == set(generate.TYPES) and
                  all(generate.digest((folder / (kind + '.json')).read_bytes()) == datasets[kind]['sha256']
                      for kind in generate.TYPES))
        inventory = mission_module('declared_inputs').load(root)
        revision = state['revision']
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        raise damaged from error
    if not intact:
        raise damaged
    if state['revision'] != snapshot['revision']:
        raise Fallback('the dependency state and the meta file describe different revisions')
    if state.get('behavior') != generate.behavior_digest() or state.get('inventory') != generate.inventory_digest(inventory):
        raise Fallback('the generator or the inventory changed since the last refresh')
    try:
        ancestor = git(root, 'merge-base', '--is-ancestor', revision, head, accepted=(0, 1)).returncode == 0
    except ValueError:
        ancestor = False
    if not ancestor:
        raise Fallback('the last refreshed revision ' + revision[:7] + ' is not an ancestor of HEAD')
    diff = git(root, 'diff', '--name-only', '-z', '--no-renames', revision, head).stdout
    declared = {path for spec in inventory['datasets'].values() for path in spec['paths']}
    directories = tuple(directory + '/' for spec in inventory['datasets'].values() for directory in spec['markdownDirectories'])
    excluded = {path for spec in inventory['datasets'].values() for path in spec.get('excludePaths', [])}
    changed = sorted(path for path in {p.decode('utf-8', 'replace') for p in diff.split(b'\0') if p}
                     if path in dependencies or path in declared or
                     (path.endswith('.md') and path.startswith(directories) and path not in excluded))
    if len(changed) > MAX_CHANGED:
        raise Fallback(f'{len(changed)} changed input paths is more than one incremental command carries')
    return changed


def local_base(root):
    """<primary checkout>/.local/mission-control; the helper finds the primary, never a fixed path."""
    entries = [str(Path(root).resolve()), str(Path(root).resolve() / 'governance' / 'tools')]
    sys.path[:0] = entries
    try:
        from governance.tools.coordination import local_jobs
    finally:
        for entry in entries:
            sys.path.remove(entry)
    primary = local_jobs.roots(root)[1]
    base = primary / LOCAL
    for part in (primary / '.local', base):
        if part.is_symlink() or part.is_junction() or (part.exists() and not part.is_dir()):
            raise ValueError('Indirect local storage: ' + str(part))
    git(primary, 'check-ignore', '--', LOCAL + '/probe.json')
    return base


def local_snapshot(root):
    """The current local copy as {folder, revision, generated_at, generator}, or None; never raises.

    Only a whole copy counts: all five datasets and a valid meta file, so a damaged or missing copy falls back
    to the committed one.
    """
    try:
        base = local_base(root)
        folder = base / LOCAL_DATA
        if folder.is_symlink() or folder.is_junction() or not folder.is_dir():
            return None
        if not all((folder / (kind + '.json')).is_file() and not (folder / (kind + '.json')).is_symlink()
                   for kind in DATASETS):
            return None
        meta = json.loads((base / LOCAL_META).read_text(encoding='utf-8'))
        if (not isinstance(meta, dict) or set(meta) != {'revision', 'generated_at', 'generator'}
                or not re.fullmatch(r'[0-9a-f]{40}', str(meta['revision']))
                or not re.fullmatch(r'[0-9a-f]{12}', str(meta['generator']))
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', str(meta['generated_at']))):
            return None
        return {'folder': folder, **meta}
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def replace_path(source, target):
    for attempt in range(50):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(0.1)


def publish_local(root, scratch, revision):
    """Replace the one local copy: build it beside the old one, swap it in, drop the old one.

    A failure before the swap leaves the previous copy in use. Tracked files are never touched.
    """
    base = local_base(root)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    state = json.loads((scratch / 'out' / 'state.json').read_text(encoding='utf-8'))
    meta = {'generated_at': now.strftime('%Y-%m-%dT%H:%M:%SZ'), 'generator': str(state['behavior'])[:12],
            'revision': revision}
    fresh, old, live = base / (LOCAL_DATA + '.new'), base / (LOCAL_DATA + '.old'), base / LOCAL_DATA
    staged_meta = base / (LOCAL_META + '.new')
    try:
        base.mkdir(parents=True, exist_ok=True)
        # Leftovers of an interrupted run are this command's own temporary names.
        for leftover in (fresh, old):
            shutil.rmtree(leftover, ignore_errors=True)
        fresh.mkdir()
        for name in [kind + '.json' for kind in DATASETS] + [STATE]:
            publish(scratch / 'out' / name, fresh / name)
        staged_meta.write_bytes((json.dumps(meta, indent=2, sort_keys=True) + '\n').encode('ascii'))
        if live.exists():
            replace_path(live, old)
        try:
            replace_path(fresh, live)
        except OSError:
            if old.exists() and not live.exists():
                replace_path(old, live)
            raise
        replace_path(staged_meta, base / LOCAL_META)
    except (OSError, ValueError) as error:
        shutil.rmtree(fresh, ignore_errors=True)
        staged_meta.unlink(missing_ok=True)
        raise ValueError('local publication failed; the previous local copy stays in use: ' + str(error)) from error
    shutil.rmtree(old, ignore_errors=True)


def publish(source, target):
    data = source.read_bytes()
    if target.is_file() and target.read_bytes() == data:
        return
    staged = target.with_name(target.name + '.publish')
    shutil.copyfile(source, staged)
    try:
        for attempt in range(50):
            try:
                os.replace(staged, target)
                break
            except PermissionError:
                if attempt == 49:
                    raise
                time.sleep(0.1)
    finally:
        staged.unlink(missing_ok=True)
    if target.read_bytes() != data:
        raise ValueError('Published bytes differ: ' + str(target))


def run(root, scratch, mode):
    """Bring the one local copy to HEAD: incrementally by default, as a full rebuild when it cannot be.

    The full rebuild of `refresh-local-full` also runs the independent verify pass. The incremental update and the
    fallback it makes skip it: this is a local convenience copy, the generator already validates every record's
    source and link traceability on every run, and the test suite covers its determinism.
    """
    started = time.monotonic()
    # Generate at HEAD with no proposal overlay, in scratch only: nothing tracked is read for selection or written.
    revision = git(root, 'rev-parse', '--verify', 'HEAD').stdout.decode('ascii').strip()
    selection = scratch / 'selection.json'
    selection.write_bytes(canonical_selection(revision))
    out = scratch / 'out'
    note, reason = '', None
    if mode == 'refresh-local':
        snapshot = local_snapshot(root)
        try:
            if snapshot is None:
                raise Fallback('there is no whole local copy yet')
            changed = incremental_plan(root, snapshot, revision)
            if revision == snapshot['revision'] and not changed:
                print(f'Already current at {revision[:7]}: nothing to rebuild ({time.monotonic() - started:.1f} s). '
                      'Tracked files untouched.')
                return
            for name in [kind + '.json' for kind in DATASETS] + [STATE]:
                shutil.copyfile(snapshot['folder'] / name, out / name)
            updated = generate(root, scratch, selection, 'normal', changed, quiet=True)['updated']
            publish_local(root, scratch, revision)
            note = (f'Incremental update at {revision[:7]} in {time.monotonic() - started:.1f} s: '
                    f'{len(updated)} of {len(DATASETS)} datasets rebuilt ({", ".join(updated) or "none"}), '
                    f'{len(changed)} changed input paths. Verify skipped on this local path; tracked files untouched.')
        except Fallback as error:
            reason = str(error)
            # Normal mode may have written part of its scratch copy; the full rebuild starts from nothing.
            shutil.rmtree(out, ignore_errors=True)
            out.mkdir()
    if not note:
        generate(root, scratch, selection, 'full')
        if mode == 'refresh-local-full':
            generate(root, scratch, selection, 'verify')
        publish_local(root, scratch, revision)
        why = f' ({reason[:120]})' if reason else ''
        kind = 'Full rebuild with verification' if mode == 'refresh-local-full' else 'Full rebuild'
        note = (f'{kind} at {revision[:7]} in {time.monotonic() - started:.1f} s{why}. '
                + ('' if mode == 'refresh-local-full' else 'Verify skipped on this local path. ') + 'Tracked files untouched.')
    print(note + ' Reload the browser now.')


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in ('refresh-local', 'refresh-local-full'):
        print('Usage: data_launcher.py refresh-local|refresh-local-full', file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[3]
    parent = root / 'tmp/mission-control-data'
    try:
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='run-', dir=parent) as directory:
            scratch = Path(directory)
            (scratch / 'out').mkdir()
            run(root, scratch, args[0])
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    finally:
        for path in (parent, parent.parent):
            try:
                path.rmdir()
            except OSError:
                pass


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
    import runtime_guard  # the core's guard, on the path set up above
    runtime_guard.enter('data_launcher', extension_mode=runtime_guard.LOCAL)  # first: version check, GIT_* scrub, lifecycle gate
    sys.exit(main())
