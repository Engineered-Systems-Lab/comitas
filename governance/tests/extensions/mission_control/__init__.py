"""Tests of the Mission Control extension. Importing this package puts the extension's code and its server folder on the
import path, so the tests import the generator, the launcher and the server by their module names."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
for entry in (ROOT / 'governance' / 'extensions' / 'mission_control' / 'ui', ROOT / 'governance' / 'extensions' / 'mission_control'):
    if str(entry) not in sys.path:  # absolute and resolved, inserted at the front once per process
        sys.path.insert(0, str(entry))
