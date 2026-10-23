# Local job coordination

This procedure implements the [G025 local job coordination extension](DECISIONS.md#g025-local-job-coordination-extension-2026-10-08). It governs machine-local handoff material, not checkpoint approval or repository recovery evidence. The [checkpoint workflow](CHECKPOINT-WORKFLOW.md) and [worktree workflow](WORKTREE-WORKFLOW.md) retain authority.

## Roles and authority

A **Job** is defined in the [agent orientation](AGENT-ORIENTATION.md#vocabulary). Existing mission language, branch slugs, lock reasons and transaction identities remain unchanged. Historical references to work items are not renamed.

The [Coordinator](ROLE-COORDINATOR.md) and [worker](ROLE-WORKER.md) role files say what each role does, and the lead developer retains authorization authority. One designated coordinator writes the queue. A local ownership assertion is not an OS lock, proof of identity, or evidence of correct execution.

No message is part of the protocol of the checkpoint and worktree tools: the former REF WINDOW OPEN and CLOSED, DONE announcements are retired. The status is a report, not proof that a process is running or has stopped: when it reads FROZEN or UNKNOWN and the cause is unclear, wait and read it again. What the Coordinator and the workers do with it is in their role files.

## Delegation and worker reassignment

Normal delegation gives one Job to one worker, and the [Coordinator role](ROLE-COORDINATOR.md#delegating-a-job) says what the instructions hold.

Worker reassignment is exceptional, after the outgoing worker stops. Preserve the existing Job, its notes and owned mission worktree/branch/index; do not create a parallel owner or copy checkpoint packets. The outgoing worker records unfinished material, repository/transaction identity, actual evidence, waiting decisions and next step. The Coordinator arranges the successor within authorized scope. The successor inspects those records, the actual checkout and checkpoint state, then records its identity, takeover time and current step in the Job. A reported stop is not proof of process liveness; flag uncertainty and resolve ownership before writing. If the outgoing worker is unavailable, obtain the lead developer's explicit reassignment and preserve uncertain context. The former owner MUST NOT resume writes without another handoff.

This exceptional takeover does not replace planned fresh review or the separately delegated runtime continuity-record step. Existing sealed worker/environment bindings and approval boundaries still apply; a successor cannot silently rebind a packet or inherit authority from local notes.

An explicit lifecycle delegation may authorize completing the exact candidate after the lead developer's sealed approval: commit, verification, required record promotion, guarded finish, exact integration and owned-worktree cleanup. It must state the covered operations and remain conditional on unchanged approval-bound facts and passing required gates. Within that delegation, unchanged promotion needs no additional approval stop. Scope alone, a preview go that the [early conditional approval](CHECKPOINT-WORKFLOW.md#early-conditional-approval) does not carry, a takeover or passing checks supplies no missing commit/integration authority. Changed facts or failed gates stop work for inspection and renewed review/authorization as applicable; no blind retry or bypass follows. Runtime continuity records still require their separate delegation and exact approval.

Coordinator release, takeover and recovery are in the [Coordinator role](ROLE-COORDINATOR.md#coordinator-release-takeover-and-recovery).

## Storage and discovery

The primary checkout owns `.local/coordination/coordinator.json` and shared job files. A new project has neither: the first Coordinator creates the Coordinator record with [`claim-coordinator`](#updates-handoff-and-disposal). A worker may maintain `.local/coordination/<handle>.json` in its owned worktree, or maintain its designated shared job file directly; never keep two writable copies of a job. Pending job notes are moved to the primary checkout before removing their worktree. The Coordinator checks both the shared folder and registered worktrees when recovering a handoff; an absent shared file does not establish that a job was never started.

`/.local/` is ignored in every checkout. Its contents are not committed, copied to new worktrees, sent to remotes or available in fresh clones. They are an explicit exception to disposable ignored output. Tracked rules and tooling describe the convention; the local files hold current unfinished material. Missing local notes mean missing context, never inferred consent. [Repository-only recovery](COMMAND-CONTINUITY.md) remains available without these files.

Discover locations without hard-coded machine paths:

```sh
python -B governance/tools/local_jobs.py --repo . paths
python -B governance/tools/local_jobs.py --repo . --primary list
python -B governance/tools/local_jobs.py --repo . list
```

The helper matches the common Git directory to the primary registered checkout's private Git directory. It does not assume the current branch, checkout name or filesystem path. Bare repositories and unavailable primary checkouts are unsupported. Local and primary locations are reported separately. Lists include each record's SHA-256 for guarded updates. Read-only commands create no folders.

## Execution status

`worktree.py execution-status` is the one source for whether a ref window is open, for workers, the Coordinator and [Mission Control](extensions/mission_control/ui/README.md#local-queue-and-refresh) alike. It only reads, always exits 0 and prints one human line followed by JSON (`--json-only` prints the JSON alone). `--wait-clear [--timeout SECONDS]` polls it inside the tool and exits 0 once the level is CLEAR or OVERLAP, or exits 3 after the timeout with the line that names the holder; UNKNOWN is not clear. Workers never write a wait loop: this option waits for the status, while the [runtime guard](CHECKPOINT-TOOL.md#runtime-guard) refuses a command that writes shared state at once, without waiting, while another owner's lifecycle operation holds its lock. [The tool notes](CHECKPOINT-TOOL.md#claims-and-execution-status) give the payload, the database and the hooks.

```sh
python -B governance/tools/worktree.py --main '<primary-checkout>' execution-status
```

- **Levels.** CLEAR: no window is open. FROZEN: a window is open, named by owner, state and since time. UNKNOWN: the evidence could not be read; treat it as FROZEN until it reads again. OVERLAP: no window is open, but two live missions are on the same ground, named by owner and by what they share, `same file(s): ...` (a textual conflict is probable) or `same subclass, different files: ...` (it usually carries forward). An overlap only warns: claims predict, and whether an approval carries forward after the other mission lands is decided by the diff-bound proof, never by a claim.
- **Windows.** A window is open from the start of a prepare through its seal (a `validated` packet counts until it is sealed or abandoned) and during a worktree lifecycle operation: create, reconcile, integrate or remove. A sealed review waiting for the lead developer is not a window, because the commit does not compare the ref inventory: chips may start while it waits, and only the short integration after the approval needs quiet, which the tool declares by itself.
- **Evidence.** The level comes from what cannot lie: packets that have not finished, operation locks, the lifecycle lock and the worktree list. The tools also declare each window in a small local database with its owner, Job handle, state, since time and the process id and start time of the running command. The status cross-checks the two and flags a disagreement instead of hiding it: a declared window with no live evidence, live evidence with no declared window, a declared command that is no longer running, and an age flag. An age flag never clears anything, and nothing is cleared on the strength of a message.
- **Claims.** At its preview the checkpoint tool claims the manifest's paths and subclasses for the mission, under its Job handle, so nobody declares anything by hand. The claims live while the mission's worktree and branch exist, are released when its integration lands, when it abandons and when its worktree is removed, and show on the page as one row per active mission in a strip at the top of the Queue tab. When another mission lands first, the waiting worker's read says `base moved: main advanced to <sha> by <owner>`, which is informational. `execution-status --owner <mission>` reads only that mission's overlaps. A claim whose worktree is gone is stale: it is ignored, counted, and reported to the lead developer, who alone runs `clear`.
- **Clear and doctor.** `worktree.py clear` and `worktree.py doctor` are commands the lead developer runs in a terminal: `clear` lists every claim as STALE or LIVE with its evidence and clears nothing without `--apply`, and `doctor` checks the database and says what a repair would do. The tool cannot tell who runs them, so the rule is a convention: workers and the Coordinator never run them, and report a stale claim instead. Nothing is cleared on the strength of a message or of a Job record alone.
- **The lead developer's entry point.** In Git Bash, `governance/scripts/claims.sh execution-status` (or `status`, `watch`, `clear`, `doctor`, with the short forms `-e`, `-w`, `-c` and `-d`) runs these commands from any folder with `--main` set to the primary checkout, passing extra arguments on and adding none; workers keep the command form above.
- **Degraded mode.** The database is advisory and never a gate on starting, finishing or committing a Job. When it is missing, locked, unreadable, corrupt or of another major version, the tools print one `NOTICE: claims database ...` line and carry on, the status falls back to the derived evidence and says so, and the approval core works with no database at all.
- **Guards.** The guards do not refuse another owner's lifecycle operation on the strength of the database. Unrelated activity is tolerated by the protected set and the existing locks refuse real collisions.

Workers read this status themselves, so the Coordinator pastes one block into every brief:

```text
COORDINATION (governance/LOCAL-COORDINATION.md, Execution status)
- Before a ref-sensitive step (creating a worktree, cleaning up, starting an integration) read the status, never ask:
  python -B governance/tools/worktree.py --main '<primary-checkout>' execution-status
  CLEAR: go on. OVERLAP: go on, and read the notes: another mission is on the same ground, and a reconcile may follow. FROZEN or UNKNOWN: another owner is preparing, sealing or integrating; read again later; never retry a failed prepare blindly.
- Before a prepare, let the tool wait; write no loop:
  python -B governance/tools/worktree.py --main '<primary-checkout>' execution-status --wait-clear
  Exit 0 (CLEAR or OVERLAP): start the prepare. Exit 3 (still FROZEN or UNKNOWN after the timeout): the line names the holder; report it and run the command again later, never a failed prepare blindly.
- The [runtime guard](CHECKPOINT-TOOL.md#runtime-guard) refuses a command that writes shared state at once, with no wait, while another owner's worktree lifecycle operation is active; run it again after the operation finishes, never in a loop of your own. Reads such as `execution-status`, `queue` and `list` answer meanwhile.
  The tools write and clear the windows; never announce one.
- Run `worktree.py create`, `integrate` and `remove` from the primary (main) checkout, with `--main <primary-checkout>`: the guard requires the main checkout and the reviewed committed tooling owns the promotion.
- Pass `--job-handle <your handle>` to `checkpoint.py preview` and `prepare` and to `worktree.py integrate`. A stale claim is reported to the lead developer; never run `clear` or `doctor` (not even a dry run).
- The Coordinator's messaging name is "Coordinator". If the name is not found, say so and wait; reply to a message by copying its from= address. Never ask the lead developer to pick or route a session.
- Never run `git config` in any scope (reading with `git config --get` is fine). Write message and manifest files with LF only.
- Run the checkpoint tool and the shell tests from Git Bash.
```

`local_jobs.py brief <handle> [--stagger] [--extra-file <file>]` prints the whole standard brief with this block read from this document and its placeholders filled in, so no copy of the block lives anywhere else; it fails without printing a brief when the block cannot be found, and it only reads: it writes no record, contacts no worker and grants no authority.

## Job state and reported worker activity

Job status describes work, independently of the worker's activity:

| Status | Meaning |
| --- | --- |
| `PENDING` | Queued and not started. |
| `WIP` | Claimed and started; does not prove correct handling or live execution. |
| `AWAITING_RESPONSE` | Waiting for a named answer or decision, described in `waiting`. |
| `BLOCKED` | The worker encountered a stated obstacle while carrying out the Job. Separate from derived dependency readiness. |
| `COMPLETE` | Work and required records are committed and integrated; pending material is empty. |
| `CANCELLED` | Deliberately discontinued; useful pending material has been recorded or transferred. |

Worker `activity` is a report: `UNKNOWN`, `ACTIVE`, `PAUSED` or `STOPPED`. `updated_at` is the record update time with a timezone; it is not an authenticated heartbeat. A crash may leave `ACTIVE` or `WIP` behind. Readers MUST describe activity as last reported, not infer liveness from status, timestamp or lock absence. A stopped worker need not make its job blocked. Fresh owners inspect existing work and approval boundaries before continuing.

Example job input (illustrative, not a live assignment):

```json
{
  "version": 1,
  "handle": "handoff_refresh",
  "status": "PENDING",
  "worker": "Unassigned",
  "activity": "UNKNOWN",
  "updated_at": "2026-10-08T12:00:00-04:00",
  "description": "Bring the Progress record up to date with landed work.",
  "kind": "record",
  "step": "Reconcile current records before proposing the Progress update",
  "waiting": null,
  "pending": [
    {
      "kind": "rationale",
      "text": "Capture supplied reasons before conversational context is lost",
      "source": "Illustrative agent summary; verify against the human discussion"
    }
  ],
  "commits": [],
  "records": []
}
```

`pending` entries have `kind`, `text` and `source`; use kinds such as decision, rationale, finding, handoff or c_log. Source references can name human messages, inspected files/commits, or explicitly labeled inference. `records` names the durable destination paths/anchors and commits, or the recipient job handle when material was transferred. Full immutable commit IDs in `commits` are checked against main during cleanup. The helper validates structure, not truth or record completeness. An integration commit can support an audit/design job whose resulting record landed with other work; do not invent a commit for an unrecorded job.

A Job record may also carry three optional keys, `description`, `kind` and `created_at`, so that reports show authored text instead of wording the Coordinator rewrites each time. `created_at` is the time the Job entered the queue, a timestamp with a timezone like `updated_at`, which changes on every write; it is a recorded assertion that may be corrected with the guarded `put`, and an unknown value is omitted. `description` is one short plain-English sentence on a single line, at most 160 characters. `kind` is one word from the closed list `KINDS` in [the helper](tools/local_jobs.py), which `local_jobs.py kinds` prints with each meaning; that list is the only place the vocabulary is kept. A kind describes the nature of the work to help prioritize it, not its change class, which the [artifact classification table](ARTIFACT-CLASSIFICATION.md) derives per path. All three keys keep `version` 1: a record without them stays valid, so existing records need no migration, and an unknown value is omitted rather than stored as null. The Coordinator sets them when a Job enters the queue; afterwards the worker owns the record and may revise them. A change the Coordinator makes to a worker-owned record uses the same guarded `put` with the record's current digest, and fails instead of overwriting if the worker updated it first. A helper from before these keys rejects a record that carries them, so a checkout that still runs an older helper must update before it reads one. Epics carry neither key.

A Job that waits for the lead developer may also carry the optional key `awaiting_lead`: `for` (`preview` for their go, `approval` for a sealed review; the list `AWAITING` in [the helper](tools/local_jobs.py)) and, when known, `session` with the worker's `vendor` and `id`. [The worker role](ROLE-WORKER.md#your-job) says when the worker sets and removes it; the Coordinator's brief asks for it. The helper validates it like the other optional keys, refuses it on a terminal Job and on the coordinator record, and checks a session id for a vendor in `LINKED_VENDORS` (`claude`, `codex`) against the UUID pattern. Nothing parses the free-text `waiting` field. The [Mission Control](extensions/mission_control/ui/README.md#local-queue-and-refresh) Queue tab marks such a Job with a `waiting for you` chip and, for those two vendors only, an `open session` link built from the validated id (`claude://claude.ai/epitaxy/<id>`, `codex://threads/<id>`); any other vendor shows the id as text and a missing session shows as unknown. The link opens a session and approves nothing.

## Epics, dependencies and priority

The coordinator file requires `version`, `worker`, `updated_at`, `queue` and `context`; optional `priorities`, `epics` and `dependencies` default to empty objects. Older version-1 records remain readable without migration. Queue entries are unique Epic or standalone Job handles. An Epic groups Jobs toward one outcome and has only `jobs`, a nonempty list of Job handles. A Job belongs to at most one Epic and cannot also be a standalone queue entry. Epic handles must be queued, cannot name Job records, and cannot be dependency endpoints; nested Epics are unsupported.

`dependencies` maps Job handles to unique prerequisite Job handles, including across Epics and to unqueued Jobs. Reject cycles and self-dependencies. Dependencies alone define execution order: an Epic's stored member order carries no sequence semantics. Independent branches may be ready in parallel; no automatic dispatch or new execution authority follows. A missing prerequisite remains unsatisfied. Cancellation does not satisfy a prerequisite. A COMPLETE prerequisite is satisfied only when its full commit IDs are ancestors of current main and its pending material is empty; semantic record completeness remains the worker's assertion.

`priorities` maps queued Epic or standalone Job handles to positive integers (lower first) or `null` (unassigned). Missing entries mean unassigned. List position never supplies inferred priority. Sort groups in the order of execution, which the `queue` helper computes once for the report and the Mission Control page: Jobs in flight first (status `WIP`, or waiting for the lead developer through `awaiting_lead`), then by priority, lowest first and unset last, then, within a priority, Jobs with every prerequisite satisfied before Jobs with an unsatisfied prerequisite, and last by handle. An Epic takes the standing of its best member, so its band sits where its first in-flight or ready member would sort. Epic members inherit their group's report priority; display them in topological dependency layers, with handles breaking ties. This is a display order, not an added dependency. The Coordinator owns membership, dependencies and priorities; workers own Job notes. There is no separate Epic status or readiness record. `dependency_ready` describes satisfied prerequisites only: it does not imply the Job has a record, is unblocked, has a worker or is authorized.

Illustrative scheduling fields, not a live assignment:

```json
{
  "queue": ["service_build", "docs_cleanup"],
  "priorities": {"service_build": 1, "docs_cleanup": null},
  "epics": {"service_build": {"jobs": ["schema_job", "read_job", "write_job", "package_job"]}},
  "dependencies": {
    "read_job": ["schema_job"],
    "write_job": ["schema_job"],
    "package_job": ["read_job", "write_job"]
  }
}
```

`R` and `SR` are defined in [Reporting R and SR](ROLE-COORDINATOR.md#reporting-r-and-sr), the canonical copy of the rules for what they show. This section keeps the canonical layout of the queue table they show, because other documents link to it.

Show the queue as one table with the columns Pri, Kind, Handle, Description and State / waiting / dependencies, in that order, so that Description sits immediately to the right of Handle. Pri is the Priority column under a shorter header. Handle is the header for the Job's stable handle: only the column header changed, and the concept stays Job. Kind shows the Job's stored `kind` and Description its stored `description`; a Job without a stored value leaves Kind empty, and the Coordinator words its Description. Markdown cannot set column widths, so short headers and one-word cells keep the narrow columns narrow. There is no Epic column. Each Epic is one bold band row: its Handle cell reads `<handle> (Epic)`, and the row gives the Epic's priority, an empty Kind, a plain-English description of its outcome and a state that summarizes its members' derived states. Its member Jobs follow directly beneath it, each Handle cell prefixed with an arrow marker, in derived dependency order. Members inherit the Epic's priority, so their Pri cell stays empty. A standalone Job is a single row with its own priority, sorted among the Epics. Show unqueued open Jobs separately as unscheduled, in a table with the same columns. Distinguish worker-reported BLOCKED from waiting on dependencies; do not invent a blocked Epic status. Priority never overrides dependencies or authorization.

`queue` is a read-only primary-storage view with deterministic sorting:

```sh
python -B governance/tools/local_jobs.py --repo . queue
```

The output has `queue` (grouped entries) and `unqueued` (open shared Job records outside the schedule). Each Job row carries the stored `description`, `work_kind` and `created_at` (`kind` there already names the row type, Job or Epic), null when the record has none. It reads shared Job records, retaining missing ones with null status and record location rather than omitting handles or inventing state. It derives unsatisfied and missing prerequisite lists without changing Job status. Inspect registered worktrees for worker-local notes before treating shared absence as missing context; the helper does not aggregate those files. Malformed records stop the view; a missing coordinator record is an error. Reports can race meaningful updates: re-read after a conflicting observation and never treat a view as dispatch authority. Report unavailable local context rather than substituting a committed projection for the live queue. Each Job row also carries `awaiting_lead` and `pending_count`, the number of pending entries and never their text. Put material awaiting durable recording under its responsible Job. Shared queue references may precede Job files.

## Updates, handoff and disposal

Use the helper with an explicit JSON input file; no provider adapter is needed:

```sh
python -B governance/tools/local_jobs.py --repo . put --input '<job-json>'
python -B governance/tools/local_jobs.py --repo . put --input '<job-json>' --expected-sha256 '<digest-from-list>'
```

Omit the digest only when creating a new record. Existing records require their exact prior digest; competing updates fail instead of overwriting. Use `--primary` before the command to update shared storage. Coordinator input belongs only in primary storage. Cooperating writers use a short exclusive lock; an interrupted writer lock may be removed by the Coordinator once it has confirmed that its writer stopped, and the Coordinator tells the lead developer afterwards. The lifecycle lock and the checkpoint operation lock are different: they are removed only with the lead developer's word ([H3](WORKER-HAZARDS.md#h3-stale-lock-files)). Do not edit live files around the helper's lock. Unrecognized/duplicate JSON fields, malformed records, unsafe handles and indirect storage stop processing. No tool authenticates the asserted author or human intent.

Rename a live Job with one guarded command. It edits the shared queue, so it belongs to the Coordinator and always uses primary storage:

```sh
python -B governance/tools/local_jobs.py --repo . rename '<old-handle>' '<new-handle>'
```

Under the writer lock it checks everything first, then rewrites the record's `handle`, file name and `updated_at` (`created_at` stays), appends a short handoff note to its `pending`, and replaces the handle in the queue entry, priorities, Epic membership and dependencies, keeping each position. It refuses, changing nothing, when the old record is missing or terminal, when the old handle names an Epic, or when the new handle already has a record or appears anywhere in the queue, an Epic or a dependency. Prose mentions of the old handle in other records and in committed text stay as written, as do worker-local copies and pointers that name the old record. If an interruption leaves both record files, the coordinator still names the old handle: inspect, remove the new file and run the command again.

Claim the Coordinator role with one guarded command. It always uses primary storage and is run by the thread the lead developer named; the [Coordinator role](ROLE-COORDINATOR.md#claiming-the-role) says when and what it records:

```sh
python -B governance/tools/local_jobs.py --repo . claim-coordinator --identity '<identity>'
python -B governance/tools/local_jobs.py --repo . claim-coordinator --identity '<identity>' --take-over --reason '<the lead developer's designation, in a line>'
```

Under the writer lock, with no Coordinator record it creates one with an empty queue, `worker` set to the identity and one attributed `context` entry (no new field exists: the marker is `worker`, `updated_at` and that entry). With a record it changes nothing, prints the holder and fails, whatever the identity; with `--take-over` and a reason it replaces `worker`, sets `updated_at` and appends a `context` entry that names the previous holder as superseded, with the time and the reason. `--take-over` without a record and `--reason` without `--take-over` fail. The identity is one trimmed printable line of at most 120 characters and cannot start with `RELEASED by `. Identity, reason and designation are assertions that nothing authenticates. The Coordinator-only commands do not check the marker: the writer lock and the record digests already stop stale overwrites, and a check needs an identity those commands do not take.

Three commands replace the one-off scripts that appended notes, settled pending entries and closed Jobs. Each reads and write under the helper's lock, so the digest they write over cannot be stale, they write UTF-8 with LF line ends, accept a byte-order mark on read, and work the same from PowerShell and Git Bash through the launcher:

```sh
python -B governance/tools/local_jobs.py --repo . note '<handle>' --kind '<kind>' --source '<text or file>' --text-file '<file>'
python -B governance/tools/local_jobs.py --repo . dispose '<handle>' <n> --route '<handle>' | --recorded-in '<ref>' [--unchecked] | --discard '<reason or file>'
python -B governance/tools/local_jobs.py --repo . complete '<handle>' --commit '<sha>' [--commit '<sha>'] [--route '<n>=<handle>'] [--leftovers '<folder>'] [--owner '<mission-id>'] [--prune]
```

`note` appends one pending entry to a live Job. Its text comes from a UTF-8 file, never the command line; `--source` is the source text, or the path of a file that holds it. `complete` always uses primary storage and runs one fixed landing check first, refusing on the first failure and naming the step, with nothing written: (1) every commit is an ancestor of main; (2) main's working tree is clean; (3) the Job's transaction worktree and its `tx/<mission-id>` branch no longer exist and no folder of the mission is left, unless `--leftovers` names the folder, which is then recorded as a rider in the `context` of the Coordinator record, which owns follow-up that belongs to no Job (with no Coordinator record the close refuses with one message and writes nothing; the guarded removal deletes the mission's review folder, so a folder is left only when a removal or a deletion failed, [G025 review folder lifecycle extension](DECISIONS.md#g025-review-folder-lifecycle-extension-2026-10-18)); (4) the execution status is not FROZEN by this Job's mission; (5) the record's `commits`, when it has any, equal the ones given. The mission is found from its claims, windows and landing event under this Job's handle, from a `tx/` branch that still points at a given commit, or from `--owner`. Only then does it mark the Job COMPLETE with the full commit ids, set the activity STOPPED and empty `pending`, which it refuses to do while any entry has no disposition. The entries that have none can be routed in the same step with `--route <n>=<handle>` (entry numbers count from 1). The routed entries and the rider are written with the close, and an interrupted write puts the earlier files back. With `--prune` it then prunes, as `prune` does, every terminal Job.

`dispose` gives one pending entry its disposition, so that no Job closes on an unchecked word. A pending entry may carry the optional key `disposition`: `kind` is `routed` (with `ref`, the live Job that now holds a copy of the entry, written by `--route`), `recorded` (with `ref` and `check`, written by `--recorded-in`) or `discarded` (with `reason`, written by `--discard`), and `at` is the time. The entry stays in `pending` and keeps its number, so the counts that the queue and Mission Control show do not change, and a record without the key reads as no disposition yet, so nothing needs migrating. `--recorded-in` names where the material now lives and the tool checks what it can, in this order: a commit id that is an ancestor of main (`check` is `commit`, stored as the full id), a handle with a record in primary storage (`job`), a path that exists on main (`path`; a `#anchor` after it is not checked). A commit that exists but is not on main is refused, and so is a reference none of these can verify, unless `--unchecked` says so: it is then stored with `check` set to `unchecked`, and `complete` names such entries in its report. `--discard` needs a non-empty reason, as text or as the path of a UTF-8 file, which keeps non-ASCII text intact in PowerShell. `dispose` refuses an entry that already has a disposition, a terminal Job and an unknown or terminal route target, and writes nothing when it refuses. The blanket `--recorded` flag of `complete` was removed rather than kept for entries without a disposition, because it was one statement for all of them that the tool could not check; the flag now fails with a message that names `dispose`. When `complete` clears `pending`, it keeps each entry's number, kind, a one-line excerpt and its disposition in the optional key `disposed` of the closed record, which `prune` removes with the rest of it. The tool checks a reference when the disposition is made, not again at the close, and proves nothing about whether the material is complete there; the Coordinator still reads every entry. A helper from before these keys rejects a record that carries them, so a checkout that still runs an older helper must update first.

Before worktree removal:

1. Promote lasting material through the normal governed records, or move it to another pending job and record the destination. Never mark a job terminal while `pending` is nonempty.
2. Run `prune` to dispose of terminal job notes. COMPLETE requires full commit IDs; every supplied commit must be an ancestor of current main. CANCELLED requires deliberate human-authorized discontinuation, empty pending material, and no unintegrated supplied commits. The tool checks mechanical ancestry and the worker's empty-pending assertion, not approval, durable semantic coverage or cancellation authority. It never removes refs, branches or checkouts.
3. Run `transfer` from the owned worktree to move remaining job JSON files to the primary checkout. This is a file move, not a packet operation. All destination conflicts are checked before the first move; nothing overwrites or merges an existing job. Interrupted moves can be resumed after inspection; moved files stay in primary and remaining files stay at source. Cross-filesystem rename failure leaves the source file intact; use a reviewed manual transfer with byte verification if required. Unsupported loose files are not silently discarded.
4. Follow the existing guarded worktree-removal procedure. Removal refuses any remaining file or indirect path under `.local`, including malformed notes. Empty folders may remain. Pending notes do not get copied into checkpoint scratch or sealed approval packets.

```sh
python -B governance/tools/local_jobs.py --repo . prune
python -B governance/tools/local_jobs.py --repo . transfer
python -B governance/tools/local_jobs.py --repo . --primary prune
```

Pruning shared COMPLETE Jobs verifies integration, removes their satisfied prerequisite references, their own dependency entries, their Epic membership or standalone queue entry, and obsolete priorities. Empty Epics are removed with their queue entries and priorities. Put any Epic-level recording obligation into its responsible Job's pending material before that Job becomes terminal; an empty Epic has no separate archive. CANCELLED Jobs may be disposed of only when no remaining Job depends on them; otherwise pruning refuses without mutation and the Coordinator must explicitly resolve the dependency without implying satisfaction. Scheduled terminal notes in worker checkouts must be transferred to primary before pruning, so their completion evidence remains available for shared cleanup. Unscheduled worker-local cleanup does not mutate the Coordinator file.

Completed job memory has no permanent archive. If later work still needs its contents, the job's durable recording or transfer is incomplete. devlog material can outlive implementation only under the job responsible for recording it. Coordinator context is reviewed on handoff and cleared when recorded or no longer useful.

## Pointer-only chat signals

Use the existing guarded JSON records and chat messaging tools for two-way signals when the lead developer explicitly authorizes messages between the named chats. Authorization should cover both directions in the dispatch; receiving a signal does not authorize a reply. A signal carries only `primary:<record>@<SHA-256>`, with no repeated report or directive. The pointer is repository-relative: both chats must already share this repository. It names the discovered primary record and the exact byte digest observed after a successful guarded write, not a historical snapshot or an approval token.

```sh
python -B governance/tools/local_jobs.py --repo . pointer worker_status_relay
python -B governance/tools/local_jobs.py --repo . read 'primary:worker_status_relay@<64-lowercase-hex-digest>'
```

`pointer` and `read` always use primary storage, even from a linked checkout. They create no storage, send no messages and change no records. `read` returns the current validated record, current digest/pointer, the signaled digest and `matches_signal`. Record content and its digest come from the same byte read. Invalid pointers, missing/indirect records and malformed JSON fail clearly. A false match means the record changed; inspect current context and acknowledgments rather than treating a delayed signal as the latest order. An exact match proves bytes only, not authorization, authorship, liveness or action completion. Worker-local notes must first be transferred through the existing procedure, with no competing primary copy, to use this primary-only signal convention.

Keep writer ownership unchanged. The Coordinator writes addressed directives in its own `context`; the worker writes progress, responses and acknowledgments in its own Job `pending`. Use the existing `{kind, text, source}` entry shape, not a new schema or another writable copy. In the entry text give each actionable event a unique sender-assigned ID, target Job/chat, human authorization source, requested action and any exact checkout/transaction/evidence pointers. Later entries supersede or cancel earlier IDs explicitly. The receiving writer records the event ID and observed digest in its own record after reading, with a disposition such as received, waiting, completed or rejected. Receipt acknowledges reading only; action completion requires its own evidence. A signal for an acknowledgment needs no further acknowledgment, avoiding an echo loop. Keep unacknowledged actionable entries until resolved or transferred; acknowledgments must identify the events, because unrelated updates change the whole-record digest.

Write details once with `put` and the expected prior digest, generate the pointer, then send it through the authorized chat tool. A guarded-write failure means no new signal. Tool acceptance establishes submission only; it does not establish reading, wakeup or delivery time. Existing Codex `send_message_to_thread` submits a follow-up to an existing chat; it is not a durable queue or a guaranteed interrupt of an active turn. `wait_threads` observes completion or need for attention, not every commentary update; an immediate snapshot is an explicit check and an up-to-date cursor suppresses previously returned final text. `read_thread` can inspect reported state. Neither these tools nor an idle/paused label proves a process stopped. Other hosts need their actual messaging capability checked rather than inheriting a Codex wake claim.

Claude Code sessions on the same machine find peer sessions with `ListAgents` and submit text to a peer's inbox with `SendMessage`; the recipient reads it at its next tool round. The submission-only rule above applies unchanged: a successful send or receipt is not reading, wake-up or authorization, and nothing here is a durable queue, so the guarded records stay authoritative. A session in a different permission mode may hold the message for its user or refuse it, and the sender gets a delivery notice when that happens. `notify_when_idle` is an opt-in, one-shot subscription to a single notice when a same-machine session next goes idle or exits; the notice may reach only the user while that session holds peer messages for approval, and it expires if the session never signals. What has been observed: sending and submission receipt; idle notices for another Claude session on the same machine; and that session's completion report arriving as a cross-session reply message. Nothing beyond those calls was observed. No bridge between Codex and Claude sessions is designed; the lead developer deferred it.

If submission fails or acknowledgment is absent, preserve the JSON and delivery uncertainty in the sender-owned record. At the next explicit check, inspect the responsible current records for unacknowledged events; a missed signal must not hide retained work. Retry the same current pointer only within messaging authorization, with no automatic polling, daemon or recurring automation. Deduplicate actions by event ID plus recorded disposition, not message count or whole-record digest. Duplicate signals merely trigger a reread; changed or superseded directives require fresh inspection and any missing authorization. A missing record means lost context, never consent. Pruning and durable promotion follow the existing disposal rules; signals are not a permanent message archive.

For sequential handback, the outgoing owner first records an explicit STOP/release event in its own record: released scope, checkout/branch/packet identity, outstanding work/evidence, which of its launched commands have returned, any retained services and liveness uncertainty. It then signals the pointer and stops writes to the released scope. The Coordinator reads it and records the authorized recipient and exact scope in its own context before signaling that recipient. The recipient reads and acknowledges there before acting, preserves existing approval boundaries, and returns scope only through the same explicit release and Coordinator handback sequence. A worker remains owner of its Job notes unless separately reassigned; checkout release does not make another writer own those notes. A paused chat, receipt acknowledgment or completion summary never substitutes for explicit release or grants ownership.

In an authorized Codex relay demonstration, the worker wrote its primary Job, sent only its pointer, and the Coordinator recorded reading that exact digest and reported that the signal triggered its turn. The Coordinator wrote its acknowledgment/directive in its own context and sent its pointer back; it arrived while the worker was active and was read after a tool boundary. A second directive requested bounded demonstration handback; the worker recorded explicit STOP/completion and released only that demonstration scope before signaling. No checkout/packet/Job-note ownership changed. Repeated reads of an older pointer returned the same current digest with `matches_signal: false` and retained the handback entry. These observations establish both-direction delivery for those calls, not a delivery deadline, guaranteed interrupt or universal wake behavior. No daemon, recurring automation or service participated.
