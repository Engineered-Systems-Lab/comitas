"""Diff viewer: one self-contained HTML page of the exact preview or sealed candidate, written beside the review.

Run it as `python -B governance/extensions/diff_viewer/render.py preview --preview <preview.json>` or `... sealed`. The page is
built only from the recorded parent commit and candidate tree object, read from Git objects, never from the working tree.
It holds one inline script that only moves the divider, no external resource and no approval control, and it changes nothing the checkpoint tool records."""
import argparse
import base64
import difflib
import hashlib
import hmac
import html
import itertools
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
PREVIEW, SEALED = 'preview', 'sealed'
LABELS = {PREVIEW: 'PREVIEW (no approval)', SEALED: 'SEALED REVIEW'}
CONTEXT = 3
MAX_LINES = 3000  # per side of one file; more is cut and the page says so
MAX_BYTES = 1_000_000  # per side of one file, read before the lines are counted
MAX_FILES = 300  # files shown side by side; the list always holds every file
MODE_NAMES = {'100644': 'file', '100755': 'executable', '120000': 'symbolic link', '160000': 'submodule', '040000': 'tree'}
PICTURES = {code: chr(0x2400 + code) for code in range(32) if code not in (9, 10)}  # control characters shown, not sent raw


# The one inline script, pinned by hash in the content security policy: it only moves the divider.
SCRIPT = "(function(){var r=document.documentElement,d=null;document.addEventListener('pointerdown',function(e){if(e.target.classList&&e.target.classList.contains('grip')){d=e.target.parentNode;e.target.setPointerCapture(e.pointerId);e.preventDefault()}});document.addEventListener('pointermove',function(e){if(!d)return;var b=d.getBoundingClientRect(),m=parseFloat(getComputedStyle(d).fontSize);r.style.setProperty('--split',Math.min(.85,Math.max(.15,(e.clientX-b.left)/b.width)))});document.addEventListener('pointerup',function(){d=null});document.addEventListener('dblclick',function(e){if(e.target.classList&&e.target.classList.contains('grip'))r.style.setProperty('--split',.5)})})();"


class ViewerError(ValueError):
    """The page cannot be built from the recorded facts; nothing is written."""


def require(condition, message):
    if not condition:
        raise ViewerError(message)


def git(repo, *args):
    try:
        result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=300)
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


def changes(repo, parent, tree):
    """Every changed path between the recorded commit and tree, in path order, renames shown as a deletion and an addition."""
    flags = ('--no-ext-diff', '--no-textconv', '--no-renames', '-z')
    raw = git(repo, 'diff', '--raw', '--no-abbrev', *flags, parent, tree).decode('utf-8', 'replace').split('\0')
    numbers = git(repo, 'diff', '--numstat', *flags, parent, tree).decode('utf-8', 'replace').split('\0')
    counts = {}
    for entry in numbers:
        if entry.count('\t') == 2:
            added, removed, path = entry.split('\t')
            counts[path] = None if added == '-' else (int(added), int(removed))
    found, index = [], 0
    while index + 1 < len(raw) and raw[index].startswith(':'):
        old_mode, new_mode, old_id, new_id, status = raw[index][1:].split(' ')
        path = raw[index + 1]
        index += 2
        found.append({'path': path, 'status': status[0], 'old_mode': old_mode, 'new_mode': new_mode, 'old_id': old_id, 'new_id': new_id,
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
    if item['status'] == 'A':
        return 'added'
    if item['status'] == 'D':
        return 'deleted'
    if item['status'] == 'T':
        return 'type changed'
    return 'modified'


def mode_text(item):
    if item['status'] in 'AD' or item['old_mode'] == item['new_mode']:
        return ''
    return 'mode %s to %s' % (item['old_mode'], item['new_mode'])


def side_rows(old, new):
    """Hunks of aligned rows: (old number, old line, new number, new line, class), grouped with a few lines of context."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    hunks, shown = [], 0
    for group in matcher.get_grouped_opcodes(CONTEXT):
        rows = []
        for tag, a0, a1, b0, b1 in group:
            if tag == 'equal':
                rows += [(a0 + i + 1, old[a0 + i], b0 + i + 1, new[b0 + i], 'same') for i in range(a1 - a0)]
            else:
                for i, j in itertools.zip_longest(range(a0, a1), range(b0, b1)):
                    rows.append((None if i is None else i + 1, None if i is None else old[i],
                                 None if j is None else j + 1, None if j is None else new[j],
                                 'change' if i is not None and j is not None else 'removed' if i is not None else 'added'))
        hunks.append((group[0][1] - shown, rows))  # lines of the old side skipped since the last hunk
        shown = group[-1][2]
    if hunks and len(old) > shown:
        hunks.append((len(old) - shown, []))
    return hunks


def cell(number, line, css):
    if number is None:
        return '<td class="empty"></td>'
    return '<td class="%s"><span class="n">%d</span>%s</td>' % (css, number, show(line))


def file_section(repo, item, number, parent, tree):
    """(html, summary) of one file: a notice for binaries and submodules, side-by-side hunks for text."""
    kind = kind_of(item)
    notes = [mode_text(item)] if mode_text(item) else []
    old_data, old_size = blob(repo, item['old_mode'], item['old_id'])
    new_data, new_size = blob(repo, item['new_mode'], item['new_id'])
    sizes = '%s bytes before, %s bytes after' % ('no' if old_size is None else old_size, 'no' if new_size is None else new_size)
    head = '<p class="back"><a href="#">&larr; All files</a></p><h3><span class="kind %s">%s</span> %s</h3>' % (kind.replace(' ', '-'), kind, show(item['path']))
    body = ''.join('<p class="note">%s</p>' % html.escape(note) for note in notes)
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
        hunks = side_rows(old, new)
        if not hunks and not (cut_old or cut_new):
            body += '<p class="note">No content change.</p>'
        if hunks:
            lines = ''.join(('<tr>%s</tr>' % (2 * ('<th class="gap">%s %d unchanged line%s</th>' % ('...', hidden, '' if hidden == 1 else 's'))) if hidden else '') +
                            ''.join('<tr>%s%s</tr>' % (cell(a, b, 'old ' + css), cell(c, d, 'new ' + css)) for a, b, c, d, css in rows)
                            for hidden, rows in hunks)
            panes = '<tr><th class="pane">Parent %s</th><th class="pane">Candidate %s</th></tr>' % (show(parent[:8]), show(tree[:8]))
            body += ('<div class="scroll"><div class="frame"><table class="side"><colgroup><col class="l"><col class="r"></colgroup>%s%s</table>'
                     '<div class="grip" title="Drag to move the divider; double-click to centre"></div></div></div>') % (panes, lines)
    return '<section id="f%d">%s%s</section>' % (number, head, body)


STYLE = """:root{--bg:#fff;--fg:#1d2129;--mute:#5b6472;--line:#d5d9e0;--panel:#f4f6f9;--del:#fde8e8;--add:#e3f6e6;--chg:#fff3d6;--banner:#8a1f11;--bannerfg:#fff;--link:#0b5cad;--split:.5}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#14171c;--fg:#e3e6eb;--mute:#9aa3b0;--line:#323844;--panel:#1c2027;--del:#4a1f22;--add:#1b3d26;--chg:#4a3d17;--banner:#b8321f;--link:#8ab4f8}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{padding:0 16px 48px}.layout{display:grid;grid-template-columns:300px minmax(0,1fr);gap:20px;align-items:start}
nav{position:sticky;top:0;max-height:100vh;overflow:auto;padding:12px 0;font-size:13px}nav ul{list-style:none;margin:0;padding-left:14px}nav>ul{padding-left:0}nav a{color:inherit;text-decoration:none;display:block;padding:1px 4px;border-radius:3px;word-break:break-all}nav a:hover{background:var(--panel)}nav .dir{color:var(--mute);padding:1px 4px}
nav .a{border-left:3px solid #3a9b4f}nav .d{border-left:3px solid #c2433b}nav .m{border-left:3px solid #c99a2e}
@media (max-width:900px){.layout{display:block}nav{position:static;max-height:none}}
.label{background:var(--banner);color:var(--bannerfg);font-size:clamp(28px,6vw,52px);font-weight:800;letter-spacing:.04em;padding:18px 16px;text-align:center}
.label small{display:block;font-size:15px;font-weight:400;letter-spacing:0;margin-top:6px}
table{border-collapse:collapse}th,td{vertical-align:top}
table.facts{margin:16px 0}table.facts th{text-align:left;padding:2px 14px 2px 0;color:var(--mute);font-weight:600;white-space:nowrap}
table.facts td{padding:2px 0;word-break:break-all}
table.files{width:100%;margin:8px 0 24px}table.files td,table.files th{border-bottom:1px solid var(--line);padding:5px 8px;text-align:left}
.kind{display:inline-block;padding:0 8px;border-radius:10px;font-size:12px;font-weight:700;background:var(--panel);border:1px solid var(--line)}
.kind.added{background:var(--add)}.kind.deleted{background:var(--del)}.kind.modified,.kind.type-changed{background:var(--chg)}
section{display:none;margin:0 0 28px}section:target{display:block}.view:has(section:target) .overview{display:none}.back{margin:0 0 8px}h3{font-size:16px;margin:0 0 8px;word-break:break-all}
.note{margin:6px 0;color:var(--mute)}
a,a:visited{color:var(--link)}nav a,nav a:visited{color:inherit}.frame{position:relative;min-width:70em}.grip{position:absolute;top:0;bottom:0;width:19px;margin-left:-10px;left:calc(100%*var(--split));cursor:col-resize;touch-action:none;background:linear-gradient(to right,transparent 7px,var(--mute) 7px,var(--mute) 12px,transparent 12px);opacity:.7}.grip:hover{opacity:1;background:linear-gradient(to right,rgba(128,128,128,.18) 0,rgba(128,128,128,.18) 100%),linear-gradient(to right,transparent 7px,var(--fg) 7px,var(--fg) 12px,transparent 12px)}table.side col.l{width:calc(var(--split)*100%)}table.side col.r{width:calc((1 - var(--split))*100%)}table.side{width:100%;table-layout:fixed;margin:8px 0;border:1px solid var(--line);font:13px/1.45 ui-monospace,Consolas,monospace}.scroll{overflow-x:auto}
table.side th.pane{background:var(--panel);text-align:left;padding:3px 8px;font-weight:600}table.side th.gap{background:var(--panel);text-align:center;padding:2px 8px;color:var(--mute);font-weight:400;border-top:1px dashed var(--line);border-bottom:1px dashed var(--line)}
table.side td{white-space:pre-wrap;overflow-wrap:anywhere;padding:0 6px}
span.n{display:inline-block;width:3.2em;margin:0 8px 0 -6px;padding-right:6px;text-align:right;color:var(--mute);background:var(--panel);user-select:none}
td.old.removed{background:var(--del)}td.new.added,td.new.change{background:var(--add)}td.old.change{background:var(--del)}td.new.removed{background:var(--panel)}
td.empty{background:var(--panel)}
.fine{color:var(--mute);margin-top:32px;font-size:13px}
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
                out += '<li><div class="dir">%s</div><ul>%s</ul></li>' % (show(name), walk(value))
            else:
                n, item = value
                css = {'added': 'a', 'deleted': 'd'}.get(kind_of(item), 'm')
                out += '<li>%s</li>' % ('<a class="%s" href="#f%d">%s</a>' % (css, n, show(name)) if n <= shown else '<span class="%s">%s</span>' % (css, show(name)))
        return out
    return '<ul>%s</ul>' % walk(root)


def page(*arguments):
    text = build_page(*arguments)
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode('utf-8')).digest()).decode('ascii')
    return text.replace('@@HASH@@', digest).replace('@@SCRIPT@@', SCRIPT)


def build_page(kind, facts, items, sections, omitted):
    rows = ''.join('<tr><th>%s</th><td>%s</td></tr>' % (html.escape(name), show(str(value))) for name, value in facts)
    listing = ''.join('<tr><td><span class="kind %s">%s</span></td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        kind_of(i).replace(' ', '-'), kind_of(i), '<a href="#f%d">%s</a>' % (n, show(i['path'])) if n <= len(sections) else show(i['path']),
        show(mode_text(i)), 'binary' if i['binary'] else '+%d -%d' % i['counts']) for n, i in enumerate(items, 1))
    more = '<p class="note">The first %d files are shown side by side; the list above has every file.</p>' % MAX_FILES if omitted else ''
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; script-src \'sha256-@@HASH@@\'">\n'
            '<title>%s</title><style>%s</style></head><body>\n<div class="label">%s<small>%s</small></div>\n<main>\n'
            '<table class="facts">%s</table>\n<div class="layout"><nav>%s</nav><div class="view">\n<div class="overview"><h2>Files (%d)</h2>\n<table class="files"><tr><th>Change</th><th>Path</th><th>Mode</th><th>Lines</th></tr>%s</table>\n<p class="note">Choose a file in the list or in the tree to read its changes.</p></div>\n'
            '%s%s\n<p class="fine">Built from the recorded parent commit and candidate tree object only, never from the working tree. '
            'This page is a reading aid: it grants no approval and changes nothing the checkpoint tool records; the decision is taken in the conversation.</p>\n'
            '</div></div></main><script>@@SCRIPT@@</script></body></html>\n') % (
        'Diff ' + kind, STYLE, html.escape(LABELS[kind]),
        'Read-only view of the exact candidate; not an approval.' if kind == PREVIEW else 'Read-only view of the exact sealed candidate; the decision is taken in the conversation.',
        rows, tree(items, len(sections)), len(items), listing, ''.join(sections), more)


def build(repo, kind, record, job, extra):
    """The page for a recorded candidate: record holds id, branch, parent, tree."""
    parent, tree = record['parent'], record['tree']
    verify_objects(repo, parent, tree)
    items = changes(repo, parent, tree)
    sections = [file_section(repo, item, n, parent, tree) for n, item in enumerate(items[:MAX_FILES], 1)]
    totals = [i['counts'] for i in items if not i['binary']]
    facts = [('Kind', LABELS[kind]), ('Job', job or 'not recorded in the preview record (pass --job)'),
             ('Mission', mission_of(record['branch'])), ('Branch', record['branch']),
             ('Parent commit', parent), ('Candidate tree', tree),
             ('Preview id' if kind == PREVIEW else 'Transaction id', record['id'])]
    facts += extra
    facts.append(('Totals', '%d files, +%d -%d lines' % (len(items), sum(c[0] for c in totals), sum(c[1] for c in totals))))
    return page(kind, facts, items, sections, len(items) > MAX_FILES)


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
    print('Link: ' + path.as_uri())
    return 0


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))  # the repository root, for the core's modules; the guard's folder is next
    sys.path.insert(0, str(ROOT / 'governance' / 'tools'))
    import runtime_guard  # the core's guard, on the path set up above
    runtime_guard.enter('render', extension_mode=runtime_guard.LOCAL)  # first: version check, GIT_* scrub, lifecycle gate
    sys.exit(main())
