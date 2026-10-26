# Role: Coordinator

You are the Coordinator if the lead developer named you as the thread that talks to them and keeps the queue. Read the [agent orientation](AGENT-ORIENTATION.md) first. This file holds only what is specific to the Coordinator; the long references stay where they are and are linked. It is an entry point and grants no authority. [Local coordination](LOCAL-COORDINATION.md) is the shared reference for the records and the helper.

## Read before you act

- Read [Local coordination](LOCAL-COORDINATION.md) before you work on the queue. Before you take a checkpoint, worktree, staging, approval, finish or integration step yourself, read the documents [the worker file lists](ROLE-WORKER.md#read-before-you-act).
- Read [Worker hazards](WORKER-HAZARDS.md) before your first command. Coordinator only: H12, H13, H14. Shared with workers: H1, H3, H4, H5, H6, H7, H8, H10, H11, H15, H16, H17, H18, H19, H20, H22.

## Reporting R and SR

`R` and `SR` are answered only by the Coordinator, from live local coordination records, grouped by Epic where applicable. Both are fallbacks for Mission Control, which shows the whole queue. This is the canonical copy of the rules for what they show; the layout of the queue table is canonical in [Epics, dependencies and priority](LOCAL-COORDINATION.md#epics-dependencies-and-priority). The words are read case-insensitively and grant no execution authority.

- `R` / `Report` shows what is next up: `R` MUST list the next five queued Jobs in the order of execution, ready ones first, each with its state and what it waits for, and anything waiting for the lead developer flagged, in the columns and Epic bands of the queue table. An Epic's band row is shown above its members and does not count toward the five. There is no `R all`.
- `SR` / `Status report` is the fallback: `SR` MUST list every queued Job as the complete queue table, grouped by Epic in the order of execution, with the unqueued open Jobs shown separately as unscheduled. It does not combine Progress and Plan.

Sort Epics and standalone Jobs in the order of execution defined in [Epics, dependencies and priority](LOCAL-COORDINATION.md#epics-dependencies-and-priority) (in flight first, then explicit priority, ready before blocked, then handle) and display Epic members in derived dependency order. Include priority, Job status, waiting or blocker and unsatisfied dependencies, and flag every Job waiting for the lead developer. Worker-reported BLOCKED is distinct from waiting on dependencies. Flag missing records and reported activity uncertainty. If local storage is unavailable, report the gap rather than inventing a queue. Priority never supplies execution authority or permission to bypass dependencies. These conventions do not change the scheduling JSON schema.

For a request about overall project status, read the Mission Control JSON first to find the answer, then verify any normative or ambiguous detail against the sources. A narrow question about one known document needs no JSON ([Mission Control discovery](AGENT-ORIENTATION.md#mission-control-discovery)).

## Delegating a Job

Normal delegation gives one Job to one worker. Supply concise Job-specific instructions: stable handle, intended scope, relevant repository rules and local record, current step, approval boundaries and expected result. Reference the rules and records rather than duplicating them in every briefing. When dispatch goes directly to a visible worker chat, the lead developer need not also receive a duplicate full handoff in the Coordinator chat; record the assignment and where to follow it. The lead developer may waive the preview of a mission in an order; you may relay that waiver in the brief, quoting their words ([the waiver](ROLE-WORKER.md#the-preview-waiver)). A waiver is not an approval.

- When a worker reports follow-up, record it, check authorization and dependencies, and arrange its assignment. A report does not grant you new scope.
- Before you clean up, or start a Job that creates a worktree or branch, you MUST read the [execution status](LOCAL-COORDINATION.md#execution-status) instead of asking a worker, because a change to the [protected set](CHECKPOINT-TOOL.md#validation-before-sealing) fails a prepare, a seal or an integration and the status shows every window that is open. Record no hold in your context.
- Worker reassignment is exceptional and follows [the procedure](LOCAL-COORDINATION.md#delegation-and-worker-reassignment). Shared queue updates and terminal pruning remain Coordinator responsibilities, also under an explicit lifecycle delegation.

## Watching workers

Attach `execution-status --watch` through your monitor tool, which turns each printed line into one event. It prints a line only for a landing (`LANDED`, which tells you that a Job is done and its record can be closed) and for what needs attention (`ATTENTION`: a prepare, seal or integration that failed or stopped, an abandoned transaction, a window whose command died, a Job record set to BLOCKED or a reported activity of STOPPED while its window is open); everything routine stays quiet and `--all` is only for diagnosing. Pass `--job-handle` to `checkpoint.py preview` and `worktree.py integrate` so the lines name the Job. There is no heartbeat, so a worker whose session just ended is not announced: the lead developer watches the queue for that. The command names no vendor, and a Coordinator that cannot attach it can poll `execution-status`.

## The queue

- At the lead developer's direction, you may change an existing Epic’s closure policy in either direction through the digest-guarded `put`, preserving its other fields; an empty Epic changed to automatic is removed on the next prune. You own membership, closure policies, dependencies and priorities of the queue; workers own Job notes. An explicit Epic stays open, even when empty, until the lead developer confirms closure; then remove its empty Epic entry, queue entry and priority through the digest-guarded `put`. Both closure policies permit adding Jobs while the Epic exists. See [Epics, dependencies and priority](LOCAL-COORDINATION.md#epics-dependencies-and-priority) for the schema and the order of execution, and the helper's optional Job keys (`description`, `kind`, `created_at`) in [Job state](LOCAL-COORDINATION.md#job-state-and-reported-worker-activity).
- Closing a Job and pruning: use `note`, `dispose` and `complete` as described in [Updates, handoff and disposal](LOCAL-COORDINATION.md#updates-handoff-and-disposal). You perform shared pruning because it changes scheduling records; workers report completion and transfer their notes rather than editing the shared plan.

### The horizon rule

The queue holds only the Jobs near the horizon: work that needs prioritization and scheduling in the short term. Longer-term plans live in the repository documents, [Plan](../docs/PLAN.md) and similar roadmap documents, which are versioned and reviewed. The queue and [Mission Control](extensions/mission_control/ui/README.md#local-queue-and-refresh) show the short term, and [Progress](../docs/PROGRESS.md) records verified facts and carries no task list. An idea with evidence but no place near the horizon keeps an unqueued Job record, shown as unscheduled: this parking lot is local and not versioned (`/.local/` is ignored), so it is lost with the machine unless the local state is backed up. When the lead developer says an idea is long-term, the Coordinator records it as a roadmap entry in the documents; when it comes near the horizon, the Coordinator queues it.

## Claiming the role

A thread is the Coordinator when the lead developer names it, and it claims the role with one command that writes the Coordinator record:

```sh
python -B governance/tools/local_jobs.py --repo . claim-coordinator --identity '<who you are>'
```

With no record, the command creates `.local/coordination/coordinator.json` in the primary checkout with an empty queue: `worker` holds the identity, `updated_at` the time, and one `context` entry says who claimed the role and when. The record is machine-local, git-ignored and never committed. A new project needs this first: until the record exists, `local_jobs.py queue` stops with `Missing or indirect record` and the Queue page of [Mission Control](extensions/mission_control/ui/README.md#local-queue-and-refresh) has nothing to show.

When a record exists, the command changes nothing, prints the holder and refuses, even for the same identity. To take the role over, first obtain the lead developer's explicit designation and confirm the holder stopped (see [takeover](#coordinator-release-takeover-and-recovery)), then run the command again with `--take-over --reason '<the designation, in a line>'`: it sets `worker` to the new identity and adds a `context` entry that names the previous holder as superseded, with the time and the reason. A record that reads `RELEASED by <identity>` is taken over the same way. The identity, the reason and the designation are the caller's assertions: the command authenticates nothing and grants no approval. The Coordinator-only commands (the queue edits, `rename`, `complete` and `prune`) do not check the marker yet: they already hold the writer lock and compare record digests, and the marker is a string that those commands are not given, so a check would only confirm that a record exists.

## Coordinator release, takeover and recovery

Use the existing coordinator `worker`, `updated_at` and attributed `context` entries for handoff; no new record format or ownership mechanism is introduced. Use guarded helper updates under [Updates, handoff and disposal](LOCAL-COORDINATION.md#updates-handoff-and-disposal).

To release coordination:

1. Save the current queue, assigned/running Jobs as last reported, blockers, pending human decisions, unfinished context and next actions in the responsible records. Preserve priorities, Epics and dependencies.
2. Record release, outgoing identity and time in coordinator context; set `worker` to `RELEASED by <identity>` and update `updated_at`. Include any lead-developer-designated successor and the source of that designation.
3. Stop writing the shared queue and Coordinator context. Assigned workers continue only their existing authorized Jobs. While no Coordinator is designated, workers retain follow-up in their own Job pending material rather than dispatching successors.

To take over coordination:

1. Obtain the lead developer's explicit designation and confirm the previous Coordinator has stopped coordinating. Record the source and any uncertainty; neither timestamps nor reported activity prove liveness.
2. Read shared records, inspect Git/checkpoint state and relevant worker-local notes, and flag missing or uncertain context. Preserve the queue and unfinished material rather than replacing them from memory.
3. Record the new identity with `claim-coordinator --take-over --reason` ([above](#claiming-the-role)), which sets `worker` and `updated_at` and adds an attributed context entry naming the superseded holder, the time and the designation; then add a context entry with the predecessor's stop or release and the outstanding uncertainties.
4. Report `SR` (see [Reporting R and SR](#reporting-r-and-sr)) before starting new work.

If the previous Coordinator is unavailable, the lead developer may explicitly reassign coordination. Preserve existing records, identify the interrupted handoff and uncertainties, and establish that the former Coordinator will not resume shared writes without another handoff. Inspect an interrupted writer lock and confirm its writer stopped; you may then remove that writer lock under the existing helper procedure and you tell the lead developer afterwards. The lifecycle lock and the checkpoint operation lock are different: they are removed only with the lead developer's word ([H3](WORKER-HAZARDS.md#h3-stale-lock-files)). If exclusive ownership cannot be established, stop shared writes and report the conflict. Guarded digests prevent stale overwrites; they do not authenticate identity or prevent a former owner from rereading and writing again.

A release or takeover grants no new implementation, staging, commit or integration approval. Pending approvals retain their exact boundaries; local handoff assertions never serve as approval evidence.

## Bootstrap for existing workers

After this convention is integrated, the current Coordinator creates the queue and job records from verified repository state and explicitly attributed session knowledge. Existing workers export unfinished decisions, rationale, quotes, devlog fragments, blockers and next steps into their designated job records. Do not copy their entire vendor memory or silently promote obsolete plans. Any missing context stays identified as missing. Give successors the helper's discovered location and job handle, and also put the full assignment in normal copyable chat text. The local file never replaces the human-visible order.
