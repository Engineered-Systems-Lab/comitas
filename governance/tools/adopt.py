"""Launcher: the adoption helper. The implementation is support/adoption.py; this file keeps the documented command line."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
import runtime_guard  # noqa: E402

if __name__ == '__main__':
    runtime_guard.enter('adopt')  # first: version check, GIT_* scrub, lifecycle gate
from governance.tools.support.adoption import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
