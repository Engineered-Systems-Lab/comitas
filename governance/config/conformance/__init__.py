"""Conformance tests that pin the adopter configuration."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
for entry in (ROOT / 'governance' / 'tools', ROOT):  # absolute and resolved, inserted at the front once per process
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
