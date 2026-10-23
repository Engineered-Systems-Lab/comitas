"""Tests of the governance tools, by function. Importing this package puts the repository root and the tools folder on the
import path, so every test module imports the tools by dotted package name rooted at the repository."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
