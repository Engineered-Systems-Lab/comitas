"""Reviewed link-only retarget: pure evaluation, policy validation and its effect on selection."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from governance.tools.continuity import artifact_classification
import checkpoint as c
from governance.tools.support import link_retarget as lr
from ..fake_git import FakeGit, bare_tool
import repo_paths as rp

REPO = rp.ROOT
MOVES = {'docs/CONSTRAINTS.md': 'governance/CONSTRAINTS.md', 'docs/DECISIONS.md': rp.DECISIONS}
DOCS = frozenset({'docs/A.md', 'components/x/B.md'})


def evaluate(old, new, document='docs/A.md', moves=MOVES, documents=DOCS):
    return lr.evaluate(document, old, new, documents, moves)


class EvaluateTest(unittest.TestCase):
    def test_a_mapped_link_is_retargeted_file_relatively(self):
        result = evaluate('See [rules](CONSTRAINTS.md).\n', 'See [rules](../governance/CONSTRAINTS.md).\n')
        self.assertEqual(result, {'eligible': True, 'changes': [
            {'old': 'CONSTRAINTS.md', 'new': '../governance/CONSTRAINTS.md'}]})

    def test_the_expected_target_depends_on_the_documents_own_folder(self):
        result = lr.evaluate('components/x/B.md', '[d](../../docs/DECISIONS.md)',
                             '[d](../../governance/DECISIONS.md)', DOCS, MOVES)
        self.assertTrue(result['eligible'])
        wrong = lr.evaluate('components/x/B.md', '[d](../../docs/DECISIONS.md)',
                            '[d](../governance/DECISIONS.md)', DOCS, MOVES)
        self.assertFalse(wrong['eligible'])

    def test_fragments_are_kept_and_must_not_change(self):
        ok = evaluate('[r](CONSTRAINTS.md#rule-one)', '[r](../governance/CONSTRAINTS.md#rule-one)')
        self.assertTrue(ok['eligible'])
        dropped = evaluate('[r](CONSTRAINTS.md#rule-one)', '[r](../governance/CONSTRAINTS.md)')
        self.assertFalse(dropped['eligible'])
        other = evaluate('[r](CONSTRAINTS.md#rule-one)', '[r](../governance/CONSTRAINTS.md#rule-two)')
        self.assertFalse(other['eligible'])

    def test_several_links_and_crlf_checkouts(self):
        old = '[a](CONSTRAINTS.md)\r\ntext [b](DECISIONS.md#d1) and [c](other.md)\r\n'
        new = '[a](../governance/CONSTRAINTS.md)\ntext [b](../governance/DECISIONS.md#d1) and [c](other.md)\n'
        result = evaluate(old, new)
        self.assertTrue(result['eligible'])
        self.assertEqual(len(result['changes']), 2)

    def test_everything_else_fails_closed(self):
        old = '[a](CONSTRAINTS.md) and [b](DECISIONS.md) and [x](other.md)\n'
        cases = {
            'a partial retarget': '[a](../governance/CONSTRAINTS.md) and [b](DECISIONS.md) and [x](other.md)\n',
            'an unmapped target': '[a](../governance/CONSTRAINTS.md) and [b](../governance/DECISIONS.md) and [x](moved.md)\n',
            'a wrong target': '[a](../governance/CONSTRAINTS.md) and [b](../governance/OTHER.md) and [x](other.md)\n',
            'other prose': '[a](../governance/CONSTRAINTS.md) and [b](../governance/DECISIONS.md) and [x](other.md) more\n',
            'link text': '[A](../governance/CONSTRAINTS.md) and [b](../governance/DECISIONS.md) and [x](other.md)\n',
            'an added link': '[a](../governance/CONSTRAINTS.md) and [b](../governance/DECISIONS.md) and [x](other.md) [y](z.md)\n',
        }
        for name, new in cases.items():
            with self.subTest(name):
                self.assertFalse(evaluate(old, new)['eligible'])

    def test_unparsed_link_forms_cannot_change(self):
        for old, new in (
                ('[a][ref]\n\n[ref]: CONSTRAINTS.md\n', '[a][ref]\n\n[ref]: ../governance/CONSTRAINTS.md\n'),
                ('<a href="CONSTRAINTS.md">a</a>\n', '<a href="../governance/CONSTRAINTS.md">a</a>\n'),
                ('![i](CONSTRAINTS.md)\n', '![i](../governance/CONSTRAINTS.md)\n'),
                ('<https://example.test/a>\n', '<https://example.test/b>\n')):
            with self.subTest(old=old):
                self.assertFalse(evaluate(old, new)['eligible'])

    def test_external_and_same_file_links_never_count_as_moves(self):
        self.assertFalse(evaluate('[a](https://example.test/CONSTRAINTS.md)',
                                  '[a](https://example.test/x.md)')['eligible'])
        self.assertFalse(evaluate('[a](#top)', '[a](#other)')['eligible'])

    def test_no_change_added_deleted_and_uncovered_documents_are_not_eligible(self):
        same = '[a](other.md)\n'
        self.assertEqual(evaluate(same, same)['reason'], 'no link target changed')
        self.assertFalse(evaluate(None, same)['eligible'])
        self.assertFalse(evaluate(same, None)['eligible'])
        self.assertFalse(evaluate('[a](CONSTRAINTS.md)', '[a](../governance/CONSTRAINTS.md)',
                                  document='docs/unlisted.md')['eligible'])
        self.assertFalse(evaluate('[a](CONSTRAINTS.md)', '[a](../governance/CONSTRAINTS.md)', moves={})['eligible'])


SYNTHETIC_ROWS = [
    '| `components/x/README.md` | runtime | component-x | runtime | - | - | - | - | - |',
    '| `docs/frozen-spec.md` | governance | governance | frozen | - | - | - | - | - |',
    '| `docs/guide.md` | governance | governance | - | - | - | - | yes | - |',
    '| `docs/runtime-guide.md` | runtime | component-x | - | - | - | - | - | - |',
    '| `docs/shared-design.md` | runtime | component-x | - | - | - | - | - | - |',
]


def with_component(policy, profiles):
    """The shipped policy and profiles plus one synthetic runtime component, `component-x`, with its own gate."""
    policy, profiles = copy.deepcopy(policy), copy.deepcopy(profiles)
    policy['commands']['component-x-gate'] = ['{python}', '-B', '-c', 'print("component-x")']
    profiles['profiles']['component-x'] = {
        'limitations': 'Synthetic component evidence only.', 'runtime_checks': ['component-x-gate'],
        'checks': [{'id': 'continuity', 'argv': policy['commands']['continuity']},
                   {'id': 'component-x-gate', 'argv': policy['commands']['component-x-gate']}]}
    return policy, profiles


def table_with_synthetic_rows():
    """The shipped table text with the synthetic rows added in sorted position."""
    lines = (REPO / c.TABLE).read_text(encoding='utf-8').split(chr(10))
    start = next(i for i, line in enumerate(lines) if line.startswith('| Path | Class | Profile |')) + 2
    end = start
    while lines[end].startswith('|'):
        end += 1
    rows = {line.split('`')[1]: line for line in lines[start:end] + SYNTHETIC_ROWS}
    return chr(10).join(lines[:start] + [rows[k] for k in sorted(rows)] + lines[end:])


class SettingsTest(unittest.TestCase):
    def table(self):
        policy, profiles = with_component(json.loads((REPO / c.POLICY).read_bytes()),
                                          json.loads((REPO / c.PROFILES).read_bytes()))
        return artifact_classification.parse_table(table_with_synthetic_rows(), profiles)

    def test_the_shipped_mechanism_is_dormant(self):
        policy = json.loads((REPO / c.POLICY).read_bytes())
        self.assertEqual(policy['link_retarget'], {'documents': [], 'moves': {}})
        self.assertEqual(lr.settings(policy, self.table()), (frozenset(), {}))

    def test_a_policy_without_the_key_is_dormant(self):
        self.assertEqual(lr.settings({}), (frozenset(), {}))

    def test_malformed_settings_are_refused(self):
        bad = [
            {'documents': []},
            {'documents': [], 'moves': {}, 'extra': 1},
            {'documents': 'docs/A.md', 'moves': {}},
            {'documents': ['docs/B.md', 'docs/A.md'], 'moves': {}},
            {'documents': ['/docs/A.md'], 'moves': {}},
            {'documents': ['docs/../A.md'], 'moves': {}},
            {'documents': [], 'moves': {'docs\\A.md': 'x.md'}},
            {'documents': [], 'moves': {'docs/A.md': 'docs/A.md'}},
            {'documents': [], 'moves': {'b.md': 'x.md', 'a.md': 'y.md'}},
            {'documents': [], 'moves': {'a.md': '../x.md'}},
            {'documents': [], 'moves': {'a.md': 'https://example.test/x.md'}},
        ]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                lr.settings({'link_retarget': value})

    def test_frozen_runtime_boundary_and_unclassified_documents_are_refused(self):
        table = self.table()
        for path in ('docs/frozen-spec.md', 'components/x/README.md', 'docs/NOT-CLASSIFIED.md'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                lr.settings({'link_retarget': {'documents': [path], 'moves': {}}}, table)
        lr.settings({'link_retarget': {'documents': ['docs/runtime-guide.md'], 'moves': {}}}, table)


class SelectionTest(unittest.TestCase):
    """The reviewed link-only edit lowers a document to continuity; nothing else changes."""
    OLD = 'See [rules](CONSTRAINTS.md#validation-scope) and [d](DECISIONS.md).\n'
    NEW = ('See [rules](../governance/CONSTRAINTS.md#validation-scope) '
           'and [d](../governance/DECISIONS.md).\n')

    def setUp(self):
        self.policy, self.profiles = with_component(json.loads((REPO / c.POLICY).read_bytes()),
                                                    json.loads((REPO / c.PROFILES).read_bytes()))
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'governance').mkdir(exist_ok=True)
        (self.root / 'docs').mkdir()
        (self.root / c.TABLE).write_text(table_with_synthetic_rows(), encoding='utf-8', newline=chr(10))
        self.fake = FakeGit({c.TABLE: table_with_synthetic_rows()})
        self.tool = bare_tool(self.root, self.fake)
        self.policy['link_retarget'] = {'documents': ['docs/guide.md', 'docs/runtime-guide.md', 'docs/shared-design.md'],
                                        'moves': {'docs/CONSTRAINTS.md': 'governance/CONSTRAINTS.md',
                                                  'docs/DECISIONS.md': rp.DECISIONS}}

    def write(self, path, old, new):
        self.fake.files[path] = old
        (self.root / path).write_text(new, encoding='utf-8', newline='\n')

    def select(self, paths):
        return self.tool.select_profile(paths, self.policy, self.profiles, 'auto')

    def test_a_runtime_class_document_needs_continuity_only(self):
        path = 'docs/runtime-guide.md'
        self.write(path, self.OLD, self.NEW)
        name, profile = self.select([path])
        self.assertEqual(name, 'record-only')
        self.assertEqual({v['id'] for v in profile['checks']}, {'continuity'})
        self.assertEqual(profile['classification'], {path: 'record'})
        self.assertEqual(profile['categories'], ['record'])
        self.assertNotIn('runtime_checks', profile)
        self.assertEqual(profile['impact'][path]['invariants'], [])
        self.assertEqual([x['new'] for x in profile['link_retarget'][path]][1], '../governance/DECISIONS.md')

    def test_a_second_runtime_document_drops_its_component_checks(self):
        path = 'docs/shared-design.md'
        self.write(path, self.OLD, self.NEW)
        self.assertEqual({v['id'] for v in self.select([path])[1]['checks']}, {'continuity'})
        self.policy['link_retarget'] = {'documents': [], 'moves': {}}
        self.assertIn('component-x-gate', {v['id'] for v in self.select([path])[1]['checks']})

    def block(self, path):
        """Give the fixture copy of the table a blocked row for the path, whatever the shipped row is today."""
        table = self.root / c.TABLE
        lines = table.read_text(encoding='ascii').split(chr(10))
        rows = [i for i, line in enumerate(lines) if line.startswith('| `%s` |' % path)]
        self.assertEqual(len(rows), 1)
        lines[rows[0]] = '| `%s` | runtime | blocked | - | - | - | - | - | - |' % path
        table.write_text(chr(10).join(lines), encoding='ascii', newline=chr(10))

    def test_a_blocked_document_becomes_preparable(self):
        path = 'docs/guide.md'
        self.block(path)
        self.write(path, self.OLD, self.NEW)
        self.assertEqual(self.select([path])[0], 'record-only')
        self.policy['link_retarget'] = {'documents': [], 'moves': {}}
        with self.assertRaisesRegex(ValueError, 'No reviewed validation scope'):
            self.select([path])

    def test_any_other_edit_keeps_the_normal_scope(self):
        path = 'docs/runtime-guide.md'
        self.write(path, self.OLD, self.NEW + 'One more sentence.\n')
        name, profile = self.select([path])
        self.assertEqual(name, 'component-x')
        self.assertNotIn('link_retarget', profile)
        self.assertIn('component-x-gate', {v['id'] for v in profile['checks']})

    def test_a_document_outside_the_policy_list_is_untouched(self):
        path = 'docs/runtime-guide.md'
        self.write(path, self.OLD, self.NEW)
        self.policy['link_retarget']['documents'] = ['docs/guide.md']
        self.assertEqual(self.select([path])[0], 'component-x')

    def test_a_mixed_manifest_combines_the_retargeted_document_with_the_others(self):
        path = 'docs/runtime-guide.md'
        self.write(path, self.OLD, self.NEW)
        name, profile = self.select([path, rp.DECISIONS])
        self.assertEqual(name, 'governance')
        self.assertEqual(profile['classification'], {path: 'record', rp.DECISIONS: 'governance'})
        self.assertEqual(profile['categories'], ['governance', 'record'])
        self.assertNotIn('component-x-gate', {v['id'] for v in profile['checks']})

    def test_the_sealed_classification_names_each_retargeted_link(self):
        path = 'docs/runtime-guide.md'
        self.write(path, self.OLD, self.NEW)
        profile = self.select([path])[1]
        sealed = self.tool.attach_classification([path], self.policy, self.profiles, profile)
        entry = sealed['artifact_classification'][path]
        self.assertEqual(entry['class'], 'runtime')
        self.assertEqual(entry['link_retarget'], profile['link_retarget'][path])
        self.assertEqual(entry['link_retarget'][0]['old'], 'CONSTRAINTS.md#validation-scope')

    def test_an_ordinary_path_is_still_checked_against_the_table(self):
        sealed = self.tool.attach_classification([rp.DECISIONS], self.policy, self.profiles,
                                                 self.select([rp.DECISIONS])[1])
        self.assertNotIn('link_retarget', sealed['artifact_classification'][rp.DECISIONS])
        forged = copy.deepcopy(self.select([rp.DECISIONS])[1])
        forged['classification'][rp.DECISIONS] = 'record'
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            self.tool.attach_classification([rp.DECISIONS], self.policy, self.profiles, forged)

    def test_the_shipped_policy_changes_nothing(self):
        shipped, _ = with_component(json.loads((REPO / c.POLICY).read_bytes()), self.profiles)
        for path in ('docs/runtime-guide.md', rp.DECISIONS):
            before = self.tool.select_profile([path], shipped, self.profiles, 'auto')
            self.assertNotIn('link_retarget', before[1])

    def test_a_frozen_document_cannot_be_listed(self):
        self.policy['link_retarget']['documents'] = ['docs/frozen-spec.md']
        with self.assertRaisesRegex(ValueError, 'frozen or runtime-boundary'):
            self.select(['docs/runtime-guide.md'])


if __name__ == '__main__':
    unittest.main()
