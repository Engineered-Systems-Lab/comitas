"""The defaults that governance/PROJECT-GOVERNANCE.md states are the defaults in the code, and it names every project file.

The document has one table of defaults (its columns are the file, the key and the default as a JSON value). This test reads
that table and compares each row with the code: the devlog settings with `DEVLOG_DEFAULTS` and the reader of the settings,
and the feature switches with the readers of the checkpoint tool, which are given an empty feature file. A row for a file of
an optional extension is checked by that extension's own tests, so this test never names an extension: it only requires that
such a file is declared by an extension manifest. It also requires that every project file the tools name is in the document."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from governance.tools.continuity import artifact_classification as ac
from governance.tools.lifecycle import early_approval as ea
import repo_paths as rp

DOCUMENT = rp.ROOT / 'governance' / 'PROJECT-GOVERNANCE.md'
FOLDER = ac.PROJECT_GOVERNANCE + '/'
ROW = re.compile(r'^\| `([^`]+)` \| `([^`]+)` \| `([^`]+)` \|$')


def documented_defaults(text=None):
    """{(file, key): value} from the rows of the document's defaults table; the value is parsed as JSON."""
    text = DOCUMENT.read_text(encoding='utf-8') if text is None else text
    found = {}
    for line in text.splitlines():
        row = ROW.match(line)
        if row:
            file, key, value = row.groups()
            found[(file, key)] = json.loads(value)
    return found


class EmptyFeatures:
    """A checkpoint tool whose feature file on main holds no key at all, so every reader answers its default."""
    def git(self, *args):
        return b''


class DocumentedDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.documented = documented_defaults()

    def values(self, file):
        return {key: value for (name, key), value in self.documented.items() if name == file}

    def test_the_parser_reads_the_table_and_nothing_else(self):
        text = '| File | Key | Default |\n| --- | --- | --- |\n| `config/a.json` | `x` | `"v"` |\n| `config/a.json` | `y` | `0` |\n| other | `z` | `1` |\n'
        self.assertEqual(documented_defaults(text), {('config/a.json', 'x'): 'v', ('config/a.json', 'y'): 0})
        self.assertTrue(self.documented)

    def test_the_devlog_defaults_are_the_documented_ones(self):
        self.assertEqual(self.values('config/devlog.json'), ac.DEVLOG_DEFAULTS)
        with tempfile.TemporaryDirectory(prefix='project-governance-') as folder:
            self.assertEqual(ac.devlog_settings(Path(folder)), self.values('config/devlog.json'))

    def test_the_citation_defaults_are_the_documented_ones(self):
        self.assertEqual(self.values('config/citations.json'), ac.CITATIONS_DEFAULTS)
        with tempfile.TemporaryDirectory(prefix='project-governance-') as folder:
            self.assertEqual({'external_repositories': list(ac.citation_labels(Path(folder)))},
                             self.values('config/citations.json'))

    def test_the_feature_defaults_are_the_documented_ones(self):
        tool = EmptyFeatures()
        settings = ea.test_settings(tool)
        code = {'early_approval_reach': ea.switch(tool)[0], 'diff_bound_approval': ea.carry_switch(tool)[0],
                'parallel_tests': settings['parallel'], 'test_workers': settings['workers']}
        self.assertEqual(self.values('config/checkpoint-features.toml'), code)

    def test_every_other_file_in_the_table_belongs_to_an_extension(self):
        core = {'config/devlog.json', 'config/checkpoint-features.toml', 'config/citations.json'}
        owned = set()
        for manifest in sorted((rp.ROOT / 'governance' / 'extensions').glob('*/extension.json')):
            owned |= {name[len(FOLDER):] for name in json.loads(manifest.read_text(encoding='utf-8'))['project_files']}
        for file in {name for name, _ in self.documented} - core:
            with self.subTest(file=file):
                self.assertIn(file, owned)

    def test_the_document_names_every_project_file_the_tools_read(self):
        text = DOCUMENT.read_text(encoding='utf-8')
        for path in (ac.PROJECT_TABLE, ac.PROJECT_POLICY, ac.PROJECT_PROFILES, ac.PROJECT_CONTRACT, ac.PROJECT_CONSTRAINTS,
                     ac.PROJECT_DECISIONS, ac.PROJECT_RATIONALE, ac.FEATURES, ac.DEVLOG, ac.CITATIONS, ac.FROZEN_MANIFEST, ac.SELECTOR_EXPECTATIONS,
                     rp.PROJECT_GOLDEN):
            with self.subTest(path=path):
                self.assertTrue(path.startswith(FOLDER))
                self.assertIn('`' + path[len(FOLDER):] + '`', text)


if __name__ == '__main__':
    unittest.main()
