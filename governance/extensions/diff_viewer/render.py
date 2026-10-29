"""Diff viewer: one self-contained HTML page of the exact preview or sealed candidate, written beside the review.

Run it as `python -B governance/extensions/diff_viewer/render.py preview --preview <preview.json>` or `... sealed`. The page is
built only from the recorded parent commit and candidate tree object, read from Git objects, never from the working tree.
It holds one inline script that only moves the divider and focuses the open file so the mouse wheel scrolls it, no external resource and no approval control, and it changes nothing the checkpoint tool records."""
import argparse
import base64
import difflib
import hashlib
import hmac
import html
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import unicodedata

ROOT = Path(__file__).resolve().parents[3]
PREVIEW, SEALED = 'preview', 'sealed'
LABELS = {PREVIEW: 'PREVIEW (no approval)', SEALED: 'SEALED REVIEW'}
CONTEXT = 3
MAX_INTRALINE_LENGTH = 4096  # characters per line; larger lines keep whole-text highlights
MAX_INTRALINE_PRODUCT = 1_000_000  # upper bound on character comparisons for one pair
MAX_INTRALINE_WORK = 8_000_000  # shared comparison budget per file, reused by both views
MIN_INTRALINE_SIMILARITY = .6
MAX_PAIR_LINES = 80  # maximum lines per side of a replacement block to align
MAX_PAIR_COMPARISONS = 1600  # maximum old/new candidate pairs in one block
PAIR_MARGIN = .05  # near-tied alternatives are too ambiguous for precise pairing
MAX_LINES = 3000  # per side of one file; more is cut and the page says so
MAX_BYTES = 1_000_000  # per side of one file, read before the lines are counted
MAX_FILES = 300  # files shown side by side; the list always holds every file
SIMILARITY = 50  # percent of a file that must stay for a deletion and an addition to be shown as one rename
RENAME_LIMIT = 1000  # files on each side that rename detection compares
RENAME_OPTIONS = ('-c', 'diff.renames=true', '-c', 'diff.renameLimit=%d' % RENAME_LIMIT, '-c', 'diff.algorithm=default')  # beat any setting of the user or the repository
RENAME_FLAGS = ('--no-ext-diff', '--no-textconv', '--find-renames=%d%%' % SIMILARITY, '-l%d' % RENAME_LIMIT, '--diff-algorithm=default', '-z')
MODE_NAMES = {'100644': 'file', '100755': 'executable', '120000': 'symbolic link', '160000': 'submodule', '040000': 'tree'}
PICTURES = {code: chr(0x2400 + code) for code in range(32) if code not in (9, 10)}  # control characters shown, not sent raw


# The one inline script, pinned by hash in the content security policy: it moves the divider, focuses the open file so the wheel scrolls it, and opens or closes every folder of the tree and jumps between the changes of a file.
SCRIPT = "(function(){var r=document.documentElement,d=null;function v(s){var l=s.querySelectorAll('.scroll');for(var i=0;i<l.length;i++)if(l[i].offsetParent)return l[i];return null}function tp(c,t){for(var i=0;i<1&&t.previousElementSibling&&t.previousElementSibling.firstElementChild.tagName==='TD';i++)t=t.previousElementSibling;return t.getBoundingClientRect().top-c.getBoundingClientRect().top+c.scrollTop}function hd(c){var h=c.querySelector('th.pane');return(h?h.offsetHeight:0)+4}function cu(s,c){if(s._t!=null&&Math.abs(c.scrollTop-s._p)<2)return s._t;s._t=null;var l=c.querySelectorAll('tr[data-c]'),n=-1;for(var i=0;i<l.length;i++)if(tp(c,l[i])<=c.scrollTop+hd(c))n=i;return n}function u(s){var c=v(s);if(!c)return;var n=c.querySelectorAll('tr[data-c]').length,k=cu(s,c),b=s.querySelectorAll('[data-nav]');for(var i=0;i<b.length;i++)b[i].disabled=b[i].getAttribute('data-nav')==='prev'?k<=0:k>=n-1}function f(){var s=document.querySelector('section:target');if(!s)return;var c=v(s);if(c)c.focus({preventScroll:true});u(s)}addEventListener('hashchange',f);addEventListener('load',f);addEventListener('change',function(e){var s=e.target.closest&&e.target.closest('section');if(s)s._t=null;f()});document.addEventListener('scroll',function(e){var c=e.target;if(c.classList&&c.classList.contains('scroll'))u(c.closest('section'))},true);document.addEventListener('click',function(e){var a=e.target.getAttribute&&e.target.getAttribute('data-nav');if(!a)return;var s=e.target.closest('section'),c=v(s),l=c.querySelectorAll('tr[data-c]'),k=cu(s,c)+(a==='next'?1:-1);if(k<0||k>=l.length)return;c.scrollTop=tp(c,l[k])-hd(c);s._t=k;s._p=c.scrollTop;c.focus({preventScroll:true});u(s)});document.addEventListener('click',function(e){var m=e.target.getAttribute&&e.target.getAttribute('data-fold');if(m){var l=document.querySelectorAll('nav details');for(var i=0;i<l.length;i++)l[i].open=m==='open'}});document.addEventListener('pointerdown',function(e){if(e.target.classList&&e.target.classList.contains('grip')){d=e.target.parentNode;e.target.setPointerCapture(e.pointerId);e.preventDefault()}});document.addEventListener('pointermove',function(e){if(!d)return;var b=d.getBoundingClientRect(),m=parseFloat(getComputedStyle(d).fontSize);r.style.setProperty('--split',Math.min(.85,Math.max(.15,(e.clientX-b.left)/b.width)))});document.addEventListener('pointerup',function(){d=null});document.addEventListener('dblclick',function(e){if(e.target.classList&&e.target.classList.contains('grip'))r.style.setProperty('--split',.5)})})();"


class ViewerError(ValueError):
    """The page cannot be built from the recorded facts; nothing is written."""


def require(condition, message):
    if not condition:
        raise ViewerError(message)


def git(repo, *args, env=None):
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=300, env=env)
    except (OSError, subprocess.SubprocessError) as error:
        raise ViewerError('cannot run Git: ' + str(error)) from error
    require(result.returncode == 0, 'Git failed (%s): %s' % (' '.join(args[:2]), result.stderr.decode('utf-8', 'replace').strip()))
    return result.stdout


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def checkout_root(repo):
    return Path(git(repo, 'rev-parse', '--show-toplevel').decode('utf-8').strip())


def receipt_key(repo):
    from governance.tools.continuity import artifact_classification
    from governance.tools.support import local_state
    store = local_state.checkout_store(checkout_root(repo))
    require(store is not None, 'The checkpoint store of this checkout cannot be located')
    path = store / artifact_classification.RECEIPT_KEY
    require(path.is_file() and not path.is_symlink(), 'No receipt key in ' + str(store) + ': run this in the checkout that made the preview')
    return path.read_bytes()


def load_preview(repo, path):
    """The body of an authenticated preview record; a record that was edited or made by another checkout is refused."""
    try:
        envelope = json.loads(Path(path).read_bytes().decode('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise ViewerError('Cannot read the preview record: ' + str(error)) from error
    require(isinstance(envelope, dict) and set(envelope) == {'body', 'mac'} and isinstance(envelope['body'], dict),
            'Malformed preview record')
    expected = hmac.new(receipt_key(repo), canonical(envelope['body']), hashlib.sha256).hexdigest()
    require(isinstance(envelope['mac'], str) and hmac.compare_digest(envelope['mac'], expected),
            'Authenticated record mismatch; the preview record was edited or belongs to another checkout')
    body = envelope['body']
    for key in ('id', 'branch', 'parent', 'tree', 'manifest'):
        require(key in body, 'The preview record has no ' + key)
    return body


def load_sealed(repo):
    """The sealed packet as the checkpoint tool presents it, read through its own present command."""
    result = subprocess.run([sys.executable, '-B', str(ROOT / 'governance' / 'tools' / 'checkpoint.py'), '--repo', str(repo), 'present'],
                            capture_output=True, timeout=600)
    lines = (result.stdout + result.stderr).decode('utf-8', 'replace').strip().splitlines()
    require(result.returncode == 0, 'The sealed review cannot be read: ' + (lines[-1] if lines else 'no output'))
    try:
        presented = json.loads(result.stdout.decode('utf-8'))
        packet = presented['packet']
        sha = presented['prepared_sha256']
    except (ValueError, KeyError, TypeError) as error:
        raise ViewerError('Unreadable sealed review: ' + str(error)) from error
    for key in ('id', 'branch', 'parent', 'tree', 'diff'):
        require(isinstance(packet.get(key), str), 'The sealed packet has no ' + key)
    return packet, sha


def mission_of(branch):
    prefix = 'refs/heads/tx/'
    require(branch.startswith(prefix) and len(branch) > len(prefix), 'The recorded branch is not a mission branch: ' + branch)
    return branch[len(prefix):]


def verify_objects(repo, parent, tree):
    for name, value, kind in (('parent', parent, 'commit'), ('tree', tree, 'tree')):
        require(isinstance(value, str) and len(value) == 40 and all(c in '0123456789abcdef' for c in value), 'The recorded %s is not a full id' % name)
        found = git(repo, 'cat-file', '-t', value).decode('utf-8').strip()
        require(found == kind, 'The recorded %s is not a %s object (%s)' % (name, kind, found))


def candidate_diff(repo, parent, tree):
    return git(repo, 'diff', '--no-ext-diff', '--no-textconv', '--binary', parent, tree).decode('utf-8', 'replace')


def display_env():
    """The environment of the rename view: no system or user Git configuration is read."""
    return dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1')


def changes(repo, parent, tree):
    """Every changed path between the recorded commit and tree, in path order. A file moved with at most half of it changed is one
    renamed entry: the page pairs the deletion and the addition for display, with the options above, so no Git setting changes it;
    the checkpoint tool still records them as a deletion and an addition."""
    def run(*args):
        out = git(repo, *RENAME_OPTIONS, 'diff', *args, *RENAME_FLAGS, parent, tree, env=display_env())
        return out.decode('utf-8', 'replace').split('\0')
    raw, numbers = run('--raw', '--no-abbrev'), run('--numstat')
    counts, index = {}, 0
    while index < len(numbers):
        entry = numbers[index]
        index += 1
        if entry.count('\t') == 2:
            added, removed, path = entry.split('\t')
            if not path:  # a rename: the old and the new path follow as fields of their own
                path, index = numbers[index + 1], index + 2
            counts[path] = None if added == '-' else (int(added), int(removed))
    found, index = [], 0
    while index + 1 < len(raw) and raw[index].startswith(':'):
        old_mode, new_mode, old_id, new_id, status = raw[index][1:].split(' ')
        path, old_path, similarity = raw[index + 1], None, None
        index += 2
        if status[0] == 'R':
            old_path, path, similarity = path, raw[index], int(status[1:])
            index += 1
        found.append({'path': path, 'old_path': old_path, 'similarity': similarity, 'status': status[0], 'old_mode': old_mode,
                      'new_mode': new_mode, 'old_id': old_id, 'new_id': new_id,
                      'counts': counts.get(path, (0, 0)), 'binary': counts.get(path, (0, 0)) is None})
    return sorted(found, key=lambda item: item['path'].encode('utf-8', 'surrogatepass'))


def blob(repo, mode, object_id):
    """(bytes, size) of one side, or (None, None) when the side does not exist or is a submodule pointer."""
    if mode == '000000' or mode == '160000':
        return None, None
    data = git(repo, 'cat-file', 'blob', object_id)
    return data, len(data)


def lines_of(data):
    """(lines, cut) of one side: at most MAX_LINES lines from at most MAX_BYTES bytes; cut says what was left out."""
    cut = []
    if len(data) > MAX_BYTES:
        data = data[:MAX_BYTES]
        cut.append('%d bytes' % MAX_BYTES)
    text = data.decode('utf-8', 'replace')
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES]
        cut.append('%d lines' % MAX_LINES)
    return lines, cut


def show(line):
    return html.escape(line.translate(PICTURES), quote=True)


def kind_of(item):
    if item['status'] == 'R':
        return 'renamed'
    if item['status'] == 'A':
        return 'added'
    if item['status'] == 'D':
        return 'deleted'
    if item['status'] == 'T':
        return 'type changed'
    return 'modified'


def move_text(item):
    """`<old> -> <new> (<similarity>%)` of a renamed entry, the path alone for any other."""
    if item['status'] != 'R':
        return show(item['path'])
    return '%s \u2192 %s (%d%%)' % (show(item['old_path']), show(item['path']), item['similarity'])


def mode_text(item):
    if item['status'] in 'AD' or item['old_mode'] == item['new_mode']:
        return ''
    return 'mode %s to %s' % (item['old_mode'], item['new_mode'])


def words(line):
    """Letter/digit runs, whitespace runs and individual punctuation, without normalization."""
    parts, previous = [], None
    for char in line:
        kind = ('word' if char.isalnum() or unicodedata.category(char).startswith('M')
                else 'space' if char.isspace() else None)
        if parts and kind is not None and kind == previous:
            parts[-1] += char
        else:
            parts.append(char)
        previous = kind
    return parts


def assignment_name(line):
    """A simple assignment target is a useful anchor even when its value changes completely."""
    left, separator, right = line.strip().partition('=')
    name = left.strip()
    return name if separator and name.isidentifier() and not right.startswith('=') else None


def shared_opening(old, new):
    """A complete shared opening word is useful when the rest of a line is rewritten.

    Ignore indentation for pairing only; quotes/comment markers must still agree.
    The preferred-pair margin rejects repeated ambiguous openings in a block.
    """
    for left, right in zip(words(old.strip()), words(new.strip())):
        if left != right:
            break
        if sum(char.isalnum() for char in left) >= 3:
            return True
    return False


def phrase_spans(line, spans):
    """Join same-kind edits across spacing/punctuation, keeping words and operators as boundaries."""
    merged = []
    for start, stop, tag in spans:
        if (merged and merged[-1][2] == tag and
                all(char.isspace() or unicodedata.category(char).startswith('P')
                    for char in line[merged[-1][1]:start])):
            merged[-1] = (merged[-1][0], stop, tag)
        else:
            merged.append((start, stop, tag))
    return merged


def intraline(old, new):
    """A pairing score and changed word ranges; None means no confident correspondence."""
    before_words, after_words = words(old), words(new)
    before_offsets = list(itertools.accumulate(map(len, before_words), initial=0))
    after_offsets = list(itertools.accumulate(map(len, after_words), initial=0))
    matcher = difflib.SequenceMatcher(None, before_words, after_words, autojunk=False)
    blocks = matcher.get_matching_blocks()
    similarity = difflib.SequenceMatcher(None, old.strip(), new.strip(), autojunk=False).ratio()
    name = assignment_name(old)
    if name is not None and name == assignment_name(new):
        similarity = max(similarity, .9 + .1 * similarity)
    elif shared_opening(old, new):
        similarity = max(similarity, .65 + .35 * similarity)
    if similarity < MIN_INTRALINE_SIMILARITY or not any(
            any(any(char.isalnum() for char in part) for part in before_words[a:a + size]) for a, _, size in blocks):
        return None
    before, after = [], []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != 'equal':
            if a0 != a1:
                before.append((before_offsets[a0], before_offsets[a1], tag))
            if b0 != b1:
                after.append((after_offsets[b0], after_offsets[b1], tag))
    return similarity, (phrase_spans(old, before), phrase_spans(new, after))


def replacement_pairs(old, new, a0, a1, b0, b1, budget):
    """Bounded ordered alignment of uniquely preferred line matches, plus remaining work budget.

    Incomplete comparisons cannot establish a preferred match: keep the whole block's fallback.
    Indentation alone, repeated near-ties, huge blocks and expensive lines supply no anchors.
    """
    n, m = a1 - a0, b1 - b0
    if max(n, m) > MAX_PAIR_LINES or n * m > MAX_PAIR_COMPARISONS:
        return [], budget
    candidates = {}
    for i in range(n):
        for j in range(m):
            left, right = old[a0 + i], new[b0 + j]
            cost = len(left) * len(right)
            if max(len(left), len(right)) > MAX_INTRALINE_LENGTH or cost > MAX_INTRALINE_PRODUCT or 2 * cost > budget:
                return [], budget
            budget -= 2 * cost
            match = intraline(left, right)
            if match is not None:
                candidates[i, j] = match
    unique = {}
    for (i, j), match in candidates.items():
        if not any((x == i or y == j) and (x, y) != (i, j) and other[0] >= match[0] - PAIR_MARGIN
                   for (x, y), other in candidates.items()):
            unique[i, j] = match
    best = [[0.] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            score = unique.get((i, j), (0., None))[0]
            best[i][j] = max(best[i + 1][j], best[i][j + 1], score + best[i + 1][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        match = unique.get((i, j))
        if match is not None and best[i][j] == match[0] + best[i + 1][j + 1]:
            pairs.append((a0 + i, b0 + j, match[1]))
            i, j = i + 1, j + 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return pairs, budget


def opcode_rows(old, new, opcodes):
    """Aligned old/new numbers and text, row class, then old/new changed ranges.

    Clear pairs retain segment ranges; unmatched intervals keep positional whole-text fallback.
    Every source line is emitted once, in its original side's order.
    """
    rows, budget = [], MAX_INTRALINE_WORK

    def append(i, j, spans=(None, None)):
        rows.append((None if i is None else i + 1, None if i is None else old[i],
                     None if j is None else j + 1, None if j is None else new[j],
                     'change' if i is not None and j is not None else 'removed' if i is not None else 'added', *spans))

    for tag, a0, a1, b0, b1 in opcodes:
        if tag == 'equal':
            rows += [(a0 + i + 1, old[a0 + i], b0 + i + 1, new[b0 + i], 'same', [], []) for i in range(a1 - a0)]
            continue
        pairs = []
        if tag == 'replace':
            pairs, budget = replacement_pairs(old, new, a0, a1, b0, b1, budget)
        left, right = a0, b0
        for i, j, spans in pairs:
            for x, y in itertools.zip_longest(range(left, i), range(right, j)):
                append(x, y)
            append(i, j, spans)
            left, right = i + 1, j + 1
        for i, j in itertools.zip_longest(range(left, a1), range(right, b1)):
            append(i, j)
    return rows


def full_rows(matcher, old, new):
    """Every line of both sides as aligned rows, the changes marked."""
    return opcode_rows(old, new, matcher.get_opcodes())


def side_rows(matcher, old, new, everything=None):
    """Hunks reuse the complete view's ranges and budget, with skipped parent lines counted."""
    if everything is None:
        everything = full_rows(matcher, old, new)
    hunks, shown = [], 0
    for group in matcher.get_grouped_opcodes(CONTEXT):
        rows = []
        for row in everything:
            if ((row[0] is not None and group[0][1] < row[0] <= group[-1][2]) or
                    (row[2] is not None and group[0][3] < row[2] <= group[-1][4])):
                rows.append(row)
        hunks.append((group[0][1] - shown, rows))
        shown = group[-1][2]
    if hunks and len(old) > shown:
        hunks.append((len(old) - shown, []))
    return hunks


def highlighted(line, ranges):
    """Escape every original fragment exactly once; markup never enters the source text."""
    out, end = [], 0
    for span in ranges:
        start, stop = span[:2]
        change = ' data-change="replace"' if len(span) == 3 and span[2] == 'replace' else ''
        out += [show(line[end:start]), '<span class="segment"%s>%s</span>' % (change, show(line[start:stop]))]
        end = stop
    return ''.join(out) + show(line[end:])


def cell(number, line, css, ranges=None):
    if number is None:
        return '<td class="empty"></td>'
    if ranges is None:
        ranges = [] if css.endswith('same') else [(0, len(line), 'replace' if css.endswith('change') else 'whole')]
    return '<td class="%s"><span class="n">%d</span>%s</td>' % (css, number, highlighted(line, ranges))


NAV = '<span class="nav"><button type="button" data-nav="prev" title="Previous change" aria-label="Previous change" disabled>&uarr;</button><button type="button" data-nav="next" title="Next change" aria-label="Next change">&darr;</button></span>'


def side_table(rows, parent, tree):
    """One side-by-side table of aligned rows (a gap row is a (hidden line count, 'gap') pair), with its movable divider."""
    lines, before = '', 'same'  # the first row of every run of changed rows is marked, for the buttons that jump between changes
    for row in rows:
        if len(row) == 2:
            lines += '<tr>%s</tr>' % (2 * ('<th class="gap">... %d unchanged line%s</th>' % (row[0], '' if row[0] == 1 else 's')))
            before = 'same'
        else:
            lines += '<tr%s>%s%s</tr>' % (' data-c' if row[4] != 'same' and before == 'same' else '', cell(row[0], row[1], 'old ' + row[4], row[5]), cell(row[2], row[3], 'new ' + row[4], row[6]))
            before = row[4]
    panes = '<tr><th class="pane">Parent %s</th><th class="pane">Candidate %s</th></tr>' % (show(parent[:8]), show(tree[:8]))
    return ('<div class="scroll" tabindex="0"><div class="frame"><table class="side"><colgroup><col class="l"><col class="r"></colgroup>%s%s</table>'
            '<div class="grip" title="Drag to move the divider; double-click to centre"></div></div></div>') % (panes, lines)


def sides_of(item):
    """The deletion and the addition a rename is made of, each as an entry of its own."""
    return (dict(item, status='D', path=item['old_path'], new_mode='000000', old_path=None, similarity=None),
            dict(item, status='A', old_mode='000000', old_path=None, similarity=None))


def file_section(repo, item, number, parent, tree, back='<a class="back" href="#">&larr; All files</a>'):
    """The section of one file: a notice for binaries and submodules, side-by-side hunks and the complete file for text; a rename
    names its pairing and links its two sides, which are sections of their own."""
    kind = kind_of(item)
    notes = [mode_text(item)] if mode_text(item) else []
    old_data, old_size = blob(repo, item['old_mode'], item['old_id'])
    new_data, new_size = blob(repo, item['new_mode'], item['new_id'])
    sizes = '%s bytes before, %s bytes after' % ('no' if old_size is None else old_size, 'no' if new_size is None else new_size)
    head = '%s<h3><span class="kind %s">%s</span> %s</h3>' % (back, kind.replace(' ', '-'), kind, move_text(item))
    toggle = ''  # the checkbox of the file, at the right end of the heading row, for a file that has two views
    body = ''.join('<p class="note">%s</p>' % html.escape(note) for note in notes)
    if item['status'] == 'R':
        body += ('<p class="note">Display aid: this page paired the deletion of %s with the addition of %s because %d%% of the file stayed; '
                 'the checkpoint tool records them as a deletion and an addition. See each side as its own file: '
                 '<a href="#f%s-old">the old path as deleted</a>, <a href="#f%s-new">the new path as added</a>.</p>') % (
            show(item['old_path']), show(item['path']), item['similarity'], number, number)
    if '160000' in (item['old_mode'], item['new_mode']):
        body += '<p class="note">Submodule: the recorded commit changed from %s to %s.</p>' % (
            show(item['old_id'] if item['old_mode'] == '160000' else 'none'), show(item['new_id'] if item['new_mode'] == '160000' else 'none'))
    elif item['binary']:
        body += '<p class="note">Binary file, not shown: %s.</p>' % sizes
    else:
        old, cut_old = lines_of(old_data or b'')
        new, cut_new = lines_of(new_data or b'')
        if cut_old or cut_new:
            body += '<p class="note">Large file: only the first %s of each side is shown (%s).</p>' % (' and '.join(sorted(set(cut_old + cut_new))), sizes)
        matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
        everything = full_rows(matcher, old, new)  # first: get_grouped_opcodes trims the matcher's cached opcodes in place
        hunks = side_rows(matcher, old, new, everything)
        unchanged = not hunks and not (cut_old or cut_new)
        if unchanged:
            body += '<p class="note">%s</p>' % ('Moved with no content change: the old and the new path hold the same bytes.' if item['status'] == 'R' else 'No content change.')
        collapsed = [row for hidden, rows in hunks for row in ([(hidden, 'gap')] if hidden else []) + rows]
        if unchanged and item['status'] == 'R':
            pass  # a pure move has no change lines; each side is a section of its own
        elif collapsed == everything:  # nothing is left out, so one table serves both states of the checkbox
            if sum(1 for n, row in enumerate(everything) if row[4] != 'same' and (n == 0 or everything[n - 1][4] == 'same')) > 1:
                toggle = NAV  # several changes in a short file: the buttons only
            body += '<div class="allview">%s</div>' % side_table(everything, parent, tree) if everything else ''
        else:
            toggle = '<label class="toggle"><input type="checkbox" class="collapse" checked> Collapse unchanged fragments</label>' + NAV
            body += '<div class="hunkview">%s</div><div class="fullview">%s</div>' % (
                side_table(collapsed, parent, tree) if collapsed else '', side_table(everything, parent, tree))
    return '<section id="f%s"><div class="bar">%s%s</div>%s</section>' % (number, head, toggle, body)


STYLE = """:root{--bg:#fff;--fg:#1d2129;--mute:#5b6472;--line:#d5d9e0;--panel:#f4f6f9;--del:#fde8e8;--add:#e3f6e6;--chg:#fff3d6;--changed:#edf0f3;--segment-del:#edb7b7;--segment-add:#b9d9b0;--segment-mod:#bdd3ef;--preview:#8a1f11;--sealed:#1b4f8f;--bannerfg:#fff;--link:#0b5cad;--split:.5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14171c;--fg:#e3e6eb;--mute:#9aa3b0;--line:#323844;--panel:#1c2027;--del:#4a1f22;--add:#1b3d26;--chg:#4a3d17;--changed:#252a30;--segment-del:#713b40;--segment-add:#3d5d35;--segment-mod:#304e73;--preview:#b8321f;--sealed:#2f6dbd;--link:#8ab4f8}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{padding:0 16px 6px}.layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px;align-items:start}
nav{position:sticky;top:0;max-height:100vh;overflow:auto;padding:12px 0;font-size:13px}nav ul{list-style:none;margin:0;padding-left:14px}nav>ul{padding-left:0}nav a{color:inherit;text-decoration:none;display:block;padding:1px 4px;border-radius:3px;word-break:break-all}nav a:hover{background:var(--panel)}nav summary{color:var(--mute);padding:1px 4px;cursor:pointer}nav details>ul{padding-left:14px}.treebar{display:flex;gap:6px;margin:0 0 6px}.treebar button{font:12px system-ui,sans-serif;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:1px 8px;cursor:pointer}
nav .a{border-left:3px solid #3a9b4f}nav .d{border-left:3px solid #c2433b}nav .m{border-left:3px solid #c99a2e}nav .r{border-left:3px solid #3b7fc4}
@media (max-width:900px){.layout{display:block}nav{position:static;max-height:none}}
.label{display:flex;flex-wrap:wrap;align-items:baseline;gap:2px 14px;color:var(--bannerfg);padding:5px 16px;font-size:12px}
.label.preview{background:var(--preview)}.label.sealed{background:var(--sealed)}
.label strong{font-size:15px;font-weight:800;letter-spacing:.04em}
table{border-collapse:collapse}th,td{vertical-align:top}
.facts{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 32px;margin:8px 0 10px;font-size:13px;line-height:1.35}
.facts .fact{display:grid;grid-template-columns:8.5em minmax(0,1fr);padding:1px 0}.facts .wide{grid-column:1/-1}
.facts .k{color:var(--mute);font-weight:600}.facts .v{word-break:break-all}
@media (max-width:700px){.facts{grid-template-columns:minmax(0,1fr)}}
table.files{width:100%;margin:8px 0 24px}table.files td,table.files th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}
.kind{display:inline-block;padding:0 8px;border-radius:10px;font-size:12px;font-weight:700;background:var(--panel);border:1px solid var(--line)}
.kind.added{background:var(--add)}.kind.deleted{background:var(--del)}.kind.modified,.kind.type-changed,.kind.renamed{background:var(--chg)}
section{display:none;margin:0}section:target{display:block}.view:has(section:target) .overview{display:none}.bar{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin:0 0 8px}.bar h3{margin:0;flex:1 1 auto}h3{font-size:16px;word-break:break-all}
.note{margin:6px 0;color:var(--mute)}
a,a:visited{color:var(--link)}nav a,nav a:visited{color:inherit}.frame{position:relative;min-width:70em}.grip{position:absolute;top:0;bottom:0;width:19px;margin-left:-10px;left:calc(100%*var(--split));cursor:col-resize;touch-action:none;background:linear-gradient(to right,transparent 7px,var(--mute) 7px,var(--mute) 12px,transparent 12px);opacity:.7}.grip:hover{opacity:1;background:linear-gradient(to right,rgba(128,128,128,.18) 0,rgba(128,128,128,.18) 100%),linear-gradient(to right,transparent 7px,var(--fg) 7px,var(--fg) 12px,transparent 12px)}table.side col.l{width:calc(var(--split)*100%)}table.side col.r{width:calc((1 - var(--split))*100%)}table.side{width:100%;table-layout:fixed;margin:8px 0;border:1px solid var(--line);font:13px/1.45 ui-monospace,Consolas,monospace}.scroll{overflow:auto;max-height:calc(100vh - 7.5em);overscroll-behavior:contain}.scroll:focus{outline:none}
table.side th.pane{position:sticky;top:0;z-index:2;background:var(--panel);text-align:left;padding:3px 8px;font-weight:600}table.side th.gap{background:var(--panel);text-align:center;padding:2px 8px;color:var(--mute);font-weight:400;border-top:1px dashed var(--line);border-bottom:1px dashed var(--line)}
table.side td{white-space:pre-wrap;overflow-wrap:anywhere;padding:0 6px}
span.n{display:inline-block;width:3.2em;margin:0 8px 0 -6px;padding-right:6px;text-align:right;color:var(--mute);background:var(--panel);user-select:none}
td.old.change,td.new.change{background:var(--changed)}
td.old.removed{background:var(--del)}td.new.added{background:var(--add)}
td.old .segment{background:var(--segment-del)}td.new .segment{background:var(--segment-add)}
td .segment[data-change="replace"]{background:var(--segment-mod)}
td.old.removed .segment,td.new.added .segment{background:transparent}
.segment{border-radius:2px;box-decoration-break:clone}
td.empty{background:var(--panel)}
label.toggle{margin-left:auto;cursor:pointer;user-select:none;white-space:nowrap}.nav{display:inline-flex;gap:4px;margin-left:auto}.toggle+.nav{margin-left:0}.nav button{font:13px system-ui,sans-serif;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:0 9px;cursor:pointer}.nav button:disabled{opacity:.35;cursor:default}
section:has(.collapse:checked) .fullview,section:has(.collapse:not(:checked)) .hunkview{display:none}
.fine{color:var(--mute);margin:2px 0 0;font-size:11px;line-height:1.25}
body:has(section:target) .facts{display:none}
@media (min-width:901px){html,body{height:100%}body{display:flex;flex-direction:column;overflow:hidden}main{flex:1;min-height:0;display:flex;flex-direction:column}.layout{flex:1;min-height:0;grid-template-rows:minmax(0,1fr);align-items:stretch}nav{position:static;max-height:none;min-height:0}.view{min-height:0;overflow:auto;display:flex;flex-direction:column}section:target{flex:1;min-height:0;display:flex;flex-direction:column}.hunkview,.allview,.fullview{flex:1;min-height:0;display:flex;flex-direction:column}.scroll{flex:1;max-height:none;min-height:0}}
@media (max-width:700px){table.side{font-size:11px}span.n{width:2.4em}}"""


def tree(items, shown):
    """The changed files as a folder tree of links, like the file list of an IDE's diff window."""
    root = {}
    for n, item in enumerate(items, 1):
        node = root
        *folders, name = item['path'].split('/')
        for folder in folders:
            node = node.setdefault(folder + '/', {})
        node[name] = (n, item)

    def walk(node):
        out = ''
        for name in sorted(node, key=lambda k: (not isinstance(node[k], dict), k)):
            value = node[name]
            if isinstance(value, dict):
                out += '<li><details open><summary>%s</summary><ul>%s</ul></details></li>' % (show(name), walk(value))
            else:
                n, item = value
                css = {'added': 'a', 'deleted': 'd', 'renamed': 'r'}.get(kind_of(item), 'm')
                title = ' title="renamed from %s"' % show(item['old_path']) if item['status'] == 'R' else ''
                out += '<li>%s</li>' % ('<a class="%s" href="#f%d"%s>%s</a>' % (css, n, title, show(name)) if n <= shown else '<span class="%s"%s>%s</span>' % (css, title, show(name)))
        return out
    return '<ul>%s</ul>' % walk(root)


def page(*arguments):
    text = build_page(*arguments)
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode('utf-8')).digest()).decode('ascii')
    return text.replace('@@HASH@@', digest).replace('@@SCRIPT@@', SCRIPT)


def headline(kind, job):
    """The bar's label: the kind as before, naming the Job when it is known."""
    if not job:
        return html.escape(LABELS[kind])
    return ('PREVIEW of %s (no approval)' if kind == PREVIEW else 'SEALED REVIEW of %s') % show(job)


def build_page(kind, facts, items, sections, omitted, job=None):
    def fact(name, value, wide=False):
        return '<div class="fact%s"><span class="k">%s</span><span class="v">%s</span></div>' % (' wide' if wide else '', html.escape(name), show(str(value)))
    shown = min(len(items), MAX_FILES)  # files that have a section; the sides of a rename are sections too, but not files
    short = [f for f in facts if f[0] != 'Manifest']  # the manifest can be long and spans both columns
    half = (len(short) + 1) // 2
    rows = ''.join(['<div>%s</div>' % ''.join(fact(*f) for f in part) for part in (short[:half], short[half:])] +
                   [fact(name, value, True) for name, value in facts if name == 'Manifest'])
    listing = ''.join('<tr><td><span class="kind %s">%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        kind_of(i).replace(' ', '-'), kind_of(i), '<a href="#f%d">%s</a>' % (n, move_text(i)) if n <= shown else move_text(i),
        show(mode_text(i)), 'binary' if i['binary'] else '+%d -%d' % i['counts']) for n, i in enumerate(items, 1))
    aid = ('<p class="note">Display aid: a file moved with at least %d%% of it unchanged is shown as one renamed entry, paired by this page from the recorded parent and candidate. '
           'The checkpoint tool records a move as a deletion and an addition, and its review and the sealed diff list it that way.</p>' % SIMILARITY) if any(i['status'] == 'R' for i in items) else ''
    more = '<p class="note">The first %d files are shown side by side; the list above has every file.</p>' % MAX_FILES if omitted else ''
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'sha256-@@HASH@@\'">\n'
            '<title>%s</title><style>%s</style></head><body>\n<div class="label %s"><strong>%s</strong><span>%s</span></div>\n<main>\n'
            '<div class="facts">%s</div>\n<div class="layout"><nav><div class="treebar"><button type="button" data-fold="open">Expand all</button><button type="button" data-fold="close">Collapse all</button></div>%s</nav><div class="view">\n<div class="overview"><h2>Files (%d)</h2>\n<table class="files"><tr><th>Change</th><th>Path</th><th>Mode</th><th>Lines</th></tr>%s</table>\n<p class="note">Choose a file in the list or in the tree to read its changes.</p>%s</div>\n'
            '%s%s\n<p class="fine">Built from the recorded parent commit and candidate tree only, never the working tree. '
            'A reading aid: it grants no approval and records nothing; the decision is taken in the conversation.</p>\n'
            '</div></div></main><script>@@SCRIPT@@</script></body></html>\n') % (
        ('Diff %s: %s' % ('preview' if kind == PREVIEW else 'sealed review', show(job))) if job else 'Diff ' + kind, STYLE, kind, headline(kind, job),
        'Read-only view of the exact candidate; not an approval.' if kind == PREVIEW else 'Read-only view of the exact sealed candidate; the decision is taken in the conversation.',
        rows, tree(items, shown), len(items), listing, aid, ''.join(sections), more)


def build(repo, kind, record, job, extra):
    """The page for a recorded candidate: record holds id, branch, parent, tree."""
    parent, tree = record['parent'], record['tree']
    verify_objects(repo, parent, tree)
    items = changes(repo, parent, tree)
    sections = []
    for n, item in enumerate(items[:MAX_FILES], 1):
        sections.append(file_section(repo, item, n, parent, tree))
        if item['status'] == 'R':  # each side as the file it is: the old path deleted, the new path added
            for label, side in zip(('old', 'new'), sides_of(item)):
                sections.append(file_section(repo, side, '%d-%s' % (n, label), parent, tree, '<a class="back" href="#f%d">&larr; The rename</a>' % n))
    totals = [i['counts'] for i in items if not i['binary']]
    facts = [('Kind', LABELS[kind]), ('Job', job or 'not recorded in the preview record (pass --job)'),
             ('Mission', mission_of(record['branch'])), ('Branch', record['branch']),
             ('Parent commit', parent), ('Candidate tree', tree),
             ('Preview id' if kind == PREVIEW else 'Transaction id', record['id'])]
    facts += extra
    facts.append(('Totals', '%d files, +%d -%d lines' % (len(items), sum(c[0] for c in totals), sum(c[1] for c in totals))))
    return page(kind, facts, items, sections, len(items) > MAX_FILES, job)


def review_folder(repo, branch):
    from governance.tools.continuity import artifact_classification
    root = checkout_root(repo)
    folder = root.parent / artifact_classification.REVIEW / mission_of(branch)  # <container>/<repo>/<mission> beside <container>/<repo>/_review/<mission>
    require(folder.is_dir() and root.name == mission_of(branch), 'The mission review folder %s does not exist; pass --output-dir' % folder)
    return folder


def write(directory, name, text):
    directory = Path(directory)
    require(directory.is_dir(), 'Not a folder: ' + str(directory))
    target = directory / name
    require(not target.is_symlink(), 'Symlink output prohibited')
    temporary = directory / (name + '.tmp')
    temporary.write_bytes(text.encode('utf-8'))
    temporary.replace(target)
    return target.resolve()


def run_preview(args):
    repo = Path(args.repo)
    record = load_preview(repo, args.preview)
    text = build(repo, PREVIEW, record, args.job, [('Manifest', ', '.join(record['manifest']))])
    folder = args.output_dir or Path(args.preview).resolve().parent
    return write(folder, 'diff-preview-%s.html' % record['id'], text)


def run_sealed(args):
    repo = Path(args.repo)
    packet, sha = load_sealed(repo)
    verify_objects(repo, packet['parent'], packet['tree'])
    require(candidate_diff(repo, packet['parent'], packet['tree']) == packet['diff'],
            'The sealed diff does not equal the recorded parent..tree difference; nothing is shown')
    text = build(repo, SEALED, packet, args.job or packet.get('job_handle'), [('Seal (prepared_sha256)', sha)])
    folder = args.output_dir or review_folder(repo, packet['branch'])
    return write(folder, 'diff-sealed-%s.html' % packet['id'], text)


def page_link(path):
    """The lead-tested desktop link: the file URI is both visible text and target."""
    uri = path.as_uri()
    return f'Diff page: [{uri}]({uri})'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Write a self-contained HTML diff of the recorded preview or sealed candidate.')
    parser.add_argument('--repo', default='.', help='the mission checkout (default: the current folder)')
    parser.add_argument('--job', help='the Job handle to show; a sealed review reads it from the packet')
    parser.add_argument('--output-dir', type=Path, help="folder for the page (default: the preview record's folder, or the mission review folder)")
    sub = parser.add_subparsers(dest='kind', required=True)
    sub.add_parser(PREVIEW, help='the candidate of a preview record').add_argument('--preview', type=Path, required=True)
    sub.add_parser(SEALED, help='the sealed candidate presented by the checkpoint tool')
    args = parser.parse_args(argv)
    try:
        path = run_preview(args) if args.kind == PREVIEW else run_sealed(args)
    except ViewerError as error:
        print('FAIL: ' + str(error), file=sys.stderr)
        return 1
    print('%s page: %s' % (LABELS[args.kind], path))
    print(page_link(path))
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))  # the repository root, for the core's modules; the guard's folder is next
    sys.path.insert(0, str(ROOT / 'governance' / 'tools'))
    import runtime_guard  # the core's guard, on the path set up above
    runtime_guard.enter('render', extension_mode=runtime_guard.LOCAL)  # first: version check, GIT_* scrub, lifecycle gate
    sys.exit(main())
