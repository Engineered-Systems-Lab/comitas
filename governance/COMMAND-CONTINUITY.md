# Command continuity

Start at [AGENTS](../AGENTS.md), then the framework's [agent orientation](AGENT-ORIENTATION.md), [Constraints](CONSTRAINTS.md), [Architecture](../docs/ARCHITECTURE.md), [Decisions](DECISIONS.md), [Plan](../docs/PLAN.md), [Progress](../docs/PROGRESS.md) and the historical [log index](../devlog/README.md). Progress owns current state; logs never override it. A project adds its own component specifications and validation reports to this reading list, and to the reconstruction contract, as it grows.

## Layer 1: deterministic verification

For a broad baseline audit, the available commands are below (repository root, Git and an explicitly verified Python 3.13+ interpreter; see the [interpreter policy](CONSTRAINTS.md#python-interpreter-policy)). This is not a per-transaction checklist. Use the [manifest-derived validation scope](CHECKPOINT-WORKFLOW.md#validation-scope) for changes; a record-only transaction runs the continuity command, not regression discovery:

```sh
python -B governance/tools/validate_continuity.py
python -B governance/tools/run_tests.py -s governance/tests -s governance/config -p 'test_*.py' -v
```

Command examples use `python` as the verified invocation. Continuity and checkpoint mechanics are standard-library-only.

The checker resolves required documents and local Markdown links and heading fragments, log index coverage and metadata/checkpoint references, and extracts typed facts from authoritative sections. For a project that declares a runtime boundary it also checks that the implementation checkpoint is an ancestor of HEAD and the latest commit touching runtime implementation paths, ties its log to that checkpoint, and compares the frozen specifications, contracts and fixtures with the [frozen manifest](PROJECT-GOVERNANCE.md#the-frozen-manifest): the frozen files are exactly its files, in the working tree and in the index, with the SHA-256 it records for each (CRLF read as LF). The comparison reads no Git history and never rewrites files. Missing Git history fails visibly. No checks traverse work/ or generated artifacts.

## Layer 2: repeatable reconstruction

Give a fresh agent only the repository and the exact [versioned prompt](COMMAND-RECOVERY-PROMPT-v1.md). Save its JSON response outside the repository (for example, `../review/reconstruction.json`), then run from the repository root:

```sh
python -B governance/tools/validate_continuity.py --reconstruction ../review/reconstruction.json
```

The v1 contract defines fields, types, evidence selectors and narrative sections, not a second snapshot of Progress. It has two parts that the checker reads as one: the framework's [part](config/reconstruction-contract-v1.json) (the version, the bootstrap prompt, the narrative sections and the framework evidence) and the project's optional `project-governance/config/reconstruction-contract.json` (the facts read from Progress, its own evidence and, for a runtime boundary, the completion report), the union refusing a clash and the framework part standing alone without the project's. Expected values are extracted on each run from Progress, with frozen identity established from Git when the contract declares it. A deliberate current-state change therefore updates its owning document, not copied expected facts. Source format or meaning changes require reviewing selectors; incompatible response changes require a new version of prompt and contract together. Keep old versions for reproducible evaluation against the corresponding repository revision.

The validator requires exact typed facts and source citations, plus nonempty narratives with resolvable evidence. It does not judge whether prose faithfully explains a citation, whether all prose claims are true, or whether an agent really had no prior context. Human review covers those aspects and authority interpretation. LLM generation is nondeterministic; the same repository state and JSON artifact produce the same validator result. Record repository revision, local diff, prompt version, model/settings and returned artifact externally for an actual agent trial. Do not claim an actual fresh-agent trial from a synthetic validator test.

Negative tests cover wrong facts and types, wrong version, missing evidence, broken links and anchors, log coverage, stale checkpoint and frozen mutations. The representative artifact is constructed in memory during tests; it tests the validator, not model performance.

## Adopting the recovery chain

The template ships the project's part of the contract, which declares three facts (`service`, `manual_acceptance`, `next_authorized_action`), all read from the Recovery facts table of [Progress](../docs/PROGRESS.md). That is enough for a project with no runtime boundary. A project that does have one adds, in one change:

1. rows marked `runtime` (and, where it freezes artifacts, `frozen`) in the Boundary column of the project's classification table, `project-governance/ARTIFACT-CLASSIFICATION.md`, which the tools read together with the framework's [table](ARTIFACT-CLASSIFICATION.md);
2. an `implementation_checkpoint` row in Progress (a full Git SHA of the latest commit that touched runtime paths) and the matching fact in the project's part of the contract, `project-governance/config/reconstruction-contract.json` ([its keys](PROJECT-GOVERNANCE.md#the-reconstruction-contract)), with the same pattern as the other rows;
3. optionally, a `frozen_contracts_and_fixtures_unchanged` fact with source `git:frozen-invariants` (a project with frozen rows then writes its manifest once with `python -B governance/tools/validate_continuity.py --freeze`, and the manifest is committed with the rows), and a `completion_evidence_fact` key naming the fact whose report section must stay anchored at the checkpoint commit;
4. one devlog entry whose `Repository checkpoint:` line carries that SHA, indexed in the log index. A project that keeps no devlog sets `"enabled": false` in `project-governance/config/devlog.json` and skips this step: the check then requires no entry beside the pointer and validates neither the log folder nor its index, and everything else in the chain is checked as before.

The runtime rows may already be committed when the project adopts the chain, for instance after an upgrade; the checkpoint row is then what is missing. The same change adds the row, the contract fact and the devlog entry, and the [checkpoint tool](CHECKPOINT-TOOL.md)'s preparation gate accepts it when HEAD's progress record holds no `implementation_checkpoint` row and the change touches no runtime path: the row names the latest commit that touched the runtime paths at HEAD, the contract file carries the fact, and the project's devlog, if it keeps one, gains the entry whose `Repository checkpoint:` line carries that SHA. A change that adds the row without them is refused with the missing part named, and once the row is committed the gate keeps the rule that applies to every runtime project.

Once those exist, a runtime commit enters the continuity-follow-up-pending phase of the [checkpoint workflow](CHECKPOINT-WORKFLOW.md) until its records land.
