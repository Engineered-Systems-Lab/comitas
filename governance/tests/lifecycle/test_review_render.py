"""The sealed review layout is pinned: one deterministic Markdown text built only from the sealed packet."""
from contextlib import redirect_stdout
import base64
import copy
import io
import json
from pathlib import Path
import unittest
import unittest.mock

import checkpoint as c
from governance.tools.lifecycle import review_render as r
from governance.tests.lifecycle import test_checkpoint as fixtures

DIGEST = 'a' * 64
MESSAGE = 'Add the thing\n\n## Part\n- do it\n\nClass: tooling\nWorker: Human\n'
DIFF = '''diff --git a/docs/a.md b/docs/a.md
index 1111111..2222222 100644
--- a/docs/a.md
+++ b/docs/a.md
@@ -1,2 +1,3 @@
 keep
--gone
+added
+added too
diff --git a/old.txt b/old.txt
deleted file mode 100644
index 3333333..0000000
--- a/old.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..4444444
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,2 @@
+a
+b
'''


def view(klass, subclass=(), boundary=(), bound=False):
    return {'resolved': True, 'class': klass, 'subclass': list(subclass), 'boundary': list(boundary), 'bound': bound}


def packet():
    return {
        'id': 'f' * 32, 'job_handle': 'example_job', 'state': 'prepared', 'parent': '1234567890abcdef', 'tree': 'fedcba0987654321',
        'branch': 'refs/heads/tx/20260101-example', 'profile': 'governance+tooling',
        'repo': {'root': 'C:\\dev\\project-example', 'gitdir': 'x', 'common': 'y', 'owner': 'transaction:20260101-example'},
        'manifest': ['docs/a.md', 'old.txt', 'src/new.py'], 'diff': DIFF,
        'message_b64': base64.b64encode(MESSAGE.encode()).decode(), 'message_sha256': 'b' * 64,
        'prepared_status': 'M  docs/a.md\0D  old.txt\0A  src/new.py\0?? notes.tmp\0 M stray.py\0',
        'evidence': [
            {'id': 'staged-whitespace', 'exit_code': 0, 'duration_seconds': 0.0},
            {'id': 'continuity', 'exit_code': 0, 'duration_seconds': 6.25},
            {'id': 'regressions', 'exit_code': 0, 'duration_seconds': 130.0},
            {'id': 'replayed', 'exit_code': 0, 'duration_seconds': 50.0}],
        'validation_runs': [{'id': 'continuity', 'action': 'executed'}, {'id': 'regressions', 'action': 'executed'},
                            {'id': 'replayed', 'action': 'reused'}],
        'timing_estimate': {'basis': 'partial', 'expected_seconds': 136.0, 'high_seconds': 140.0, 'gaps': ['check:replayed'],
                            'summary': 'At least 2m 16s; no usable history for: replayed.',
                            'checks': {'continuity': {'mean': 6.0, 'n': 5, 'basis': 'measured'},
                                       'regressions': {'mean': 100.0, 'n': 2, 'basis': 'thin'},
                                       'replayed': {'mean': None, 'n': 0, 'basis': 'none'},
                                       'staged-whitespace': {'mean': 0.0, 'n': 5, 'basis': 'measured'}}},
        'timing_observations': [{'subject': 'check:regressions', 'kind': 'slower', 'seconds': 130.0, 'mean': 100.0, 'runs': 2}],
        'profile_data': {
            'categories': ['governance', 'tooling'], 'limitations': 'No component or runtime acceptance.',
            'classification': {'docs/a.md': 'governance', 'old.txt': 'tooling', 'src/new.py': 'tooling'},
            'artifact_classification': {'docs/a.md': view('governance', ['decisions'], bound=True),
                                        'old.txt': view('tooling'),
                                        'src/new.py': view('tooling', ['checkpoint'], ['runtime'])},
            'impact': {'docs/a.md': {'mode': 'mapped'}, 'old.txt': {'mode': 'broad'}, 'src/new.py': {'mode': 'mapped'}},
            'commit_metadata': {'worker': 'Human'}}}


GOLDEN = '''## Sealed review

| Item | Value |
| --- | --- |
| Job | `example_job` |
| Worktree | `C:\\dev\\project-example` |
| Branch | `tx/20260101-example` |
| Parent | `1234567` |
| Tree | `fedcba0` |
| Worker | Human |
| Transaction | `ffffffffffffffffffffffffffffffff` |
| State | prepared |
| Digest | `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` |

### Manifest: 3 paths, governance + tooling (mixed), profile `governance+tooling`, +4 -3 lines

| Path | Class | Subclass | Flags | Change |
| --- | --- | --- | --- | --- |
| `docs/a.md` | governance | decisions | bound | +2 -1 |
| `old.txt` | tooling | - | broad | deleted -2 |
| `src/new.py` | tooling | checkpoint | runtime | new +2 |

### Checks

4 of 4 checks passed, 1 reused. Took 2m 16s. Estimate when sealing began: At least 2m 16s; no usable history for: replayed.

| Check | Result | Seconds | Expected | Run | Flag |
| --- | --- | --- | --- | --- | --- |
| staged-whitespace | pass | 0.0 | 0.0 (n=5) | executed | - |
| continuity | pass | 6.2 | 6.0 (n=5) | executed | - |
| regressions | pass | 130.0 | 100.0 (n=2, thin) | executed | slower |
| replayed | pass | 50.0 | - | reused | - |

### Limits

No component or runtime acceptance.

### Outside the manifest

Not part of this commit:
- ` M stray.py`
- `?? notes.tmp`

### Exact commit message

```
Add the thing

## Part
- do it

Class: tooling
Worker: Human
```

SHA-256 `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`, 61 bytes. Escaped form, which shows trailing spaces and line endings:

```
'Add the thing\\n\\n## Part\\n- do it\\n\\nClass: tooling\\nWorker: Human\\n'
```

### Decision

Choose Approve or Reject, or reply **go**, **approve**, **approved**, **proceed** or **do it** to approve, or **reject** to reject. The decision is bound to the digest above.
'''


class RenderTest(unittest.TestCase):
    def test_the_layout_is_pinned(self):
        self.assertEqual(r.render(packet(), DIGEST), GOLDEN)

    def test_a_link_only_path_is_flagged(self):
        value = packet()
        value['profile_data']['artifact_classification']['docs/a.md']['link_retarget'] = [
            {'old': 'X.md', 'new': '../governance/X.md'}]
        self.assertIn('link-only', r.flags('docs/a.md', value['profile_data']))
        self.assertNotIn('link-only', r.flags('src/new.py', value['profile_data']))

    def test_it_is_deterministic_and_does_not_touch_the_packet(self):
        value = packet()
        before = copy.deepcopy(value)
        self.assertEqual(r.render(value, DIGEST), r.render(value, DIGEST))
        self.assertEqual(value, before)

    def test_sections_come_in_the_agreed_order(self):
        text = r.render(packet(), DIGEST)
        headings, inside = [], False
        for line in text.splitlines():  # headings inside the fenced message are the message's, not the layout's
            inside ^= line.startswith('```')
            if line.startswith('#') and not inside:
                headings.append(line)
        self.assertEqual([h.split(':')[0] for h in headings],
                         ['## Sealed review', '### Manifest', '### Checks', '### Limits', '### Outside the manifest',
                          '### Exact commit message', '### Decision'])
        self.assertNotIn('Review focus', text)  # judgment is the agent's, never generated

    def test_the_job_comes_first_then_the_worktree_and_branch(self):
        first = r.render(packet(), DIGEST).splitlines()[4:7]
        self.assertEqual(first, ['| Job | `example_job` |', '| Worktree | `C:\\dev\\project-example` |',
                                 '| Branch | `tx/20260101-example` |'])

    def test_an_undeclared_job_shows_a_hyphen(self):
        undeclared = packet()
        del undeclared['job_handle']
        self.assertEqual(r.render(undeclared, DIGEST).splitlines()[4], '| Job | - |')
        undeclared['job_handle'] = None
        self.assertEqual(r.render(undeclared, DIGEST).splitlines()[4], '| Job | - |')

    def test_a_clean_tree_says_so_and_a_missing_status_says_that_instead(self):
        clean = packet()
        clean['prepared_status'] = 'M  docs/a.md\0D  old.txt\0A  src/new.py\0'
        self.assertIn('Clean: nothing else is modified or untracked.', r.render(clean, DIGEST))
        del clean['prepared_status']
        self.assertIn('Not recorded in this packet.', r.render(clean, DIGEST))

    def test_without_an_estimate_the_wording_says_why(self):
        off = packet()
        del off['timing_estimate'], off['timing_observations']
        self.assertIn('The timing record is off, so there was no estimate.', r.render(off, DIGEST))
        old = packet()
        del old['timing_estimate']
        self.assertIn('sealed before estimates were captured', r.render(old, DIGEST))

    def test_an_unavailable_record_is_stated_not_hidden(self):
        broken = packet()
        broken['timing_estimate'] = {'unavailable': 'line 1: bad'}
        broken['timing_observations'] = {'unavailable': 'line 1: bad'}
        text = r.render(broken, DIGEST)
        self.assertIn('The estimate was unavailable: line 1: bad', text)
        self.assertIn('Departure flags were unavailable: line 1: bad', text)

    def test_failures_are_visible_in_capitals(self):
        failing = packet()
        failing['evidence'][1]['exit_code'] = 1
        text = r.render(failing, DIGEST)
        self.assertIn('| continuity | FAIL |', text)
        self.assertIn('3 of 4 checks passed', text)

    def test_trailing_spaces_are_visible_in_the_escaped_form(self):
        spaced = packet()
        message = 'Title' + chr(32) * 2 + chr(10)
        spaced['message_b64'] = base64.b64encode(message.encode()).decode()
        self.assertIn(chr(39) + 'Title' + chr(32) * 2 + chr(92) + 'n' + chr(39), r.render(spaced, DIGEST))

    def test_a_message_containing_backticks_gets_a_longer_fence(self):
        tricky = packet()
        message = 'Title\n\n```\ncode\n```\n'
        tricky['message_b64'] = base64.b64encode(message.encode()).decode()
        text = r.render(tricky, DIGEST)
        self.assertIn('````\nTitle\n\n```\ncode\n```\n````', text)

    def test_pipes_and_newlines_cannot_break_a_table(self):
        odd = packet()
        odd['manifest'] = ['a|b.txt']
        odd['profile_data']['classification'] = {'a|b.txt': 'tool|ing'}
        odd['diff'] = ''
        row = next(l for l in r.render(odd, DIGEST).splitlines() if 'a\\|b' in l)
        self.assertEqual(row.count('|') - row.count('\\|'), 6)

    def test_binary_and_unknown_changes_do_not_invent_counts(self):
        binary = packet()
        binary['diff'] = ('diff --git a/docs/a.md b/docs/a.md\nindex 1..2 100644\nGIT binary patch\nliteral 1\nAA\n\n'
                          'diff --git a/old.txt b/old.txt\ndeleted file mode 100644\n@@ -1 +0,0 @@\n-x\n')
        text = r.render(binary, DIGEST)
        self.assertIn('| `docs/a.md` | governance | decisions | bound | binary |', text)
        self.assertIn('| `src/new.py` | tooling | checkpoint | runtime | changed |', text)

    def test_a_text_file_that_quotes_the_binary_markers_is_still_text(self):
        quoting = packet()
        quoting['manifest'] = ['docs/a.md']
        quoting['diff'] = ('diff --git a/docs/a.md b/docs/a.md\nindex 1..2 100644\n--- a/docs/a.md\n+++ b/docs/a.md\n'
                           '@@ -1 +1,3 @@\n keep\n+if "GIT binary patch" in block\n+\n+Binary files differ\n')
        self.assertIn('| `docs/a.md` | governance | decisions | bound | +3 |', r.render(quoting, DIGEST))

    def test_a_legacy_packet_without_the_newer_fields_still_renders(self):
        legacy = {k: v for k, v in packet().items() if k not in (
            'timing_estimate', 'timing_observations', 'prepared_status', 'repo')}
        legacy['profile_data'] = {'limitations': 'x'}
        text = r.render(legacy, DIGEST)
        self.assertIn('| Worktree | `-` |', text)
        self.assertIn('| Job | `example_job` |', text)
        del legacy['job_handle']
        self.assertIn('| Job | - |', r.render(legacy, DIGEST))
        self.assertIn('| `docs/a.md` | - | - | - |', text)


GOLDEN_PREVIEW = '''## Preview

| Item | Value |
| --- | --- |
| Job | `example_job` |
| Workspace | `C:\\dev\\project-example` |
| Branch | `tx/20260101-example` |
| Parent | `1234567` |
| Tree | `fedcba0` |
| Preview | `cccccccccccccccccccccccccccccccc` |
| Early approval reach | `medium` |

Nothing is staged or sealed yet and no suite has run. You can read the change in the workspace folder in IntelliJ or another IDE.

### Manifest: 3 paths, governance + tooling (mixed), profile `governance+tooling`, +4 -3 lines

| Path | Class | Subclass | Sensitivity | Flags | Change |
| --- | --- | --- | --- | --- | --- |
| `docs/a.md` | governance | decisions | medium (HEAD low, candidate medium) | bound, approval-medium, sensitivity-raised | +2 -1 |
| `old.txt` | tooling | - | low | broad | deleted -2 |
| `src/new.py` | tooling | checkpoint | no row | runtime | new +2 |

### Derived checks

These run when the candidate is sealed, and the worker cannot reduce the list. Estimate: About 2m 16s.

| Check | Expected seconds |
| --- | --- |
| staged-whitespace | 0.0 (n=5) |
| continuity | 6.0 (n=5) |
| regressions | 100.0 (n=2, thin) |

Limits: No component or runtime acceptance.

### Draft commit message

```
Add the thing

## Part
- do it

Class: tooling
Worker: Human
```

SHA-256 `bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb`, 61 bytes. Escaped form, which shows trailing spaces and line endings:

```
'Add the thing\\n\\n## Part\\n- do it\\n\\nClass: tooling\\nWorker: Human\\n'
```

### Early approval

Not eligible:
- paths above the early approval reach (medium) or unclassified: src/new.py

### Review focus

The worker replaces this line with its own Review focus and adds nothing else.

### Go file values

The go file names this preview by these two values; copy them, compute nothing.

```
preview_id: cccccccccccccccccccccccccccccccc
preview_sha256: 2f69aef9977083479f793d329f7a4d6d80ff45c31199ecbf7f31fd590c67e97e
```

### Go

Reply **go**, **approve**, **approved**, **proceed** or **do it** to stage and seal. This manifest is not eligible, so the go only stages and seals; the sealed review and its Approve decide.
'''


def preview_facts(eligible=False):
    value = packet()
    views = value['profile_data']['artifact_classification']
    views['docs/a.md'].update(approval_sensitivity='medium', head_sensitivity='low', candidate_sensitivity='medium')
    views['old.txt'].update(approval_sensitivity='low', head_sensitivity='low', candidate_sensitivity='low')
    views['src/new.py'] = dict(views['src/new.py'], resolved=False)
    body = {'id': 'c' * 32, 'branch': value['branch'], 'parent': '1234567890abcdef', 'tree': 'fedcba0987654321',
            'manifest': value['manifest'], 'message_sha256': 'b' * 64, 'check_ids': ['continuity', 'regressions'],
            'switch': {'on': True, 'reach': 'medium', 'reason': 'reach medium'}, 'eligible': eligible,
            'reasons': [] if eligible else ['paths above the early approval reach (medium) or unclassified: src/new.py']}
    return {'body': body, 'profile': 'governance+tooling', 'profile_data': value['profile_data'], 'diff': DIFF,
            'message': MESSAGE, 'workspace': 'C:\\dev\\project-example', 'job_handle': 'example_job',
            'estimate': {'summary': 'About 2m 16s.', 'checks': value['timing_estimate']['checks']}}


class PreviewRenderTest(unittest.TestCase):
    def test_the_layout_is_pinned(self):
        self.assertEqual(r.render_preview(preview_facts()), GOLDEN_PREVIEW)

    def test_it_is_deterministic_and_does_not_touch_its_input(self):
        facts = preview_facts()
        before = copy.deepcopy(facts)
        self.assertEqual(r.render_preview(facts), r.render_preview(facts))
        self.assertEqual(facts, before)

    def test_an_eligible_preview_says_the_go_approves_the_exact_candidate(self):
        text = r.render_preview(preview_facts(eligible=True))
        self.assertIn('Eligible: every path is within the early approval reach `medium`', text)
        self.assertIn('to approve this exact candidate (tree `fedcba0`) if every check passes', text)
        self.assertNotIn('the go only stages and seals', text)

    def test_without_an_estimate_it_says_so_and_a_missing_job_is_a_hyphen(self):
        facts = preview_facts()
        facts.update(estimate=None, job_handle=None)
        text = r.render_preview(facts)
        self.assertIn('No estimate: the timing record is off or unavailable.', text)
        self.assertIn('| Job | - |', text)
        self.assertIn('| continuity | - |', text)

    def test_the_go_file_values_are_what_the_go_check_computes(self):
        from governance.tools.lifecycle import early_approval as ea
        facts = preview_facts()
        text = r.render_preview(facts)
        self.assertIn('preview_id: ' + facts['body']['id'] + '\n', text)
        self.assertIn('preview_sha256: ' + ea.digest(ea.canonical(facts['body'])) + '\n', text)

    def test_the_estimate_command_form_with_a_list_of_checks_renders_the_same_times(self):
        facts = preview_facts()
        listed = copy.deepcopy(facts)
        listed['estimate']['checks'] = [dict(item, subject='check:' + name)
                                        for name, item in facts['estimate']['checks'].items()]
        self.assertEqual(r.render_preview(listed), r.render_preview(facts))

    def test_a_message_with_backticks_and_pipes_cannot_break_the_block(self):
        facts = preview_facts()
        facts['message'] = 'Subject\n\n```\n- a | b\n'
        facts['profile_data']['classification']['docs/a.md'] = 'gov|ernance'
        text = r.render_preview(facts)
        self.assertIn('````\nSubject\n\n```\n- a | b\n````', text)
        self.assertIn('gov\\|ernance', text)


class TransactionTest(unittest.TestCase):
    """The checkpoint attaches the review and captures the estimate before the checks begin."""

    def setUp(self):
        self.f = fixtures.CheckpointTest()
        self.addCleanup(self.f.doCleanups)
        self.f.setUp()
        self.tool = self.f.tool

    def enable_timing(self):
        self.f.policy.update(timing_record={})
        self.f.profiles['profiles']['test']['limitations'] = 'Disposable review fixture.'
        self.f.write_json(c.POLICY, self.f.policy)
        self.f.write_json(c.PROFILES, self.f.profiles)

    def test_present_returns_the_review_with_the_worktree_and_digest(self):
        self.f.prepare()
        shown = self.tool.present()
        self.assertEqual(shown['review'].splitlines()[0], '## Sealed review')
        self.assertIn(shown['prepared_sha256'], shown['review'])
        self.assertIn(str(self.f.root), shown['review'])
        self.assertIn('| `a.txt` |', shown['review'])
        self.assertNotIn('review_error', shown)

    def test_the_estimate_is_sealed_before_this_runs_own_times_exist(self):
        self.enable_timing()
        first = self.f.prepare()
        self.assertEqual(first['timing_estimate']['basis'], 'none')
        self.assertIsNone(first['timing_estimate']['expected_seconds'])
        self.assertIn('no estimate', first['timing_estimate']['summary'])
        self.tool.abandon()
        second = self.f.prepare()
        self.assertEqual(second['timing_estimate']['basis'], 'partial')  # one earlier run is too few, not none
        self.assertIn('Estimate when sealing began', self.tool.present()['review'])

    def test_a_policy_without_timing_seals_no_estimate(self):
        self.assertNotIn('timing_estimate', self.f.prepare())

    def test_a_rendering_fault_never_blocks_the_decision(self):
        self.f.prepare()
        with unittest.mock.patch.object(r, 'render', side_effect=KeyError('boom')):
            shown = self.tool.present()
        self.assertIn('review_error', shown)
        self.assertEqual(shown['approval_request']['prepared_sha256'], shown['prepared_sha256'])

    def test_a_declared_job_handle_is_sealed_shown_first_and_bound_by_the_digest(self):
        sealed = self.tool.prepare(['a.txt'], self.f.message, 'test', self.f.parent, 'refs/heads/main',
                                   'Lead developer delegated this exact fixture scope', job_handle='example_job')
        self.assertEqual(sealed['job_handle'], 'example_job')
        shown = self.tool.present()
        self.assertEqual(shown['review'].splitlines()[4], '| Job | `example_job` |')
        changed = dict(shown['packet'], job_handle='other_job')
        self.assertNotEqual(c.digest(c.canonical(changed)), shown['prepared_sha256'])
        self.assertNotIn('example_job', base64.b64decode(sealed['message_b64']).decode())

    def test_an_epic_and_job_reference_is_sealed_and_shown_as_given_in_the_one_job_row(self):
        sealed = self.tool.prepare(['a.txt'], self.f.message, 'test', self.f.parent, 'refs/heads/main',
                                   'Lead developer delegated this exact fixture scope', job_handle='service_build--service_read')
        self.assertEqual(sealed['job_handle'], 'service_build--service_read')
        shown = self.tool.present()
        lines = shown['review'].splitlines()
        self.assertEqual(lines[4], '| Job | `service_build--service_read` |')
        self.assertEqual(sum(1 for line in lines if line.startswith('| Job |')), 1)
        changed = dict(shown['packet'], job_handle='service_read')
        self.assertNotEqual(c.digest(c.canonical(changed)), shown['prepared_sha256'])
        self.assertNotIn('service_build', base64.b64decode(sealed['message_b64']).decode())

    def test_an_undeclared_job_handle_is_sealed_as_null(self):
        self.assertIsNone(self.f.prepare()['job_handle'])
        self.assertEqual(self.tool.present()['review'].splitlines()[4], '| Job | - |')

    def test_an_invalid_job_handle_stops_before_staging(self):
        for value in ('', 'Upper_case', 'has-hyphen', 'trailing_', '_leading', 'coordinator', 'x' * 65, 42,
                      'epic-job', 'epic--job--extra', 'epic---job', '--job', 'epic--', 'Epic--job', 'epic--Job',
                      'epic__job--', 'epic--coordinator', 'x' * 65 + '--job'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'job handle'):
                self.tool.prepare(['a.txt'], self.f.message, 'test', self.f.parent, 'refs/heads/main',
                                  'Lead developer delegated this exact fixture scope', job_handle=value)
        self.assertEqual(self.tool.inventory(), [])

    def test_the_text_flag_prints_only_the_review(self):
        self.f.prepare()
        out = io.StringIO()
        out.buffer = io.BytesIO()
        with unittest.mock.patch.object(c.sys, 'argv', ['checkpoint.py', '--repo', str(self.f.root), 'present', '--text']), \
                unittest.mock.patch.object(c.sys, 'stdout', out):
            self.assertEqual(c.main(), 0)
        text = out.buffer.getvalue().decode('utf-8')
        self.assertTrue(text.startswith('## Sealed review\n'))
        self.assertNotIn('"packet"', text)


if __name__ == '__main__':
    unittest.main()
