"""Focused offline terminal checks; requires Node.js on PATH.

The datasets are never committed, so the Node tests read a copy generated for this run. They are generated from a small
synthetic repository (fixture_repo.py), never from this project's own records, so the checks neither depend on what the
project has recorded so far nor change when it records more. Set MC_DATA_DIR to a folder that already holds the five
datasets, such as .local/mission-control/data, to check the terminal against them instead.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

EXTENSION = 'governance/extensions/mission_control'
TESTS = 'governance/tests/extensions/mission_control'


def generate(root, folder):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import fixture_repo
    head, inventory = fixture_repo.build(folder / 'repo', root)
    selection = folder / 'selection.json'
    selection.write_bytes(('{\n  "proposal": {},\n  "revision": "' + head + '"\n}\n').encode('ascii'))
    (folder / 'inventory.json').write_text(json.dumps(inventory), encoding='utf-8')
    subprocess.run([sys.executable, '-B', EXTENSION + '/generate.py', 'full', '--repo', str(folder / 'repo'), '--selection',
                    str(selection), '--inventory', str(folder / 'inventory.json'), '--output', str(folder / 'data')],
                   cwd=root, check=True)
    return folder / 'data'


def main():
    root = Path(__file__).resolve().parents[4]
    node = shutil.which('node')
    if not node:
        raise SystemExit('Node.js is required on PATH for Mission Control UI checks')
    with tempfile.TemporaryDirectory(prefix='mc-ui-check-') as scratch:
        data = os.environ.get('MC_DATA_DIR') or str(generate(root, Path(scratch)))
        environment = {**os.environ, 'MC_DATA_DIR': data}
        for args in ([node, '--test', TESTS + '/test_core.mjs'],
                     [node, '--check', EXTENSION + '/ui/app.mjs'],
                     [sys.executable, '-B', '-m', 'unittest', 'governance.tests.extensions.mission_control.test_serve', '-v']):
            subprocess.run(args, cwd=root, check=True, env=environment)


if __name__ == '__main__':
    main()
