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

    def test_the_only_script_is_the_divider_and_the_policy_pins_it_by_hash(self):
        text = self.build()
        script = text.split('<script>', 1)[1].split('</script>', 1)[0]
        self.assertEqual(script, render.SCRIPT)
        digest = base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode('ascii')
        self.assertIn("script-src 'sha256-" + digest + "'", text)
        self.assertNotIn('@@', text)

    def test_the_divider_and_visible_links_are_there(self):
        text = self.build()
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
