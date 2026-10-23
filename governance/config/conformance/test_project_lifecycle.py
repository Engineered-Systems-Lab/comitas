"""Every command and script that governance/PROJECT-LIFECYCLE.md names exists at its path.

The page is a selection of what a person runs, not a list of every tool, so this test checks only that nothing it names is
missing: each path that starts with `governance/` exists, and the subcommands it gives to the claims script and to the job
helper are the ones those files accept. It does not check that the page names every command."""
import re
import unittest

import repo_paths as rp

PAGE = rp.ROOT / 'governance' / 'PROJECT-LIFECYCLE.md'
PATH = re.compile(r'(?<![\w./-])governance/[A-Za-z0-9_./-]*[A-Za-z0-9_/]')


def named_paths(text):
    """The sorted set of repository paths under governance/ that the text names."""
    return sorted({m.group() for m in PATH.finditer(text) if text[m.end():m.end() + 1] != '<'})


class ProjectLifecyclePageTest(unittest.TestCase):
    def setUp(self):
        self.text = PAGE.read_text(encoding='utf-8')

    def test_the_path_reader_finds_a_path_and_skips_a_placeholder(self):
        text = 'run `python -B governance/tools/adopt.py` or governance/scripts/claims.sh, not `governance/tools/<tool>.py` or project-governance/x.json.'
        self.assertEqual(named_paths(text), ['governance/scripts/claims.sh', 'governance/tools/adopt.py'])

    def test_every_governance_path_the_page_names_exists(self):
        paths = named_paths(self.text)
        self.assertIn('governance/tools/adopt.py', paths)
        missing = [path for path in paths if not (rp.ROOT / path).exists()]
        self.assertEqual(missing, [])

    def test_the_claims_script_accepts_the_words_the_page_gives_it(self):
        script = (rp.ROOT / 'governance' / 'scripts' / 'claims.sh').read_text(encoding='utf-8')
        for word in sorted(set(re.findall(r'claims\.sh (status|watch|clear|doctor)\b', self.text))):
            self.assertIn(word + ')', script.replace('|', ')\n'), word)

    def test_the_job_helper_has_the_claim_command_the_page_gives_it(self):
        self.assertIn('claim-coordinator', self.text)
        source = (rp.ROOT / 'governance' / 'tools' / 'coordination' / 'local_jobs.py').read_text(encoding='utf-8')
        self.assertIn("'claim-coordinator'", source)


if __name__ == '__main__':
    unittest.main()
