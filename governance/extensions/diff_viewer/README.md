# Diff viewer

An optional extension that turns the exact candidate of a preview or a sealed review into one readable HTML page, so the lead developer can read the changes in a browser beside the review without opening an IDE project. It is read-only and decides nothing: it grants no approval, adds no approval control and changes nothing the checkpoint tool records. The core does not know it exists ([G042](../../DECISIONS.md#g042---the-core-never-depends-on-an-extension-2026-10-18-accepted)); delete the folder, or run `remove_extension.py diff_viewer`, and every tool still works.

## Use

Run from the mission checkout, the worktree that owns the candidate. Python 3.13+ and Git are the only dependencies.

```sh
python -B governance/extensions/diff_viewer/render.py --job '<job-handle>' preview --preview '../_review/<mission-id>/preview.json'
python -B governance/extensions/diff_viewer/render.py sealed
```

Each command writes one file into the mission's review folder (`../_review/<mission-id>/`), `diff-preview-<preview id>.html` or `diff-sealed-<transaction id>.html`, and prints its path and a `file://` link. The worker puts that link on one line directly beside the preview or the sealed review it presents, so the lead developer opens it in a browser. The review folder is deleted with the mission, so the page leaves nothing behind. `--output-dir` names another folder, and `--repo` another checkout (the default is the current folder).

A preview record does not hold the Job handle, so `--job` supplies it for the page; the sealed review reads it from the packet.

## What the page shows

- A narrow bar, `PREVIEW of <job> (no approval)` in red or `SEALED REVIEW of <job>` in blue (without the Job handle, `PREVIEW (no approval)` and `SEALED REVIEW`), so the kind reads at a glance; the browser title is `Diff preview: <job>` or `Diff sealed review: <job>`. Below it the Job, the mission and branch, the full parent commit and candidate tree ids, the preview id or the transaction id, for a sealed review its `prepared_sha256`, and the totals, in two columns (the manifest of a preview spans both; one column on a narrow window).
- A folder tree, whose folders open and close and start open, with `Expand all` and `Collapse all` buttons above it, and a list of every changed file: added, modified, deleted, type changed, mode changes (`100644` to `100755` and back), binary files with their sizes and lines added and removed.
- The page opens on the list of files. A click on a file, in the tree on the left or in the list, shows only that file on the right, with a link back to the list; this is plain CSS (`:target`). The page reads in any browser; a viewer that shows a static snapshot of the file may not follow the links.
- For the selected file, the parent on the left and the candidate on the right, side by side with line numbers, a scroll area of its own, filling the window below the bar (the identity fields give way to an open file; on a narrow window the area is as tall as the window), whose scroll bars are always in reach and whose Parent and Candidate titles stay at the top, a divider between them that can be dragged left or right (a double click centres it), three lines of context and the stretches left out counted as unchanged lines. The checkbox `Collapse unchanged fragments`, at the right end of the row with the back link and the file's heading, named as in IntelliJ and checked by default, switches that view: unchecked, the page shows the complete file, parent on the left and candidate on the right, with the changes still marked. It is plain CSS (a checkbox), is kept for each file on its own and needs no script. To its right two buttons, up and down, jump to the previous and the next change of the file in whichever view is shown, with the change one line below the top; the first is disabled at the first change and the second at the last; both views are read from the same recorded objects, so the complete file is the parent's and the candidate's, never the working tree's. A deleted file shows only its parent side and an added file only its candidate side. A binary file and a submodule pointer show a notice. A file larger than 1,000,000 bytes or 3,000 lines per side is cut in both views, and the page says so. A file that was moved, with at least half of it unchanged, appears as one entry, `renamed <old> → <new> (<similarity>%)`, in the list, the tree (hover shows the old path) and its own section, which shows only its real changes (a pure move shows none and says so) and links the old path as a deletion and the new path as an addition, each a section of its own. The pairing is a display aid computed by the page itself from the same recorded parent commit and candidate tree, with the options on the command line (`--find-renames=50%`, `-l1000`, `diff.renames=true`) and no system or user Git configuration read, so no Git setting changes it; a move is counted once in the totals. The checkpoint tool is not involved: its review, the sealed diff and the packet still list a move as a deletion and an addition, as they record it.

## What it reads, and what it refuses

- **Only recorded identities.** The page is built from the recorded parent commit and candidate tree object, every byte read from Git objects (`git diff`, `git cat-file`), never from the working tree. Editing a file after the preview changes nothing on the page.
- **Authentic records only.** The preview record must carry the authentication code of this checkout's receipt key, so an edited record or one from another checkout is refused. The sealed review is read through `checkpoint.py present`, which checks its own seal, and the viewer refuses it unless the packet's diff equals `git diff <parent> <tree>` exactly.
- **Escaped and self-contained.** Every file name and content byte is HTML-escaped and control characters are shown as pictures. The page has no server and no font, image or link to another site. Its one inline script only moves the divider and focuses the open file, so that the mouse wheel scrolls the file as soon as the pointer is over it, and opens or closes the folders of the tree for the two buttons (it never reads or changes a file's content), and the content security policy allows that script by its hash and nothing else, so no byte of a file can run.

The page is a reading aid. It never replaces the preview or the sealed review the worker presents, and the go and the Approve stay in the conversation. There is no review history, no comparison between candidates and no approval control.

## Removal and tests

The manifest in `extension.json` lists what the extension owns, so `python -B governance/extensions/remove_extension.py diff_viewer` removes it as the [extensions README](../README.md#removal-list) describes. Its tests are in `governance/tests/extensions/diff_viewer/`, and the policy command `diff-viewer` runs them.
