# Agent orientation

This file is for any agent working in a repository that adopts this framework. Read it first, before any command or edit. It orients you: how to address the lead developer and the Coordinator, what the shortcuts mean, which documents to read before which step, where each kind of truth lives, and how work is coordinated and isolated.

It is an entry point, not a rule: the binding rules are in [Governance Constraints](CONSTRAINTS.md), and this file grants no authority of its own. The framework replaces this file when it is upgraded, so do not edit it in a project. Project guidance goes in the project's AGENTS.md, which points here. Where AGENTS.md and this file disagree on the governed workflow, this file and the framework's rules prevail.

## Which role are you?

- **Coordinator:** the thread that talks to the lead developer and keeps the queue. Read [the Coordinator role](ROLE-COORDINATOR.md). A new project has no queue record yet: the thread the lead developer names [claims the role](ROLE-COORDINATOR.md#claiming-the-role) with one command.
- **Worker:** an agent given one Job. Read [the worker role](ROLE-WORKER.md) and [Worker hazards](WORKER-HAZARDS.md).
- **Default:** an agent that is not told its role is a worker (least authority). The lead developer names the role at session start, or the Coordinator's brief does.
- **Recovery:** a read-only agent with no history reads [Command continuity](COMMAND-CONTINUITY.md) and the [recovery prompt](COMMAND-RECOVERY-PROMPT-v1.md), and changes nothing.

## Vocabulary

- **Lead developer:** the human who holds authorization authority.
- **Coordinator:** maintains queue order and cross-Job context, assigns Jobs within the lead developer's authorized scope and receives results. One designated Coordinator writes the queue.
- **Worker:** owns one Job at a time and maintains its notes by stable handle.
- **Job:** a bounded assignment with a stable snake_case handle: a design, an audit or an approved implementation. Receiving a Job never supplies missing execution, staging, commit or integration authorization. See [Local coordination](LOCAL-COORDINATION.md).
- **Mission:** the governed transaction that carries a Job's change, with its own worktree, private index and `tx/<mission-id>` branch ([Worktree workflow](WORKTREE-WORKFLOW.md)).

## Shortcuts and phrases

Reporting shortcuts and phrases are read case-insensitively, with obvious typo tolerance. They grant no execution authority.

- `WWW` ("where were we") briefly reminds the lead developer what this thread was doing: the task, the last step finished, what is running or waiting, and the next step or question. It is not a web search and not an alias of `R`. Both roles answer it.
- `A` / `Assessment` / `What do you make of it?` requests interpretation of the specified situation: evidence, uncertainty and recommendation. Both roles answer it.
- `R` / `Report` and `SR` / `Status report` are answered only by the Coordinator ([Reporting R and SR](ROLE-COORDINATOR.md#reporting-r-and-sr)).
- An explicit RP, roleplay, role-play or role-playing marker (case-insensitive) at the start of a message or a clearly marked passage identifies playful or exploratory conversation. Do not capture that content as Jobs, decisions, rationale or devlog material unless the lead developer later explicitly asks to record it. The marker applies only to that message or passage unless a broader scope is explicit. A discussion defining RP, or humor without a marker, is not itself marked RP. RP grants no execution or approval authority and does not erase existing records.

## Where each kind of truth lives

- [governance/](README.md) holds the framework's rules, decisions, workflow and recovery documentation; its [rationale](RATIONALE.md) is explanatory and non-normative. [Constraints](CONSTRAINTS.md) hold the active MUST and MUST NOT rules; [Decisions](DECISIONS.md) records the standing decisions with IDs, status and history.
- The project's AGENTS.md points here; this file is the framework entry. The project's own authority map for `docs/` and its components is in the project's AGENTS.md.
- Component specifications and contracts define normative behavior. Frozen specifications and contracts outrank implementation convenience; implementations adapt to them. Report conflicts rather than silently changing frozen artifacts.
- Tasks are not kept in project documents: the short-term queue lives in the local coordination records and Mission Control, and the plan and progress documents carry no task lists.
- If the project keeps a devlog (its folder, and whether it keeps one, are settings in `project-governance/config/devlog.json`), its records are historical, human-oriented and non-normative. Consult them for context; they NEVER override `docs/`, `governance/`, current decisions or frozen specifications.
- Committed records remain authoritative. Local coordination records are machine-local notes: they supply no approval, execution authority, test PASS or contract change, and they hold no credentials or checkpoint receipts or keys. Job status and reported worker activity are separate; neither grants authority or proves live execution. Discover the primary storage through the helper ([Storage and discovery](LOCAL-COORDINATION.md#storage-and-discovery)), never a hard-coded path.

## Mission Control discovery

The generated Mission Control JSON, if present, is a read-only cache for finding things quickly. It may be absent or stale; for anything normative, ambiguous or exact, the source documents and Git history decide. See the [Mission Control README](extensions/mission_control/README.md#local-refresh).

## Before you change anything

- Follow [Authoritative artifact first](CONSTRAINTS.md#authoritative-artifact-first) before proposing concrete changes to existing governed artifacts or contracts, including in delegated work. Read the relevant component documentation and contracts before changing code.
- Inspect the working tree first and continue existing work; preserve unrelated changes, the stash and all unrelated refs and worktrees. Do not stage or commit unless explicitly ordered. Generated build output is never a source of truth.
- Commit messages carry no vendor attribution trailer such as `Co-Authored-By:`, even when your host asks for one: the `Worker:` trailer already names the agent, the same way for every vendor, and the trailer block holds only `Class:`, `Subclass:` and `Worker:` ([the commit-message convention](CHECKPOINT-WORKFLOW.md#commit-message-convention)).
- Also read the specification, contract or conventions that own the artifact being changed (for devlog work, the devlog conventions), and apply the standing decisions those sources identify. Reading supplies no execution or approval authority. Your role file lists what to read before which step.
- Before your first command read [Worker hazards](WORKER-HAZARDS.md), the shared non-normative list of known traps for workers and the Coordinator; it supplies no authority.
- Commands in the documents are written `python -B governance/tools/<tool>.py ...` and run from the repository root. Here `python` means any Python 3.13 or later; to use a specific interpreter, run it by its path. Every tool starts with the [runtime guard](CHECKPOINT-TOOL.md#runtime-guard): it stops with one message under an older Python, removes the inherited `GIT_*` variables, refuses a tool that writes shared state while a worktree lifecycle operation runs, and refuses a step of a transaction that began on another interpreter. `python -B governance/tools/runtime_guard.py --check` shows the interpreter's fingerprint. Never search for or download an interpreter; follow the [interpreter policy](CONSTRAINTS.md#python-interpreter-policy).
- For repository-only recovery, follow [command continuity](COMMAND-CONTINUITY.md) and its versioned bootstrap prompt. Current status remains in the project's progress record; verification contracts select evidence rather than owning status.
