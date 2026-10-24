"""Tests of the diff viewer extension. Importing this package puts the extension's code and the repository root on the import path."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
for entry in (ROOT / 'governance' / 'extensions' / 'diff_viewer', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
