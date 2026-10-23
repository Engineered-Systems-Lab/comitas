"""Launcher: the worker context freezer. The implementation is lifecycle/commit_metadata.py; this file keeps the documented command line."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import runpy  # noqa: E402
import runtime_guard  # noqa: E402

if __name__ == '__main__':
    runtime_guard.enter('commit_metadata')  # first: version check, GIT_* scrub, lifecycle gate
    runpy.run_module('governance.tools.lifecycle.commit_metadata', run_name='__main__')
