"""Commit ids cited as `<label> commit` and the id in backticks (G051), read by the one shared reader and by the continuity check.

The reader tests run on strings and on empty folders. The check tests run on the disposable repository of the continuity tests,
with a second devlog entry that cites what each test needs; nothing here touches this repository."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from governance.tests.continuity.test_continuity import FIXTURE_FROZEN, FIXTURE_REQUIRED, FIXTURE_RUNTIME, build_fixture
from governance.tools.continuity import artifact_classification as ac
from governance.tools.continuity import validate_continuity as v

SHORT = 'abc1234'
FULL = 'ab' * 20
LABELS = ('upstream',)


class ReaderTest(unittest.TestCase):
    def split(self, text, labels=LABELS):
        found = ac.split_citations(text, labels)
        return found.local, found.external, found.malformed

    def test_a_declared_labeled_id_is_external_at_seven_and_at_forty_characters(self):
        local, external, malformed = self.split('upstream commit `%s` and upstream commit `%s`' % (SHORT, FULL))
        self.assertEqual((local, external, malformed), ([], [('upstream', SHORT), ('upstream', FULL)], []))

    def test_an_undeclared_labeled_id_is_local_like_a_bare_one(self):
        local, external, malformed = self.split('other commit `%s` and `%s`' % (FULL, SHORT))
        self.assertEqual((local, external, malformed), ([('other', FULL), (None, SHORT)], [], []))

    def test_a_declared_label_with_a_token_that_is_not_an_id_is_malformed(self):
        for token in ('nothex', 'ABC1234', 'abc12', 'ab' * 21, ''):
            with self.subTest(token=token):
                self.assertEqual(self.split('upstream commit `%s`' % token), ([], [], [('upstream', token)]))

    def test_the_phrase_is_exact(self):
        for text in ('upstream Commit `%s`' % SHORT, 'upstream  commit `%s`' % SHORT, 'upstream commit: `%s`' % SHORT,
                     'upstream commit\n`%s`' % SHORT, 'xupstream commit `%s`' % SHORT, 'Upstream commit `%s`' % SHORT):
            with self.subTest(text=text):
                self.assertEqual(self.split(text)[1:], ([], []))
        self.assertEqual(self.split('A row | upstream commit `%s` |' % SHORT)[1], [('upstream', SHORT)])

    def test_no_declared_label_makes_every_id_local(self):
        local, external, malformed = self.split('upstream commit `%s`' % SHORT, ())
        self.assertEqual((local, external, malformed), ([('upstream', SHORT)], [], []))

    def test_a_missing_file_means_no_label_and_a_bad_one_stops(self):
        with tempfile.TemporaryDirectory(prefix='citations-') as folder:
            root = Path(folder)
            self.assertEqual(ac.citation_labels(root), ())
            path = root / ac.CITATIONS
            path.parent.mkdir(parents=True)
            path.write_text('not json', encoding='utf-8')
            self.assertEqual(ac.citation_labels(root), ())
            path.write_text(json.dumps({'version': 1, 'external_repositories': ['upstream', 'a.b-c_1']}), encoding='utf-8')
            self.assertEqual(ac.citation_labels(root), ('upstream', 'a.b-c_1'))
            for bad in ({'external_repository': ['x']}, {'version': 2}, {'external_repositories': 'upstream'},
                        {'external_repositories': ['two words']}, {'external_repositories': ['x', 'x']},
                        {'external_repositories': [1]}):
                with self.subTest(bad=bad):
                    path.write_text(json.dumps(bad), encoding='utf-8')
                    with self.assertRaises(ValueError):
                        ac.citation_labels(root)


class DevlogEntryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = tempfile.TemporaryDirectory(prefix='citations-fixture-')
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name) / 'repo'
        self.checkpoint, self.base, self.entry = build_fixture(self.root)

        def scope(where):
            return (FIXTURE_FROZEN, FIXTURE_RUNTIME, FIXTURE_REQUIRED)
        patcher = patch.object(v, 'scope', scope)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8', newline='\n')

    def declare(self, *labels):
        self.write(ac.CITATIONS, json.dumps({'version': 1, 'external_repositories': list(labels)}))

    def add_entry(self, body):
        name = 'devlog/2026-10-11T1000Z-cites.md'
        self.write(name, '# Cites\n\nDate: 2026-10-11T10:00Z\nSubject: Cites\nStatus: Historical / non-normative\n'
                   'Provenance: Fixture\n' + body + '\n')
        index = (self.root / 'devlog/README.md').read_text(encoding='utf-8')
        self.write('devlog/README.md', index + '[Cites](' + Path(name).name + ')\n')

    def validate(self):
        return v.verify_repository(self.root)

    def test_the_fixture_passes_without_a_setting(self):
        self.validate()

    def test_an_entry_citing_only_a_declared_labeled_commit_passes_at_seven_and_forty(self):
        self.declare('upstream')
        for token in (SHORT, FULL):
            with self.subTest(token=token):
                self.add_entry('upstream commit `%s`' % token)
                self.validate()

    def test_an_undeclared_labeled_id_that_does_not_resolve_fails(self):
        self.add_entry('upstream commit `%s`' % FULL)
        with self.assertRaisesRegex(ValueError, 'Git evidence unavailable'):
            self.validate()
        self.declare('other')
        with self.assertRaisesRegex(ValueError, 'Git evidence unavailable'):
            self.validate()

    def test_a_labeled_id_that_resolves_passes_without_a_declaration(self):
        self.add_entry('upstream commit `%s`' % self.checkpoint)
        self.validate()

    def test_a_bare_id_that_does_not_resolve_fails_even_when_a_label_is_declared(self):
        self.declare('upstream')
        self.add_entry('upstream commit `%s`\n`%s`' % (SHORT, FULL))
        with self.assertRaisesRegex(ValueError, 'Git evidence unavailable'):
            self.validate()

    def test_a_declared_label_followed_by_a_token_that_is_not_an_id_fails(self):
        self.declare('upstream')
        self.add_entry('upstream commit `%s`\nupstream commit `not-an-id`' % self.checkpoint)
        with self.assertRaisesRegex(ValueError, 'label without a valid id'):
            self.validate()

    def test_an_entry_that_cites_no_commit_still_fails(self):
        self.declare('upstream')
        self.add_entry('Nothing cited here, only a tree 1234567 and upstream commit without backticks.')
        with self.assertRaisesRegex(ValueError, 'Log has no checkpoint'):
            self.validate()

    def test_an_implementation_checkpoint_entry_cannot_cite_only_a_labeled_commit(self):
        self.declare('upstream')
        path = self.root / self.entry
        text = path.read_text(encoding='utf-8')
        path.write_text(text.replace('Repository checkpoint: `%s`' % self.checkpoint,
                                     'Repository checkpoint: upstream commit `%s`' % self.checkpoint), encoding='utf-8',
                        newline='\n')
        with self.assertRaisesRegex(ValueError, 'Implementation checkpoint needs exactly one indexed log'):
            self.validate()


if __name__ == '__main__':
    unittest.main()
