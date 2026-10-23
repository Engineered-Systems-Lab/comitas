"""The workflow carries the rule that a go and an Approve are asked alone, and the worker role links to it (G047).

The rule is written once, in governance/CHECKPOINT-WORKFLOW.md beside the approval words. The worker role links to
that section and does not repeat it. The test pins only that the documents carry the rule; no tool can see how a
worker asks."""
import re
import unittest

import repo_paths as rp

WORKFLOW = 'governance/CHECKPOINT-WORKFLOW.md'
ROLE = 'governance/ROLE-WORKER.md'
HEADING = '#### The go and the Approve are asked alone'
ANCHOR = 'CHECKPOINT-WORKFLOW.md#the-go-and-the-approve-are-asked-alone'
RULE = 'A worker MUST NOT combine the request for a go or an Approve with any other question in one message.'
COMBINED = 'A reply that holds an approval word together with other text is not a go and not an Approve'
UNCHANGED = 'the approval words, the matcher and every behavior of the tools stay exactly as they are'


def text(relative):
    return (rp.ROOT / relative).read_text(encoding='utf-8').replace('\r\n', '\n')


def section(document, heading):
    """The flattened text from the heading up to the next heading of the same or a higher level."""
    start = document.index('\n' + heading + '\n') + 1
    level = len(heading) - len(heading.lstrip('#'))
    end = re.compile(r'^#{1,%d} ' % level, re.MULTILINE).search(document, start + len(heading))
    return ' '.join(document[start:end.start() if end else None].split())


class GoAskedAloneTest(unittest.TestCase):
    def test_the_workflow_section_carries_the_rule(self):
        body = section(text(WORKFLOW), HEADING)
        for phrase in (RULE, COMBINED, UNCHANGED):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, body)

    def test_the_section_sits_under_the_approval_words(self):
        document = text(WORKFLOW)
        self.assertLess(document.index('\n### Approval words\n'), document.index('\n' + HEADING + '\n'))

    def test_the_worker_role_links_to_the_rule_and_does_not_repeat_it(self):
        role = ' '.join(text(ROLE).split())
        self.assertIn(ANCHOR, role)
        self.assertNotIn(RULE, role)


if __name__ == '__main__':
    unittest.main()
