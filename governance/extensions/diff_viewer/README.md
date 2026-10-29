# Diff viewer

An optional extension that turns the exact candidate of a preview or a sealed review into one readable HTML page, so the lead developer can read the changes in a browser beside the review without opening an IDE project. It is read-only and decides nothing: it grants no approval, adds no approval control and changes nothing the checkpoint tool records. The core does not know it exists ([G042](../../DECISIONS.md#g042---the-core-never-depends-on-an-extension-2026-10-18-accepted)); delete the folder, or run `remove_extension.py diff_viewer`, and every tool still works.

## Use

Run from the mission checkout, the worktree that owns the candidate. Python 3.13+ and Git are the only dependencies.

```sh
python -B governance/extensions/diff_viewer/render.py --job '<job-handle>' preview --preview '../_review/<mission-id>/preview.json'
python -B governance/extensions/diff_viewer/render.py sealed
```

Each command writes one file into the mission's review folder (`../_review/<mission-id>/`), `diff-preview-<preview id>.html` or `diff-sealed-<transaction id>.html`, and prints its path and one ready-to-copy `Diff page:` Markdown line whose visible text and target both equal `path.as_uri()`. The worker copies the generated `Diff page:` line verbatim into the message that presents the preview or the sealed review, as described under [Where the link goes](#where-the-link-goes), so the lead developer opens it in a browser. The review folder is deleted with the mission, so the page leaves nothing behind. `--output-dir` names another folder, and `--repo` another checkout (the default is the current folder).

## Where the link goes

While the folder `governance/extensions/diff_viewer/` exists, every message in which a worker presents a preview or a sealed review ENDS with exactly one line, placed last, immediately before the go or Approve question, so the link sits next to the answer the lead developer gives:

```text
Diff page: [<file:// URI>](<file:// URI>)
```

or, when rendering failed:

```text
Diff page: not available (<reason>)
```

The line is never omitted and never placed elsewhere in the message. Copy the renderer's complete Markdown line, including the label and target, without a code fence or backticks; a bare path or bare `file://` URI is not a substitute. Replace both placeholders with the same `path.as_uri()` value, without angle brackets around the target. `as_uri()` percent-encodes spaces, parentheses, brackets and other URL-sensitive characters. The lead developer tested this visible-URI link in both the Claude and Codex desktop apps. The desktop app still decides whether a click opens an internal viewer, an external browser or an app menu; the visible URI can also be pasted into a browser. Use its Open in or Show in Explorer/Finder action when needed; this extension does not change app settings or launch a browser. Run the renderer before you write the message, so that the line is known. The line grants nothing: the go and the Approve are asked exactly as before.

A preview record does not hold the Job handle, so `--job` supplies it for the page; the sealed review reads it from the packet.

## What the page shows

- A narrow bar, `PREVIEW of <job> (no approval)` in red or `SEALED REVIEW of <job>` in blue (without the Job handle, `PREVIEW (no approval)` and `SEALED REVIEW`), so the kind reads at a glance; the browser title is `Diff preview: <job>` or `Diff sealed review: <job>`. Below it the Job, the mission and branch, the full parent commit and candidate tree ids, the preview id or the transaction id, for a sealed review its `prepared_sha256`, and the totals, in two columns (the manifest of a preview spans both; one column on a narrow window).
- A folder tree, whose folders open and close and start open, with `Expand all` and `Collapse all` buttons above it, and a list of every changed file: added, modified, deleted, type changed, mode changes (`100644` to `100755` and back), binary files with their sizes and lines added and removed.
- The page opens on the list of files. A click on a file, in the tree on the left or in the list, shows only that file on the right, with a link back to the list; this is plain CSS (`:target`). The page reads in any browser; a viewer that shows a static snapshot of the file may not follow the links.
- For the selected file, the parent on the left and the candidate on the right, side by side with line numbers, a scroll area of its own, filling the window below the bar (the identity fields give way to an open file; on a narrow window the area is as tall as the window), whose scroll bars are always in reach and whose Parent and Candidate titles stay at the top, a divider between them that can be dragged left or right (a double click centres it), three lines of context and the stretches left out counted as unchanged lines. The checkbox `Collapse unchanged fragments`, at the right end of the row with the back link and the file's heading, named as in IntelliJ and checked by default, switches that view: unchecked, the page shows the complete file, parent on the left and candidate on the right, with the changes still marked. It is plain CSS (a checkbox), is kept for each file on its own and needs no script. To its right two buttons, up and down, jump to the previous and the next change of the file in whichever view is shown, with the change one line below the top; the first is disabled at the first change and the second at the last; both views are read from the same recorded objects, so the complete file is the parent's and the candidate's, never the working tree's. A deleted file shows only its parent side and an added file only its candidate side. A binary file and a submodule pointer show a notice. A file larger than 1,000,000 bytes or 3,000 lines per side is cut in both views, and the page says so. A file that was moved, with at least half of it unchanged, appears as one entry, `renamed <old> → <new> (<similarity>%)`, in the list, the tree (hover shows the old path) and its own section, which shows only its real changes (a pure move shows none and says so) and links the old path as a deletion and the new path as an addition, each a section of its own. The pairing is a display aid computed by the page itself from the same recorded parent commit and candidate tree, with the options on the command line (`--find-renames=50%`, `-l1000`, `diff.renames=true`) and no system or user Git configuration read, so no Git setting changes it; a move is counted once in the totals. The checkpoint tool is not involved: its review, the sealed diff and the packet still list a move as a deletion and an addition, as they record it.

## Changes within a line

Whole-line additions fill the candidate cell with green; whole-line removals fill the parent
cell with red, including the area beyond the text. Partially changed rows have a neutral
grey background. Muted red marks removed text on the parent side;
muted green marks added text on the candidate side. Muted blue marks modified text on both
sides of a replacement.
The surrounding text keeps its normal foreground color. Both themes and both file views use
the same ranges. These are display aids, not approval or validation inputs.

Within replacement blocks, clear corresponding lines get whole-word highlights, including
blocks with different line counts. Pairing preserves each side's original line order.
A word is a run of letters and digits of any case, with Unicode combining marks kept with
their letters. Underscores and other punctuation separate words: changing `old_only_call()`
to `new_only_call()` marks only `old` and `new`. Every added or removed character remains highlighted, including punctuation and spaces
adjacent to an added word such as `analysis2`. Standalone punctuation and whitespace edits
are also highlighted. Adjacent edits of the same kind form a continuous phrase across
preserved whitespace and punctuation. Preserved words, mathematical operators and different
change kinds keep separate highlights. Comparison preserves case, so a case change still
counts as an edit.
Pairing requires at least 60% similarity after excluding leading/trailing whitespace, a
shared simple assignment target such as `rows =`, or a shared complete opening word of at
least three letters/digits (with any preceding quotes or comment markers also matching).
These anchors let a short assignment value or a rewritten docstring retain its unchanged
text. Some shared letters or digits are always required.
Each pair must be preferred over alternatives for both its lines by more than 0.05; ambiguous
repeated matches keep whole-text fallback. A bounded alignment selects pairs in order, leaving
unmatched intervals in positional layout. Blocks over 80 lines per side or 1,600 candidate
pairs, unrelated lines and incomplete comparisons keep whole-text fallback. Comparisons are
limited to 4,096 characters per line,
a length product of 1,000,000 per pair and a comparison budget of 8,000,000 per file
(each pair charges twice its length product for character similarity and word matching);
anything beyond those limits uses the same whole-text fallback. Empty lines retain their corresponding
row background. No source characters are added, removed or normalized by highlighting.

## What it reads, and what it refuses

- **Only recorded identities.** The page is built from the recorded parent commit and candidate tree object, every byte read from Git objects (`git diff`, `git cat-file`), never from the working tree. Editing a file after the preview changes nothing on the page.
- **Authentic records only.** The preview record must carry the authentication code of this checkout's receipt key, so an edited record or one from another checkout is refused. The sealed review is read through `checkpoint.py present`, which checks its own seal, and the viewer refuses it unless the packet's diff equals `git diff <parent> <tree>` exactly.
- **Escaped and self-contained.** Every file name and content byte is HTML-escaped and control characters are shown as pictures. The page has no server and no font, image or link to another site. Its one inline script only moves the divider and focuses the open file, so that the mouse wheel scrolls the file as soon as the pointer is over it, and opens or closes the folders of the tree for the two buttons (it never reads or changes a file's content), and the content security policy allows that script by its hash and nothing else, so no byte of a file can run.

The page is a reading aid. It never replaces the preview or the sealed review the worker presents, and the go and the Approve stay in the conversation. There is no review history, no comparison between candidates and no approval control.

## Removal and tests

The manifest in `extension.json` lists what the extension owns, so `python -B governance/extensions/remove_extension.py diff_viewer` removes it as the [extensions README](../README.md#removal-list) describes. Its tests are in `governance/tests/extensions/diff_viewer/`, and the policy command `diff-viewer` runs them.
