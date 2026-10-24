"""remove_extension.py on small synthetic trees: what it deletes, what it strips, what it turns into text and when it fails."""
import json
from pathlib import Path
import tempfile
import unittest

import remove_extension as r

ROW = '| `%s` | tooling | tooling | - | - | %s | %s | - | - |'


def manifest(**changes):
    value = {'version': 1, 'name': 'demo', 'folder': 'governance/extensions/demo', 'tests': 'governance/tests/extensions/demo',
             'policy_commands': ['demo-check'], 'invariants': ['demo-inv'], 'subclasses': ['demo-sub'],
             'project_files': ['project-governance/config/demo.json'], 'markers': ['demo-inv', 'extensions/demo'],
             'prose': ['The Demo']}
    value.update(changes)
    return value


class PiecesTest(unittest.TestCase):
    def test_strip_tokens_keeps_the_rest_and_a_bare_list_becomes_the_hyphen(self):
        self.assertEqual(r.strip_tokens('guidance, demo-inv', {'demo-inv'}), 'guidance')
        self.assertEqual(r.strip_tokens('demo-inv', {'demo-inv'}), '-')
        self.assertEqual(r.strip_tokens('a, b', {'demo-inv'}), 'a, b')

    def test_table_rows_under_the_folders_go_and_the_names_are_stripped_from_the_others(self):
        text = '\n'.join(['intro', ROW % ('governance/extensions/demo/', '-', 'demo-sub'),
                          ROW % ('governance/extensions/demo/a.py', 'demo-inv', '-'),
                          ROW % ('governance/tests/extensions/demo/test_a.py', 'demo-inv', 'demo-sub'),
                          ROW % ('governance/DECISIONS.md', 'guidance, demo-inv', 'decisions'),
                          ROW % ('governance/RATIONALE.md', 'demo-inv', 'demo-sub, rationale'),
                          ROW % ('governance/extensions/demo-like.md', '-', '-'), 'outro'])
        result = r.edit_table(text, ['governance/extensions/demo', 'governance/tests/extensions/demo'], ['demo-inv'], ['demo-sub'])
        self.assertEqual(result.split('\n'), ['intro', ROW % ('governance/DECISIONS.md', 'guidance', 'decisions'),
                                              ROW % ('governance/RATIONALE.md', '-', 'rationale'),
                                              ROW % ('governance/extensions/demo-like.md', '-', '-'), 'outro'])

    def test_a_historical_row_loses_the_subclass_name_too(self):
        row = '| `old/path.py` | tooling | demo-sub, checkpoint |'
        self.assertEqual(r.edit_table(row, [], [], ['demo-sub']), '| `old/path.py` | tooling | checkpoint |')

    def test_the_golden_policy_and_profiles_lose_exactly_the_extensions_entries(self):
        golden = {'version': 1, 'paths': {'governance/extensions/demo/a.py': {'invariants': ['demo-inv'], 'subclass': []},
                                          'governance/DECISIONS.md': {'invariants': ['guidance', 'demo-inv'], 'subclass': ['demo-sub', 'decisions']},
                                          'governance/RATIONALE.md': {'invariants': ['demo-inv'], 'subclass': []}}}
        golden = r.edit_golden(golden, ['governance/extensions/demo'], ['demo-inv'], ['demo-sub'])
        self.assertEqual(golden['paths'], {'governance/DECISIONS.md': {'invariants': ['guidance'], 'subclass': ['decisions']},
                                           'governance/RATIONALE.md': {'invariants': None, 'subclass': []}})
        policy = r.edit_policy({'commands': {'demo-check': [], 'continuity': []}, 'required_checks': ['continuity']}, ['demo-check'])
        self.assertEqual(policy, {'commands': {'continuity': []}, 'required_checks': ['continuity']})
        profiles = {'impact': {'invariants': {'demo-inv': {'groups': ['demo-check']},
                                              'other': {'groups': ['demo-check', 'continuity'], 'requires': ['demo-inv', 'x']}}},
                    'commit_metadata': {'subclasses': [{'value': 'demo-sub'}, {'value': 'checkpoint'}]}}
        profiles = r.edit_profiles(profiles, ['demo-check'], ['demo-inv'], ['demo-sub'])
        self.assertEqual(profiles, {'impact': {'invariants': {'other': {'groups': ['continuity'], 'requires': ['x']}}},
                                    'commit_metadata': {'subclasses': [{'value': 'checkpoint'}]}})

    def test_links_into_the_removed_folders_become_text_and_others_stay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'governance/extensions/demo').mkdir(parents=True)
            (root / 'governance/extensions/demo/README.md').write_text('[inside](../../DECISIONS.md)\n', encoding='utf-8')
            (root / 'governance/README.md').write_text(
                'See [the demo](extensions/demo/README.md#top) and [a page](extensions/demo/ui/x.md), [the rules](CONSTRAINTS.md), '
                '[web](https://example.test/extensions/demo/) and [up](../README.md).\n', encoding='utf-8')
            (root / 'README.md').write_text('[demo](governance/extensions/demo/README.md) [rules](governance/CONSTRAINTS.md)\n', encoding='utf-8')
            count = r.unlink_documents(root, ['governance/extensions/demo'])
            self.assertEqual(count, 3)
            self.assertEqual((root / 'governance/README.md').read_text(encoding='utf-8'),
                             'See the demo and a page, [the rules](CONSTRAINTS.md), [web](https://example.test/extensions/demo/) and [up](../README.md).\n')
            self.assertEqual((root / 'README.md').read_text(encoding='utf-8'), 'demo [rules](governance/CONSTRAINTS.md)\n')
            self.assertIn('[inside]', (root / 'governance/extensions/demo/README.md').read_text(encoding='utf-8'))

    def test_the_manifest_is_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'governance/extensions/demo'
            folder.mkdir(parents=True)
            for bad in ({'version': 2}, {'name': 'other'}, {'policy_commands': 'demo-check'}, {'folder': ''}):
                (folder / 'extension.json').write_text(json.dumps(manifest(**bad)), encoding='utf-8')
                with self.subTest(bad=bad), self.assertRaises(SystemExit):
                    r.load_manifest(root, 'demo')
            (folder / 'extension.json').write_text(json.dumps(dict(manifest(), extra=1)), encoding='utf-8')
            with self.assertRaises(SystemExit):
                r.load_manifest(root, 'demo')
            group = {'label': 'Demo', 'paths': ['governance/extensions/demo/README.md']}
            for bad in ({'label': 'Demo'}, {'label': 3, 'paths': []}, {'label': 'Demo', 'paths': 'x.md'}, 'Demo'):
                (folder / 'extension.json').write_text(json.dumps(manifest(group=bad)), encoding='utf-8')
                with self.subTest(group=bad), self.assertRaises(SystemExit):
                    r.load_manifest(root, 'demo')
            (folder / 'extension.json').write_text(json.dumps(manifest(group=group)), encoding='utf-8')
            self.assertEqual(r.load_manifest(root, 'demo')['group'], group)
            (folder / 'extension.json').write_text(json.dumps(manifest()), encoding='utf-8')
            self.assertEqual(r.load_manifest(root, 'demo')['name'], 'demo')
            with self.assertRaises(SystemExit):
                r.load_manifest(root, 'missing')


class RemovalTest(unittest.TestCase):
    def build(self, root, table_extra=()):
        for relative, text in {
            'governance/extensions/demo/extension.json': json.dumps(manifest()),
            'governance/extensions/demo/a.py': 'x = 1\n',
            'governance/tests/extensions/demo/test_a.py': 'x = 1\n',
            'project-governance/config/demo.json': '{}\n',
            'governance/ARTIFACT-CLASSIFICATION.md': '\n'.join([ROW % ('governance/extensions/demo/a.py', 'demo-inv', 'demo-sub'),
                                                                 ROW % ('governance/DECISIONS.md', 'guidance, demo-inv', '-'), *table_extra]) + '\n',
            'governance/config/artifact-classification-golden.json': json.dumps({'version': 1, 'paths': {}}, indent=2) + '\n',
            'governance/config/checkpoint-policy.json': json.dumps({'commands': {'demo-check': [], 'continuity': []}}, indent=2) + '\n',
            'governance/config/checkpoint-profiles.json': json.dumps({'impact': {'invariants': {'demo-inv': {'groups': ['demo-check']}}},
                                                                      'commit_metadata': {'subclasses': [{'value': 'demo-sub'}]}}, indent=2) + '\n',
            'governance/README.md': 'The [demo](extensions/demo/README.md) and The Demo.\n',
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='\n')

    def test_one_extension_goes_and_the_layer_stays(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root)
            r.remove(root, ['demo'], False)
            self.assertFalse((root / 'governance/extensions/demo').exists())
            self.assertFalse((root / 'governance/tests/extensions/demo').exists())
            self.assertFalse((root / 'project-governance').exists())
            self.assertEqual((root / 'governance/ARTIFACT-CLASSIFICATION.md').read_text(encoding='utf-8'),
                             ROW % ('governance/DECISIONS.md', 'guidance', '-') + '\n')
            self.assertEqual(json.loads((root / 'governance/config/checkpoint-policy.json').read_text(encoding='utf-8')),
                             {'commands': {'continuity': []}})
            self.assertEqual((root / 'governance/README.md').read_text(encoding='utf-8'), 'The demo and The Demo.\n')

    def test_a_row_that_the_script_cannot_attribute_fails_the_removal_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build(root, table_extra=[ROW % ('governance/extensions-notes/demo-inv.md', '-', '-')])
            with self.assertRaises(SystemExit) as raised:
                r.remove(root, ['demo'], False)
            self.assertIn('governance/ARTIFACT-CLASSIFICATION.md:2', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
