"""The project's AGENTS.md stays a thin pointer to the framework entry, and the entry states the precedence rule.

The framework replaces governance/AGENT-ORIENTATION.md when it is upgraded, so a project keeps its own guidance in
AGENTS.md. That file must lead with the exact pointer line, must stay short, and the entry must keep saying that it
prevails over AGENTS.md on the governed workflow."""
import unittest

import repo_paths as rp

POINTER = 'Read and follow governance/AGENT-ORIENTATION.md before anything else.'
ORIENTATION = 'governance/AGENT-ORIENTATION.md'
MAX_LINES = 30
PRECEDENCE = "this file and the framework's rules prevail"


def lines(relative):
    return (rp.ROOT / relative).read_text(encoding='utf-8').replace('\r\n', '\n').split('\n')


class AgentPointerTest(unittest.TestCase):
    def test_the_pointer_is_the_first_line_that_is_neither_blank_nor_a_heading(self):
        first = next(line for line in lines(rp.AGENTS) if line.strip() and not line.startswith('#'))
        self.assertEqual(first, POINTER)

    def test_agents_stays_short(self):
        count = len((rp.ROOT / rp.AGENTS).read_text(encoding='utf-8').splitlines())
        self.assertLessEqual(count, MAX_LINES, 'project guidance beyond the pointer belongs in docs/')

    def test_the_orientation_states_the_precedence_rule(self):
        text = ' '.join(' '.join(lines(ORIENTATION)).split())
        self.assertIn(PRECEDENCE, text)
        self.assertIn('AGENTS.md', text)


if __name__ == '__main__':
    unittest.main()
