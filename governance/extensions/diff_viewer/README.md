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
- A folder tree, whose folders open and close and start open, with `Expand all` and `Collapse all` buttons above it, and a list of every changed file: added, modified, deleted, type changed, mode changes (`100644` to `100755` and back), binary files with their sizes and lines added and removed. The list and the tree share one order and the files are numbered in it (so are the sections, `#f1`, `#f2` and so on): at each level the subfolders come first, then the files, each by name. The `Lines` heading of the list is a button that sorts it by the lines changed, added plus removed, the largest first; a second click puts the smallest first, and binary files, which have no line count, come last either way; the `Path` heading puts the list back in the order of the tree, and files of equal size keep that order. The file that is open is marked in the tree (`aria-current`, bold with an outline), the folders on its path open, and the mark goes when the reader returns to the list; the old and the new side of a renamed file mark the renamed entry.
- The heading path of each file section, and the value of the `Mission` row, are links to the mission checkout on disk, as `file://` links built with `path.as_uri()` like the `Diff page:` line: every folder segment links that folder, the last segment links the file, and `Mission` links the worktree folder, the root of every heading path. A renamed file's heading links its new path and shows the old path as text; a deleted file's name, and a segment with nothing on disk in the checkout, stay plain text. The folder tree and the list of files carry no such links. Each link has the same short note as its title, shown on hover: it opens the mission checkout's working copy, which can differ from the reviewed candidate, and stops working once the mission lands. The desktop app or the browser decides what a click on a `file://` link does; the page launches nothing and changes no setting, and the links need no script.
- The page opens on the list of files. A click on a file, in the tree on the left or in the list, shows only that file on the right, with a link back to the list; this is plain CSS (`:target`). The page reads in any browser; a viewer that shows a static snapshot of the file may not follow the links.
- For the selected file, the parent on the left and the candidate on the right, side by side with line numbers, a scroll area of its own, filling the window below the bar (the identity fields give way to an open file; on a narrow window the area is as tall as the window), whose scroll bars are always in reach and whose Parent and Candidate titles stay at the top, a divider between them that can be dragged left or right (a double click centres it), three lines of context and the stretches left out counted as unchanged lines. The checkbox `Collapse unchanged fragments`, at the right end of the row with the back link and the file's heading, named as in IntelliJ and checked by default, switches that view: unchecked, the page shows the complete file, parent on the left and candidate on the right, with the changes still marked. It is plain CSS (a checkbox), is kept for each file on its own and needs no script. To its right two buttons, up and down, jump to the previous and the next change of the file in whichever view is shown, with the change one line below the top; the first is disabled at the first change and the second at the last; to their left a count (so the buttons keep their place as its text changes) gives the changes of the file, `12 changes` (a run of changed lines is one change), and once the reader has jumped or scrolled, where the view is, `Change 3 of 12`; pointing at any row of a change shows the same position at once, as `Change 3 of 12` beside the pointer (a small box of the page's own, not the browser's delayed tooltip), and nothing else; the total is written when the page is built and the position follows the buttons and the scroll bar; both views are read from the same recorded objects, so the complete file is the parent's and the candidate's, never the working tree's. A deleted file shows only its parent side and an added file only its candidate side. A binary file and a submodule pointer show a notice. A file larger than 1,000,000 bytes or 3,000 lines per side is cut in both views, and the page says so. A file that was moved, with at least half of it unchanged, appears as one entry, `renamed <old> → <new> (<similarity>%)`, in the list, the tree (hover shows the old path) and its own section, which shows only its real changes (a pure move shows none and says so) and links the old path as a deletion and the new path as an addition, each a section of its own. The pairing is a display aid computed by the page itself from the same recorded parent commit and candidate tree, with the options on the command line (`--find-renames=50%`, `-l1000`, `diff.renames=true`) and no system or user Git configuration read, so no Git setting changes it; a move is counted once in the totals. The checkpoint tool is not involved: its review, the sealed diff and the packet still list a move as a deletion and an addition, as they record it.

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
pairs are not searched for pairs. The lines that stay unpaired are taken in order, old with new: two
lines that were compared and are not similar show as a removed line and an added line, not as one
edit, and a pair that could not be compared keeps the whole-text fallback. Comparisons cost what the
matcher compares, the word tokens of the two lines (a word, a run of spaces or one punctuation mark each),
so a long paragraph is compared when it holds few enough tokens: the limits are 4,096 characters per line,
a product of 4,000,000 tokens per pair and a comparison budget of 16,000,000 per file. A pair also
charges the product of its lengths while that is at most 1,000,000 characters, the range where the
character similarity is exact; a longer pair is scored by the share of its characters that its matching
words cover. A line over the length limit and a pair over the token limit are left out alone, and the other
lines of the block are paired as usual; only a pair left uncompared because the file's budget ran out
leaves its two lines without precise pairing, since an incomplete comparison cannot prove a preferred match. Empty lines retain their corresponding
row background. No source characters are added, removed or normalized by highlighting.

## What it reads, and what it refuses

- **Only recorded identities.** The page is built from the recorded parent commit and candidate tree object, every byte read from Git objects (`git diff`, `git cat-file`), never from the working tree. Editing a file after the preview changes nothing on the page.
- **Authentic records only.** The preview record, and every history record read for the review rounds, must carry the authentication code of this checkout's receipt key, so an edited record or one from another checkout is refused. The sealed review is read through `checkpoint.py present`, which checks its own seal, and the viewer refuses it unless the packet's diff equals `git diff <parent> <tree>` exactly.
- **Escaped and self-contained.** Every file name and content byte is HTML-escaped and control characters are shown as pictures. The page has no server and no font, image or link to another site. Its one inline script only moves the divider and focuses the open file, so that the mouse wheel scrolls the file as soon as the pointer is over it, opens or closes the folders of the tree for the two buttons, marks the open file in the tree, sorts the list of files for its two heading buttons, shows the position of a change beside the pointer and writes the change count (it never reads or changes a file's content), and the content security policy allows that script by its hash and nothing else, so no byte of a file can run.

The page is a reading aid. It never replaces the preview or the sealed review the worker presents, and the go and the Approve stay in the conversation. It has no approval control.

## Review rounds

When a mission has been previewed more than once, the page can compare the candidate with an earlier preview and show every round at once. Both are display only: they grant no approval and change nothing the checkpoint tool records.

A round is the work up to one preview of the same mission, numbered `R1`, `R2`, ... in the order of the previews; each has an exact candidate tree. A reviewed preview is one for which the mission's review folder holds a findings file `review-<preview id>.md` (the pattern of the [Job lookup](../../LOCAL-COORDINATION.md#finding-the-work-of-a-job)).

**Where the rounds come from.** `checkpoint.py preview` keeps one authenticated record per preview, `history-<preview id>.json`, in the mission's review folder, beside `last-preview.json`, which stays the pointer to the newest (preview id, tree, parent, branch, time and the place of the preview in the mission, `seq`). The viewer reads them with the receipt key of the checkout and refuses a record that was edited, was made by another checkout, names another mission or was made on another parent commit (a carry moved the base, so its tree is not a round of this candidate); a refused file is not trusted, and the round it stands for stays in the list as an unavailable round (its lines changed across it are marked `R?`, never given to a neighbouring round) with a `Rounds notice` that names the file. A preview made before the history existed has no record, so a page for it says `This preview is not in the mission's preview history`, offers only the comparison with the parent and shows no stripes. The Job lookup (`local_jobs.py where`) does not list the history yet.

**The toggle.** Radio buttons at the top of the page choose the view; the page needs no script for them. The page opens on all rounds at once, whose button comes first. Hovering the `View` box says, in the page's own instant hover box, that neither view approves anything or changes what the checkpoint tool records; the legend box of the rounds explains itself the same way. The row also holds, for the comparison, the choice of the base, and for the stripes, the legend of the rounds.

- **Compare with an earlier preview**. A plain diff from the chosen base to the current candidate. The base is the parent or any earlier preview of the mission, listed by round, time and preview id with `reviewed` or `not reviewed`; the default base is the last reviewed preview, else the previous preview, else the parent. The target stays the candidate, so the page embeds one diff per earlier preview and not one per pair. The list of files and the tree then show only the files that differ from the chosen base. Each base shows the files that differ from the candidate, with the usual three lines of context and the checkbox `Collapse unchanged fragments`; unchecked, a base points to the complete file of the comparison with the parent instead of repeating it, and a file that has not changed since that preview says so. A file that differs from the preview but not from the parent, because a later round restored the parent's content, is not in the parent's list of files: it is added to the list of files while that base is chosen, each linking a section of its own that shows what changed since that preview (at most 20 per base). A file the parent's page shows as a rename keeps its two side sections, which are the parent's.
- **All rounds at once, as margin stripes.** The full diff from the parent, with a stripe in the margin of each changed line that names the round in which it last changed (`R1`, `R2`, ...), a legend of the rounds beside the toggle (hovering it gives the explanation: every changed line carries its round, the stripes keep the backgrounds, and a line added and removed again is not shown) and the page's instant hover box (`Round 2, preview <id>, reviewed`; a removed line says `Removed in Round 2, ...`). The stripe carries the number, so it never depends on colour alone, and it does not replace the add, delete and change backgrounds. The last three rounds have a stripe each (solid, dashed, dotted); older rounds share one `earlier` stripe, whose hover box still names the round. A line's round comes from comparing each file preview by preview and carrying the origin of each line forward, like `git blame` across trees; a line removed from the parent is marked with the round that removed it. A line added in one round and removed in a later one is in neither the parent nor the candidate, so this view cannot show it; the explanation on the legend says so and the comparison with an earlier preview can.

The list of files and the tree mark the files changed since the last reviewed preview (a column `Since review` and a dot), or say that no earlier preview was reviewed. The baseline is always the latest reviewed preview, whether or not it is one of the embedded bases and even when its tree equals the candidate's (then no file is marked); when its tree cannot be read the page says so and no older reviewed preview stands in for it.

**Bounds.** At most 6 earlier previews are embedded, newest first, and a further one is left out when the lines it would add, estimated at 400 characters each, pass 16,000,000 characters; a preview that is not embedded stays in the list with its reason. A comparison view keeps the existing limits of one file (1,000,000 bytes and 3,000 lines per side). The round of the lines is computed within a comparison budget of 40,000,000 line pairs per page; a file past it, a binary file, a submodule pointer and a file whose steps do not lead from the parent's bytes to the candidate's bytes get no stripes and are shown as before.

**Missing trees.** A preview's tree is not referenced by any commit, so Git may prune it after its grace period. A round whose tree is gone is listed as unavailable (`its tree is no longer in the repository`) and cannot be a base; the lines changed in the step that spans it are marked `R?`, a round that is not known, and not attributed to a guessed round. A preview whose tree equals the candidate's is listed and is not offered as a base.


## Removal and tests

The manifest in `extension.json` lists what the extension owns, so `python -B governance/extensions/remove_extension.py diff_viewer` removes it as the [extensions README](../README.md#removal-list) describes. Its tests are in `governance/tests/extensions/diff_viewer/`, and the policy command `diff-viewer` runs them.
