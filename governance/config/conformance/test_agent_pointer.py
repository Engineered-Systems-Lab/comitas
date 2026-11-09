"""The project's AGENTS.md stays a thin pointer to the framework entry, and the entry states the precedence rule.

The framework replaces governance/AGENT-ORIENTATION.md when it is upgraded, so a project keeps its own guidance in
AGENTS.md. That file must lead with the exact pointer line, must stay short, and the entry must keep saying that it
prevails over AGENTS.md on the governed workflow."""
import re
import unittest

from governance.tools.continuity import validate_continuity as vc
from governance.tools.coordination import local_jobs as j
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


MAP = 'governance/READING-MAP.md'
LINK = re.compile(r'\]\(([^)\s]+)\)')


class ReadingMapTest(unittest.TestCase):
    """The reading map is the one source of what a worker reads: its lists name real files and real headings."""

    def resolves(self, target, base='governance'):
        path, _, fragment = target.partition('#')
        file = (rp.ROOT / base / path).resolve()
        self.assertTrue(file.is_file(), target)
        if fragment:
            slugs = [h[0] for h in vc.headings(file.read_text(encoding='utf-8'))]
            self.assertIn(fragment, slugs, target)

    def test_the_two_lists_name_existing_documents_and_sections(self):
        for heading in (j.READING_CORE, j.READING_SECTIONS):
            for item in j.reading_list(rp.ROOT, heading):
                with self.subTest(item=item):
                    self.resolves(item, base='')

    def test_the_core_holds_the_documents_every_worker_reads_in_full(self):
        core = j.reading_list(rp.ROOT, j.READING_CORE)
        for document in (rp.AGENTS, ORIENTATION, 'governance/ROLE-WORKER.md', 'governance/WORKER-HAZARDS.md',
                         'governance/CONSTRAINTS.md', 'governance/CHECKPOINT-WORKFLOW.md'):
            self.assertIn(document, core)
        self.assertTrue(all('#' not in item for item in core), 'the core names whole documents')

    def test_every_link_of_the_by_step_table_resolves(self):
        text = (rp.ROOT / MAP).read_text(encoding='utf-8').replace('\r\n', '\n')
        table = text.split('\n## By step\n', 1)[1]
        targets = LINK.findall(table)
        self.assertTrue(targets)
        for target in targets:
            with self.subTest(target=target):
                self.resolves(target)

    def test_a_step_without_a_preview_still_reads_the_inputs_and_a_layout_change_reads_the_layout_contract(self):
        rows = {line.split('|')[1].strip(): line for line in (rp.ROOT / MAP).read_text(encoding='utf-8').splitlines()
                if line.startswith('| ')}
        prepare = next(row for step, row in rows.items() if step.startswith('Prepare and seal'))
        self.assertIn('CHECKPOINT-TOOL-COMMANDS.md#inputs-and-commands', prepare)
        change = next(row for step, row in rows.items() if step.startswith('Change the checkpoint tool'))
        self.assertIn('CHECKPOINT-TOOL-INTERNALS.md#layout-of-the-tools-commands-and-packages', change)

    def test_every_file_of_the_checkpoint_tool_notes_is_reachable_from_the_map(self):
        text = (rp.ROOT / MAP).read_text(encoding='utf-8')
        for name in sorted(path.name for path in (rp.ROOT / 'governance').glob('CHECKPOINT-TOOL-*.md')):
            self.assertIn('](' + name, text, name)

    def test_the_worker_role_points_to_the_map_and_keeps_no_list_of_its_own(self):
        role = (rp.ROOT / 'governance/ROLE-WORKER.md').read_text(encoding='utf-8')
        section = role.split('## Read before you act', 1)[1].split('\n## ', 1)[0]
        self.assertIn('READING-MAP.md#the-core', section)
        self.assertIn('READING-MAP.md#by-step', section)
        before_hazards = section.split('Read [Worker hazards]')[0]
        for document in ('CONSTRAINTS.md', 'CHECKPOINT-WORKFLOW.md', 'WORKTREE-WORKFLOW.md', 'ARTIFACT-CLASSIFICATION.md'):
            self.assertNotIn('](' + document, before_hazards)


if __name__ == '__main__':
    unittest.main()
