"""The diff viewer on a disposable repository: the page shows the recorded candidate, labels it, escapes it and refuses a sealed diff that differs."""
import base64
import hashlib
import hmac
import io
from html.parser import HTMLParser
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
import subprocess
import tempfile
import unittest
from unittest import mock

import render

TRICKY = "a&b 'x'.txt"
ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, env=ENV).stdout.decode('utf-8').strip()


def write(root, name, data):
    (Path(root) / name).write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


class PageLinkTest(unittest.TestCase):
    def test_link_text_and_target_both_equal_the_encoded_file_uri(self):
        cases = (
            PureWindowsPath('C:/review/diff.html'),
            PureWindowsPath('C:/review folder/diff (1) [x].html'),
            PurePosixPath('/review/café [x]#%.html'),
            PurePosixPath('/review/a>b\nc.html'),
        )
        for path in cases:
            with self.subTest(path=path):
                line = render.page_link(path)
                match = re.fullmatch(r'Diff page: \[(.*?)\]\((.*?)\)', line)
                self.assertIsNotNone(match)
                self.assertEqual(match.group(1), path.as_uri())
                self.assertEqual(match.group(2), path.as_uri())
        self.assertIn('diff%20%281%29%20%5Bx%5D.html', render.page_link(cases[1]))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / 'repo'
        self.root.mkdir()
        self.review = base / 'review'
        self.review.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        write(self.root, 'a.txt', 'one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten\n')
        write(self.root, 'run.sh', '#!/bin/sh\n')
        write(self.root, 'gone.txt', 'bye\n')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'base')
        self.parent = git(self.root, 'rev-parse', 'HEAD')
        write(self.root, 'a.txt', 'one\ntwo\nthree\nfour\nfive\nsix\nseven\neight\nnine\nten <script>alert("&")</script>\n')
        git(self.root, 'update-index', '--chmod=+x', 'run.sh')
        (self.root / 'gone.txt').unlink()
        write(self.root, 'new.txt', 'brand new\n')
        write(self.root, 'tricky.tmp', 'a < b && "c"\n')  # the name goes into the index directly, as a name with markup cannot live on Windows
        blob = git(self.root, 'hash-object', '-w', 'tricky.tmp')
        (self.root / 'tricky.tmp').unlink()
        write(self.root, 'bin.dat', b'\x00\x01\x02binary')
        git(self.root, 'add', '-A')
        git(self.root, 'update-index', '--add', '--cacheinfo', '100644,%s,%s' % (blob, TRICKY))
        git(self.root, 'commit', '-q', '-m', 'candidate')
        self.tree = git(self.root, 'rev-parse', 'HEAD^{tree}')
        git(self.root, 'reset', '-q', '--hard', self.parent)  # the working tree is back at the parent, as if edited since
        write(self.root, 'a.txt', 'THE WORKING TREE CHANGED LATER\n')

    def record(self):
        return {'id': 'p' * 32, 'branch': 'refs/heads/tx/demo', 'parent': self.parent, 'tree': self.tree, 'manifest': ['a.txt']}

    def build(self, kind=render.PREVIEW, record=None):
        return render.build(self.root, kind, record or self.record(), 'demo_job', [])


class RenderTest(Fixture):
    def test_the_page_shows_the_recorded_candidate_not_the_working_tree(self):
        text = self.build()
        self.assertIn('alert(&quot;&amp;&quot;)', text)
        self.assertNotIn('THE WORKING TREE CHANGED LATER', text)

    def test_the_right_pane_is_the_candidate_and_never_the_current_version(self):
        text = self.build()
        self.assertIn('Candidate', text)
        self.assertNotIn('Current version', text)

    def test_the_label_names_the_kind_and_the_recorded_identities(self):
        preview, sealed = self.build(), self.build(render.SEALED)
        self.assertIn('PREVIEW (no approval)', preview)
        self.assertNotIn('SEALED REVIEW', preview)
        self.assertIn('SEALED REVIEW', sealed)
        self.assertNotIn('PREVIEW (no approval)', sealed)
        for value in (self.parent, self.tree, 'demo_job', 'demo', 'p' * 32):
            self.assertIn(value, preview)

    def test_additions_deletions_mode_changes_and_binaries_are_shown_as_such(self):
        text = self.build()
        self.assertRegex(text, r'kind deleted">deleted</span></td><td><a href="#f\d+">gone.txt')
        self.assertRegex(text, r'kind added">added</span></td><td><a href="#f\d+">new.txt')
        self.assertIn('mode 100644 to 100755', text)
        self.assertIn('Binary file, not shown: no bytes before, 9 bytes after.', text)
        self.assertIn('bye', text)  # a deleted file shows its old side

    def test_file_names_and_contents_are_escaped(self):
        text = self.build()
        self.assertNotIn(TRICKY, text)
        self.assertIn('a&amp;b &#x27;x&#x27;.txt', text)
        self.assertEqual(text.count('<script'), 1)  # the divider script alone: the file's own markup is escaped

    def test_the_only_script_moves_the_divider_and_focuses_the_open_file_and_the_policy_pins_it_by_hash(self):
        text = self.build()
        script = text.split('<script>', 1)[1].split('</script>', 1)[0]
        self.assertEqual(script, render.SCRIPT)
        digest = base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode('ascii')
        self.assertIn("script-src 'sha256-" + digest + "'", text)
        self.assertNotIn('@@', text)

    def test_the_divider_and_visible_links_are_there(self):
        text = self.build()
        self.assertIn('.scroll{overflow:auto;max-height:calc(100vh - 7.5em);overscroll-behavior:contain}', text)  # the file scrolls in its own area, scroll bars in reach
        self.assertIn('table.side th.pane{position:sticky;top:0;', text)
        self.assertIn('body:has(section:target) .facts{display:none}', text)  # an open file takes the room of the identity fields
        self.assertIn('@media (min-width:901px){html,body{height:100%}body{display:flex;flex-direction:column;overflow:hidden}', text)  # on a wide window the file fills the screen below the header
        self.assertIn('.fine{color:var(--mute);margin:2px 0 0;font-size:11px;line-height:1.25}', text)  # the closing note takes little height
        self.assertIn('class="grip"', text)
        self.assertIn('--split', text)
        self.assertIn('a,a:visited{color:var(--link)}', text)

    def test_the_page_has_no_external_resource(self):
        text = self.build()
        for needle in ('http://', 'https://', 'src=', '<link', '<iframe'):
            self.assertNotIn(needle, text)
        self.assertIn("default-src 'none'", text)

    def test_each_link_targets_one_file_section_that_is_shown_alone(self):
        text = self.build()
        targets = set(re.findall(r'href="#(f\d+)"', text))
        self.assertTrue(targets)
        for target in targets:
            self.assertEqual(text.count('<section id="%s">' % target), 1)
        self.assertIn('section:target{display:block}', text)
        self.assertIn('href="#">&larr; All files', text)

    def test_unchanged_stretches_are_named_not_shown(self):
        text = self.build()
        self.assertIn('unchanged line', text)
        self.assertNotIn('colspan', text)  # each pane names its own left-out lines, so the divider never runs through a spanning cell

    def test_a_tree_that_is_not_a_tree_object_is_refused(self):
        record = dict(self.record(), tree=self.parent)
        with self.assertRaises(render.ViewerError):
            self.build(record=record)


def section_of(text, name):
    """The html of the file section whose heading names the file."""
    found = [part for part in text.split('<section ')[1:] if name in part.split('</h3>', 1)[0]]
    assert len(found) == 1, name
    return found[0].split('</section>', 1)[0]


def views_of(section):
    """(collapsed view, complete view) of a section that holds both."""
    collapsed, complete = section.split('<div class="hunkview">', 1)[1].split('<div class="fullview">', 1)
    return collapsed, complete


class TitleTest(Fixture):
    def test_the_browser_title_and_the_header_label_carry_the_escaped_job_handle_and_stay_plain_without_one(self):
        title = lambda text: text.split('<title>', 1)[1].split('</title>', 1)[0]
        self.assertEqual(title(self.build()), 'Diff preview: demo_job')
        self.assertEqual(title(self.build(render.SEALED)), 'Diff sealed review: demo_job')
        self.assertEqual(title(render.build(self.root, render.PREVIEW, self.record(), 'a<i>&', [])), 'Diff preview: a&lt;i&gt;&amp;')
        self.assertEqual(title(render.build(self.root, render.PREVIEW, self.record(), None, [])), 'Diff preview')
        label = lambda text: text.split('<div class="label ', 1)[1].split('</strong>', 1)[0].split('<strong>')[1]
        self.assertEqual(label(self.build()), 'PREVIEW of demo_job (no approval)')
        self.assertEqual(label(self.build(render.SEALED)), 'SEALED REVIEW of demo_job')
        self.assertEqual(label(render.build(self.root, render.PREVIEW, self.record(), 'a<i>&', [])), 'PREVIEW of a&lt;i&gt;&amp; (no approval)')
        self.assertEqual(label(render.build(self.root, render.PREVIEW, self.record(), None, [])), 'PREVIEW (no approval)')
        self.assertEqual(label(render.build(self.root, render.SEALED, self.record(), None, [])), 'SEALED REVIEW')
        self.assertEqual(title(render.build(self.root, render.SEALED, self.record(), None, [])), 'Diff sealed')


class CompactHeaderTest(Fixture):
    def test_the_header_is_a_narrow_bar_that_names_the_kind_in_its_own_colour(self):
        preview, sealed = self.build(), self.build(render.SEALED)
        self.assertIn('<div class="label preview"><strong>PREVIEW of demo_job (no approval)</strong>', preview)
        self.assertIn('<div class="label sealed"><strong>SEALED REVIEW of demo_job</strong>', sealed)
        self.assertNotIn('clamp(', preview)  # no more title sized by the window
        self.assertIn('.label.preview{background:var(--preview)}.label.sealed{background:var(--sealed)}', preview)
        self.assertNotEqual(re.search(r'--preview:(#\w+)', preview).group(1), re.search(r'--sealed:(#\w+)', preview).group(1))

    def test_the_identity_fields_are_in_two_columns_and_the_manifest_spans_both(self):
        text = render.build(self.root, render.PREVIEW, self.record(), 'demo_job', [('Manifest', 'a.txt, b.txt')])
        facts = text.split('<div class="facts">', 1)[1].split('<div class="layout">', 1)[0]
        columns = facts.split('<div class="fact wide">', 1)[0].split('</div></div><div>')
        self.assertEqual(len(columns), 2)
        for name in ('Kind', 'Job', 'Mission', 'Branch'):
            self.assertIn('<span class="k">%s</span>' % name, columns[0])
        for name in ('Parent commit', 'Candidate tree', 'Preview id', 'Totals'):
            self.assertIn('<span class="k">%s</span>' % name, columns[1])
        self.assertIn('<div class="fact wide"><span class="k">Manifest</span><span class="v">a.txt, b.txt</span>', facts)
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', text)
        self.assertIn('@media (max-width:700px){.facts{grid-template-columns:minmax(0,1fr)}}', text)  # one column on a narrow window

    def test_every_identity_field_is_still_shown_and_escaped(self):
        record = dict(self.record(), branch='refs/heads/tx/<b>x')
        text = render.build(self.root, render.SEALED, record, 'a<i>', [('Seal (prepared_sha256)', 's' * 64)])
        for name in ('Kind', 'Job', 'Mission', 'Branch', 'Parent commit', 'Candidate tree', 'Transaction id', 'Seal (prepared_sha256)', 'Totals'):
            self.assertIn('<span class="k">%s</span>' % name, text)
        self.assertNotIn('<b>x', text)
        self.assertNotIn('a<i>', text)
        self.assertIn('a&lt;i&gt;', text)


class FolderTreeTest(Fixture):
    def test_the_folders_open_and_close_and_start_open_with_buttons_for_all(self):
        text = self.build()
        nav = text.split('<nav>', 1)[1].split('</nav>', 1)[0]
        self.assertIn('<button type="button" data-fold="open">Expand all</button><button type="button" data-fold="close">Collapse all</button>', nav)
        self.assertEqual(nav.count('<details'), 0)  # the fixture has no folder: the files sit at the top
        sub = render.tree([dict(path='dir/<b>/x.txt', status='M'), dict(path='dir/y.txt', status='M')], 2)
        self.assertEqual(sub.count('<details open>'), 2)  # every folder starts open
        self.assertIn('<summary>dir/</summary>', sub)
        self.assertIn('<summary>&lt;b&gt;/</summary>', sub)
        self.assertNotIn('<b>', sub)
        self.assertIn("getAttribute('data-fold')", render.SCRIPT)  # the buttons are wired by the pinned script


class ChangeButtonsTest(Fixture):
    def test_the_first_row_of_every_run_of_changes_is_marked_in_both_views(self):
        old = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']
        new = ['a', 'B', 'C', 'd', 'e', 'f', 'g', 'H', 'i']
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        everything = render.full_rows(matcher, old, new)
        self.assertEqual(render.side_table(everything, self.parent, self.tree).count('<tr data-c'), 2)  # b and c are one run, h and i another
        collapsed = [row for hidden, rows in render.side_rows(matcher, old, new) for row in ([(hidden, 'gap')] if hidden else []) + rows]
        self.assertEqual(render.side_table(collapsed, self.parent, self.tree).count('<tr data-c'), 2)

    def test_a_file_with_two_views_has_the_buttons_after_the_checkbox_and_the_first_starts_disabled(self):
        text = self.build()
        modified, added = section_of(text, 'a.txt'), section_of(text, 'new.txt')
        self.assertIn('Collapse unchanged fragments</label><span class="nav"><span class="count" data-count>1 change</span><button type="button" data-nav="prev" title="Previous change" aria-label="Previous change" disabled>', modified)
        self.assertIn('<button type="button" data-nav="next" title="Next change" aria-label="Next change">', modified)
        self.assertNotIn('data-nav', added)  # one change in one table: nothing to jump to
        self.assertEqual(modified.count('<tr data-c'), 2)  # the one change, in each view
        self.assertIn("data-nav", render.SCRIPT)


class CollapseToggleTest(Fixture):
    def test_the_checkbox_is_named_like_the_ide_checked_by_default_and_on_the_heading_row_without_a_script(self):
        text = self.build()
        modified, added = section_of(text, 'a.txt'), section_of(text, 'new.txt')
        self.assertIn('<label class="toggle"><input type="checkbox" class="collapse" checked> Collapse unchanged fragments</label><span class="nav"><span class="count" data-count>', modified)
        self.assertRegex(modified, r'^id="f\d+"><div class="bar"><a class="back" href="#">&larr; All files</a><h3>.*</h3><label class="toggle">')  # one row: back link, heading, checkbox
        self.assertNotIn('Collapse unchanged', added)  # one table only: nothing to switch
        self.assertIn('section:has(.collapse:checked) .fullview,section:has(.collapse:not(:checked)) .hunkview{display:none}', text)
        self.assertEqual(text.count('<script'), 1)  # still the divider alone

    def test_a_modified_file_holds_the_changes_with_context_and_the_complete_file(self):
        collapsed, complete = views_of(section_of(unmark(self.build()), 'a.txt'))
        self.assertIn('unchanged line', collapsed)
        self.assertNotIn('<span class="n">1</span>one', collapsed)  # the first line is left out of the collapsed view
        self.assertNotIn('unchanged line', complete)
        for number, word in enumerate('one two three four five six seven eight nine'.split(), 1):
            self.assertEqual(complete.count('<td class="old same"><span class="n">%d</span>%s</td>' % (number, word)), 1)
            self.assertEqual(complete.count('<td class="new same"><span class="n">%d</span>%s</td>' % (number, word)), 1)
        self.assertIn('<td class="old change"><span class="n">10</span>ten</td>', complete)
        self.assertIn('<td class="new change"><span class="n">10</span>ten &lt;script&gt;alert(&quot;&amp;&quot;)&lt;/script&gt;</td>', complete)
        self.assertEqual(complete.count('<td class="old same">') + complete.count('<td class="old change">'), 10)

    def test_added_and_deleted_files_have_one_table_with_one_side_and_a_binary_has_none(self):
        text = unmark(self.build())
        added, deleted, binary = section_of(text, 'new.txt'), section_of(text, 'gone.txt'), section_of(text, 'bin.dat')
        self.assertIn('<td class="new added"><span class="n">1</span>brand new</td>', added)
        self.assertIn('<td class="old removed"><span class="n">1</span>bye</td>', deleted)
        for part in (added, deleted):
            self.assertIn('<div class="allview">', part)
            self.assertNotIn('hunkview', part)
            self.assertEqual(part.count('<table class="side">'), 1)
        self.assertNotIn('<table', binary)
        self.assertNotIn('view">', binary)


class LargeFileTest(unittest.TestCase):
    def test_the_complete_view_keeps_the_large_file_limit_and_the_escaping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            git(root, 'init', '-q', '-b', 'main')
            lines = ['<img src=x onerror=1>'] + ['line %d' % n for n in range(2, render.MAX_LINES + 101)]
            write(root, 'big.txt', '\n'.join(lines) + '\n')
            git(root, 'add', '-A')
            git(root, 'commit', '-q', '-m', 'base')
            parent = git(root, 'rev-parse', 'HEAD')
            write(root, 'big.txt', '\n'.join(lines[:5] + ['changed'] + lines[6:]) + '\n')
            git(root, 'add', '-A')
            git(root, 'commit', '-q', '-m', 'candidate')
            tree = git(root, 'rev-parse', 'HEAD^{tree}')
            record = {'id': 'q' * 32, 'branch': 'refs/heads/tx/demo', 'parent': parent, 'tree': tree, 'manifest': ['big.txt']}
            text = render.build(root, render.PREVIEW, record, 'demo_job', [])
        collapsed, complete = views_of(section_of(text, 'big.txt'))
        self.assertIn('Large file: only the first %d lines of each side is shown' % render.MAX_LINES, text)
        self.assertEqual(complete.count('<td class="old'), render.MAX_LINES)
        self.assertIn('&lt;img src=x onerror=1&gt;', complete)
        self.assertNotIn('<img', text)
        self.assertLess(collapsed.count('<td class="old'), 20)


class ReviewFolderTest(unittest.TestCase):
    def test_the_default_folder_is_the_mission_review_folder_beside_the_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            container = Path(temp).resolve() / '_worktrees' / 'repo'
            workspace, review = container / 'demo', container / '_review' / 'demo'
            workspace.mkdir(parents=True)
            git(workspace, 'init', '-q')
            review.mkdir(parents=True)
            self.assertEqual(render.review_folder(workspace, 'refs/heads/tx/demo'), review)
            with self.assertRaises(render.ViewerError):
                render.review_folder(workspace, 'refs/heads/tx/other')


class PreviewRecordTest(Fixture):
    def setUp(self):
        super().setUp()
        store = self.root / '.git' / 'governance'
        store.mkdir()
        self.key = b'k' * 32
        (store / 'receipt.key').write_bytes(self.key)

    def signed(self, body, key=None):
        mac = hmac.new(key or self.key, render.canonical(body), hashlib.sha256).hexdigest()
        path = self.review / 'preview.json'
        path.write_text(json.dumps({'body': body, 'mac': mac}), encoding='utf-8')
        return path

    def test_an_authenticated_preview_writes_its_page_beside_the_record(self):
        path = self.signed(self.record())
        args = type('A', (), {'repo': str(self.root), 'preview': path, 'job': 'demo_job', 'output_dir': None})
        target = render.run_preview(args)
        self.assertEqual(target, self.review / ('diff-preview-%s.html' % ('p' * 32)))
        self.assertIn('PREVIEW (no approval)', target.read_text(encoding='utf-8'))
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            self.assertEqual(render.main(['--repo', str(self.root), '--job', 'demo_job', 'preview', '--preview', str(path)]), 0)
        self.assertEqual(output.getvalue().splitlines()[-1], f'Diff page: [{target.as_uri()}]({target.as_uri()})')
        self.assertEqual(len(output.getvalue().splitlines()), 2)
        self.assertNotIn('Link:', output.getvalue())

    def test_an_edited_or_foreign_record_is_refused(self):
        path = self.signed(self.record(), key=b'x' * 32)
        with self.assertRaises(render.ViewerError):
            render.load_preview(self.root, path)
        path = self.signed(self.record())
        envelope = json.loads(path.read_text(encoding='utf-8'))
        envelope['body']['tree'] = self.parent
        path.write_text(json.dumps(envelope), encoding='utf-8')
        with self.assertRaises(render.ViewerError):
            render.load_preview(self.root, path)


class SealedTest(Fixture):
    def sealed(self, diff):
        packet = dict(self.record(), diff=diff, job_handle='demo_job')
        original = render.load_sealed
        render.load_sealed = lambda repo: (packet, 's' * 64)
        self.addCleanup(setattr, render, 'load_sealed', original)
        return type('A', (), {'repo': str(self.root), 'job': None, 'output_dir': self.review})

    def test_the_sealed_page_is_written_when_the_packet_diff_equals_parent_to_tree(self):
        target = render.run_sealed(self.sealed(render.candidate_diff(self.root, self.parent, self.tree)))
        text = target.read_text(encoding='utf-8')
        self.assertEqual(target.name, 'diff-sealed-%s.html' % ('p' * 32))
        self.assertIn('SEALED REVIEW', text)
        self.assertIn('demo_job', text)
        self.assertIn('s' * 64, text)
        output = io.StringIO()
        with mock.patch('sys.stdout', output):
            self.assertEqual(render.main(['--repo', str(self.root), '--output-dir', str(self.review), 'sealed']), 0)
        self.assertEqual(output.getvalue().splitlines()[-1], f'Diff page: [{target.as_uri()}]({target.as_uri()})')

    def test_a_packet_diff_that_differs_is_refused_and_nothing_is_written(self):
        args = self.sealed(render.candidate_diff(self.root, self.parent, self.tree) + 'extra\n')
        with self.assertRaises(render.ViewerError):
            render.run_sealed(args)
        self.assertEqual(list(self.review.iterdir()), [])


class DiskLinkTest(unittest.TestCase):
    """The heading path of a file section and the Mission value link the mission checkout's working copy; the tree and the list stay as they are."""
    BIG = ''.join('line %d of the moved file\n' % n for n in range(1, 41))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'mission demo'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        for folder in ('dir with space/sub', 'old dir', 'src'):
            (self.root / folder).mkdir(parents=True)
        write(self.root, 'dir with space/sub/mod.txt', 'one\ntwo\n')
        write(self.root, 'dir with space/gone.txt', 'bye\n')
        write(self.root, 'old dir/lost.txt', 'lost\n')
        write(self.root, 'src/moved.txt', self.BIG)
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'base')
        self.parent = git(self.root, 'rev-parse', 'HEAD')
        write(self.root, 'dir with space/sub/mod.txt', 'one\nTWO\n')
        write(self.root, 'dir with space/new.txt', 'new\n')
        git(self.root, 'rm', '-q', 'dir with space/gone.txt', 'old dir/lost.txt')
        (self.root / 'moved').mkdir()
        git(self.root, 'mv', 'src/moved.txt', 'moved/moved.txt')
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'candidate')  # the checkout stays at the candidate
        self.tree = git(self.root, 'rev-parse', 'HEAD^{tree}')
        record = {'id': 'd' * 32, 'branch': 'refs/heads/tx/mission-demo', 'parent': self.parent, 'tree': self.tree, 'manifest': []}
        self.text = render.build(self.root, render.PREVIEW, record, 'demo_job', [])
        self.note = render.html.escape(render.DISK_NOTE, quote=True)

    def heading(self, name):
        """The h3 of the section whose heading names the file."""
        found = [part.split('</h3>', 1)[0] for part in self.text.split('<h3>')[1:] if name in part.split('</h3>', 1)[0]]
        self.assertEqual(len(found), 1, name)
        return found[0]

    def links(self, name):
        """[(link text, target)] of the heading's disk links."""
        found = re.findall(r'<a class="disk" href="([^"]*)" title="[^"]*">([^<]*)</a>', self.heading(name))
        return [(text, href) for href, text in found]

    def test_every_heading_segment_links_its_folder_and_the_last_one_the_file(self):
        base = self.root.as_uri()
        self.assertEqual(self.links('mod.txt'), [
            ('dir with space', base + '/dir%20with%20space'),
            ('sub', base + '/dir%20with%20space/sub'),
            ('mod.txt', base + '/dir%20with%20space/sub/mod.txt')])
        self.assertIn('</a>/<a', self.heading('mod.txt'))  # the segments are separated as before
        self.assertEqual([text for text, _ in self.links('new.txt')], ['dir with space', 'new.txt'])  # an added file

    def test_paths_with_spaces_are_encoded_and_the_target_equals_as_uri(self):
        for _, href in self.links('mod.txt'):
            self.assertNotIn(' ', href)
        self.assertEqual(self.links('new.txt')[-1][1], (self.root / 'dir with space' / 'new.txt').as_uri())

    def test_a_deleted_file_is_plain_text_and_a_folder_that_is_gone_is_too(self):
        kept = self.heading('gone.txt')  # the folder survives, the file does not
        self.assertEqual([text for text, _ in self.links('gone.txt')], ['dir with space'])
        self.assertTrue(kept.endswith('/gone.txt'))
        self.assertEqual(self.links('lost.txt'), [])  # neither the folder nor the file exists in the checkout
        self.assertIn('old dir/lost.txt', self.heading('lost.txt'))

    def test_a_renamed_file_links_its_new_path_and_names_the_old_one_as_text(self):
        heading = self.heading(chr(0x2192))  # only the renamed entry holds the arrow; its two sides are sections of their own
        self.assertEqual([text for text, _ in self.links(chr(0x2192))], ['moved', 'moved.txt'])
        self.assertEqual(self.links(chr(0x2192))[-1][1], (self.root / 'moved' / 'moved.txt').as_uri())
        self.assertIn('src/moved.txt → ', heading)
        self.assertEqual(heading.count('src/moved.txt'), 1)

    def test_a_segment_that_leads_outside_the_checkout_is_plain_text(self):
        outside = self.root.parent / 'elsewhere'
        outside.mkdir()
        write(outside, 'mod.txt', 'x')
        link = self.root / 'dir with space' / 'sub'
        git(self.root, 'rm', '-q', '-r', '--cached', '--', 'dir with space/sub')  # the index keeps no say over the disk; only the page's view of the disk matters
        for child in (self.root / 'dir with space' / 'sub').iterdir():
            child.unlink()
        link.rmdir()
        try:
            os.symlink(outside, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('symbolic links need a system setting here')
        self.addCleanup(lambda: link.is_symlink() and os.rmdir(link))  # a directory link is removed with rmdir, never recursively
        self.assertFalse(render.inside(self.root, link))
        self.assertFalse(render.inside(self.root, link / 'mod.txt'))
        self.assertTrue(render.inside(self.root, self.root / 'dir with space'))
        heading = render.path_links(self.root, 'dir with space/sub/mod.txt')
        self.assertEqual(re.findall(r'>([^<]*)</a>', heading), ['dir with space'])
        self.assertTrue(heading.endswith('/sub/mod.txt'))

    def test_the_mission_value_links_the_worktree_folder(self):
        match = re.search(r'<span class="k">Mission</span><span class="v"><a class="disk" href="([^"]*)" title="[^"]*">mission-demo</a></span>', self.text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), self.root.as_uri())

    def test_every_disk_link_has_the_note_as_its_title_and_the_page_has_no_note(self):
        anchors = re.findall(r'<a class="disk"[^>]*>', self.text)
        self.assertGreater(len(anchors), 5)
        for anchor in anchors:
            self.assertIn(' title="%s"' % self.note, anchor)
        self.assertEqual(self.text.count(self.note), len(anchors))  # the sentence occurs nowhere else
        self.assertNotIn('working copy', re.sub(r'<[^>]*>', '', self.text))

    def test_the_tree_and_the_file_list_carry_no_disk_links_and_are_unchanged(self):
        nav = self.text.split('<nav>', 1)[1].split('</nav>', 1)[0]
        listing = self.text.split('<table class="files">', 1)[1].split('</table>', 1)[0]
        for part in (nav, listing):
            self.assertNotIn('file://', part)
            self.assertNotIn('class="disk"', part)
        items = render.changes(self.root, self.parent, self.tree)
        self.assertEqual(nav, '<div class="treebar"><button type="button" data-fold="open">Expand all</button>'
                              '<button type="button" data-fold="close">Collapse all</button></div>' + render.tree(items, len(items)))
        self.assertEqual(listing.count('<a href="#f'), len(items))
        self.assertNotIn('file://', self.text.replace(self.root.as_uri(), ''))


class RenameTest(unittest.TestCase):
    """A moved file is one renamed entry computed by the page itself; the checkpoint tool's own records are not involved."""
    BIG = ''.join('line %d of the moved file\n' % n for n in range(1, 41))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'repo'
        self.root.mkdir()
        git(self.root, 'init', '-q', '-b', 'main')
        (self.root / 'old').mkdir()
        write(self.root, 'old/moved.txt', self.BIG)
        write(self.root, 'old/edited.txt', self.BIG.replace('line', 'row'))
        write(self.root, 'old/gone.txt', ''.join('alpha %d\n' % n for n in range(30)))
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'base')
        self.parent = git(self.root, 'rev-parse', 'HEAD')
        (self.root / 'new').mkdir()
        git(self.root, 'mv', 'old/moved.txt', 'new/moved.txt')
        git(self.root, 'mv', 'old/edited.txt', 'new/edited.txt')
        write(self.root, 'new/edited.txt', self.BIG.replace('line', 'row').replace('row 7 ', 'ROW SEVEN '))
        git(self.root, 'rm', '-q', 'old/gone.txt')
        write(self.root, 'new/unrelated.txt', ''.join('omega %d\n' % n for n in range(30)))
        git(self.root, 'add', '-A')
        git(self.root, 'commit', '-q', '-m', 'candidate')
        self.tree = git(self.root, 'rev-parse', 'HEAD^{tree}')

    def build(self):
        record = {'id': 'r' * 32, 'branch': 'refs/heads/tx/demo', 'parent': self.parent, 'tree': self.tree, 'manifest': []}
        text = render.build(self.root, render.PREVIEW, record, 'demo_job', [])
        return re.sub(r'<a class="disk"[^>]*>(.*?)</a>', lambda match: match.group(1), text)  # the disk links have their own tests; here the headings read as plain paths

    def test_a_pure_move_is_one_entry_with_no_change_lines_and_says_so(self):
        text = self.build()
        section = section_of(text, 'old/moved.txt →')
        self.assertIn('renamed</span> old/moved.txt → new/moved.txt (100%)</h3>', section)
        self.assertIn('Moved with no content change', section)
        self.assertNotIn('<table', section)
        self.assertNotIn('class="old change"', section)
        self.assertRegex(text, r'kind renamed">renamed</span></td><td><a href="#f\d+">old/moved.txt → new/moved.txt \(100%\)</a></td><td></td><td>\+0 -0</td>')

    def test_a_move_with_an_edit_shows_its_similarity_and_only_the_edited_lines(self):
        section = section_of(unmark(self.build()), 'old/edited.txt →')
        self.assertRegex(section, r'old/edited.txt → new/edited.txt \(9\d%\)</h3>')
        collapsed, complete = views_of(section)
        self.assertEqual(collapsed.count('<tr data-c'), 1)
        self.assertIn('<td class="old change"><span class="n">7</span>row 7 of the moved file</td>', collapsed)
        self.assertIn('<td class="new change"><span class="n">7</span>ROW SEVEN of the moved file</td>', collapsed)
        self.assertEqual(complete.count('class="old change"'), 1)

    def test_a_real_deletion_and_an_unrelated_addition_are_not_paired(self):
        text = self.build()
        self.assertRegex(text, r'kind deleted">deleted</span></td><td><a href="#f\d+">old/gone.txt</a>')
        self.assertRegex(text, r'kind added">added</span></td><td><a href="#f\d+">new/unrelated.txt</a>')
        self.assertNotIn('gone.txt →', text)
        self.assertNotIn('→ new/unrelated.txt', text)

    def test_a_move_counts_once_and_only_its_real_changes_are_totalled(self):
        text = self.build()
        self.assertIn('4 files, +31 -31 lines', text)  # moved, edited (+1 -1), the deletion (-30) and the addition (+30)
        self.assertIn('<h2>Files (4)</h2>', text)

    def test_the_page_labels_the_pairing_and_links_each_side_as_its_own_file(self):
        text = unmark(self.build())
        section = section_of(text, 'old/moved.txt →')
        self.assertIn('Display aid', section)
        self.assertIn('Display aid', text.split('<div class="overview">', 1)[1].split('</div>', 1)[0])
        match = re.search(r'id="(f\d+)"><div class="bar"><a class="back" href="#">&larr; All files</a><h3><span class="kind renamed">renamed</span> old/moved', text)
        number = match.group(1)
        self.assertIn('href="#%s-old"' % number, section)
        self.assertIn('href="#%s-new"' % number, section)
        old_side = text.split('<section id="%s-old">' % number, 1)[1].split('</section>', 1)[0]
        new_side = text.split('<section id="%s-new">' % number, 1)[1].split('</section>', 1)[0]
        self.assertIn('kind deleted">deleted</span> old/moved.txt', old_side)
        self.assertIn('kind added">added</span> new/moved.txt', new_side)
        self.assertIn('<td class="old removed"><span class="n">1</span>line 1 of the moved file</td>', old_side)
        self.assertIn('<td class="new added"><span class="n">1</span>line 1 of the moved file</td>', new_side)
        self.assertIn('href="#%s">&larr; The rename' % number, old_side)

    def test_the_page_is_the_same_under_other_git_settings_for_renames(self):
        reference = self.build()
        config = Path(self.temp.name) / 'global.config'
        for name, value in (('diff.renames', 'false'), ('diff.renames', 'copies'), ('diff.renameLimit', '1'), ('diff.algorithm', 'patience')):
            config.write_text('[diff]\n\t%s = %s\n' % (name.split('.')[1], value), encoding='utf-8')
            with self.subTest(setting=name + '=' + value), mock.patch.dict(os.environ, {'GIT_CONFIG_GLOBAL': str(config)}):
                self.assertEqual(self.build(), reference)
        with (self.root / '.git' / 'config').open('a', encoding='utf-8') as handle:  # the repository's own configuration, written without `git config`
            handle.write('[diff]\n\trenames = false\n\trenameLimit = 1\n\talgorithm = histogram\n')
        self.assertEqual(self.build(), reference)

    def test_the_options_are_passed_explicitly(self):
        self.assertIn('--find-renames=50%', render.RENAME_FLAGS)
        self.assertIn('-l1000', render.RENAME_FLAGS)
        self.assertIn('diff.renames=true', render.RENAME_OPTIONS)
        env = render.display_env()
        self.assertEqual((env['GIT_CONFIG_GLOBAL'], env['GIT_CONFIG_NOSYSTEM']), (os.devnull, '1'))



def unmark(text):
    return re.sub(r'<span class="segment"(?: data-change="replace")?>(.*?)</span>', r'\1', text, flags=re.S)


class CellText(HTMLParser):
    """Read cell content and highlights, excluding the generated line-number span."""
    def __init__(self, markup):
        super().__init__()
        self.content, self.segments, self.kind = [], [], None
        self.changes = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        if tag == 'span':
            self.kind = dict(attrs).get('class')
            if self.kind == 'segment':
                self.segments.append('')
                self.changes.append(dict(attrs).get('data-change'))

    def handle_endtag(self, tag):
        if tag == 'span':
            self.kind = None

    def handle_data(self, data):
        if self.kind != 'n':
            self.content.append(data)
        if self.kind == 'segment':
            self.segments[-1] += data


class IntralineTest(unittest.TestCase):
    def pair(self, old, new):
        matcher = render.difflib.SequenceMatcher(None, [old], [new], autojunk=False)
        row = render.full_rows(matcher, [old], [new])[0]
        return (CellText(render.cell(row[0], row[1], 'old ' + row[4], row[5])),
                CellText(render.cell(row[2], row[3], 'new ' + row[4], row[6])))

    def test_screenshot_list_insertion_colors_only_candidate_added_text(self):
        old = "['corpus', 'corpus2', 'mcp', 'analysis', 'launch', 'preflight']"
        new = old.replace("'launch'", "'analysis2', 'launch'")
        before, after = self.pair(old, new)
        self.assertEqual(before.segments, [])
        self.assertEqual(after.segments, ["analysis2', '"])
        self.assertEqual(after.changes, [None])
        self.assertEqual(''.join(before.content), old)
        self.assertEqual(''.join(after.content), new)

    def test_removal_and_replacement_use_the_corresponding_sides(self):
        before, after = self.pair('run(alpha, beta)', 'run(alpha)')
        self.assertEqual(before.segments, [', beta'])
        self.assertEqual(before.changes, [None])
        self.assertEqual(after.segments, [])
        before, after = self.pair('color = "red"', 'color = "green"')
        self.assertEqual(''.join(before.content), 'color = "red"')
        self.assertEqual(''.join(after.content), 'color = "green"')
        self.assertEqual(before.segments, ['red'])
        self.assertEqual(after.segments, ['green'])
        self.assertEqual(before.changes, ['replace'])
        self.assertEqual(after.changes, ['replace'])

    def test_alphanumeric_segments_and_unicode_words_are_never_split_into_letters(self):
        for old, new, removed, added in (
                ('run(analysis)', 'run(analysis2)', 'analysis', 'analysis2'),
                ('run(old_name)', 'run(new_name)', 'old', 'new'),
                ('old_only_call()', 'new_only_call()', 'old', 'new'),
                ('run(Alpha2)', 'run(alpha2)', 'Alpha2', 'alpha2'),
                ('name = "café"', 'name = "cafés"', 'café', 'cafés'),
                ('name = "e\u0301"', 'name = "e\u0300"', 'e\u0301', 'e\u0300')):
            with self.subTest(old=old, new=new):
                before, after = self.pair(old, new)
                self.assertEqual(before.segments, [removed])
                self.assertEqual(after.segments, [added])

    def test_standalone_punctuation_and_whitespace_edits_stay_visible(self):
        before, after = self.pair('value = left + right', 'value = left - right')
        self.assertEqual(before.segments, ['+'])
        self.assertEqual(after.segments, ['-'])
        before, after = self.pair('value = left', 'value  = left')
        self.assertEqual(before.segments, [' '])
        self.assertEqual(after.segments, ['  '])

    def test_added_and_removed_punctuation_and_spaces_are_covered_with_words(self):
        for old, new in (
                ('call(alpha)', 'call(alpha, beta)'),
                ('items = ["alpha"]', 'items = ["alpha", "beta"]')):
            with self.subTest(old=old, new=new):
                before, after = self.pair(old, new)
                self.assertEqual(before.segments, [])
                # All additional characters, including quotes, separators and spaces, are colored.
                self.assertEqual(len(''.join(after.segments)), len(new) - len(old))
                self.assertEqual(after.changes, [None])
                removed, remaining = self.pair(new, old)
                self.assertEqual(len(''.join(removed.segments)), len(new) - len(old))
                self.assertEqual(remaining.segments, [])

    def test_multiple_separate_changes_keep_surrounding_text_readable(self):
        before, after = self.pair('alpha = 123; beta = 456', 'alpha = 789; beta = 012')
        self.assertEqual(before.segments, ['123', '456'])
        self.assertEqual(after.segments, ['789', '012'])

    def test_whitespace_unicode_controls_and_markup_reconstruct_exact_display(self):
        cases = [
            ('name = "café 😀"', 'name = "café 🦊"'),
            ('prefix\tvalue  suffix', 'prefix\tvalue   suffix'),
            ('value = "<a>&\x01"', 'value = "<b>&\x02"'),
            ('text = "e\u0301"', 'text = "e\u0300"'),
        ]
        for old, new in cases:
            with self.subTest(old=old, new=new):
                before, after = self.pair(old, new)
                self.assertEqual(''.join(before.content), old.translate(render.PICTURES))
                self.assertEqual(''.join(after.content), new.translate(render.PICTURES))
        self.assertNotIn('<b>', render.highlighted(cases[2][1], [(0, len(cases[2][1]))]))

    def test_unequal_blocks_find_clear_pairs_without_highlighting_unrelated_lines_precisely(self):
        old, new = ['alpha(value)'], ['unrelated()', 'alpha(value, extra)']
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        rows = render.full_rows(matcher, old, new)
        self.assertIsNone(rows[0][0])
        self.assertEqual(rows[0][3], 'unrelated()')
        self.assertEqual(rows[1][0:4], (1, old[0], 2, new[1]))
        self.assertEqual(CellText(render.cell(2, rows[1][3], 'new change', rows[1][6])).segments, [', extra'])
        matcher = render.difflib.SequenceMatcher(None, ['    aaa'], ['    bbb'], autojunk=False)
        rows = render.full_rows(matcher, ['    aaa'], ['    bbb'])  # compared and not similar: a removed line and an added line, not one edit
        self.assertEqual([row[:5] for row in rows], [(1, '    aaa', None, None, 'removed'), (None, None, 1, '    bbb', 'added')])

    def test_long_repetitive_lines_and_comparison_budget_fall_back(self):
        old = ['x' * 1100]
        new = ['x' * 1099 + 'y']
        with mock.patch.object(render, 'intraline', wraps=render.intraline) as spy:
            rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new), old, new)
        self.assertEqual(spy.call_args.args[2], False)  # too long for the exact character score, so scored from its single word
        self.assertEqual([row[4] for row in rows], ['removed', 'added'])
        old, new = ['!' * 3000], ['!' * 2999 + '?']  # every punctuation mark is a token: 9,000,000 tokens to compare
        with mock.patch.object(render, 'intraline', side_effect=AssertionError('must not compare')):
            rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new), old, new)
        self.assertEqual([row[4:] for row in rows], [('change', None, None)])  # beyond the limit nothing is known: the whole-text fallback
        with mock.patch.object(render, 'MAX_INTRALINE_LENGTH', 5), mock.patch.object(render, 'intraline', side_effect=AssertionError('must not compare')):
            self.assertEqual(self.pair('long old', 'long new')[0].segments, ['long old'])
        old, new = ['key = 1', 'key = 2'], ['key = 3', 'key = 4']
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        with mock.patch.object(render, 'MAX_INTRALINE_WORK', 74):  # one pair of these lines costs 25 word tokens and 49 characters
            rows = render.full_rows(matcher, old, new)
        self.assertTrue(all(row[5:] == (None, None) for row in rows))  # incomplete block comparison cannot prove a match

    def test_both_views_reuse_ranges_even_when_earlier_changes_spend_budget(self):
        old = ['key = 1'] + ['same %d' % n for n in range(12)] + ['key = 2']
        new = ['key = 3'] + old[1:-1] + ['key = 4']
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        with mock.patch.object(render, 'MAX_INTRALINE_WORK', 74):
            rows = render.full_rows(matcher, old, new)
        collapsed = [row for _, group in render.side_rows(matcher, old, new, rows) for row in group]
        self.assertEqual([row for row in rows if row[4] != 'same'], [row for row in collapsed if row[4] != 'same'])
        self.assertIsNone(collapsed[-1][5])

    def test_reported_assignment_in_unequal_block_highlights_only_its_value(self):
        old = [
            '        rows = opcode_rows(old, new, group)',
            '        hunks.append((group[0][1] - shown, rows))  # skipped lines',
        ]
        new = [
            '        rows = []',
            '        for _, a0, a1, b0, b1 in group:',
            '            for i, j in itertools.zip_longest(range(a0, a1), range(b0, b1)):',
            '                rows.append(row)',
            '        hunks.append((group[0][1] - shown, rows))',
        ]
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        rows = render.full_rows(matcher, old, new)
        before = CellText(render.cell(rows[0][0], rows[0][1], 'old change', rows[0][5]))
        after = CellText(render.cell(rows[0][2], rows[0][3], 'new change', rows[0][6]))
        self.assertEqual(before.segments, ['opcode_rows(old, new, group)'])
        self.assertEqual(after.segments, ['[]'])
        self.assertEqual(before.changes, ['replace'])
        self.assertEqual(after.changes, ['replace'])
        self.assertEqual([row[1] for row in rows if row[0] is not None], old)
        self.assertEqual([row[3] for row in rows if row[2] is not None], new)
        self.assertEqual([row[4] for row in rows], ['change', 'added', 'added', 'added', 'change'])
        self.assertEqual([row for _, group in render.side_rows(matcher, old, new, rows) for row in group], rows)

    def test_rewritten_docstring_keeps_shared_hunks_and_other_equal_words_unmarked(self):
        old = '    """Hunks of aligned rows, grouped with a few lines of context; with each hunk the lines of the old side skipped before it."""'
        new = '    """Hunks reuse the complete view\'s ranges and budget, with skipped parent lines counted."""'
        before, after = self.pair(old, new)
        self.assertEqual(''.join(before.content), old)
        self.assertEqual(''.join(after.content), new)
        self.assertNotIn('Hunks', ''.join(before.segments))
        self.assertNotIn('Hunks', ''.join(after.segments))
        matcher = render.difflib.SequenceMatcher(None, [old], [new, '    if everything is None:', '        everything = full_rows(matcher, old, new)'])
        rows = render.full_rows(matcher, [old], [new, '    if everything is None:', '        everything = full_rows(matcher, old, new)'])
        self.assertEqual(rows[0][3], new)
        self.assertIsNotNone(rows[0][5])
        for side, spans in ((old, rows[0][5]), (new, rows[0][6])):
            # Every equal word block, including the opening quotes and Hunks, stays outside spans.
            prefix_end = side.index('Hunks') + len('Hunks')
            self.assertTrue(all(start >= prefix_end for start, _, _ in spans))

    def test_reported_aligned_docstring_groups_the_requested_phrase(self):
        old = '    """Aligned rows (old number, old line, new number, new line, class) for the given opcodes."""'
        new = '    """Aligned old/new numbers and text, row class, then old/new changed ranges.'
        before, after = self.pair(old, new)
        self.assertEqual(after.segments[0], 'old/new numbers and text, row class')
        self.assertEqual(after.changes[0], 'replace')
        self.assertEqual(after.segments[1], 'then old/')
        self.assertIsNone(after.changes[1])  # a separate insertion keeps green
        self.assertNotIn('Aligned', ''.join(before.segments))
        self.assertNotIn('Aligned', ''.join(after.segments))
        self.assertEqual(''.join(before.content), old)
        self.assertEqual(''.join(after.content), new)

    def test_preserved_spaces_and_tabs_join_adjacent_replacements(self):
        for separator in (' ', '\t', ', '):
            old, new = 'label old' + separator + 'red done', 'label new' + separator + 'blue done'
            with self.subTest(separator=separator):
                before, after = self.pair(old, new)
                self.assertEqual(before.segments, ['old' + separator + 'red'])
                self.assertEqual(after.segments, ['new' + separator + 'blue'])
                self.assertEqual(before.changes, ['replace'])
                self.assertEqual(after.changes, ['replace'])

    def test_preserved_words_and_operators_still_separate_changed_phrases(self):
        for separator in (' stable ', ' + '):
            old, new = 'label old' + separator + 'red done', 'label new' + separator + 'blue done'
            with self.subTest(separator=separator):
                before, after = self.pair(old, new)
                self.assertEqual(before.segments, ['old', 'red'])
                self.assertEqual(after.segments, ['new', 'blue'])
                self.assertEqual(''.join(before.content), old)
                self.assertEqual(''.join(after.content), new)

    def test_shared_opening_applies_to_comments_and_indentation_without_normalizing_text(self):
        for old, new, opening in (
                ('  # Summary of the old procedure and its inputs', '    # Summary now describes the shorter replacement', 'Summary'),
                ('    return extremely_long_old_expression(value)', '  return []', 'return')):
            with self.subTest(old=old, new=new):
                before, after = self.pair(old, new)
                self.assertNotIn(opening, ''.join(before.segments))
                self.assertNotIn(opening, ''.join(after.segments))
                self.assertEqual(''.join(before.content), old)
                self.assertEqual(''.join(after.content), new)

    def test_repeated_docstring_openings_remain_ambiguous(self):
        old = ['    """Hunks explain the original procedure."""']
        new = ['    """Hunks explain a different replacement A."""', '    """Hunks explain a different replacement B."""']
        rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new), old, new)
        self.assertTrue(all(row[5:] == (None, None) for row in rows))

    def test_shifted_pairs_keep_line_numbers_and_both_views_share_alignment(self):
        context = ['context %d' % n for n in range(12)]
        old = context + ['alpha = one(value)', 'beta = two(value)'] + context
        new = context + ["log('start')", 'alpha = one(extra)', 'beta = two(extra)'] + context
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        rows = render.full_rows(matcher, old, new)
        changed = [row for row in rows if row[4] != 'same']
        self.assertEqual([(row[0], row[2]) for row in changed], [(None, 13), (13, 14), (14, 15)])
        collapsed = [row for _, group in render.side_rows(matcher, old, new, rows) for row in group if row[4] != 'same']
        self.assertEqual(collapsed, changed)
        self.assertEqual([row[0] for row in rows if row[0] is not None], list(range(1, len(old) + 1)))
        self.assertEqual([row[2] for row in rows if row[2] is not None], list(range(1, len(new) + 1)))

    def test_repeated_ambiguous_assignments_keep_whole_text_fallback(self):
        old, new = ['value = old'], ['value = new_a', 'value = new_b']
        rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new), old, new)
        self.assertTrue(all(row[5:] == (None, None) for row in rows))

    def test_alignment_never_crosses_or_duplicates_source_lines(self):
        old, new = ['alpha = old', 'beta = old'], ['beta = new', 'alpha = new']
        rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new), old, new)
        self.assertEqual([row[0] for row in rows if row[0] is not None], [1, 2])
        self.assertEqual([row[2] for row in rows if row[2] is not None], [1, 2])
        self.assertEqual([row[1] for row in rows if row[0] is not None], old)
        self.assertEqual([row[3] for row in rows if row[2] is not None], new)

    def test_pair_count_and_block_length_caps_fall_back_without_comparing(self):
        for old, new in ((['old %d' % i for i in range(41)], ['new %d' % i for i in range(41)]),
                         (['old'], ['new %d' % i for i in range(81)])):
            with mock.patch.object(render, 'intraline', wraps=render.intraline) as spy:
                rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new), old, new)
            self.assertTrue(all(row[5:] == (None, None) for row in rows))
            self.assertLessEqual(spy.call_count, min(len(old), len(new)))  # no search for pairs: only the lines taken in order are judged

    def test_whole_line_add_remove_and_empty_lines_keep_exact_text(self):
        for css in ('new added', 'old removed'):
            parsed = CellText(render.cell(1, 'whole <line>', css))
            self.assertEqual(parsed.segments, ['whole <line>'])
            self.assertEqual(''.join(parsed.content), 'whole <line>')
            empty = CellText(render.cell(2, '', css))
            self.assertEqual(''.join(empty.content), '')
        self.assertIn('td.old.change,td.new.change{background:var(--changed)}', render.STYLE)
        self.assertIn('td.old.removed{background:var(--del)}td.new.added{background:var(--add)}', render.STYLE)
        self.assertIn('td.old.removed .segment,td.new.added .segment{background:transparent}', render.STYLE)
        for token in ('--segment-add:', '--segment-del:', '--segment-mod:', '--changed:'):
            self.assertEqual(render.STYLE.count(token), 2)


class LongLineTest(unittest.TestCase):
    """Long near-identical lines, one expensive pair in a block, unrelated lines, and the bounds that stay."""
    SENTENCE = 'The worker reads the core first, then the file of the step it is on, and records what it learns in the Job. '

    def long_pair(self, changed='JOB'):
        old = (self.SENTENCE * 20)[:1500]
        return old, old.replace('Job', changed, 1)

    def pair(self, old, new):
        matcher = render.difflib.SequenceMatcher(None, [old], [new], autojunk=False)
        row = render.full_rows(matcher, [old], [new])[0]
        return (CellText(render.cell(row[0], row[1], 'old ' + row[4], row[5])),
                CellText(render.cell(row[2], row[3], 'new ' + row[4], row[6])))

    def test_two_long_lines_that_differ_in_one_word_get_a_word_segment(self):
        old, new = self.long_pair()
        self.assertGreater(len(old), 1400)
        before, after = self.pair(old, new)
        self.assertEqual(before.segments, ['Job'])
        self.assertEqual(after.segments, ['JOB'])
        self.assertEqual(''.join(before.content), old)
        self.assertEqual(''.join(after.content), new)

    def test_a_pair_too_long_for_the_character_score_is_scored_from_its_words_and_costs_its_word_tokens(self):
        old, new = self.long_pair()
        self.assertGreater(len(old) * len(new), render.MAX_CHARACTER_PRODUCT)
        with mock.patch.object(render, 'intraline', wraps=render.intraline) as spy:
            render.full_rows(render.difflib.SequenceMatcher(None, [old], [new]), [old], [new])
        self.assertEqual(spy.call_args.args[2], False)
        compared, match, remaining = render.compare(old, new, (len(render.words(old)), len(render.words(new))), render.MAX_INTRALINE_WORK)
        self.assertTrue(compared)
        self.assertEqual(render.MAX_INTRALINE_WORK - remaining, len(render.words(old)) * len(render.words(new)))

    def test_one_over_budget_pair_leaves_the_other_pairs_of_the_block_segmented(self):
        old, new = self.long_pair()
        olds, news = [old, 'alpha = one(value)', 'beta = two(value)'], [new, 'alpha = one(extra)', 'beta = two(extra)']
        with mock.patch.object(render, 'MAX_INTRALINE_PRODUCT', 100_000):  # only the pair of the two long lines is beyond the limit
            rows = render.full_rows(render.difflib.SequenceMatcher(None, olds, news, autojunk=False), olds, news)
        self.assertEqual([row[4] for row in rows], ['change'] * 3)
        self.assertEqual(rows[0][5:], (None, None))  # the long pair keeps the whole-text fallback: nothing is known about it
        self.assertEqual(CellText(render.cell(rows[1][0], rows[1][1], 'old change', rows[1][5])).segments, ['value'])
        self.assertEqual(CellText(render.cell(rows[2][2], rows[2][3], 'new change', rows[2][6])).segments, ['extra'])

    def test_unrelated_lines_in_a_replace_block_are_a_removal_and_an_addition(self):
        old = ['## Inputs and commands', 'alpha = one(value)']
        new = ['The notes are an index and seven files by purpose.', 'alpha = one(extra)']
        rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new, autojunk=False), old, new)
        self.assertEqual([(row[0], row[2], row[4]) for row in rows], [(1, None, 'removed'), (None, 1, 'added'), (2, 2, 'change')])
        self.assertNotEqual(rows[2][5:], (None, None))

    def test_a_block_beyond_the_pairing_limits_judges_its_lines_in_order_only(self):
        old = ['## Heading number %d of the old notes' % n for n in range(120)]
        new = ['An index sentence that says nothing alike, number %d' % n for n in range(100)]
        rows = render.full_rows(render.difflib.SequenceMatcher(None, old, new, autojunk=False), old, new)
        self.assertEqual({row[4] for row in rows}, {'removed', 'added'})
        self.assertEqual([row[0] for row in rows if row[0] is not None], list(range(1, 121)))
        self.assertEqual([row[2] for row in rows if row[2] is not None], list(range(1, 101)))

    def test_one_oversized_line_does_not_disqualify_the_comparable_lines_of_its_block(self):
        huge = 'x ' * (render.MAX_INTRALINE_LENGTH // 2 + 10)
        olds, news = [huge, 'alpha = one(value)', 'beta = two(value)'], [huge + 'y', 'alpha = one(extra)', 'beta = two(extra)']
        rows = render.full_rows(render.difflib.SequenceMatcher(None, olds, news, autojunk=False), olds, news)
        self.assertEqual(rows[0][5:], (None, None))
        self.assertEqual(CellText(render.cell(rows[1][0], rows[1][1], 'old change', rows[1][5])).segments, ['value'])
        self.assertEqual(CellText(render.cell(rows[2][2], rows[2][3], 'new change', rows[2][6])).segments, ['extra'])

    def test_a_pair_over_the_token_limit_does_not_disqualify_the_pairs_that_fit(self):
        big = '!' * 4000  # 4,000 tokens, within the length limit
        edit_old = ' '.join('w%d' % n for n in range(600)) + ' end'  # about 1,200 tokens
        edit_new = edit_old.replace('w300', 'CHANGED')
        olds, news = [big, edit_old], [big + '?', edit_new]
        self.assertLessEqual(len(big), render.MAX_INTRALINE_LENGTH)
        self.assertGreater(len(render.words(big)) * len(render.words(edit_new)), render.MAX_INTRALINE_PRODUCT)
        rows = render.full_rows(render.difflib.SequenceMatcher(None, olds, news, autojunk=False), olds, news)
        self.assertEqual(CellText(render.cell(rows[1][0], rows[1][1], 'old change', rows[1][5])).segments, ['w300'])
        self.assertEqual(CellText(render.cell(rows[1][2], rows[1][3], 'new change', rows[1][6])).segments, ['CHANGED'])

    def test_similar_lines_without_a_letter_or_digit_are_one_change_not_a_removal_and_an_addition(self):
        old, new = '-' * 10, '-' * 9 + '!'
        self.assertIs(render.intraline(old, new), render.UNANCHORED)
        self.assertIsNone(render.intraline('aaa', 'bbb'))
        rows = render.full_rows(render.difflib.SequenceMatcher(None, [old], [new], autojunk=False), [old], [new])
        self.assertEqual([row[4] for row in rows], ['change'])
        self.assertEqual(rows[0][5:], (None, None))

    def test_a_pathological_file_is_still_bounded_by_the_budget(self):
        line = ' '.join('w%d' % n for n in range(400))
        olds = [line + ' a%d' % n for n in range(30)]
        news = [line + ' b%d' % n for n in range(30)]
        with mock.patch.object(render, 'intraline', wraps=render.intraline) as spy:
            rows = render.full_rows(render.difflib.SequenceMatcher(None, olds, news, autojunk=False), olds, news)
        self.assertLess(spy.call_count, 30 * 30)  # the budget stops the comparisons long before every pair is compared
        self.assertEqual(len([row for row in rows if row[0] is not None]), 30)


class ChangeCountTest(Fixture):
    def test_the_total_is_written_beside_the_buttons_and_matches_the_marked_rows(self):
        text = self.build()
        modified = section_of(text, 'a.txt')
        self.assertIn('<span class="count" data-count>1 change</span>', modified)
        self.assertEqual(modified.count('<tr data-c'), 2)  # the one change, once in each view
        old = ['keep %d' % n for n in range(40)]
        new = list(old)
        for n in (2, 12, 22, 32):
            new[n] = 'changed %d' % n
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        everything = render.full_rows(matcher, old, new)
        marked = render.side_table(everything, self.parent, self.tree).count('<tr data-c')
        collapsed = [row for hidden, rows in render.side_rows(matcher, old, new, everything) for row in ([(hidden, 'gap')] if hidden else []) + rows]
        self.assertEqual(render.side_table(collapsed, self.parent, self.tree).count('<tr data-c'), marked)
        self.assertEqual(marked, 4)
        self.assertIn('data-count>4 changes</span>', render.nav(marked))

    def test_a_file_with_several_changes_shows_its_total_in_the_page(self):
        old = ''.join('line %d\n' % n for n in range(8))
        new = old.replace('line 1\n', 'one\n').replace('line 5\n', 'five\n')
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        repo = Path(folder.name)
        git(repo, 'init', '-q', '-b', 'main')
        write(repo, 'f.txt', old)
        git(repo, 'add', '-A')
        git(repo, 'commit', '-q', '-m', 'a')
        parent = git(repo, 'rev-parse', 'HEAD')
        write(repo, 'f.txt', new)
        git(repo, 'commit', '-q', '-am', 'b')
        record = {'id': 'p' * 32, 'branch': 'refs/heads/tx/demo', 'parent': parent, 'tree': git(repo, 'rev-parse', 'HEAD^{tree}'), 'manifest': ['f.txt']}
        section = section_of(render.build(repo, render.PREVIEW, record, 'demo_job', []), 'f.txt')
        self.assertIn('<span class="count" data-count>2 changes</span>', section)
        self.assertEqual(section.count('<tr data-c'), 2)

    def test_the_script_keeps_the_count_current_and_shows_the_total_before_any_navigation(self):
        self.assertIn('data-count', render.SCRIPT)
        self.assertIn("'Change '+(k+1)+' of '+n", render.SCRIPT)
        self.assertIn('s._t!=null||c.scrollTop>0', render.SCRIPT)  # before a jump or a scroll: the total alone
        self.assertIn('.nav .count{', render.STYLE)


class NavigationTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = Path(folder.name).resolve()
        git(self.repo, 'init', '-q', '-b', 'main')
        write(self.repo, 'seed.txt', 'seed\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'a')
        self.parent = git(self.repo, 'rev-parse', 'HEAD')
        for name, data in (('a.txt', 'a\n'), ('dir/z.txt', 'z\n1\n2\n3\n'), ('dir/sub/m.txt', 'm\n'), ('zdir/x.txt', 'x\nx\n'), ('b.bin', b'\x00\x01')):
            (self.repo / name).parent.mkdir(parents=True, exist_ok=True)
            write(self.repo, name, data)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-q', '-m', 'b')
        self.tree_id = git(self.repo, 'rev-parse', 'HEAD^{tree}')
        record = {'id': 'p' * 32, 'branch': 'refs/heads/tx/demo', 'parent': self.parent, 'tree': self.tree_id, 'manifest': ['a.txt']}
        self.text = render.build(self.repo, render.PREVIEW, record, 'demo_job', [])
        self.tree = self.text.split('<nav>', 1)[1].split('</nav>', 1)[0]
        self.table = self.text.split('<table class="files">', 1)[1].split('</table>', 1)[0]

    def test_the_list_follows_the_order_of_the_tree_and_the_section_numbers_follow_both(self):
        expected = ['dir/sub/m.txt', 'dir/z.txt', 'zdir/x.txt', 'a.txt', 'b.bin']  # at each level the subfolders come before the files, each by name
        self.assertEqual([item['path'] for item in render.changes(self.repo, self.parent, self.tree_id)], expected)
        in_tree = re.findall(r'<a class="\w" href="#f(\d+)">([^<]*)</a>', self.tree)
        self.assertEqual(in_tree, [('1', 'm.txt'), ('2', 'z.txt'), ('3', 'x.txt'), ('4', 'a.txt'), ('5', 'b.bin')])
        in_table = re.findall(r'<tr data-n="(\d+)" data-size="(-?\d+)">.*?<a href="#f(\d+)">([^<]*)</a>', self.table)
        self.assertEqual([(n, link, path) for n, _, link, path in in_table], [(str(k), str(k), p) for k, p in enumerate(expected, 1)])
        self.assertEqual([size for _, size, _, _ in in_table], ['1', '4', '2', '1', '-1'])  # lines changed; a binary file has none
        for number, path in enumerate(expected, 1):
            self.assertIn(path.rsplit('/', 1)[-1], section_of(self.text, path).split('</h3>', 1)[0])
            self.assertEqual(self.text.count('<section id="f%d">' % number), 1)

    def test_the_table_has_the_sort_buttons_and_the_script_sorts_by_size_with_binaries_last_and_back_by_place(self):
        self.assertIn('<th aria-sort="ascending"><button type="button" data-sort="name"', self.table)
        self.assertIn('<th class="size" aria-sort="none"><button type="button" data-sort="size"', self.table)
        for needle in ("getAttribute('data-sort')", "'descending'", "(a<0)!==(b<0)", "x.getAttribute('data-n')-y.getAttribute('data-n')"):
            self.assertIn(needle, render.SCRIPT)
        self.assertEqual(self.text.count('<script'), 1)  # the page stays one file under its hashed policy
        self.assertIn(base64.b64encode(hashlib.sha256(render.SCRIPT.encode('utf-8')).digest()).decode('ascii'), self.text)

    def test_the_open_file_is_marked_in_the_tree_and_the_mark_is_cleared_for_the_list(self):
        for needle in ('aria-current', '/^f(\\d+)/', 'p.tagName===\'DETAILS\''):
            self.assertIn(needle, render.SCRIPT)  # a rename's #fN-old and #fN-new mark the entry #fN
        self.assertIn('removeAttribute(\'aria-current\')', render.SCRIPT)  # runs before the early return for a page with no open file
        self.assertIn('nav a[aria-current]{', render.STYLE)
        self.assertNotIn('aria-current', self.tree)  # nothing is open when the page loads

    def test_every_row_of_a_change_names_its_place_with_the_numbers_of_the_counter(self):
        old = ['keep %d' % n for n in range(40)]
        new = list(old)
        new[2:4] = ['two', 'three', 'extra']
        new[20] = 'twenty'
        new[30:30] = ['inserted']
        matcher = render.difflib.SequenceMatcher(None, old, new, autojunk=False)
        everything = render.full_rows(matcher, old, new)
        collapsed = [row for hidden, rows in render.side_rows(matcher, old, new, everything) for row in ([(hidden, 'gap')] if hidden else []) + rows]
        for rows in (everything, collapsed):
            table = render.side_table(rows, 'p' * 40, 't' * 40)
            self.assertEqual(re.findall(r'<tr data-c data-tip="(Change \d of \d)">', table), ['Change 1 of 3', 'Change 2 of 3', 'Change 3 of 3'])
            titled = re.findall(r'<tr(?: data-c)? data-tip="(Change \d of \d)">(.*?)</tr>', table)
            self.assertGreater(len(titled), 3)  # the later rows of a change carry the title too
            for title, row in titled:
                self.assertNotIn('class="old same"', row)  # an unchanged row has none
            self.assertEqual(len(titled), table.count('data-tip="Change'))
        self.assertIn('data-count>3 changes</span>', render.nav(3))  # the counter's total is the n of the titles
        self.assertNotIn('data-tip', render.side_table(render.full_rows(render.difflib.SequenceMatcher(None, ['x'], ['x']), ['x'], ['x']), 'p' * 40, 't' * 40))

    def test_the_position_shows_at_once_in_a_box_of_the_page_not_in_a_delayed_title(self):
        self.assertNotIn('title="Change', render.side_table([(1, 'a', 1, 'b', 'change', None, None)], 'p' * 40, 't' * 40))
        for needle in ("'tr[data-tip]'", "getAttribute('data-tip')", "className='tip'", "addEventListener('mousemove'"):
            self.assertIn(needle, render.SCRIPT)
        self.assertIn('.tip{position:fixed;', render.STYLE)
        self.assertIn('pointer-events:none', render.STYLE)


class IntralinePageTest(Fixture):
    def test_preview_and_sealed_views_share_highlights_and_unchanged_security_script(self):
        for kind in (render.PREVIEW, render.SEALED):
            text = self.build(kind)
            collapsed, complete = views_of(section_of(text, 'a.txt'))
            self.assertIn('class="segment"', collapsed)
            self.assertIn('class="segment"', complete)
            self.assertEqual(text.count('<script>'), 1)
            self.assertIn('<script>' + render.SCRIPT + '</script>', text)
            self.assertNotIn('THE WORKING TREE CHANGED LATER', text)


if __name__ == '__main__':
    unittest.main()
