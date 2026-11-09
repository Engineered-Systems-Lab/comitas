# Reading map

This map says what a worker reads, and when. It is the one source of that rule: the [worker role](ROLE-WORKER.md#read-before-you-act) points here, the brief that `local_jobs.py brief` prints reads the two lists below, and continuity checks that every document and section they name exists. It is an entry point and grants no authority: reading supplies no execution or approval authority.

## The core

Read these in full before any worktree, checkpoint, staging, approval, finish or integration step. They are about 12,500 words together; the list holds the documents and nothing else.

- [AGENTS.md](../AGENTS.md)
- [Agent orientation](AGENT-ORIENTATION.md)
- [Worker role](ROLE-WORKER.md)
- [Worker hazards](WORKER-HAZARDS.md)
- [Constraints](CONSTRAINTS.md)
- [Checkpoint workflow](CHECKPOINT-WORKFLOW.md)

## Core sections

Read these two sections of [Local coordination](LOCAL-COORDINATION.md) with the core. The rest of that document is read by step, below.

- [Execution status](LOCAL-COORDINATION.md#execution-status)
- [Updates, handoff and disposal](LOCAL-COORDINATION.md#updates-handoff-and-disposal)

## By step

Read the files and sections of a row before the step it names, and again whenever a tool refuses or the facts change. The [checkpoint tool notes](CHECKPOINT-TOOL.md) are an index and seven files; a row names only the files and sections the step needs. Also read the specification, contract or conventions that own the artifact you change.

| Step | Read |
| --- | --- |
| Create the worktree, integrate, remove | [Transaction worktrees](WORKTREE-WORKFLOW.md), in full. |
| Choose the manifest paths and learn how a path is classified | [Artifact classification](ARTIFACT-CLASSIFICATION.md): its columns, rules and approval-sensitive paths, and the rows of your paths (`show_classification.py` prints them). In the tool notes: [Artifact classification table](CHECKPOINT-TOOL-EVIDENCE.md#artifact-classification-table), [The project overlay](CHECKPOINT-TOOL-EVIDENCE.md#the-project-overlay) and [Runtime profiles](CHECKPOINT-TOOL-EVIDENCE.md#runtime-profiles). |
| Show a preview and ask for the go | [Inputs and commands](CHECKPOINT-TOOL-COMMANDS.md#inputs-and-commands), [Runtime guard](CHECKPOINT-TOOL-COMMANDS.md#runtime-guard), [Early conditional approval](CHECKPOINT-TOOL-CONSENT.md#early-conditional-approval), [Feature switches](CHECKPOINT-TOOL-CONSENT.md#feature-switches) and [Approval words](CHECKPOINT-TOOL-CONSENT.md#approval-words). |
| Prepare and seal, with or without a preview | [Inputs and commands](CHECKPOINT-TOOL-COMMANDS.md#inputs-and-commands) (the manifest, the message, the delegation and the worker context that `prepare` takes; a waived or skipped preview does not skip it), [One bootstrap pass](CHECKPOINT-TOOL-COMMANDS.md#one-bootstrap-pass), [State and evidence](CHECKPOINT-TOOL-EVIDENCE.md#state-and-evidence), [Timing record](CHECKPOINT-TOOL-EVIDENCE.md#timing-record) and [Validation before sealing](CHECKPOINT-TOOL-EVIDENCE.md#validation-before-sealing). |
| Present the sealed review, take the Approve or Reject and commit | [Conversation consent interface](CHECKPOINT-TOOL-CONSENT.md#conversation-consent-interface-agent-execution-contract), [Approval words](CHECKPOINT-TOOL-CONSENT.md#approval-words) and [Sealed review](CHECKPOINT-TOOL-CONSENT.md#sealed-review). |
| Finish a transaction and clear its scratch | [Scratch completion](CHECKPOINT-TOOL-RECOVERY.md#scratch-completion). |
| A tool refuses, a run is interrupted or verification is blocked | [Recovery and limits](CHECKPOINT-TOOL-RECOVERY.md#recovery-and-limits), [Recovering blocked post-commit verification](CHECKPOINT-TOOL-RECOVERY.md#recovering-blocked-post-commit-verification) and [State and evidence](CHECKPOINT-TOOL-EVIDENCE.md#state-and-evidence). |
| Main moved after your base | [Bounded stale-base reconciliation](WORKTREE-WORKFLOW.md#bounded-stale-base-reconciliation) and the whole of the [carry notes](CHECKPOINT-TOOL-CARRY.md). |
| The execution status is not CLEAR, or a claim or overlap needs explaining | [Claims and execution status](CHECKPOINT-TOOL-CLAIMS.md#claims-and-execution-status). |
| Change the checkpoint tool, its tests or its layout | [Components of the checkpoint tool](CHECKPOINT-TOOL-INTERNALS.md#components-of-the-checkpoint-tool), [Layout of the tools: commands and packages](CHECKPOINT-TOOL-INTERNALS.md#layout-of-the-tools-commands-and-packages) (the public launcher paths and command lines to keep), [Test layout](CHECKPOINT-TOOL-INTERNALS.md#test-layout) and [Test runner](CHECKPOINT-TOOL-INTERNALS.md#test-runner). |
| Work on Job records, an Epic, a handoff or a closure beyond the core sections | [Local coordination](LOCAL-COORDINATION.md), in full. |
