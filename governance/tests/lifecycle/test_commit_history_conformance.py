"""Every commit after the boundary follows the commit-message convention: the strict trailer parse and a `Status:`
line that is its own paragraph outside the trailer block. Every commit after the trailer boundary also holds no trailer
outside the allowlist (Class, Subclass, Worker); the commits up to it are history and keep their other trailers.

The check reads the Git history of this checkout. It skips, with its reason, when there is no usable history: not a
Git checkout, a shallow clone, or a clone that does not hold the boundary commit, so archives and shallow clones
never fail. Commits at or before the boundary predate the convention and are never checked."""
import json
import re
import subprocess
import unittest

from governance.tools.lifecycle import commit_metadata as m
import repo_paths as rp

# The last commit that is NOT checked; every commit after it is.
BOUNDARY = 'a97e9fdd02855cdc0670d46701da09cf81a386c8'
# The last commit whose trailer block may hold a key outside the allowlist; every commit after it may not.
TRAILER_BOUNDARY = 'c708fff89f8f80f4f74cb210145c6454ff82381b'


def git(*args):
    """Run a read-only Git command in the repository root; None when it cannot run or fails."""
    try:
        result = subprocess.run(['git', '-C', str(rp.ROOT)] + list(args), capture_output=True)
    except OSError:
        return None
    return result.stdout.decode('utf-8') if result.returncode == 0 else None


def history_problem():
    """The reason there is no usable history to check, or None when there is one."""
    inside = git('rev-parse', '--is-inside-work-tree')
    if inside is None or inside.strip() != 'true':
        return 'not a Git checkout'
    shallow = git('rev-parse', '--is-shallow-repository')
    if shallow is None or shallow.strip() != 'false':
        return 'shallow clone'
    if git('cat-file', '-e', BOUNDARY + '^{commit}') is None:
        return 'boundary commit ' + BOUNDARY[:7] + ' is not in this repository'
    if git('rev-parse', 'HEAD') is None or git('merge-base', '--is-ancestor', BOUNDARY, 'HEAD') is None:
        return 'boundary commit ' + BOUNDARY[:7] + ' is not an ancestor of HEAD'
    return None


def checked_commits():
    """(commit id, message) of every commit after the boundary, oldest first."""
    ids = git('rev-list', '--reverse', BOUNDARY + '..HEAD').split()
    return [(commit, git('show', '-s', '--format=%B', commit)) for commit in ids]


def before_the_allowlist():
    """The checked commits up to the trailer boundary, whose other trailers are history."""
    return set((git('rev-list', BOUNDARY + '..' + TRAILER_BOUNDARY) or '').split())


def status_problems(message):
    """Why a `Status:` line is misplaced in the message; an empty list when every one is a single-line paragraph
    that is neither the subject nor the final trailer block."""
    paragraphs = re.split(r'\n[ \t]*\n', message.replace('\r\n', '\n').strip('\n'))
    problems = []
    for index, paragraph in enumerate(paragraphs):
        lines = paragraph.split('\n')
        found = [line for line in lines if re.match(r'\s*Status\s*:', line, re.IGNORECASE)]
        if not found:
            continue
        if index == 0:
            problems.append('Status line in the subject paragraph')
        elif index == len(paragraphs) - 1:
            problems.append('Status line inside the final trailer block')
        elif len(lines) != 1 or not re.fullmatch(r'Status: \S.*', lines[0]):
            problems.append('Status line must be its own paragraph of the form "Status: text"')
    return problems


class CommitHistoryConformanceTest(unittest.TestCase):
    def test_every_commit_after_the_boundary_follows_the_convention(self):
        reason = history_problem()
        if reason:
            self.skipTest('no usable Git history to check: ' + reason)
        profiles = json.loads((rp.ROOT / rp.PROFILES).read_text(encoding='utf-8'))
        commits = checked_commits()
        self.assertTrue(commits, 'the boundary is HEAD itself, so nothing is checked')
        history = before_the_allowlist()
        for commit, message in commits:
            with self.subTest(commit=commit[:7]):
                m.parse_metadata(message.rstrip('\n'), profiles, required=True, other_trailers=commit in history)
                self.assertEqual(status_problems(message), [])


class StatusPlacementTest(unittest.TestCase):
    """The placement rule, on synthetic messages, so it is pinned even where the history is not available."""
    TRAILERS = 'Class: tooling\nWorker: Human'

    def message(self, middle):
        return 'Subject line\n\n' + middle + self.TRAILERS + '\n'

    def test_no_status_line_is_fine(self):
        self.assertEqual(status_problems(self.message('- Detail.\n\n')), [])

    def test_a_status_paragraph_before_the_trailers_is_fine(self):
        self.assertEqual(status_problems(self.message('- Detail.\n\nStatus: design only\n\n')), [])

    def test_a_status_line_inside_the_trailer_block_is_refused(self):
        self.assertEqual(status_problems('Subject\n\n- Detail.\n\nStatus: tests pass\nClass: tooling\nWorker: Human\n'),
                         ['Status line inside the final trailer block'])
        self.assertEqual(status_problems(self.message('- Detail.\n\n').replace('Class:', 'Status: x\nClass:')),
                         ['Status line inside the final trailer block'])

    def test_a_status_line_glued_to_the_body_is_refused(self):
        self.assertEqual(status_problems(self.message('- Detail.\nStatus: nothing implemented\n\n')),
                         ['Status line must be its own paragraph of the form "Status: text"'])

    def test_a_status_line_in_the_subject_paragraph_is_refused(self):
        self.assertEqual(status_problems('Status: draft\n\n- Detail.\n\n' + self.TRAILERS + '\n'),
                         ['Status line in the subject paragraph'])

    def test_a_split_trailer_block_is_refused_by_the_strict_parser(self):
        profiles = json.loads((rp.ROOT / rp.PROFILES).read_text(encoding='utf-8'))
        with self.assertRaises(ValueError):
            m.parse_metadata('Subject\n\n- Detail.\n\nClass: tooling\n\nWorker: Human\n', profiles, required=True)


if __name__ == '__main__':
    unittest.main()
