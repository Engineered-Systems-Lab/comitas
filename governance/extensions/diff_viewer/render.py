"""Diff viewer: one self-contained HTML page of the exact preview or sealed candidate, written beside the review.

Run it as `python -B governance/extensions/diff_viewer/render.py preview --preview <preview.json>` or `... sealed`. The page is
built only from the recorded parent commit and candidate tree object, read from Git objects, never from the working tree.
It holds one inline script that only moves the divider, focuses the open file so the mouse wheel scrolls it, marks it in the tree, sorts the list of files and shows the position of a change at once, no external resource and no approval control, and it changes nothing the checkpoint tool records."""
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
MAX_INTRALINE_PRODUCT = 4_000_000  # upper bound on word tokens compared for one pair (tokens of the old line times tokens of the new line)
MAX_CHARACTER_PRODUCT = 1_000_000  # the character score is exact up to this length product; a longer pair is scored from its word matches
MAX_INTRALINE_WORK = 16_000_000  # shared comparison budget per file, reused by both views
MIN_INTRALINE_SIMILARITY = .6
UNANCHORED = ()  # intraline's answer for lines that are similar but share no letter or digit to anchor word highlights; None means not similar
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
MAX_BASES = 6  # earlier previews that can be compared with the candidate on one page; the page holds one diff for each
PAGE_BUDGET = 16_000_000  # characters of comparison views a page may carry beyond the parent's; a further base is not embedded
ATTRIBUTION_BUDGET = 40_000_000  # line comparisons the round stripes may spend on a page; a file past it gets no stripes
HISTORY_KIND = 'preview-history'
HISTORY_PREFIX = 'history-'
FINDINGS_PREFIX = 'review-'  # a reviewed preview is one that the mission's review folder holds review-<preview id>.md for (job_where's FINDINGS_PATTERN)
MAX_ONLY = 20  # files per base that differ from the preview and not from the parent, each given a section of its own
STRIPES = 3  # the last rounds that get a stripe of their own; older ones share the earlier stripe
HEX = '0123456789abcdef'
PICTURES = {code: chr(0x2400 + code) for code in range(32) if code not in (9, 10)}  # control characters shown, not sent raw


# The one inline script, pinned by hash in the content security policy: it moves the divider, focuses the open file so the wheel scrolls it, and opens or closes every folder of the tree, jumps between the changes of a file and says which change of how many the reader is at, marks the open file in the tree and sorts the list of files by lines changed or back by the tree's order.
SCRIPT = "(function(){var r=document.documentElement,d=null;function v(s){var l=s.querySelectorAll('.scroll');for(var i=0;i<l.length;i++)if(l[i].offsetParent)return l[i];return null}function tp(c,t){for(var i=0;i<1&&t.previousElementSibling&&t.previousElementSibling.firstElementChild.tagName==='TD';i++)t=t.previousElementSibling;return t.getBoundingClientRect().top-c.getBoundingClientRect().top+c.scrollTop}function hd(c){var h=c.querySelector('th.pane');return(h?h.offsetHeight:0)+4}function cu(s,c){if(s._t!=null&&Math.abs(c.scrollTop-s._p)<2)return s._t;s._t=null;var l=c.querySelectorAll('tr[data-c]'),n=-1;for(var i=0;i<l.length;i++)if(tp(c,l[i])<=c.scrollTop+hd(c))n=i;return n}function u(s){var c=v(s);if(!c)return;var n=c.querySelectorAll('tr[data-c]').length,k=cu(s,c),b=s.querySelectorAll('[data-nav]'),t=s.querySelector('[data-count]');for(var i=0;i<b.length;i++)b[i].disabled=b[i].getAttribute('data-nav')==='prev'?k<=0:k>=n-1;if(t)t.textContent=k>=0&&(s._t!=null||c.scrollTop>0)?'Change '+(k+1)+' of '+n:n+(n===1?' change':' changes')}function mk(s){var o=document.querySelectorAll('nav a[aria-current]');for(var i=0;i<o.length;i++)o[i].removeAttribute('aria-current');var m=s&&/^f(\\d+)/.exec(s.id),l=m?document.querySelectorAll('nav a'):[],a=null;for(i=0;i<l.length;i++)if(l[i].getAttribute('href')==='#f'+m[1])a=l[i];if(!a)return;a.setAttribute('aria-current','true');for(var p=a.parentNode;p&&p.tagName!=='NAV';p=p.parentNode)if(p.tagName==='DETAILS')p.open=true;a.scrollIntoView({block:'nearest'})}function f(){var s=document.querySelector('section:target');mk(s);if(!s)return;var c=v(s);if(c)c.focus({preventScroll:true});u(s)}addEventListener('hashchange',f);addEventListener('load',f);addEventListener('change',function(e){var s=e.target.closest&&e.target.closest('section');if(s)s._t=null;f()});document.addEventListener('scroll',function(e){var c=e.target;if(c.classList&&c.classList.contains('scroll'))u(c.closest('section'))},true);document.addEventListener('click',function(e){var a=e.target.getAttribute&&e.target.getAttribute('data-nav');if(!a)return;var s=e.target.closest('section'),c=v(s),l=c.querySelectorAll('tr[data-c]'),k=cu(s,c)+(a==='next'?1:-1);if(k<0||k>=l.length)return;c.scrollTop=tp(c,l[k])-hd(c);s._t=k;s._p=c.scrollTop;c.focus({preventScroll:true});u(s)});document.addEventListener('click',function(e){var m=e.target.getAttribute&&e.target.getAttribute('data-fold');if(m){var l=document.querySelectorAll('nav details');for(var i=0;i<l.length;i++)l[i].open=m==='open'}});document.addEventListener('click',function(e){var k=e.target.getAttribute&&e.target.getAttribute('data-sort');if(!k)return;var t=e.target.closest('table'),h=t.querySelector('th.size'),n=t.querySelector('th[aria-sort]'),z=k==='name'?'':h.getAttribute('aria-sort')==='descending'?'ascending':'descending',r=[].slice.call(t.querySelectorAll('tr[data-n]'));r.sort(function(x,y){var a=+x.getAttribute('data-size'),b=+y.getAttribute('data-size');if(z&&(a<0)!==(b<0))return a<0?1:-1;if(z&&a!==b)return z==='descending'?b-a:a-b;return x.getAttribute('data-n')-y.getAttribute('data-n')});r.forEach(function(x){x.parentNode.appendChild(x)});h.setAttribute('aria-sort',z||'none');n.setAttribute('aria-sort',z?'none':'ascending')});var tp_=null;document.addEventListener('mousemove',function(e){var r=e.target.closest&&(e.target.closest('tr[data-tip]')||e.target.closest('[data-hint]'));if(!r){if(tp_)tp_.style.display='none';return}if(!tp_){tp_=document.createElement('div');tp_.className='tip';document.body.appendChild(tp_)}var mb=document.getElementById('mode-b'),h=r.getAttribute('data-hint');tp_.classList.toggle('wrap',!!h);tp_.textContent=h||(mb&&mb.checked&&r.getAttribute('data-rtip'))||r.getAttribute('data-tip');tp_.style.display='block';tp_.style.left=Math.max(4,Math.min(e.clientX+12,innerWidth-tp_.offsetWidth-4))+'px';tp_.style.top=(e.clientY+16)+'px'});document.addEventListener('mouseleave',function(){if(tp_)tp_.style.display='none'});document.addEventListener('pointerdown',function(e){if(e.target.classList&&e.target.classList.contains('grip')){d=e.target.parentNode;e.target.setPointerCapture(e.pointerId);e.preventDefault()}});document.addEventListener('pointermove',function(e){if(!d)return;var b=d.getBoundingClientRect(),m=parseFloat(getComputedStyle(d).fontSize);r.style.setProperty('--split',Math.min(.85,Math.max(.15,(e.clientX-b.left)/b.width)))});document.addEventListener('pointerup',function(){d=null});document.addEventListener('dblclick',function(e){if(e.target.classList&&e.target.classList.contains('grip'))r.style.setProperty('--split',.5)})})();"


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
    """Every changed path between the recorded commit and tree, in the order of the folder tree (`tree_key`). A file moved with at most half of it changed is one
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
    return sorted(found, key=lambda item: tree_key(item['path']))


def tree_key(path):
    """The place of a path in the folder tree: at each level the subfolders (named with their slash) come before the files, each by name.
    The list, the tree and the section numbers all follow it."""
    *folders, name = path.split('/')
    return tuple((0, folder + '/') for folder in folders) + ((1, name),)


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


DISK_NOTE = "Opens the mission checkout's working copy, which can differ from the reviewed candidate; stops working once the mission lands."


class Markup(str):
    """Text that is already HTML."""


def disk_link(label, path):
    """One link to a place in the mission checkout; the note about the working copy is its title."""
    return '<a class="disk" href="%s" title="%s">%s</a>' % (html.escape(path.as_uri(), quote=True), html.escape(DISK_NOTE, quote=True), label)


def inside(root, target):
    """True when the place the path really leads to (links and junctions followed) lies within the checkout."""
    try:
        return Path(target).resolve().is_relative_to(Path(root).resolve())
    except (OSError, RuntimeError):
        return False


def path_links(root, path, file_exists=True):
    """A repository path as links, one per segment, separated as plain text: each folder that exists in the checkout links itself, the last
    segment links the file when it exists and is not a deletion. A segment with nothing on disk, or that leads outside the checkout through a link, stays plain text."""
    names = path.split('/')
    parts = []
    for number, name in enumerate(names, 1):
        last = number == len(names)
        target = None if root is None else Path(root).joinpath(*names[:number])
        if target is not None and (target.exists() and file_exists if last else target.is_dir()) and inside(root, target):
            parts.append(disk_link(show(name), target))
        else:
            parts.append(show(name))
    return '/'.join(parts)


def heading_text(root, item):
    """What move_text shows, with the path of the file as disk links; a rename links its new path and names the old one as text."""
    links = path_links(root, item['path'], item['status'] != 'D')
    if item['status'] != 'R':
        return links
    return '%s \u2192 %s (%d%%)' % (show(item['old_path']), links, item['similarity'])


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


def intraline(old, new, exact=True):
    """A pairing score and changed word ranges; None means the lines are not similar, UNANCHORED that they are but cannot be highlighted by word.

    The score is the character similarity of the two lines when exact; a pair too long for that is scored by the share of its
    characters that its matching words cover, which costs nothing beyond the word matching.
    """
    before_words, after_words = words(old), words(new)
    before_offsets = list(itertools.accumulate(map(len, before_words), initial=0))
    after_offsets = list(itertools.accumulate(map(len, after_words), initial=0))
    matcher = difflib.SequenceMatcher(None, before_words, after_words, autojunk=False)
    blocks = matcher.get_matching_blocks()
    if exact:
        similarity = difflib.SequenceMatcher(None, old.strip(), new.strip(), autojunk=False).ratio()
    else:
        covered = sum(before_offsets[a + size] - before_offsets[a] for a, _, size in blocks)
        similarity = 2 * covered / (len(old) + len(new)) if old or new else 1.
    name = assignment_name(old)
    if name is not None and name == assignment_name(new):
        similarity = max(similarity, .9 + .1 * similarity)
    elif shared_opening(old, new):
        similarity = max(similarity, .65 + .35 * similarity)
    if similarity < MIN_INTRALINE_SIMILARITY:
        return None
    if not any(any(any(char.isalnum() for char in part) for part in before_words[a:a + size]) for a, _, size in blocks):
        return UNANCHORED
    before, after = [], []
    for tag, a0, a1, b0, b1 in matcher.get_opcodes():
        if tag != 'equal':
            if a0 != a1:
                before.append((before_offsets[a0], before_offsets[a1], tag))
            if b0 != b1:
                after.append((after_offsets[b0], after_offsets[b1], tag))
    return similarity, (phrase_spans(old, before), phrase_spans(new, after))


def compare(left, right, sizes, budget):
    """(compared, match, remaining budget) for one pair of lines; sizes are their word counts.

    The cost is what the matchers compare: the word tokens of both lines, plus their characters while the exact character score
    is affordable. A pair beyond a limit is not compared at all (compared is False) and spends nothing.
    """
    exact = len(left) * len(right) <= MAX_CHARACTER_PRODUCT
    tokens = sizes[0] * sizes[1]
    cost = tokens + (len(left) * len(right) if exact else 0)
    if max(len(left), len(right)) > MAX_INTRALINE_LENGTH or tokens > MAX_INTRALINE_PRODUCT or cost > budget:
        return False, None, budget
    return True, intraline(left, right, exact), budget - cost


def replacement_pairs(old, new, a0, a1, b0, b1, budget):
    """Bounded ordered alignment of uniquely preferred line matches, the remaining work budget and the pairs judged.

    The judged pairs map (old index, new index) to their match, or to None when the two lines are not similar.
    A line over the length limit and a pair over the token limit are left out alone, because the limits depend on nothing else.
    A pair left uncompared because the file's budget ran out is an incomplete comparison, which cannot establish a preferred
    match for its two lines, so they get no precise pairing; the other lines of the block are paired as usual.
    Indentation alone, repeated near-ties, huge blocks and expensive lines supply no anchors.
    """
    n, m = a1 - a0, b1 - b0
    if max(n, m) > MAX_PAIR_LINES or n * m > MAX_PAIR_COMPARISONS:
        return [], budget, {}
    sizes_old, sizes_new = [len(words(line)) for line in old[a0:a1]], [len(words(line)) for line in new[b0:b1]]
    candidates, judged = {}, {}
    long_old = {i for i in range(n) if len(old[a0 + i]) > MAX_INTRALINE_LENGTH}
    long_new = {j for j in range(m) if len(new[b0 + j]) > MAX_INTRALINE_LENGTH}
    open_old, open_new = set(long_old), set(long_new)
    for i in range(n):
        for j in range(m):
            if len(old[a0 + i]) > MAX_INTRALINE_LENGTH or len(new[b0 + j]) > MAX_INTRALINE_LENGTH:
                continue  # a line over the length limit is left out of the pairing alone, without disqualifying its neighbors
            if sizes_old[i] * sizes_new[j] > MAX_INTRALINE_PRODUCT:
                continue  # a pair too costly for its size alone is not a candidate; it depends on nothing else, so the lines keep their other pairs
            compared, match, budget = compare(old[a0 + i], new[b0 + j], (sizes_old[i], sizes_new[j]), budget)
            if not compared:
                open_old.add(i)
                open_new.add(j)
                continue
            judged[a0 + i, b0 + j] = match
            if match:
                candidates[i, j] = match
    candidates = {(i, j): match for (i, j), match in candidates.items() if i not in open_old and j not in open_new}
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
    return pairs, budget, judged


def opcode_rows(old, new, opcodes):
    """Aligned old/new numbers and text, row class, then old/new changed ranges.

    Clear pairs retain segment ranges. Unmatched lines are taken in order, old with new: two lines that were compared and are not
    similar show as a removed line and an added line, and any other positional pair keeps the whole-text fallback.
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
        pairs, judged = [], {}
        if tag == 'replace':
            pairs, budget, judged = replacement_pairs(old, new, a0, a1, b0, b1, budget)

        def settle(x, y):
            nonlocal budget
            if x is None or y is None:
                append(x, y)
                return
            if (x, y) not in judged:
                compared, match, budget = compare(old[x], new[y], (len(words(old[x])), len(words(new[y]))), budget)
                if compared:
                    judged[x, y] = match
            if (x, y) in judged and judged[x, y] is None:
                append(x, None)
                append(None, y)
            else:
                append(x, y)

        left, right = a0, b0
        for i, j, spans in pairs:
            for x, y in itertools.zip_longest(range(left, i), range(right, j)):
                settle(x, y)
            append(i, j, spans)
            left, right = i + 1, j + 1
        for i, j in itertools.zip_longest(range(left, a1), range(right, b1)):
            settle(i, j)
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


# --- Review rounds: the history of previews, the base a comparison starts from and the round of each line.


def is_id(value, size):
    return isinstance(value, str) and len(value) == size and all(char in HEX for char in value)


def read_history(repo, folder, record):
    """(entries, problems): the verified preview-history records of the mission in `folder`, in the mission's order, and one line for each file that
    could not be used. A record is trusted only when it carries this checkout's authentication code and names this mission's branch and parent, so an
    edited file cannot make the page show a wrong round. Nothing is written or guessed."""
    entries, problems = [], []
    try:
        key = receipt_key(repo)
        names = sorted(path for path in Path(folder).iterdir() if path.name.startswith(HISTORY_PREFIX) and path.name.endswith('.json'))
    except (OSError, ViewerError) as error:
        return entries, ['the preview history cannot be read: %s' % error]
    for path in names:
        try:
            require(path.is_file() and not path.is_symlink(), 'not a plain file')
            envelope = json.loads(path.read_bytes().decode('utf-8-sig'))
            require(isinstance(envelope, dict) and set(envelope) == {'body', 'mac'} and isinstance(envelope['body'], dict), 'malformed')
            body = envelope['body']
            expected = hmac.new(key, canonical(body), hashlib.sha256).hexdigest()
            require(isinstance(envelope['mac'], str) and hmac.compare_digest(envelope['mac'], expected), 'edited, or made by another checkout')
            require(body.get('kind') == HISTORY_KIND and is_id(body.get('preview_id'), 32) and path.name == '%s%s.json' % (HISTORY_PREFIX, body['preview_id']),
                    'not the history record its name says')
            require(is_id(body.get('tree'), 40) and is_id(body.get('parent'), 40) and isinstance(body.get('seq'), int) and body['seq'] >= 1, 'incomplete')
            require(body.get('branch') == record['branch'], 'belongs to another mission')
            require(body['parent'] == record['parent'], 'made on another parent commit (the base moved), so its tree is not a round of this candidate')
            entries.append(body)
        except (OSError, ValueError) as error:
            problems.append('%s: %s' % (path.name, error))
    return sorted(entries, key=lambda body: body['seq']), problems


def tree_exists(repo, tree):
    try:
        return git(repo, 'cat-file', '-t', tree).decode('utf-8').strip() == 'tree'
    except ViewerError:
        return False


def load_rounds(repo, kind, record):
    """(rounds, reason): the rounds of this candidate in order, the last being the candidate's own, or (None, why there are none).

    A round is the work up to one preview of the mission. A reviewed round is one whose findings file `review-<preview id>.md` is in the review folder."""
    try:
        folder = review_folder(repo, record['branch'])
    except ViewerError:
        return None, 'No preview history: the mission review folder was not found, so only the comparison with the parent is possible.'
    entries, problems = read_history(repo, folder, record)
    why = ''.join(' (%s)' % problem for problem in problems)
    if kind == PREVIEW:
        ids = [entry['preview_id'] for entry in entries]
        if record['id'] not in ids:
            return None, 'This preview is not in the mission\'s preview history, so only the comparison with the parent is possible. Previews made before the history existed have none.' + why
        chosen = entries[:ids.index(record['id']) + 1]
        if chosen[-1]['tree'] != record['tree']:
            return None, 'The history record of this preview names another tree than the preview, so no round is shown.'
        pseudo = False
    else:
        matching = [n for n, entry in enumerate(entries) if entry['tree'] == record['tree']]
        chosen = entries[:matching[-1] + 1] if matching else list(entries)
        pseudo = not matching
    if not chosen:
        return None, 'No preview history is recorded for this mission, so only the comparison with the parent is possible. Previews made before the history existed have none.' + why
    rounds, previous = [], 0
    for entry in chosen:
        for missing in range(previous + 1, entry['seq']):  # a record that was refused or is gone: the round stays, with nothing known of it
            rounds.append({'number': missing, 'id': None, 'tree': None, 'time': None, 'reviewed': False, 'available': False, 'missing': True})
        previous = entry['seq']
        findings = folder / (FINDINGS_PREFIX + entry['preview_id'] + '.md')
        rounds.append({'number': entry['seq'], 'id': entry['preview_id'], 'tree': entry['tree'], 'time': entry['recorded_at'],
                       'reviewed': findings.is_file() and not findings.is_symlink(), 'available': tree_exists(repo, entry['tree'])})
    if pseudo:
        rounds.append({'number': chosen[-1]['seq'] + 1, 'id': None, 'tree': record['tree'], 'time': None, 'reviewed': False, 'available': True})
    for rank, item in enumerate(reversed(rounds)):
        item['rank'] = rank  # the candidate's own round is 0
    return rounds, ('Some history records were refused, so the rounds they stand for are shown as unknown, not guessed: ' + '; '.join(problems) + '.') if problems else None


def round_name(item):
    return 'R%d' % item['number']


def round_text(item):
    """The words of a round for the hover box and the legend: `Round 2, preview <id>, reviewed`."""
    if item.get('missing'):
        return 'Round %d, a preview whose history record was refused or is missing' % item['number']
    who = 'the sealed candidate (no preview of its own)' if item['id'] is None else 'preview ' + item['id'][:8]
    return 'Round %d, %s, %s' % (item['number'], who, 'reviewed' if item['reviewed'] else 'not reviewed')


def line_total(items):
    return sum(sum(item['counts']) for item in items if not item['binary'])


def history_plan(repo, parent, tree, items, rounds):
    """What the comparison and the stripes need beyond the parent's diff: the earlier previews as bases (each with its own file changes against the
    candidate, so the page holds one diff per earlier preview and not one per pair), the default base, the files changed since the last reviewed
    preview and the steps that carry each line's round forward.

    The size of the page is bounded: at most MAX_BASES earlier previews are embedded, newest first, and a further one is left out when the lines it
    would add, estimated at 400 characters each, pass PAGE_BUDGET. A base that is not embedded stays in the list with its reason."""
    paths = {path for item in items for path in (item['path'], item['old_path']) if path}
    earlier = []
    embedded, estimate = 0, 0
    for item in reversed(rounds[:-1]):
        item = dict(item, items=None, state=None, only=[], k=None)
        if item.get('missing'):
            item['state'] = 'its history record was refused or is missing; nothing is guessed'
        elif not item['available']:
            item['state'] = 'its tree is no longer in the repository (Git pruned it); nothing is guessed'
        elif item['tree'] == tree:
            item['state'] = 'it holds the same tree as the candidate'
        elif embedded >= MAX_BASES:
            item['state'] = 'not embedded: the page holds at most %d earlier previews' % MAX_BASES
        else:
            try:
                found = changes(repo, item['tree'], tree)
            except ViewerError as error:
                item['state'] = 'its tree cannot be read (%s)' % error
            else:
                size = 400 * line_total(found)
                if estimate + size > PAGE_BUDGET:
                    item['state'] = 'not embedded: the page would pass its size limit'
                else:
                    item['items'] = {found_item['path']: found_item for found_item in found}
                    item['only'] = sorted(path for path in item['items'] if path not in paths)
                    embedded, estimate = embedded + 1, estimate + size
        earlier.append(item)
    earlier.reverse()
    bases = [item for item in earlier if item['items'] is not None]
    for k, item in enumerate(bases, 1):
        item['k'] = k
    reviewed = [item for item in bases if item['reviewed']]
    default = (reviewed or bases or [{'k': 0}])[-1]['k']
    since, since_gone = None, None  # the latest reviewed preview, embedded or not, even when it equals the candidate: then nothing changed since it
    latest = [item for item in earlier if item['reviewed']]
    if latest:
        item = latest[-1]
        if item['items'] is not None:
            since = item
        elif item['tree'] == tree:
            since = dict(item, items={})
        elif item['available']:
            try:
                since = dict(item, items={found['path']: found for found in changes(repo, item['tree'], tree)})
            except ViewerError:
                since_gone = item
        else:
            since_gone = item
    return {'rounds': rounds, 'earlier': earlier, 'bases': bases, 'default': default, 'since': since, 'since_gone': since_gone, 'steps': round_steps(repo, parent, rounds),
            'info': {item['number']: item for item in rounds}, 'budget': ATTRIBUTION_BUDGET}


def round_steps(repo, parent, rounds):
    """The changes of each round from the tree before it, or None when they cannot be read. A round whose tree is gone is not a step of its own: the
    step after it spans it and its lines are marked as of an unknown round (-1), because nothing is guessed."""
    steps, before, gap = [], parent, False
    try:
        for item in rounds:
            if not item['available']:
                gap = True
                continue
            if item['tree'] == before:
                continue
            found = changes(repo, before, item['tree'])
            steps.append({'round': -1 if gap else item['number'], 'by_new': {i['path']: i for i in found},
                          'by_old': {(i['old_path'] or i['path']): i for i in found}})
            before, gap = item['tree'], False
    except ViewerError:
        return None
    return steps


def chain_of(steps, item):
    """The steps that changed one file, oldest first, found by following its path back from the candidate (forward from the parent for a deleted file)."""
    chain = []
    if item['status'] == 'D':
        path = item['path']
        for step in steps:
            found = step['by_old'].get(path)
            if found is not None:
                chain.append((step['round'], found))
                path = found['path']
                if found['status'] == 'D':
                    break
        return chain
    path = item['path']
    for step in reversed(steps):
        found = step['by_new'].get(path)
        if found is None:
            continue
        chain.append((step['round'], found))
        if found['status'] == 'A':
            break
        path = found['old_path'] or path
    chain.reverse()
    return chain


def attribute(repo, plan, item):
    """The round of each changed line of one file: (marks of the parent's lines, marks of the candidate's lines), each a map from line number to the
    round in which the line was removed or added (-1: a round whose tree is gone), or None when the rounds cannot be told for this file.

    The lines of the first version are the parent's. Each round's changes are compared with the version before it and every line that survives keeps
    its origin, like git blame across trees; a line that was added in one round and removed in a later one is in neither side of the diff and has no mark."""
    steps = plan['steps']
    if steps is None or item['binary'] or '160000' in (item['old_mode'], item['new_mode']):
        return None
    chain = chain_of(steps, item)
    if not chain or any(found['binary'] or '160000' in (found['old_mode'], found['new_mode']) for _, found in chain):
        return None
    first, last = chain[0][1], chain[-1][1]
    if first['old_id'] != item['old_id'] or last['new_id'] != item['new_id']:
        return None  # the steps do not lead from the parent's bytes to the candidate's: no guess
    state = [(0, number) for number in range(len(lines_of(blob(repo, first['old_mode'], first['old_id'])[0] or b'')[0]))]
    removed = {}
    for value, found in chain:
        old, _ = lines_of(blob(repo, found['old_mode'], found['old_id'])[0] or b'')
        new, _ = lines_of(blob(repo, found['new_mode'], found['new_id'])[0] or b'')
        cost = max(1, len(old) * len(new))
        if len(old) != len(state) or cost > plan['budget']:
            return None
        plan['budget'] -= cost
        following = [None] * len(new)
        for tag, a0, a1, b0, b1 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag == 'equal':
                following[b0:b1] = state[a0:a1]
                continue
            for number in range(b0, b1):
                following[number] = (value, None)
            for number in range(a0, a1):
                if state[number][1] is not None:
                    removed[state[number][1]] = value
        state = following
    return ({number + 1: value for number, value in removed.items()},
            {number + 1: origin for number, (origin, _) in enumerate(state) if origin != 0})


def stripe(value, marks, removed):
    """The margin stripe of a changed line: the number of its round in the words `R2`, so it never depends on colour alone, in the style of its
    place among the last rounds (s0 the candidate's own round, s1 and s2 the two before it, se every earlier one) or su for an unknown round."""
    if value is None:
        return ''
    if value == -1:
        return '<span class="rd su" title="%s">R?</span>' % html.escape(UNKNOWN_ROUND, quote=True)
    info = marks['info'][value]
    style = 's%d' % info['rank'] if info['rank'] < STRIPES else 'se'
    return '<span class="rd %s" title="%s">%s</span>' % (style, html.escape(('Removed in ' if removed else '') + round_text(info), quote=True), round_name(info))


UNKNOWN_ROUND = 'Round not known: a preview between two rounds is unavailable (its tree is no longer in the repository, or its history record was refused)'


def cell(number, line, css, ranges=None, mark=''):
    if number is None:
        return '<td class="empty"></td>'
    if ranges is None:
        ranges = [] if css.endswith('same') else [(0, len(line), 'replace' if css.endswith('change') else 'whole')]
    return '<td class="%s">%s<span class="n">%d</span>%s</td>' % (css, mark, number, highlighted(line, ranges))


def nav(changes):
    """The buttons that jump between the changes of a file, and to their left the count the script keeps (the buttons stay put as its text changes width): the total, then where the reader is."""
    return ('<span class="nav"><span class="count" data-count>%d change%s</span>'
            '<button type="button" data-nav="prev" title="Previous change" aria-label="Previous change" disabled>&uarr;</button>'
            '<button type="button" data-nav="next" title="Next change" aria-label="Next change">&darr;</button></span>') % (changes, '' if changes == 1 else 's')


def side_table(rows, parent, tree, left='Parent', marks=None):
    """One side-by-side table of aligned rows (a gap row is a (hidden line count, 'gap') pair), with its movable divider.

    `marks` (the round of each changed line, see `attribute`, with the rounds' facts) puts a stripe in the margin of every changed line and a second
    hover text on its row; both show only in the view of all rounds."""
    lines, before, position = '', 'same', 0  # the first row of every run of changed rows is marked, for the buttons that jump between changes
    total = sum(1 for n, row in enumerate(rows) if len(row) != 2 and row[4] != 'same' and (n == 0 or len(rows[n - 1]) == 2 or rows[n - 1][4] == 'same'))  # the runs, as nav counts them
    for row in rows:
        if len(row) == 2:
            lines += '<tr>%s</tr>' % (2 * ('<th class="gap">... %d unchanged line%s</th>' % (row[0], '' if row[0] == 1 else 's')))
            before = 'same'
        else:
            start = row[4] != 'same' and before == 'same'
            position += start
            attributes = (' data-c' if start else '') + (' data-tip="Change %d of %d"' % (position, total) if row[4] != 'same' else '')  # every row of a change names its place, shown by the script at once
            old_mark = new_mark = None
            if marks and row[4] != 'same':
                old_mark = marks['old'].get(row[0]) if row[0] is not None else None
                new_mark = marks['new'].get(row[2]) if row[2] is not None else None
                tip = new_mark if new_mark is not None else old_mark
                if tip is not None:  # the hover box of the view of all rounds names the round, the first line of it the same as the stripe
                    attributes += ' data-rtip="%s"' % html.escape(UNKNOWN_ROUND if tip == -1 else ('Removed in ' if new_mark is None else '') + round_text(marks['info'][tip]), quote=True)
            lines += '<tr%s>%s%s</tr>' % (attributes, cell(row[0], row[1], 'old ' + row[4], row[5], stripe(old_mark, marks, True) if old_mark is not None else ''),
                                          cell(row[2], row[3], 'new ' + row[4], row[6], stripe(new_mark, marks, False) if new_mark is not None else ''))
            before = row[4]
    panes = '<tr><th class="pane">%s %s</th><th class="pane">Candidate %s</th></tr>' % (show(left), show(parent[:8]), show(tree[:8]))
    return ('<div class="scroll" tabindex="0"><div class="frame"><table class="side"><colgroup><col class="l"><col class="r"></colgroup>%s%s</table>'
            '<div class="grip" title="Drag to move the divider; double-click to centre"></div></div></div>') % (panes, lines)


def sides_of(item):
    """The deletion and the addition a rename is made of, each as an entry of its own."""
    return (dict(item, status='D', path=item['old_path'], new_mode='000000', old_path=None, similarity=None),
            dict(item, status='A', old_mode='000000', old_path=None, similarity=None))


def diff_body(repo, item, parent, tree, left='Parent', marks=None, full=True):
    """(html, checkbox, runs) of the side-by-side views of one file: a notice for binaries and submodules, the hunks and the complete file for text.

    `full` False (an earlier preview's comparison) keeps the page small: its complete-file view is a pointer to the parent's, not a second copy."""
    old_data, old_size = blob(repo, item['old_mode'], item['old_id'])
    new_data, new_size = blob(repo, item['new_mode'], item['new_id'])
    sizes = '%s bytes before, %s bytes after' % ('no' if old_size is None else old_size, 'no' if new_size is None else new_size)
    body, checkbox, runs = '', False, 0
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
        runs = sum(1 for n, row in enumerate(everything) if row[4] != 'same' and (n == 0 or everything[n - 1][4] == 'same'))  # the rows marked data-c, the same in either view
        if unchanged and item['status'] == 'R':
            pass  # a pure move has no change lines; each side is a section of its own
        elif collapsed == everything:  # nothing is left out, so one table serves both states of the checkbox
            body += '<div class="allview">%s</div>' % side_table(everything, parent, tree, left, marks) if everything else ''
        else:
            checkbox = True
            body += '<div class="hunkview">%s</div><div class="fullview">%s</div>' % (
                side_table(collapsed, parent, tree, left, marks) if collapsed else '',
                side_table(everything, parent, tree, left, marks) if full else '<p class="note">The complete file is shown in the comparison with the parent.</p>')
    return body, checkbox, runs


def file_section(repo, item, number, parent, tree, back='<a class="back" href="#">&larr; All files</a>', root=None, plan=None, marks=None, left='Parent'):
    """The section of one file: a notice for binaries and submodules, side-by-side hunks and the complete file for text; a rename
    names its pairing and links its two sides, which are sections of their own.

    With a `plan` (the review rounds of the candidate) the views are those of the comparison with the parent, `cmp-0`, followed by one for every
    earlier preview that was embedded as a base; the page shows one of them, by the radio buttons at its top. `marks` are the rounds of the lines."""
    kind = kind_of(item)
    notes = [mode_text(item)] if mode_text(item) else []
    head = '%s<h3><span class="kind %s">%s</span> %s</h3>' % (back, kind.replace(' ', '-'), kind, heading_text(root, item))
    body = ''.join('<p class="note">%s</p>' % html.escape(note) for note in notes)
    if item['status'] == 'R':
        body += ('<p class="note">Display aid: this page paired the deletion of %s with the addition of %s because %d%% of the file stayed; '
                 'the checkpoint tool records them as a deletion and an addition. See each side as its own file: '
                 '<a href="#f%s-old">the old path as deleted</a>, <a href="#f%s-new">the new path as added</a>.</p>') % (
            show(item['old_path']), show(item['path']), item['similarity'], number, number)
    views, checkbox, runs = diff_body(repo, item, parent, tree, left=left, marks=marks)
    if plan:
        views = '<div class="cmp cmp-0">%s</div>' % views
        for base in plan['bases']:
            found = base['items'].get(item['path'])
            if found is None:
                text, box, count = '<p class="note">No change since this preview: the file is as the candidate has it.</p>', False, 0
            else:
                text, box, count = diff_body(repo, found, base['tree'], tree, left='%s preview' % round_name(base), full=False)
            checkbox, runs = checkbox or box, max(runs, count)
            views += '<div class="cmp cmp-%d">%s</div>' % (base['k'], text)
    body += views
    toggle = ''  # the checkbox of the file, at the right end of the heading row, for a file that has two views
    if checkbox:
        toggle = '<label class="toggle"><input type="checkbox" class="collapse" checked> Collapse unchanged fragments</label>' + nav(runs)
    elif runs > 1:
        toggle = nav(runs)  # several changes in a short file: the buttons and the count, no checkbox
    return '<section id="f%s"><div class="bar">%s%s</div>%s</section>' % (number, head, toggle, body)


STYLE = """:root{--bg:#fff;--fg:#1d2129;--mute:#5b6472;--line:#d5d9e0;--panel:#f4f6f9;--del:#fde8e8;--add:#e3f6e6;--chg:#fff3d6;--changed:#edf0f3;--segment-del:#edb7b7;--segment-add:#b9d9b0;--segment-mod:#bdd3ef;--preview:#8a1f11;--sealed:#1b4f8f;--bannerfg:#fff;--link:#0b5cad;--split:.5;--r0:#7a3fd1;--r1:#0b7f7f;--r2:#b45f06;--re:#6b7380}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14171c;--fg:#e3e6eb;--mute:#9aa3b0;--line:#323844;--panel:#1c2027;--del:#4a1f22;--add:#1b3d26;--chg:#4a3d17;--changed:#252a30;--segment-del:#713b40;--segment-add:#3d5d35;--segment-mod:#304e73;--preview:#b8321f;--sealed:#2f6dbd;--link:#8ab4f8;--r0:#b890f0;--r1:#4fc3c3;--r2:#e0a050;--re:#a0a8b5}}
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
table.files th button{font:inherit;font-weight:700;color:inherit;background:none;border:0;padding:0;cursor:pointer}th.size[aria-sort=descending] button::after{content:" \\2193"}th.size[aria-sort=ascending] button::after{content:" \\2191"}.tip{position:fixed;z-index:9;display:none;pointer-events:none;background:var(--fg);color:var(--bg);font:12px system-ui,sans-serif;padding:2px 8px;border-radius:4px;white-space:nowrap}nav a[aria-current]{background:var(--panel);font-weight:700;outline:1px solid var(--line)}
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
label.toggle{margin-left:auto;cursor:pointer;user-select:none;white-space:nowrap}.nav{display:inline-flex;gap:4px;margin-left:auto}.toggle+.nav{margin-left:0}.nav .count{align-self:center;margin-right:6px;color:var(--mute);font-size:13px;white-space:nowrap}.nav button{font:13px system-ui,sans-serif;color:var(--fg);background:var(--panel);border:1px solid var(--line);border-radius:4px;padding:0 9px;cursor:pointer}.nav button:disabled{opacity:.35;cursor:default}
section:has(.collapse:checked) .fullview,section:has(.collapse:not(:checked)) .hunkview{display:none}
.fine{color:var(--mute);margin:2px 0 0;font-size:11px;line-height:1.25}
body:has(section:target) .facts{display:none}
@media (min-width:901px){html,body{height:100%}body{display:flex;flex-direction:column;overflow:hidden}main{flex:1;min-height:0;display:flex;flex-direction:column}.layout{flex:1;min-height:0;grid-template-rows:minmax(0,1fr);align-items:stretch}nav{position:static;max-height:none;min-height:0}.view{min-height:0;overflow:auto;display:flex;flex-direction:column}section:target{flex:1;min-height:0;display:flex;flex-direction:column}.hunkview,.allview,.fullview{flex:1;min-height:0;display:flex;flex-direction:column}.scroll{flex:1;max-height:none;min-height:0}}
@media (max-width:700px){table.side{font-size:11px}span.n{width:2.4em}}
.rounds{margin:6px 0;font-size:12px;line-height:1.35}.rounds fieldset{border:1px solid var(--line);border-radius:4px;margin:0 0 4px;padding:2px 8px}.rounds legend{font-weight:700;padding:0 4px}.rounds label{margin-right:14px;white-space:nowrap}.rounds label.off{color:var(--mute)}.rounds .why{color:var(--mute)}
.cmp,.ob{display:none}.legend,.rd,.sr-col{display:none}.rounds{display:flex;flex-wrap:wrap;align-items:flex-start;gap:0 8px}.rounds fieldset[data-hint]{cursor:help}.tip.wrap{white-space:normal;max-width:36em}
body:has(#mode-b:checked) .cmp-0{display:flex;flex-direction:column;flex:1;min-height:0}body:has(#mode-b:checked) .bases,body:has(#mode-b:checked) .nobase{display:none}body:has(#mode-b:checked) .legend{display:block}body:has(#mode-b:checked) .rd{display:inline-block}
.rd{width:2.6em;margin:0 0 0 -6px;padding:0 2px;text-align:center;font-size:11px;font-weight:700;color:var(--fg);border-left:5px solid var(--re);user-select:none;box-sizing:border-box}.rd+span.n{margin-left:0}
.rd.s0{border-left:5px solid var(--r0)}.rd.s1{border-left:5px dashed var(--r1)}.rd.s2{border-left:5px dotted var(--r2)}.rd.se{border-left:5px double var(--re)}.rd.su{border-left:5px groove var(--re)}
.legend .rd{display:inline-block;width:auto;min-width:2.6em;margin:0 6px 0 0;padding:0 5px}.legend span.lg{margin-right:14px;white-space:nowrap}.legend p{margin:2px 0}
.sr{color:var(--mute);font-size:11px;margin-left:4px}"""


def tree(items, shown, since=None, plan=None):
    """The changed files as a folder tree of links, like the file list of an IDE's diff window. `since` (the files changed since the last reviewed
    preview, and its round) adds a dot to each of them, with the round in its hover text."""
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
                dot = '<span class="sr" title="Changed since %s, the last reviewed preview">&#9679;</span>' % round_name(since[0]) if since and (item['path'] in since[1] or item['old_path'] in since[1]) else ''
                out += '<li%s>%s</li>' % (' class="f %s"' % change_classes(plan, item) if plan else '', '<a class="%s" href="#f%d"%s>%s%s</a>' % (css, n, title, show(name), dot) if n <= shown else '<span class="%s"%s>%s%s</span>' % (css, title, show(name), dot))
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



def controls(plan):
    """The radio buttons of the page and what the two views say: the view (a comparison with an earlier preview, or every round at once), the base of
    the comparison, and the legend of the stripes. They are plain radio buttons read by the page's style sheet; the page does not need the script for them."""
    rounds = plan['rounds']
    last = rounds[-1]
    bases = ['<label><input type="radio" name="base" id="base-0"%s> Parent</label>' % (' checked' if plan['default'] == 0 else '')]
    for item in plan['earlier']:
        label = '%s &middot; %s &middot; %s' % (round_name(item), show(item['time'] or ('unknown' if item.get('missing') else 'sealed')), 'reviewed' if item['reviewed'] else 'not reviewed')
        if item['items'] is None:
            bases.append('<label class="off"><input type="radio" name="base" disabled> %s</label> <span class="why">(%s)</span>' % (label, show(item['state'])))
        else:
            bases.append('<label><input type="radio" name="base" id="base-%d"%s> %s &middot; preview %s</label>' % (
                item['k'], ' checked' if plan['default'] == item['k'] else '', label, show(item['id'][:8])))
    legend = []
    for item in reversed(rounds):
        style = 's%d' % item['rank'] if item['rank'] < STRIPES else 'se'
        if item['rank'] < STRIPES:
            legend.append('<span class="lg"><span class="rd %s">%s</span>%s%s</span>' % (style, round_name(item), show(round_text(item)),
                                                                                   '' if item['available'] else ' (unavailable)'))
    if len(rounds) > STRIPES:
        legend.append('<span class="lg"><span class="rd se">R%d&ndash;R%d</span>earlier rounds share this stripe; the stripe names the round, the hover box says which</span>' % (rounds[0]['number'], rounds[-STRIPES - 1]['number']))
    if any(not item['available'] for item in rounds):
        legend.append('<span class="lg"><span class="rd su">R?</span>a round that is unavailable (its tree is gone or its history record was refused): its lines cannot be told from the next round\'s</span>')
    return ('<div class="rounds"><fieldset data-hint="%s"><legend>View</legend>'
            '<label><input type="radio" name="mode" id="mode-b" checked> All rounds at once, as margin stripes</label>'
            '<label><input type="radio" name="mode" id="mode-a"> Compare with an earlier preview</label></fieldset>'
            '<fieldset class="bases"><legend>Compare the candidate (%s) with</legend>%s</fieldset>'
            '<fieldset class="legend" data-hint="%s"><legend>Rounds</legend>%s</fieldset></div>') % (
        html.escape(VIEW_NOTE, quote=True), show(round_text(last)), ' '.join(bases), html.escape(LEGEND_NOTE, quote=True), ''.join(legend))


VIEW_NOTE = 'Display only: neither view approves anything or changes what the checkpoint tool records.'
LEGEND_NOTE = ('Every changed line carries the round in which it last changed (lines removed: the round that removed them); '
               'the stripes do not replace the add, delete and change backgrounds. '
               'A line added in one round and removed in a later one is in neither the parent nor the candidate, so this view cannot show it; '
               'the comparison with an earlier preview can.')


def change_classes(plan, item):
    """The comparisons a file belongs to: `ck-0` the parent's, `ck-k` every embedded base k that differs from the candidate in this file. The comparison
    view shows only the rows of the base that is selected."""
    return ' '.join('ck-%d' % k for k in [0] + [base['k'] for base in plan['bases'] if item['path'] in base['items'] or item['old_path'] in base['items']])


def base_rules(plan):
    """The style rules that show one base of the comparison: the one whose radio button is checked, and only in the comparison view. The list of files and
    the tree then show only the files that differ from that base, and the files only it differs in (a later round restored the parent's content)."""
    out = 'body:has(#mode-a:checked):has(#base-0:checked) .cmp-0{display:flex;flex-direction:column;flex:1;min-height:0}\n'
    for base in plan['bases']:
        k = base['k']
        pick = 'body:has(#mode-a:checked):has(#base-%d:checked)' % k
        out += '%s .cmp-%d{display:flex;flex-direction:column;flex:1;min-height:0}\n' % (pick, k)
        out += '%s tr[data-n]:not(.ck-%d),%s li.f:not(.ck-%d),%s nav li:has(>details):not(:has(li.ck-%d)){display:none}\n' % (pick, k, pick, k, pick, k)
        out += '%s .ob-%d{display:table-row}\n' % (pick, k)
    return out


def build_page(kind, facts, items, sections, omitted, job=None, plan=None):
    def fact(name, value, wide=False):
        return '<div class="fact%s"><span class="k">%s</span><span class="v">%s</span></div>' % (
            ' wide' if wide else '', html.escape(name), value if isinstance(value, Markup) else show(str(value)))
    shown = min(len(items), MAX_FILES)  # files that have a section; the sides of a rename are sections too, but not files
    short = [f for f in facts if f[0] != 'Manifest']  # the manifest can be long and spans both columns
    half = (len(short) + 1) // 2
    rows = ''.join(['<div>%s</div>' % ''.join(fact(*f) for f in part) for part in (short[:half], short[half:])] +
                   [fact(name, value, True) for name, value in facts if name == 'Manifest'])
    since = (plan['since'], set(plan['since']['items'])) if plan and plan['since'] else None  # the last reviewed preview and the paths changed since it
    mark = (lambda i: '<td>%s</td>' % ('changed since ' + round_name(since[0]) if since and (i['path'] in since[1] or i['old_path'] in since[1]) else '')) if since else (lambda i: '')
    listing = ''.join('<tr data-n="%d" data-size="%d"%s><td><span class="kind %s">%s</span></td><td>%s</td><td>%s</td><td>%s</td>%s</tr>' % (
        n, -1 if i['binary'] else sum(i['counts']), ' class="%s"' % change_classes(plan, i) if plan else '', kind_of(i).replace(' ', '-'), kind_of(i), '<a href="#f%d">%s</a>' % (n, move_text(i)) if n <= shown else move_text(i),
        show(mode_text(i)), 'binary' if i['binary'] else '+%d -%d' % i['counts'], mark(i)) for n, i in enumerate(items, 1))  # data-n is the place in the tree's order, data-size the lines changed (-1: binary)
    head_extra = '<th>Since review</th>' if since else ''
    for base in plan['bases'] if plan else []:  # files that differ from this preview only, shown in the comparison with it
        for n, path in enumerate(base['only'][:MAX_ONLY], 1):
            found = base['items'][path]
            listing += '<tr class="ob ob-%d"><td><span class="kind modified">%s only</span></td><td><a href="#fo%d-%d">%s</a></td><td>%s</td><td>%s</td>%s</tr>' % (
                base['k'], round_name(base), base['k'], n, move_text(found), show(mode_text(found)), 'binary' if found['binary'] else '+%d -%d' % found['counts'], '<td></td>' if since else '')
    control = controls(plan) if plan else ''
    rules = base_rules(plan) if plan else ''
    notice = ('<p class="note">Files marked changed since %s, the last reviewed preview, differ from it; the dot in the tree marks them too.</p>' % round_name(since[0])) if since else (
        ('<p class="note">The last reviewed preview, %s, cannot be compared (%s), so no file is marked as changed since a review.</p>' % (
            round_name(plan['since_gone']), show(plan['since_gone']['state'] or 'its tree cannot be read'))) if plan and plan['since_gone'] else
        '<p class="note">No earlier preview of this mission was reviewed, so no file is marked as changed since a review.</p>' if plan else '')
    aid = ('<p class="note">Display aid: a file moved with at least %d%% of it unchanged is shown as one renamed entry, paired by this page from the recorded parent and candidate. '
           'The checkpoint tool records a move as a deletion and an addition, and its review and the sealed diff list it that way.</p>' % SIMILARITY) if any(i['status'] == 'R' for i in items) else ''
    more = '<p class="note">The first %d files are shown side by side; the list above has every file.</p>' % MAX_FILES if omitted else ''
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'sha256-@@HASH@@\'">\n'
            '<title>%s</title><style>%s%s</style></head><body>\n<div class="label %s"><strong>%s</strong><span>%s</span></div>\n<main>\n'
            '%s<div class="facts">%s</div>\n<div class="layout"><nav><div class="treebar"><button type="button" data-fold="open">Expand all</button><button type="button" data-fold="close">Collapse all</button></div>%s</nav><div class="view">\n<div class="overview"><h2>%s</h2>\n<table class="files"><tr><th>Change</th><th aria-sort="ascending"><button type="button" data-sort="name" title="Sort in the order of the tree">Path</button></th><th>Mode</th><th class="size" aria-sort="none"><button type="button" data-sort="size" title="Sort by lines changed; binary files last">Lines</button></th>%s</tr>%s</table>\n<p class="note">Choose a file in the list or in the tree to read its changes.</p>%s%s</div>\n'
            '%s%s\n<p class="fine">Built from the recorded parent commit and candidate tree only, never the working tree. '
            'A reading aid: it grants no approval and records nothing; the decision is taken in the conversation.</p>\n'
            '</div></div></main><script>@@SCRIPT@@</script></body></html>\n') % (
        ('Diff %s: %s' % ('preview' if kind == PREVIEW else 'sealed review', show(job))) if job else 'Diff ' + kind, STYLE, rules, kind, headline(kind, job),
        'Read-only view of the exact candidate; not an approval.' if kind == PREVIEW else 'Read-only view of the exact sealed candidate; the decision is taken in the conversation.',
        control, rows, tree(items, shown, since, plan), 'Files' if plan else 'Files (%d)' % len(items), head_extra, listing, aid, notice, ''.join(sections), more)


def build(repo, kind, record, job, extra, rounds=None, reason=None):
    """The page for a recorded candidate: record holds id, branch, parent, tree.

    `rounds` (see `load_rounds`) are the previews of the mission up to this candidate; with more than one the page offers the comparison with an
    earlier preview and the view of every round at once. Without them (`reason` says why) the page is the plain comparison with the parent."""
    parent, tree = record['parent'], record['tree']
    verify_objects(repo, parent, tree)
    items = changes(repo, parent, tree)
    root = checkout_root(repo)
    plan = history_plan(repo, parent, tree, items, rounds) if rounds and len(rounds) > 1 else None
    if rounds and len(rounds) == 1:
        reason = 'This is the first preview of the mission: there is no earlier round to compare with.' + (' ' + reason if reason else '')
    sections = []
    for n, item in enumerate(items[:MAX_FILES], 1):
        marks = None
        if plan:
            found = attribute(repo, plan, item)
            marks = {'old': found[0], 'new': found[1], 'info': plan['info']} if found else None
        sections.append(file_section(repo, item, n, parent, tree, root=root, plan=plan, marks=marks))
        if item['status'] == 'R':  # each side as the file it is: the old path deleted, the new path added
            for label, side in zip(('old', 'new'), sides_of(item)):
                sections.append(file_section(repo, side, '%d-%s' % (n, label), parent, tree, '<a class="back" href="#f%d">&larr; The rename</a>' % n, root))
    for base in plan['bases'] if plan else []:  # what a later round reverted: a file the parent's list does not have, as the comparison with this preview shows it
        for n, path in enumerate(base['only'][:MAX_ONLY], 1):
            back = '<a class="back" href="#">&larr; All files</a> <span class="note">Differs from %s only: the candidate holds the parent\'s content.</span>' % round_name(base)
            found, number, left = base['items'][path], 'o%d-%d' % (base['k'], n), '%s preview' % round_name(base)
            sections.append(file_section(repo, found, number, base['tree'], tree, back, root, left=left))
            if found['status'] == 'R':  # a rename, as in the main diff: each side is a section of its own, which the rename's notice links
                for label, side in zip(('old', 'new'), sides_of(found)):
                    sections.append(file_section(repo, side, '%s-%s' % (number, label), base['tree'], tree, '<a class="back" href="#f%s">&larr; The rename</a>' % number, root, left=left))
    totals = [i['counts'] for i in items if not i['binary']]
    facts = [('Kind', LABELS[kind]), ('Job', job or 'not recorded in the preview record (pass --job)'),
             ('Mission', Markup(disk_link(show(mission_of(record['branch'])), root))), ('Branch', record['branch']),
             ('Parent commit', parent), ('Candidate tree', tree),
             ('Preview id' if kind == PREVIEW else 'Transaction id', record['id'])]
    facts += extra
    if plan:
        facts.append(('Rounds', '%d previews of the mission up to this candidate (%s); earlier previews compared: %d' % (
            len(rounds), ', '.join(round_name(item) for item in rounds), len(plan['bases']))))
    elif reason:
        facts.append(('Rounds', reason))
    if plan and reason:
        facts.append(('Rounds notice', reason))
    facts.append(('Totals', '%d files, +%d -%d lines' % (len(items), sum(c[0] for c in totals), sum(c[1] for c in totals))))
    return page(kind, facts, items, sections, len(items) > MAX_FILES, job, plan)


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
    rounds, reason = load_rounds(repo, PREVIEW, record)
    text = build(repo, PREVIEW, record, args.job, [('Manifest', ', '.join(record['manifest']))], rounds, reason)
    folder = args.output_dir or Path(args.preview).resolve().parent
    return write(folder, 'diff-preview-%s.html' % record['id'], text)


def run_sealed(args):
    repo = Path(args.repo)
    packet, sha = load_sealed(repo)
    verify_objects(repo, packet['parent'], packet['tree'])
    require(candidate_diff(repo, packet['parent'], packet['tree']) == packet['diff'],
            'The sealed diff does not equal the recorded parent..tree difference; nothing is shown')
    rounds, reason = load_rounds(repo, SEALED, packet)
    text = build(repo, SEALED, packet, args.job or packet.get('job_handle'), [('Seal (prepared_sha256)', sha)], rounds, reason)
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
