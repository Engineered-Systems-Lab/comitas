"""The diff viewer on a disposable repository: the page shows the recorded candidate, labels it, escapes it and refuses a sealed diff that differs."""
import base64
import hashlib
import hmac
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

import render

TRICKY = "a&b 'x'.txt"
ENV = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@example.invalid', GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@example.invalid')


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True, check=True, env=ENV).stdout.decode('utf-8').strip()


def write(root, name, data):
    (Path(root) / name).write_bytes(data if isinstance(data, bytes) else data.encode('utf-8'))


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


class CompactHeaderTest(Fixture):
    def test_the_header_is_a_narrow_bar_that_names_the_kind_in_its_own_colour(self):
        preview, sealed = self.build(), self.build(render.SEALED)
        self.assertIn('<div class="label preview"><strong>PREVIEW (no approval)</strong>', preview)
        self.assertIn('<div class="label sealed"><strong>SEALED REVIEW</strong>', sealed)
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
        self.assertEqual(render.side_table(everything, self.parent, self.tree).count('<tr data-c>'), 2)  # b and c are one run, h and i another
        collapsed = [row for hidden, rows in render.side_rows(matcher, old, new) for row in ([(hidden, 'gap')] if hidden else []) + rows]
        self.assertEqual(render.side_table(collapsed, self.parent, self.tree).count('<tr data-c>'), 2)

    def test_a_file_with_two_views_has_the_buttons_after_the_checkbox_and_the_first_starts_disabled(self):
        text = self.build()
        modified, added = section_of(text, 'a.txt'), section_of(text, 'new.txt')
        self.assertIn('Collapse unchanged fragments</label><span class="nav"><button type="button" data-nav="prev" title="Previous change" aria-label="Previous change" disabled>', modified)
        self.assertIn('<button type="button" data-nav="next" title="Next change" aria-label="Next change">', modified)
        self.assertNotIn('data-nav', added)  # one change in one table: nothing to jump to
        self.assertEqual(modified.count('<tr data-c>'), 2)  # the one change, in each view
        self.assertIn("data-nav", render.SCRIPT)


class CollapseToggleTest(Fixture):
    def test_the_checkbox_is_named_like_the_ide_checked_by_default_and_on_the_heading_row_without_a_script(self):
        text = self.build()
        modified, added = section_of(text, 'a.txt'), section_of(text, 'new.txt')
        self.assertIn('<label class="toggle"><input type="checkbox" class="collapse" checked> Collapse unchanged fragments</label><span class="nav">', modified)
        self.assertRegex(modified, r'^id="f\d+"><div class="bar"><a class="back" href="#">&larr; All files</a><h3>.*</h3><label class="toggle">')  # one row: back link, heading, checkbox
        self.assertNotIn('Collapse unchanged', added)  # one table only: nothing to switch
        self.assertIn('section:has(.collapse:checked) .fullview,section:has(.collapse:not(:checked)) .hunkview{display:none}', text)
        self.assertEqual(text.count('<script'), 1)  # still the divider alone

    def test_a_modified_file_holds_the_changes_with_context_and_the_complete_file(self):
        collapsed, complete = views_of(section_of(self.build(), 'a.txt'))
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
        text = self.build()
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
        self.assertEqual(render.main(['--repo', str(self.root), '--job', 'demo_job', 'preview', '--preview', str(path)]), 0)

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

    def test_a_packet_diff_that_differs_is_refused_and_nothing_is_written(self):
        args = self.sealed(render.candidate_diff(self.root, self.parent, self.tree) + 'extra\n')
        with self.assertRaises(render.ViewerError):
            render.run_sealed(args)
        self.assertEqual(list(self.review.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
