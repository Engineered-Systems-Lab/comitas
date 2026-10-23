"""Reviewed link-only retarget: decide whether a document edit only repoints links after a file move.

The policy key `link_retarget` names `documents` and `moves`, both repository-root-relative. A document
edit qualifies only if the new text equals the old text with some inline link targets rewritten to
exactly the file-relative path the mapped move implies, keeping fragments. Anything else, including
a partial retarget, an unmapped target, a changed external link, a changed reference-style or HTML
link, or any other text, is not eligible and the document keeps its normal validation scope.
Eligibility carries no authority: it lowers a document's checks to continuity, which then proves
that every new link resolves. Python 3.13+, standard library only.
"""
import posixpath
import re
from urllib.parse import unquote, urlsplit

# The same inline form the continuity checker validates; other link forms stay in the unchanged text.
LINK = re.compile(r'(?<!!)\[[^\]\n]+\]\(([^)\s]+)\)')
KEYS = {'documents', 'moves'}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def clean(path, what):
    require(isinstance(path, str) and path and '\\' not in path and not path.startswith('/')
            and path == posixpath.normpath(path) and not path.startswith('../') and path != '..'
            and not urlsplit(path).scheme and '#' not in path and '%' not in path,
            'link_retarget %s must be a normalized repository-root-relative path: %r' % (what, path))
    return path


def settings(policy, table=None):
    """The validated (documents, moves) of a policy; both empty when the mechanism is dormant."""
    value = policy.get('link_retarget')
    if value is None:
        return frozenset(), {}
    require(isinstance(value, dict) and set(value) == KEYS, 'link_retarget requires exactly documents and moves')
    documents, moves = value['documents'], value['moves']
    require(isinstance(documents, list) and isinstance(moves, dict), 'link_retarget documents must be a list and moves a map')
    require(documents == sorted(set(documents)), 'link_retarget documents must be unique and sorted')
    for path in documents:
        clean(path, 'document')
        if table is not None:
            require(table.resolve(path) is not None, 'link_retarget document has no classification row: ' + path)
            require(not table.in_boundary(path, 'frozen') and not table.in_boundary(path, 'runtime'),
                    'link_retarget cannot cover a frozen or runtime-boundary document: ' + path)
    for old, new in moves.items():
        clean(old, 'move source')
        clean(new, 'move target')
        require(old != new, 'link_retarget move must change the path: ' + old)
    require(list(moves) == sorted(moves), 'link_retarget moves must be sorted by source')
    return frozenset(documents), dict(moves)


def targets(text):
    """(segments, targets): the text between link targets, and the targets in order."""
    segments, found, last = [], [], 0
    for match in LINK.finditer(text):
        segments.append(text[last:match.start(1)])
        found.append(match[1])
        last = match.end(1)
    segments.append(text[last:])
    return segments, found


def expected(document, old, moves):
    """The only new target a link may take when its old target moves, or None if it does not move."""
    path, sep, fragment = old.partition('#')
    if not path or urlsplit(old).scheme:
        return None
    folder = posixpath.dirname(document)
    resolved = posixpath.normpath(posixpath.join(folder, unquote(path)))
    if resolved not in moves:
        return None
    return posixpath.relpath(moves[resolved], folder or '.') + sep + fragment


def evaluate(document, old_text, new_text, documents, moves):
    """{'eligible': True, 'changes': [...]} or {'eligible': False, 'reason': ...}; never raises on content."""
    def no(reason):
        return {'eligible': False, 'reason': reason}
    if document not in documents or not moves:
        return no('document is not covered by link_retarget')
    if old_text is None or new_text is None:
        return no('document is added or deleted, not edited')
    old_text, new_text = old_text.replace('\r\n', '\n'), new_text.replace('\r\n', '\n')
    old_segments, old_links = targets(old_text)
    new_segments, new_links = targets(new_text)
    if old_segments != new_segments or len(old_links) != len(new_links):
        return no('text other than inline link targets changed')
    changes = []
    for number, (before, after) in enumerate(zip(old_links, new_links), 1):
        if before == after:
            if expected(document, before, moves) is not None:
                return no('link %d still targets a moved path, so the retarget is partial: %s' % (number, before))
            continue
        want = expected(document, before, moves)
        if want is None:
            return no('link %d changes a target that no move covers: %s' % (number, before))
        if after != want:
            return no('link %d is not the expected retarget %s -> %s: %s' % (number, before, want, after))
        changes.append({'old': before, 'new': after})
    if not changes:
        return no('no link target changed')
    return {'eligible': True, 'changes': changes}
