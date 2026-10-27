# Mission Control terminal

Read-only v1 terminal under the [v1 design](../MISSION-CONTROL-V1-DESIGN.md). Static HTML/CSS/JavaScript, no framework, build step, third-party assets or packages. The Python helper is a loopback server for the static files, the five datasets and the two local endpoints described under [Local queue and refresh](#local-queue-and-refresh). The browser fetches the five JSON snapshots; it never reads Markdown, Git or transaction scratch.

## Run locally

From this owned worktree's repository root, use a verified Python 3.13+ interpreter:

```powershell
python -B governance/extensions/mission_control/ui/serve.py
```

Open the address it prints, <http://127.0.0.1:8765/ui/> with the shipped setting. The port comes from an explicit `--port`, then the project's setting `project-governance/config/mission-control.json` (`{"version": 1, "port": 8765, "project_name": "Comitas", "tagline": "Governed work for AI agents"}`), then the default 8765. The file and each of its keys are optional; a file that cannot be read or holds another key or invalid value also means the defaults (8765, primary folder name and no tagline), with a diagnostic. Each repository on a host needs its own port: the server binds its port exclusively (on Windows with `SO_EXCLUSIVEADDRUSE`, so no second server can share it), and when the port is taken it prints one line naming the port and how to change it and exits with status 1, without trying another port. `governance/extensions/mission_control/mission-control.sh` passes its arguments on to the server. Stop the server with Ctrl+C. Opening `index.html` directly is unsupported because browsers restrict file-origin JSON loading. The server exposes only five UI assets, five JSON datasets, the read-only `GET /groups.json` (the display groups of the Status briefing) and `GET /queue.json` (the queue and the status light's `execution` field) and the one action `POST /refresh`, with no source-document endpoints and no other write routes.

The project name beneath Mission Control comes from optional `project_name` in the primary checkout's setting. Missing,
empty or whitespace-only means the primary checkout folder's name, also when the server runs from a linked worktree.
The server uses `governance/tools/coordination/local_jobs.py`'s existing `roots` resolver, shared with queue discovery
and the local data helper. An optional `tagline` single-line string in the primary setting follows the name and a middle
dot, for example `Comitas · Governed work for AI agents`. A missing, empty or whitespace-only tagline shows just the name.
Both strings are trimmed and are not read from the README or GitHub. Both values are escaped as UTF-8 text in both index routes and are available without datasets
or JavaScript. If discovery fails, the page says `Project unavailable` and its
hover text gives the discovery error; it never substitutes the mission folder's name. Restart an already running server
after taking this update.

## Review route

The source selection uses the authoritative main revision the datasets are generated at plus declared UI-settlement proposal sources. Rationale entries retain explicitly joint explanations as one record. Decision/rationale navigation uses the generator's explicit links in both directions, with exact record IDs and matching source identities. Ambiguous source-only section targets remain traceable without choosing an arbitrary record. The UI does not infer those associations.

The terminal opens on the Queue; for this review start at Overview, then Status briefing, then select a project document and follow its explicit outgoing links. Visit the same record again to see repeated breadcrumbs. Clicking the current destination is a no-op, preserving filters and forward history. The browser's Back and Forward buttons and the breadcrumb buttons restore the visited trail; a direct detail URL starts with that actual entry alone. Each browser-history entry retains its browse query, filters, sort and result page. Deep links use `#detail/<encoded-full-record-id>/<optional-encoded-source-fragment>`; full Git SHA is identity and abbreviations are display only.

The main bar has two cells with a divider between them. The first holds the pages Queue, Overview and Status briefing; the second holds one button per record section, from the most to the least authoritative: Decisions, Rationale, Documents, Commits and Devlog. Each button has a hover text that says what it holds, and a record button opens the search with its section selected in the type filter, where more sections can be added. The search provides section/type, category, topic, status-keyword, log-class and commit class/subclass/worker buttons plus search over identity, title, summary, full content, keywords and source identity. Search terms all match; no selected values means all, values within one dimension match OR, and active dimensions plus search match AND. Status keywords (FROZEN, PASS, Accepted, Historical, Superseded and other supported terms present in the snapshot) match case-insensitive substrings of the source status, not the complete phrase. Keyword matches may overlap and are text discovery filters, not normalized status assertions; full source wording remains unchanged in details. Status, log-class and commit class/subclass/worker options/counts reflect the current search and other filters, excluding their own dimension so multiple selections remain possible. Hide those controls when no values apply, and clear selections that become unavailable. Commit filters use the projected values as-is, including inferred ones; absent values offer no option. Category and topic controls are hidden while the snapshot has no values for them. Status keywords are always shown, without an expand/collapse control. Section/category/topic counts describe the whole snapshot; result count describes matching entries out of the current section total (or the selected type union), before search and other filters. Results page in groups of 20. Sort by recorded date descending (the default; the displayed timestamp, including commit-derived values, with untimed records last), by decision ID descending when every record in scope is a decision or every record is rationale (rationale uses the newest decision it explains; entries without one sort last), by title A→Z when every record in scope is a document or every record is a commit, or by literal source status when some record in scope has one. Date and ID orders break ties by full record ID descending; title and status break ties by full record ID ascending. No topic/category facts are inferred when metadata is empty.

Status briefing presents status-bearing documents plus Progress/Plan/Architecture and decisions with a source status excluding those explicitly starting with Superseded. Overview owns recent commits/logs; they are not repeated in the briefing. Documents sort alphabetically and decisions by recorded date descending, with full identity tie-breaks. Compact cards retain titles, status, dates and source-selection labels while omitting hashes, paths and previews. Project-document status wraps beside Documents without a disclosure. Other long statuses may expand. The briefing does not assert live status, current governing work, HEAD, approval or active transactions. Coordinates remains the broader agent question; old prototype #coordinates links open Status briefing. This UI never reads committed datasets: none exist, and it serves only the local copy that the Refresh button or the data script generates.

Decision labels include descriptive text beside their IDs. Rationale display labels use explicit decisionIds as a comma-separated list on the left, preserving exact compound labels; unnumbered explanations remain unnumbered. Full source prose stays intact in details.

Details render embedded Markdown conservatively, with headings, simple lists/tables, code, emphasis and quotes. Original Markdown remains available in an expandable panel; plain-text commits display their complete content once. Source HTML and inline Markdown links remain inert text; explicit outgoing links in the traceability panel supply navigation. Internal links resolve only when the target source identity matches a rendered record, including revision/blob or proposal hash. Other source targets display identity and evidence without fetching their files. External HTTP(S) citations open only when clicked. No reverse relationships or graph are created. Log entries remain historical and non-normative.

## Focused checks

With an available Node.js interpreter:

```powershell
python -B governance/tests/extensions/mission_control/check.py
node --check governance/extensions/mission_control/ui/app.mjs
```

The datasets are never committed, so `check.py` generates them at the checkout's HEAD into a temporary folder (about 29 seconds) and runs the Node tests, the syntax check and the server tests against that copy. Set `MC_DATA_DIR` to a folder that already holds the five datasets, such as `.local/mission-control/data`, to skip the generation, and run `node --test governance/extensions/mission_control/ui/test_core.mjs` directly with the same variable.

The tests cover real JSON loading, source identity resolution, filter combinations, empty metadata, log classes, full-SHA identity, repeated navigation, Status briefing aggregation and escaped source content. Browser smoke review should cover all five sections, a source-backed detail transition, browser/breadcrumb history, filters/search and mobile layout. The static server should return 404 for Markdown/Git/scratch paths. Run `git diff --check` for the proposed change.

## Local queue and refresh

The Queue tab shows the job queue from the machine-local coordination records, read when the tab opens (press F5 to read it again), labelled local, non-authoritative and with its read time; the repository wins on any conflict. Columns are Pri, Kind, Handle, Description and State / waiting / dependencies, with a band row for each Epic showing its closure policy (automatic when omitted), and `empty; awaiting explicit closure` for an empty explicit Epic, members in dependency order and the unscheduled Jobs in a second table. A Job waiting for the lead developer shows a `waiting for you` chip with an `open session` link. When `.local/coordination` is absent the tab says so and shows no queue.

The Refresh updates button brings the five datasets to the checkout's HEAD in an ignored folder shared by all worktrees (`.local/mission-control/data/` in the primary checkout), replacing the one current copy, and changes no tracked file. It updates incrementally, rebuilding only what the commits since the last refresh affect, and rebuilds everything automatically when it cannot, for example when no copy exists yet or the generator changed. Both take about 20 s on the development machine, against 39 s for the former button, because it skips the verify pass, which the full command below keeps. The status line names the kind that ran, incremental update, full rebuild with its reason or already current, and its time, and stays visible after the page reloads. The page serves that copy and nothing else: with no whole copy the server answers 404 and the page asks you to press Refresh. The footer says when the copy was generated, from which revision and whether that is the checkout's HEAD. Delete the folder to discard the copy. The same update runs from a terminal, and the full update with verification stays on the command line with no second button:

```powershell
python -B governance/extensions/mission_control/data_launcher.py refresh-local
python -B governance/extensions/mission_control/data_launcher.py refresh-local-full
```

The [design record](../MISSION-CONTROL-V1-DESIGN.md#local-queue-view-and-refresh-2026-10-10) owns the allow-list, the guards and the data location. Run `python -B governance/tests/extensions/mission_control/check.py` for the focused checks, which cover the allow-list, the Origin and Host checks, the refresh outcomes, the queue payload fields and the session-link rules.

## Status light

The masthead shows a status light to the left of the Refresh button: the execution status of ref windows, from `GET /queue.json`, on every tab. It reads CLEAR, `REFS FROZEN: <owner>, <since>` while a prepare, seal or lifecycle operation is open, `OVERLAP: <owner> with <owner>, same file(s): ...` or `same subclass, different files: ...` while two live missions are on the same ground, or STATUS UNKNOWN; hover it for the windows and flags. It refreshes on load and when the tab regains focus. To refresh it on a timer as well, start the server with `--light-poll-seconds`, for example:

```powershell
python -B governance/extensions/mission_control/ui/serve.py --light-poll-seconds 30
```

The default is 0 (no timer) and the minimum is 10 seconds. The [design record](../MISSION-CONTROL-V1-DESIGN.md#local-queue-view-and-refresh-2026-10-10) owns the allow-list.

## Active claims

The top of the Queue tab, under the notice, shows one row per active claim of the execution status: the owner and its Job handle, its level (CLEAR, OVERLAP or FROZEN), its subclasses, since when, and flags for age and for a Job the queue calls COMPLETE or CANCELLED while its transaction still exists, with the overlap it is in. With no active claim it says `No active claims`. The page only reads: the lead developer clears a stale claim in a terminal with `worktree.py clear`, which the light's hover text mentions when one exists. The server also stays quiet when a browser closes a connection before the response is written; any other exception still prints.

## Prototype limits

This is a locally generated snapshot of one revision, not a live HEAD monitor. Category/topic controls currently report no source values. The conservative Markdown renderer is not full CommonMark and inline links are not clickable; use the explicit link panel. Source targets outside the rendered datasets cannot open inside this terminal. Per lead developer prototype direction, trails longer than five visits display the first visit, an ellipsis and the last four; the full actual history is retained for the browser's Back and Forward and visible breadcrumbs still navigate to their original visit. This revises the initial full-trail display during prototype review. Dates from commits, logs and documents retain their source meanings rather than a common claimed event time. The UI does not alter generator semantics or runtime implementation. Its paths have explicit tooling classification and focused checkpoint validation.

The October 4 UI settlement closes prototype iteration and authorizes finalization. Exact G018 approval remains required before commit. See the [owning design settlement](../MISSION-CONTROL-V1-DESIGN.md#ui-review-settlement-2026-10-04).

## Status briefing presentation refinement (2026-10-04)

The [owning design refinement](../MISSION-CONTROL-V1-DESIGN.md#status-briefing-presentation-refinement-2026-10-04) records the lead-developer-approved layout. COMMAND / PROJECT VIEW presents Plan, Progress and Architecture above IMPLEMENTATION STATUS; actual project card titles use the accent color without repeated labels. The approximately 70/30 decision rail has an 18rem minimum width and stacks beneath the primary column below 900px. Its original compact spacing is retained, with the first 15 entries from the unchanged eligible decision selection/sort and View all Decisions opening the search with Decisions selected.

Implementation cards use exact existing JSON source paths for the groups that `GET /groups.json` serves: the group of this extension's own manifest, then the group of the manifest of every other installed extension, then the project's `groups` (see [Declared inputs](../README.md#declared-inputs)). `app.mjs` lists none, so removing an extension removes its group; a declaration the server refuses is shown above the groups as one message and every record falls into Other implementation. Unmapped eligible documents appear in Other implementation; empty groups stay hidden. Every selected document appears once with unchanged recorded status wording; these headings do not assert runtime implementation. Future eligible documents appear after dataset regeneration, but assignment to a named group requires an explicit declaration. No metadata or generator change is implied.

The lead developer separately authorized refreshing the snapshot selection and regenerating affected datasets for this refinement. The declared historical revision and existing record selection remain unchanged; amended proposal-source identities make the new decision/design prose available in record details. Proposal sources remain labeled proposal, and the future enclosing commit is intentionally absent. Normal regeneration preserves unaffected dataset bytes. The temporary preview helper and framing are excluded from the promoted UI.

### Hash display refinement (2026-10-04)

The lead developer also approved 12-character hash display in UI metadata and rendered prose, preserving exact JSON/source bytes and full identities. Commit/revision/parent hashes navigate through existing detail/history behavior only on an exact full SHA match to a loaded commits record; prefixes are never resolution keys. Blob and proposal content hashes are abbreviated without commit links. Missing commits stay unlinked and traceable. Full values remain available in hover/accessibility labels, full source-identity disclosures and original Markdown/text disclosures. Commit-link hover text shows the recorded commit message first line, followed by the full SHA on the next line; accessible labels include both. This is an identity navigation convenience, not a new source relationship, live Git read or source fetch.

Conservative display exceptions retain complete, inert fenced code samples, mixed inline code samples, Markdown citations and external URLs; bare inline hash literals may abbreviate and navigate on the same exact-match rule. Already abbreviated source hashes remain as recorded and unlinked. Original raw content always retains full hashes. Status wording and source headings/fragments retain their recorded identities; no generator or dataset content is rewritten.
