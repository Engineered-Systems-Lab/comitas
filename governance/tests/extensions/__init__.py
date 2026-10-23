"""Tests of the optional extensions. Importing this package puts the extension layer's own folder on the import path, so
the removal script is imported by its module name."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / 'governance' / 'extensions',):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
