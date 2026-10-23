"""Every commit id the framework documents cite in backticks resolves in this repository, and is written with its label.

A document that cites evidence by commit id is only useful when the id can be looked up. The rule is narrow so that
the tree and blob ids of H9 (written in seven characters) do not trip it:

- a backticked token of 40 hex characters is a commit citation (H9 writes commits in full), so it must name a commit;
- a backticked token of 7 to 12 hex characters that holds a letter and a digit must name some object, a commit or a
  tree, so a short tree id passes while an id from another repository fails.

Digits-only tokens and tokens without a digit are not ids for this test. The documents are every `.md` file under
`governance/`, the extensions included, so an id of another project's history cannot hide in a design or a README.

The framework writes every commit it cites as `template commit` and the id in backticks (G051), so a project that copies
the framework declares `template` in its citation settings and the copy stays valid without the framework's history. The
label is checked here: a commit id of these documents without it fails. The template declares no label, so each id must
still resolve here; a project made from the template has the label declared by the adoption helper, so the reader skips
the lookup of those ids there and checks their form only. The labels are the ones the repository declares
(`artifact_classification.citation_labels`) and the reader is the one the continuity check uses,
`artifact_classification.split_citations`."""
import os
import re
import subprocess
import unittest

from governance.tools.continuity import artifact_classification as ac
import repo_paths as rp

LABEL = 'template'


def cited(document, labels=()):
    """(full, short) (label, token) pairs a document cites in backticks that must resolve here, read with the declared
    labels (none by default); the short ones need a letter and a digit."""
    found = ac.split_citations(document, labels).local
    full = [pair for pair in found if len(pair[1]) == 40]
    short = [pair for pair in found if len(pair[1]) <= 12 and re.search('[a-f]', pair[1]) and re.search('[0-9]', pair[1])]
    return full, short


def object_type(token):
    """The Git type of the object a token names in this repository, or None."""
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    done = subprocess.run(['git', '-C', str(rp.ROOT), 'cat-file', '-t', token], env=env, capture_output=True, text=True,
                          timeout=60)
    return done.stdout.strip() if done.returncode == 0 else None


def problems_of(name, document, labels=()):
    """What is wrong with the commit ids a document cites: an id that does not resolve, a short id that names no object, a
    commit that is not written with the framework's label."""
    found = []
    full, short = cited(document, labels)
    for label, token in full:
        if object_type(token) != 'commit':
            found.append('%s: `%s` is not a commit of this repository' % (name, token))
    for label, token in short:
        if object_type(token) is None:
            found.append('%s: `%s` names no object of this repository' % (name, token))
    for label, token in full + short:
        if object_type(token) == 'commit' and label != LABEL:
            found.append('%s: the commit `%s` is not written as `%s commit` and the id' % (name, token, LABEL))
    return found


class CitedCommitsTest(unittest.TestCase):
    def test_extraction_separates_commits_from_short_ids(self):
        full, short = cited('`' + 'a1' * 20 + '` and `1a2b3c4` and `deadbeef` and `1234567` and `a1b2c3d4e5f6a`')
        self.assertEqual(full, [(None, 'a1' * 20)])
        self.assertEqual(short, [(None, '1a2b3c4')])

    def test_extraction_keeps_the_label_of_a_phrase(self):
        full, short = cited('template commit `' + 'a1' * 20 + '` and `1a2b3c4`')
        self.assertEqual(full, [('template', 'a1' * 20)])
        self.assertEqual(short, [(None, '1a2b3c4')])

    def test_a_short_tree_id_passes_and_a_foreign_id_does_not(self):
        tree = object_type('HEAD^{tree}')
        self.assertEqual(tree, 'tree')
        self.assertIsNone(object_type('0' * 6 + '1' + 'f' * 33))

    def test_a_bare_commit_id_fails_the_label_requirement(self):
        head = object_type('HEAD')
        self.assertEqual(head, 'commit')
        commit = subprocess.run(['git', '-C', str(rp.ROOT), 'rev-parse', 'HEAD'], capture_output=True, text=True,
                                timeout=60).stdout.strip()
        self.assertEqual(problems_of('x.md', 'template commit `%s`' % commit), [])
        self.assertEqual(problems_of('x.md', 'a short one: template commit `%s`' % commit[:10]), [])
        for text in ('see `%s`' % commit, 'the commit `%s`' % commit, 'lab commit `%s`' % commit, 'see `%s`' % commit[:10]):
            with self.subTest(text=text):
                self.assertEqual(len(problems_of('x.md', text)), 1)

    def test_a_labeled_id_that_does_not_resolve_still_fails(self):
        self.assertEqual(len(problems_of('x.md', 'template commit `' + 'a1' * 20 + '`')), 1)

    def test_a_declared_label_skips_the_lookup_of_its_ids_only(self):
        gone = 'a1' * 20
        self.assertEqual(len(problems_of('x.md', 'template commit `%s`' % gone)), 1)
        self.assertEqual(problems_of('x.md', 'template commit `%s` and template commit `1a2b3c4`' % gone, ('template',)), [])
        self.assertEqual(len(problems_of('x.md', 'see `%s`' % gone, ('template',))), 1)
        self.assertEqual(len(problems_of('x.md', 'lab commit `%s`' % gone, ('template',))), 1)

    def test_every_cited_commit_resolves_and_carries_the_label(self):
        found = []
        root = rp.ROOT / 'governance'
        labels = ac.citation_labels(rp.ROOT)
        for path in sorted(root.rglob('*.md')):
            found += problems_of(path.relative_to(root).as_posix(), path.read_text(encoding='utf-8'), labels)
        self.assertEqual(found, [])


if __name__ == '__main__':
    unittest.main()
