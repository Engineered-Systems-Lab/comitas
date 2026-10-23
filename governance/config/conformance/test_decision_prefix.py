"""The framework's decisions carry the prefix G; no D-prefixed framework decision reference remains under governance/ (G049).

A project's own decisions carry the prefix D, so the rule is scoped to the framework's files: every tracked text file
under `governance/`. History lives in commit messages, which are never rewritten and are not files. A reference is a D
followed by exactly three digits that is not part of a longer word, or the lowercase anchor form of it (`#d` and three
digits, or the same after an underscore or a hyphen), which is how a heading of the Decisions document becomes an anchor.

`EXCEPTIONS` is the narrow list of exact phrases that may spell such a reference, each keyed by its file and carrying
its reason. A phrase exempts only the tokens inside it, so a stale framework reference on any other line, or elsewhere
on the same line, is still found. Such a phrase describes a project's own numbering (the prefix D is the project's
by G049), never a framework decision. The Mission Control tests that exercise a project's D-prefixed registry derive
the text from the framework's G-prefixed fixture instead of writing it out."""
import re
import subprocess
import unittest

import repo_paths as rp

UPPER = re.compile(r'(?<![0-9A-Za-z])D\d{3}(?![0-9A-Za-z])')
LOWER = re.compile(r'(?<=[#_\-\'"`(])d\d{3}(?=[-_()\'"`\s.,;:\]]|$)')
# relative POSIX path -> {exact phrase: reason}; a token is exempt only inside one of the file's phrases
EXCEPTIONS = {
    'governance/PROJECT-GOVERNANCE.md': {
        '`## Checkpoints: D' + '001`': 'the example heading of a project rationale section, which names a project decision (G049)',
    },
}


def references(text, phrases=()):
    """The D-prefixed decision references in a text, as (line number, matched text), apart from those inside a phrase."""
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        spans = [(m.start(), m.end()) for phrase in phrases for m in re.finditer(re.escape(phrase), line)]
        found += [(number, m.group(0)) for pattern in (UPPER, LOWER) for m in pattern.finditer(line)
                  if not any(start <= m.start() and m.end() <= end for start, end in spans)]
    return found


def framework_files():
    done = subprocess.run(['git', '-C', str(rp.ROOT), 'ls-files', '-z', 'governance'], capture_output=True, check=True)
    return [name for name in done.stdout.decode('utf-8').split('\0') if name]


class DecisionPrefixTest(unittest.TestCase):
    def test_the_detector_finds_both_spellings_and_spares_other_words(self):
        old = 'D' + '018'
        self.assertEqual(references('see ' + old + ' and (' + old + ')'), [(1, old), (1, old)])
        self.assertEqual(references('a [link](DECISIONS.md#d' + '018---title)'), [(1, 'd' + '018')])
        self.assertEqual(references('a_checkpoint_d' + '018(self)'), [(1, 'd' + '018')])
        self.assertEqual(references('G018 and #g018---title and ID018 and D0180 and D18 and a3d018f'), [])

    def test_a_phrase_exempts_only_its_own_tokens(self):
        old = 'D' + '018'
        phrase = '`## Title: ' + old + '`'
        self.assertEqual(references('example ' + phrase, [phrase]), [])
        self.assertEqual(references('example ' + phrase + ' and ' + old, [phrase]), [(1, old)])
        self.assertEqual(references('example ' + old, [phrase]), [(1, old)])

    def test_no_d_prefixed_framework_decision_reference_remains(self):
        problems = []
        for name in framework_files():
            data = (rp.ROOT / name).read_bytes()
            if b'\0' in data or name in EXCEPTIONS:
                continue
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                continue
            problems += ['%s:%d %s' % (name, line, token) for line, token in references(text, EXCEPTIONS.get(name, {}))]
        self.assertEqual(problems, [], 'write the framework decision with the prefix G (G049)')

    def test_every_exception_still_needs_its_place(self):
        files = set(framework_files())
        for name, phrases in EXCEPTIONS.items():
            self.assertIn(name, files)
            text = (rp.ROOT / name).read_text(encoding='utf-8')
            for phrase, reason in phrases.items():
                self.assertTrue(reason.strip())
                self.assertTrue(references(phrase), phrase + ' holds no reference')
                self.assertIn(phrase, text, phrase + ' no longer needs its exception')

    def test_the_registry_numbers_only_with_the_prefix_g(self):
        text = (rp.ROOT / 'governance' / 'DECISIONS.md').read_text(encoding='utf-8')
        rows = re.findall(r'^\| ([A-Z])(\d{3})[^|]* \|', text, re.M)
        headings = re.findall(r'^## ([A-Z])(\d{3}) - ', text, re.M)
        self.assertTrue(rows and headings)
        self.assertEqual({letter for letter, _ in rows + headings}, {'G'})
        self.assertIn(('G', '049'), headings)


if __name__ == '__main__':
    unittest.main()
