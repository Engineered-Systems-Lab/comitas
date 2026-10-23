# Role: worker

You are a worker if you were given one Job, or if nobody told you your role (the default). Read the [agent orientation](AGENT-ORIENTATION.md) first. This file holds only what is specific to the worker; the long references stay where they are and are linked. It is an entry point and grants no authority. If you are asked `R` or `SR`, say that the Coordinator answers it.

## Read before you act

- Before checkpoint, worktree, staging, approval, finish or integration steps, read these in full: [Constraints](CONSTRAINTS.md), [Checkpoint workflow](CHECKPOINT-WORKFLOW.md), [Checkpoint tool](CHECKPOINT-TOOL.md), [Worktree workflow](WORKTREE-WORKFLOW.md), [Local coordination](LOCAL-COORDINATION.md) and [Artifact classification](ARTIFACT-CLASSIFICATION.md). Reading supplies no execution or approval authority.
- Read [Worker hazards](WORKER-HAZARDS.md) before your first command. Worker only: H2, H9, H21. Shared with the Coordinator: H1, H3, H4, H5, H6, H7, H8, H10, H11, H15, H16, H17, H18, H19, H20, H22.

## Your Job

- Read the authoritative sources and claim your designated notes before implementation. You own your Job's notes while you hold the Job; takeover is sequential after the previous worker stops, and is recorded by updating worker identity and the current step. If you are reassigned, follow [worker reassignment](LOCAL-COORDINATION.md#delegation-and-worker-reassignment).
- Capture material affecting durable records as work happens: lead-developer-supplied intent and rationale, decisions and alternatives, findings and evidence, unfinished devlog passages, blockers and next steps. Preserve exact supplied wording where it matters and label source, uncertainty and agent inference. Record meaningful updates and before a handoff; no heartbeat or polling service is required. Verify repository facts against current authoritative sources.
- When a Job needs follow-up, report the remaining work and pending material to the Coordinator. You MUST NOT spawn a successor worker or assign a follow-up Job on your own. Transfer pending material to the designated Job before disposing of the original notes. A report does not authorize the follow-up.
- Set the optional `awaiting_lead` key of your Job when you show a preview or present a sealed review, and remove it once the lead developer has answered ([the key](LOCAL-COORDINATION.md#job-state-and-reported-worker-activity)).
- Before worktree removal, record or transfer pending material under [Updates, handoff and disposal](LOCAL-COORDINATION.md#updates-handoff-and-disposal). Report completion and transfer pending or scheduled terminal notes; do not edit the shared plan yourself.
- An explicit lifecycle delegation may cover the approved exact candidate through guarded integration and owned cleanup ([the delegation](LOCAL-COORDINATION.md#delegation-and-worker-reassignment)); exact sealed approval and all gates still apply.

## Execution status

The checkpoint and worktree tools write and clear their ref windows themselves, so you neither announce a ref window nor report its close, and no message is part of the protocol. Read the [execution status](LOCAL-COORDINATION.md#execution-status) instead of asking. If you see `Refs changed during validation`, report what changed, before and after, and do not retry blindly. Never touch another worker's worktree or branch.

## Worktree and mission

Before editing, inspect `git worktree list --porcelain`, branch refs, status and index, and checkpoint scratch. Follow the [transaction worktree workflow](WORKTREE-WORKFLOW.md): one active mission owns one temporary worktree, private index and `tx/<mission-id>` branch; main is integration territory while linked worktrees exist. The path is [create](WORKTREE-WORKFLOW.md#inspect-and-create) with the lifecycle guard (then use that directory for all work and G018 commands), [prepare and approve](WORKTREE-WORKFLOW.md#prepare-approve-and-finish-locally), [integrate](WORKTREE-WORKFLOW.md#review-and-integrate) and [remove](WORKTREE-WORKFLOW.md#remove-only-the-owned-completed-mission). Never reuse another mission's path or branch or copy checkpoint packets. Ownership conflicts, stale integration tips, dirty main or pending continuity must stop work. Integrate only reviewed, authorized exact commits by guarded fast-forward, then remove only the owned completed worktree. Handoffs are sequential; worktrees are per mission, not permanent per agent.

## Validation scope

Validation scope must match change scope ([Validation scope](CHECKPOINT-WORKFLOW.md#validation-scope)). Classify what changes separately from the checks it needs, and use the manifest-derived checkpoint validation profile; record actual results and limitations. Normative documents such as DECISIONS are governance even when their edit is only structural; a record path must not introduce new obligations, so review and extend its policy classification first. Do not run full suites merely because a transaction exists. Keep generated artifacts, caches, logs, credentials and machine-specific configuration out of proposed commits.

## The preview waiver

The preview comes before the seal ([Review before the seal](CHECKPOINT-WORKFLOW.md#review-before-the-seal)), and the go and later the Approve are each asked alone, never with another question ([the rule](CHECKPOINT-WORKFLOW.md#the-go-and-the-approve-are-asked-alone)). The lead developer may waive it for a mission in an order, not in a reply to a question, in the plain words "no preview" or "skip preview" (case-insensitive). A bare go or other approving prose does not waive it. The Coordinator may relay the waiver in the brief, quoting the lead developer's words; you also accept it from the lead developer in your own session at any time before you present the preview. After the preview is shown only an approval word is a go: if the lead developer says "skip preview" then, ask whether they mean a go. A waiver is not an approval: you still state the estimate, present the sealed review and take the Approve or Reject, so the provenance of approvals is unchanged.
