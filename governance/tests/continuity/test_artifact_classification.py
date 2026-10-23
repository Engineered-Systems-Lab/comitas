"""Strict parsing, cascade resolution and historical paths of the artifact classification table, on synthetic tables."""
import unittest
from governance.tools.continuity import artifact_classification as ac
import repo_paths as rp

VOCAB = {'profiles': {'governance', 'record-only', 'tooling', 'component-runtime', 'component-contract'},
         'invariants': {'guidance', 'selector', 'component-contract', 'classification'},
         'subclasses': {'devlog', 'decisions', 'plan', 'checkpoint'}}


def table(*rows, header=ac.HEADER, separator=ac.SEPARATOR, prose=''):
    return prose + '\n'.join([header, separator, *rows]) + '\n'


def row(path, *cells):
    # The Approval-sensitivity cell is the eighth; a row that fills every other cell is complete, so it gets a hyphen.
    given = list(cells)
    padded = given + [('-' if all(given) else '')] * (8 - len(given)) if len(given) == 7 else given + [''] * (8 - len(given))
    return '| `' + path + '` | ' + ' | '.join(padded) + ' |'


def full(path, klass='record', profile='record-only', boundary='-', bound='-', invariants='-', subclass='-',
         reachable='-', critical='-'):  # `critical` fills the Approval-sensitivity cell
    return row(path, klass, profile, boundary, bound, invariants, subclass, reachable, critical)


# Complete directory and filter rows, then sparse file rows that inherit.
GOOD = table(
    row('devlog/', 'record', 'blocked', '-', '-', '-', 'devlog', '-', '-'),
    row('devlog/*.md', 'record', 'record-only', '-', '-', '-', 'devlog', '-', '-'),
    row(rp.DEVLOG_INDEX, 'governance', 'governance', '', 'yes', 'guidance', '', 'yes', ''),
    row('devlog/notes/*.md', 'record', 'record-only', '-', '-', '-', 'checkpoint, devlog', '-', '-'),
    row('governance/', 'governance', 'blocked', '-', '-', '-', '-', '-', '-'),
    row(rp.DECISIONS, 'governance', 'governance', '-', 'yes', 'guidance', 'decisions', 'yes', 'medium'),
    row('subsystem/frozen-part/', 'runtime', 'blocked', 'frozen', '-', '-', '-', '-', '-'),
    row('subsystem/part/', 'runtime', 'component-runtime', 'runtime', '-', '-', '-', '-', '-'),
    row('subsystem/part/README.md', 'record', 'record-only', '-', '', '', '', 'yes', ''),
)


def parse(text):
    return ac.Table.parse(text, VOCAB)


class ParserTest(unittest.TestCase):
    def test_valid_table_parses_and_ignores_surrounding_prose(self):
        parsed = parse(table(full('a/'), prose='# Title\n\nSome prose with | pipes and `code`.\n\n')
                       + '\nTrailing prose.\n')
        self.assertEqual(parsed.exact_paths(), [])
        self.assertEqual(len(parsed.rows), 1)
        parse(GOOD)

    def test_crlf_is_accepted(self):
        parse(GOOD.replace('\n', '\r\n'))

    def test_exactly_one_table_with_the_exact_header(self):
        bad_header = table(full('a/'), header=ac.HEADER.replace('Path', 'Where'))
        for label, text in (('none', 'no table here\n'), ('two', GOOD + GOOD), ('header', bad_header)):
            with self.subTest(label), self.assertRaisesRegex(ValueError, 'Exactly one classification table'):
                parse(text)

    def test_separator_row_is_required_and_exact(self):
        with self.assertRaisesRegex(ValueError, 'separator'):
            parse(table(full('a/'), separator='| --- | --- |'))

    def test_ascii_only(self):
        with self.assertRaisesRegex(ValueError, 'ASCII'):
            parse(GOOD + 'café\n')

    def test_malformed_rows(self):
        cases = {
            'missing cell': '| `a/` | record | record-only | - | - | - | - | - |',
            'extra cell': '| `a/` | record | record-only | - | - | - | - | - | - | - |',
            'unquoted path': '| a/ | record | record-only | - | - | - | - | - | - |',
            'double quoted path': '| ``a/`` | record | record-only | - | - | - | - | - | - |',
            'no closing pipe': '| `a/` | record | record-only | - | - | - | - | - | -',
            'backtick in cell': '| `a/` | `record` | record-only | - | - | - | - | - | - |',
        }
        for label, line in cases.items():
            with self.subTest(label), self.assertRaises(ValueError):
                parse(table(line))

    def test_path_forms(self):
        ok = ['a/', 'a/b/', 'a/*.md', 'a/b/*.xml', 'a/file.txt', 'README.md', 'a/b-c/d_e.f.json']
        bad = ['/a/', 'a//b/', '../a/', 'a/../b', 'a/*', 'a/**/b.md', 'a/*.MD', '*.md', 'a/b*.md', 'a b/', 'a\\b']
        for path in sorted(ok):
            if path.endswith('/') or '*' in path or path in ('README.md', 'a/file.txt', 'a/b-c/d_e.f.json'):
                parse(table(full(path)))
        for path in bad:
            with self.subTest(path), self.assertRaises(ValueError):
                parse(table(full(path)))

    def test_rows_must_be_sorted_by_ascii_ordinal_and_unique(self):
        with self.assertRaisesRegex(ValueError, 'sorted by ASCII ordinal'):
            parse(table(full('b/'), full('a/')))
        # Uppercase sorts before lowercase, so README.md precedes a lowercase sibling directory.
        parse(table(full('a/'), full('a/README.md'), full('a/contracts/')))
        with self.assertRaisesRegex(ValueError, 'sorted by ASCII ordinal'):
            parse(table(full('a/'), full('a/contracts/'), full('a/README.md')))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            parse(table(full('a/'), full('a/')))

    def test_directory_and_filter_rows_must_be_complete(self):
        for path in ('a/', 'a/*.md'):
            for blank in range(7):
                cells = ['record', 'record-only', '-', '-', '-', '-', '-']
                cells[blank] = ''
                with self.subTest(path=path, blank=blank), self.assertRaisesRegex(ValueError, 'every column'):
                    parse(table(row(path, *cells)))

    def test_sparse_file_row_needs_an_enclosing_complete_row(self):
        with self.assertRaisesRegex(ValueError, 'enclosing'):
            parse(table(row('a/file.md', 'record', 'record-only', '', '', '', '', '')))
        parse(table(full('a/'), row('a/file.md', 'record', 'record-only', '', '', '', '', '')))

    def test_hyphen_only_where_a_no_value_is_meaningful(self):
        for column, index in (('Class', 0), ('Profile', 1)):
            cells = ['record', 'record-only', '-', '-', '-', '-', '-']
            cells[index] = '-'
            with self.subTest(column), self.assertRaisesRegex(ValueError, 'no-value hyphen'):
                parse(table(row('a/', *cells)))

    def test_vocabulary_is_closed(self):
        base = ['record', 'record-only', '-', '-', '-', '-', '-']
        for index, value in ((0, 'docs'), (1, 'made-up'), (2, 'sideways'), (3, 'maybe'), (4, 'nope'),
                             (5, 'nope'), (6, 'nope')):
            cells = list(base)
            cells[index] = value
            with self.subTest(value), self.assertRaises(ValueError):
                parse(table(row('a/', *cells)))

    def test_list_cells_are_unique_sorted_and_comma_space_separated(self):
        for bad in ('selector,guidance', 'selector, guidance', 'guidance, guidance', 'guidance,', ' , guidance'):
            with self.subTest(bad), self.assertRaises(ValueError):
                parse(table(row('a/', 'record', 'record-only', '-', '-', bad, '-', '-')))
        parse(table(row('a/', 'record', 'record-only', '-', '-', 'guidance, selector', '-', '-')))
        parse(table(row('a/', 'runtime', 'record-only', 'frozen, runtime', '-', '-', '-', '-')))
        with self.assertRaises(ValueError):
            parse(table(row('a/', 'runtime', 'record-only', 'runtime, frozen', '-', '-', '-', '-')))

    def test_symbol_map_is_a_reserved_invariant_token(self):
        parse(table(row('a/', 'tooling', 'tooling', '-', '-', 'symbol-map', '-', '-')))


HISTORY = ac.HISTORICAL_HEADER + chr(10) + ac.HISTORICAL_SEPARATOR + chr(10)


def history(*rows):
    return chr(10) + HISTORY + chr(10).join('| ' + ' | '.join(r) + ' |' for r in rows) + chr(10)


class HistoricalTest(unittest.TestCase):
    def parse(self, *rows):
        return parse(GOOD + history(*rows))

    def test_rows_reconstruct_class_and_subclass_but_never_resolve(self):
        t = self.parse(('`docs/old/gone.md`', 'governance', '-'), ('`docs/old/zzz.md`', 'record', 'decisions'))
        self.assertEqual(t.classify('docs/old/zzz.md'), ('record', ['decisions']))
        self.assertEqual(t.classify('docs/old/gone.md'), ('governance', []))
        self.assertIsNone(t.resolve('docs/old/gone.md'))
        self.assertIsNone(t.classify('docs/old/other.md'))

    def test_the_section_is_optional_but_at_most_once(self):
        self.assertEqual(parse(GOOD).historical, {})
        with self.assertRaisesRegex(ValueError, 'At most one historical'):
            parse(GOOD + history(('`a/b.md`', 'record', '-')) + history(('`a/c.md`', 'record', '-')))

    def test_strictness(self):
        bad = {
            'unsorted': ((('`b/x.md`', 'record', '-'), ('`a/x.md`', 'record', '-')), 'sorted'),
            'duplicate': ((('`a/x.md`', 'record', '-'), ('`a/x.md`', 'record', '-')), 'sorted|Duplicate'),
            'class': ((('`a/x.md`', 'nonsense', '-'),), 'unknown class'),
            'subclass': ((('`a/x.md`', 'record', 'nonsense'),), 'unknown subclass'),
            'unsorted subclass': ((('`a/x.md`', 'record', 'decisions, checkpoint'),), 'ascending'),
            'directory': ((('`a/`', 'record', '-'),), 'exact file'),
            'filter': ((('`a/*.md`', 'record', '-'),), 'exact file'),
            'dotdot': ((('`a/../x.md`', 'record', '-'),), 'exact file'),
            'cells': ((('`a/x.md`', 'record'),), 'expected 3 cells'),
            'empty': ((), 'no rows'),
        }
        for name, (rows, message) in bad.items():
            with self.subTest(name), self.assertRaisesRegex(ValueError, message):
                self.parse(*rows)

    def test_a_path_cannot_be_both_live_and_historical(self):
        live = next(r.path for r in parse(GOOD).rows if r.kind == 'file')
        with self.assertRaisesRegex(ValueError, 'also a live row'):
            self.parse((f'`{live}`', 'record', '-'))


class ResolutionTest(unittest.TestCase):
    def setUp(self):
        self.table = parse(GOOD)

    def test_most_specific_row_wins_and_exact_beats_everything(self):
        self.assertEqual(self.table.scope('devlog/e-1.md'), ('record', 'record-only'))
        self.assertEqual(self.table.scope(rp.DEVLOG_INDEX), ('governance', 'governance'))
        self.assertEqual(self.table.scope('devlog/notes/e-2.md'), ('record', 'record-only'))
        self.assertEqual(self.table.resolve('devlog/notes/e-2.md').row.path, 'devlog/notes/*.md')

    def test_unknown_paths_and_file_types_fail_closed(self):
        self.assertIsNone(self.table.resolve('new.py'))
        self.assertIsNone(self.table.resolve('docs/script.py'))
        # The plain directory row is blocked, so an unlisted type under it is refused.
        self.assertEqual(self.table.scope('devlog/evil.sh'), ('record', 'blocked'))
        self.assertEqual(self.table.scope('governance/new-policy.md'), ('governance', 'blocked'))

    def test_cells_inherit_from_the_enclosing_complete_row_and_override_replaces(self):
        resolved = self.table.resolve(rp.DEVLOG_INDEX)
        self.assertEqual(resolved.sources['Class'], 'row')
        self.assertEqual(resolved.sources['Bound'], 'row')
        self.assertEqual(resolved['Boundary'], '-')
        self.assertEqual(resolved.sources['Boundary'], 'devlog/*.md')
        self.assertEqual(resolved.subclasses, ['devlog'])
        self.assertEqual(resolved.parent.path, 'devlog/*.md')
        readme = self.table.resolve('subsystem/part/README.md')
        self.assertEqual((readme.klass, readme.profile, readme.boundary), ('record', 'record-only', []))
        self.assertTrue(readme.reachable)

    def test_a_component_readme_can_be_a_record_inside_a_runtime_directory(self):
        self.assertTrue(self.table.in_boundary('subsystem/part/src/Main.java', 'runtime'))
        self.assertFalse(self.table.in_boundary('subsystem/part/README.md', 'runtime'))

    def test_list_cells_are_replaced_not_merged(self):
        text = table(row('a/', 'tooling', 'tooling', '-', '-', 'guidance, selector', 'checkpoint', '-'),
                     row('a/x.py', 'tooling', 'tooling', '', '', 'classification', '', ''))
        resolved = parse(text).resolve('a/x.py')
        self.assertEqual(resolved.invariants, ['classification'])
        self.assertEqual(resolved.subclasses, ['checkpoint'])

    def test_invariants_none_means_unmapped_and_subclass_list_expands(self):
        self.assertIsNone(self.table.resolve('governance/new.md').invariants)
        self.assertEqual(self.table.resolve(rp.DECISIONS).invariants, ['guidance'])
        self.assertEqual(self.table.resolve('devlog/notes/x.md').subclasses,
                         ['checkpoint', 'devlog'])

    def test_frozen_may_not_be_cleared_by_a_child_row(self):
        frozen = row('f/', 'runtime', 'blocked', 'frozen', '-', '-', '-', '-')
        child_dir = row('f/sub/', 'runtime', 'blocked', '-', '-', '-', '-', '-')
        child_file_clear = row('f/x.md', 'record', 'record-only', '-', '', '', '', '')
        child_file_inherit = row('f/y.md', 'record', 'record-only', '', '', '', '', '')
        child_file_keep = row('f/z.md', 'record', 'record-only', 'frozen', '', '', '', '')
        for rows in ((frozen, child_dir), (frozen, child_file_clear)):
            with self.subTest(child=rows[1][:14]), self.assertRaisesRegex(ValueError, 'may not clear frozen'):
                parse(table(*rows))
        parsed = parse(table(frozen, child_file_inherit, child_file_keep))
        self.assertTrue(parsed.in_boundary('f/y.md', 'frozen'))
        self.assertTrue(parsed.in_boundary('f/z.md', 'frozen'))

    def test_runtime_boundary_may_be_cleared_by_a_more_specific_row(self):
        parse(table(row('r/', 'runtime', 'blocked', 'runtime', '-', '-', '-', '-'),
                    row('r/README.md', 'record', 'record-only', '-', '', '', '', '')))

    def test_derivations(self):
        self.assertEqual(self.table.bound_paths(), [rp.DEVLOG_INDEX, rp.DECISIONS])
        self.assertEqual(self.table.reachable_paths(),
                         [rp.DEVLOG_INDEX, rp.DECISIONS, 'subsystem/part/README.md'])


if __name__ == '__main__':
    unittest.main()
