# Artifact classification

This document is the single authored source of what every path in the repository is and which validation a change to it needs (G026). One table below names each file, directory or filter and gives its change class, validation profile, protection boundary, whether its bytes are sealed into approvals, the impact invariants a change can affect, its commit subclasses and whether it must be reachable and how sensitive its change is to the approval machinery. The checkpoint and continuity tools read this table directly, and no other document or list states these facts. Rationale and history are in the [G026 artifact-classification extension](DECISIONS.md#g026-artifact-classification-extension-2026-10-07) and its [rationale](RATIONALE.md#change-classification-and-validation-scope-g026).

**Two files, one table.** This document holds the framework's rows: everything under `governance/` and the few files the tools own at the root (the agent entry floor `AGENTS.md` and the checkout rules `.gitignore` and `.gitattributes`). The project's own rows are in `project-governance/ARTIFACT-CLASSIFICATION.md`, in the same format, and the tools read the two as one table; an upgrade replaces this document and never touches the project's. The [project overlay](CHECKPOINT-TOOL.md#the-project-overlay) says how the files are loaded and bound.

**Single source.** The checkpoint, worktree, continuity and commit-metadata tools read this table and no other list of paths. `governance/config/checkpoint-policy.json` holds commands, and `governance/config/checkpoint-profiles.json` holds the profiles, the invariants and the commit-subclass vocabulary; neither names a path. The location of this table, like each other repository anchor the tools share, is named once in `governance/tools/continuity/artifact_classification.py` and imported by every tool. Each prepared transaction seals the resolved row of every manifest path.

## Columns

| Column | Meaning |
| --- | --- |
| Path | An exact file, a directory prefix ending in `/`, or a filter `dir/*.ext` covering files with that extension at any depth under `dir/`. |
| Class | `record`, `governance`, `tooling` or `runtime`, as defined by G026. |
| Profile | The validation profile in `checkpoint-profiles.json`, or `blocked`: the path is refused and has no validation scope. |
| Boundary | `runtime` for the runtime-continuity boundary, `frozen` for a frozen artifact (its checksum is in the [frozen manifest](PROJECT-GOVERNANCE.md#the-frozen-manifest), written when it is frozen, and the continuity check compares the file with it, never with a commit), or both as `frozen, runtime` in ascending order. A path is on the runtime boundary when it is on it in the table at HEAD or in the candidate's table, so a candidate cannot drop the cell it is judged by. |
| Bound | `yes` when the file's bytes are sealed into approvals even if it is not in the manifest. |
| Invariants | The impact invariants, ascending and comma separated, that select the narrow checks. `symbol-map` means the changed code symbols decide. A hyphen means unmapped, so the whole profile envelope runs. |
| Subclass | The commit subclasses that describe the path, ascending and comma separated. It is a label: it never selects a check, and a transaction's `Subclass` trailer must equal the labels of its manifest. |
| Reachable | `yes` when the document must exist and, if Markdown, be reachable from `AGENTS.md`. Continuity enforces this. It is not required reading. |
| Approval-sensitivity | How much a change to the path could alter what a go approves, how the check list is derived, how an approval is recognized, recorded or bound, how a candidate is committed or integrated, or the rules for those. `-` is low: a change cannot. `medium` is the former `yes`: a change could, as the classification table, the golden file, the tests that pin the machinery and the documents that carry its rules. `high` is the approval machinery itself, an explicit short list: the early-approval, approval-words, checkpoint and worktree code and the files that configure them. The [early approval reach](CHECKPOINT-TOOL.md#feature-switches) says which values may carry a preview go: at reach `low` only a manifest whose every path is `-`, at reach `medium` also `medium` paths; a `high` path, a path with no usable row, and a value the parser does not know never carry a go and keep the sealed Approve ([G018 early conditional approval extension](DECISIONS.md#g018-early-conditional-approval-extension-2026-10-10)). A path is judged by the higher of its row at HEAD and its row in the candidate's table ([sensitivity of a path](CHECKPOINT-TOOL.md#sensitivity-of-a-path)). It is not a Subclass and selects no check. |

## Rules

1. One row per path, sorted by plain ASCII ordinal comparison of the Path, so uppercase sorts before lowercase. Duplicates and unsorted rows are errors, and the parser names the correct position.
2. The most specific row wins: an exact path, then the longest directory prefix, with a filter ranking above a bare directory of the same length. The order of rows never changes meaning.
3. A path that no row covers is blocked. Unknown classes, profiles, invariants or subclasses, malformed rows and an unreadable table also stop the tools. A directory row is blocked unless it names the file types it accepts.
4. Directory and filter rows fill every column. An exact-file row may leave cells empty: an empty or whitespace-only cell inherits from the nearest enclosing complete row, and a filled cell overrides it.
5. A single hyphen is a deliberate no-value and clears an inherited value. Only Boundary, Bound, Invariants, Subclass, Reachable and Approval-sensitivity may be a hyphen; Class and Profile never.
6. A list cell is replaced and never merged. A child row may not clear `frozen`.
7. The whole document is ASCII. The Path is one backtick-quoted path. The table is the only one with the exact header below, apart from the optional historical table described next.
8. Continuity requires every exact-file row to name a file that exists. When a file is deleted, its row moves to Historical paths in the same transaction, and that transaction validates the deletion as [Historical paths](#historical-paths) describes.
9. Continuity requires every file Git tracks to resolve to a row of the framework's table or the project's, and fails with the list of paths no row covers. A `blocked` row is a row. A new file therefore needs its row in the transaction that adds it.
10. The project's table may classify no path under `governance/` or `project-governance/`, and a path it repeats from this table is a duplicate. This table classifies `project-governance/` as `high`. The loader refuses a clash and never resolves one, so a project row can never lower a framework path.

To see how any path resolves, run `python -B governance/tools/show_classification.py <path>...` from the repository root or anywhere else; it needs no transaction. It is read-only, reads the tables committed at HEAD (the framework's and the project's, when HEAD has one) with a few Git calls and prints each cell with its source, then the effective approval sensitivity, the higher of the HEAD row and the working row; `--checks` adds the checks a change would select, and `-v` adds the row and its parent row as written with their line numbers.

## Historical paths

A deleted file keeps its Class and Subclass here so that old commits that touched it still classify the same way when their class and subclass are reconstructed from their paths. A transaction that deletes a tracked file, or renames it away, also moves its row here, and the tool then scopes the deleted path by the row it had at HEAD. That is the only time a transaction consults these rows, and only when four things hold: the file is absent from the working tree, the working table has no live row covering the path, a Historical row names it with the same Class the HEAD row gave it, and the HEAD table resolves the path to a live row that is not `blocked` and has no Boundary. The deletion then validates by the HEAD row's profile and invariants, so moving a path to another place never lowers what a change to it needed, and its sealed view takes the approval sensitivity of that HEAD row, so deleting a `high` file is `high` and deleting a low one is low. A live file, a path with no row anywhere, a path whose Historical Class differs from its HEAD row, a path whose HEAD row was blocked or on the runtime or frozen boundary, and any malformed Historical row stay blocked like any unknown path. The rules are those of the live table, with exact-file paths only, sorted by ASCII ordinal Path with no duplicates, and no path may also have a live row. Continuity requires every historical path to be absent.

## Approval-sensitive paths

The column grades what the early conditional approval may carry. `medium` marks the checkpoint profiles, the classification table and its golden file, the review renderer, the continuity checker, the tests that pin them and the documents that carry the approval, worktree and delegation rules. `high` marks the approval machinery itself and the overlay folder, and nothing else: `governance/tools/lifecycle/early_approval.py`, `governance/tools/lifecycle/approval_words.py` with its launcher `governance/tools/approval_words.py`, the five files of the checkpoint driver (`governance/tools/checkpoint.py` and its four components `governance/tools/lifecycle/checkpoint_state.py`, `governance/tools/lifecycle/checkpoint_selection.py`, `governance/tools/lifecycle/checkpoint_evidence.py` and `governance/tools/lifecycle/checkpoint_lifecycle.py`), the lifecycle guard (its launcher `governance/tools/worktree.py` and `governance/tools/worktrees/guard.py`, `governance/tools/worktrees/worktree_carry.py` and `governance/tools/worktrees/worktree_support.py`), the runtime guard `governance/tools/runtime_guard.py` that every launcher enters first and that fingerprints the interpreter a transaction began with, the `__init__.py` of the `lifecycle` and `worktrees` packages and the two above them (`governance/__init__.py`, `governance/tools/__init__.py`), and `governance/config/checkpoint-policy.json`, and the project's own folder `project-governance/`, whose table, profiles, policy, feature switches and log settings decide the fast track and which a project therefore cannot classify itself: every file in it is `high`. `DECISIONS.md` counts as a whole file, because the table cannot mark a section. The reach in the feature file decides what is eligible: at reach `low` a manifest is eligible only when every path resolves to an explicit hyphen; at reach `medium` every path is a hyphen or `medium`; a `high` path is never eligible whatever the reach. A cell the parser does not know, an empty cell without a parent value, a missing column, an unreadable table or a path with no row counts as `high`. A new path needs its row in the transaction that adds it, which edits this table, which is itself marked, so adding a row never reaches the early approval. The [golden file](config/artifact-classification-golden.json) pins the marks of the sensitive rows, so lowering a mark needs a governance edit and a tooling edit in one sealed transaction.

## Changing the table

Editing a row is a governance change. It validates with continuity, the classifier tests and the selector tests, which also read the real table, through the `classification` impact invariant. A change that alters the classification of a sensitive path must also update the independent [golden file](config/artifact-classification-golden.json) in the same transaction, which is tooling: a table edit alone cannot reclassify a sensitive path. The project's rows are pinned the same way by `project-governance/config/artifact-classification-golden.json`.

## The table

| Path | Class | Profile | Boundary | Bound | Invariants | Subclass | Reachable | Approval-sensitivity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `.gitattributes` | tooling | tooling | - | - | - | - | - | medium |
| `.gitignore` | tooling | tooling | - | - | jobs | - | - | medium |
| `AGENTS.md` | governance | governance | - | yes | conformance, guidance | agent-guidance | yes | medium |
| `governance/AGENT-ORIENTATION.md` | governance | governance | - | yes | conformance, guidance | agent-guidance | yes | medium |
| `governance/ARTIFACT-CLASSIFICATION.md` | governance | governance | - | yes | classification | checkpoint | yes | medium |
| `governance/CHANGELOG.md` | governance | governance | - | - | conformance, guidance | - | yes | - |
| `governance/CHECKPOINT-TOOL.md` | governance | governance | - | - | conformance, guidance | checkpoint | yes | medium |
| `governance/CHECKPOINT-WORKFLOW.md` | governance | governance | - | yes | conformance, guidance | checkpoint | yes | medium |
| `governance/COMMAND-CONTINUITY.md` | governance | governance | - | - | continuity | - | yes | - |
| `governance/COMMAND-RECOVERY-PROMPT-v1.md` | governance | governance | - | - | continuity | - | yes | - |
| `governance/CONSTRAINTS.md` | governance | governance | - | yes | conformance, guidance | constraints | yes | medium |
| `governance/CONTRIBUTING.md` | governance | governance | - | - | conformance, guidance | - | yes | - |
| `governance/CONTRIBUTION-RECORD.md` | record | record-only | - | - | conformance | - | yes | - |
| `governance/CONTRIBUTION-REGISTER.md` | record | record-only | - | - | conformance | - | yes | - |
| `governance/DECISIONS.md` | governance | governance | - | yes | conformance, guidance, mission-control-generator | decisions | yes | medium |
| `governance/LOCAL-COORDINATION.md` | governance | governance | - | yes | conformance, guidance | agent-guidance | yes | medium |
| `governance/NOTICE` | record | record-only | - | - | conformance | - | - | - |
| `governance/PROJECT-GOVERNANCE.md` | governance | governance | - | - | conformance, guidance | - | yes | - |
| `governance/PROJECT-LIFECYCLE.md` | governance | governance | - | - | conformance, guidance | - | yes | - |
| `governance/RATIONALE.md` | record | record-only | - | - | conformance, mission-control-generator | rationale | yes | - |
| `governance/README.md` | governance | governance | - | yes | conformance, guidance | - | yes | - |
| `governance/ROLE-COORDINATOR.md` | governance | governance | - | yes | conformance, guidance | agent-guidance | yes | medium |
| `governance/ROLE-WORKER.md` | governance | governance | - | yes | conformance, guidance | agent-guidance | yes | medium |
| `governance/VERSION` | governance | governance | - | - | conformance, guidance | - | - | - |
| `governance/WORKER-HAZARDS.md` | governance | governance | - | - | conformance, guidance | agent-guidance | yes | medium |
| `governance/WORKTREE-WORKFLOW.md` | governance | governance | - | - | conformance, guidance | - | yes | medium |
| `governance/__init__.py` | tooling | tooling | - | - | - | - | - | high |
| `governance/config/artifact-classification-golden.json` | tooling | tooling | - | - | classification | checkpoint | - | medium |
| `governance/config/checkpoint-policy.json` | tooling | tooling | - | - | selector | checkpoint | - | high |
| `governance/config/checkpoint-profiles.json` | tooling | tooling | - | - | selector | checkpoint | - | medium |
| `governance/config/conformance/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/config/conformance/test_agent_pointer.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_artifact_classification.py` | tooling | tooling | - | - | classification | checkpoint | - | medium |
| `governance/config/conformance/test_checkpoint_scope.py` | tooling | tooling | - | - | selector | - | - | medium |
| `governance/config/conformance/test_cited_commits.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_continuity.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/config/conformance/test_decision_prefix.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_extension_removal.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_go_asked_alone.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_license_notice.py` | tooling | tooling | - | - | conformance | - | - | - |
| `governance/config/conformance/test_project_governance.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_project_lifecycle.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/conformance/test_runtime_guard_commands.py` | tooling | tooling | - | - | governance-runtime | - | - | - |
| `governance/config/conformance/test_script_modes.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/config/reconstruction-contract-v1.json` | tooling | tooling | - | - | continuity | - | yes | medium |
| `governance/extensions/README.md` | governance | governance | - | - | conformance, guidance | - | yes | - |
| `governance/extensions/mission_control/` | governance | blocked | - | - | - | mission-control | - | - |
| `governance/extensions/mission_control/*.json` | tooling | tooling | - | - | mission-control-generator | mission-control | - | - |
| `governance/extensions/mission_control/.gitattributes` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/.gitignore` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/MISSION-CONTROL-V1-DESIGN.md` |  | governance |  | yes | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/README.md` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/data_launcher.py` | tooling | tooling | - | - | mission-control-generator | mission-control | - | - |
| `governance/extensions/mission_control/declared_inputs.py` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/extension.json` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/generate.py` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/inputs.json` | tooling | tooling |  |  | mission-control-generator |  |  |  |
| `governance/extensions/mission_control/mission-control-data.sh` | tooling | tooling | - | - | mission-control-generator | - | - | - |
| `governance/extensions/mission_control/mission-control.sh` | tooling | tooling | - | - | mission-control-ui | - | - | - |
| `governance/extensions/mission_control/ui/README.md` | tooling | tooling |  |  | mission-control-ui |  |  |  |
| `governance/extensions/mission_control/ui/app.mjs` | tooling | tooling |  |  | mission-control-ui |  |  |  |
| `governance/extensions/mission_control/ui/core.mjs` | tooling | tooling |  |  | mission-control-ui |  |  |  |
| `governance/extensions/mission_control/ui/index.html` | tooling | tooling |  |  | mission-control-ui |  |  |  |
| `governance/extensions/mission_control/ui/serve.py` | tooling | tooling |  |  | mission-control-ui |  |  |  |
| `governance/extensions/mission_control/ui/style.css` | tooling | tooling |  |  | mission-control-ui |  |  |  |
| `governance/extensions/remove_extension.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/scripts/*.sh` | tooling | tooling | - | - | - | - | - | - |
| `governance/scripts/claims.sh` | tooling | tooling | - | - | - | checkpoint | - | - |
| `governance/tests/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/continuity/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/continuity/test_artifact_classification.py` | tooling | tooling | - | - | classification | checkpoint | - | medium |
| `governance/tests/continuity/test_citations.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/tests/continuity/test_classification_overlay.py` | tooling | tooling | - | - | classification | checkpoint | - | medium |
| `governance/tests/continuity/test_continuity.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/tests/continuity/test_frozen_manifest.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/tests/continuity/test_project_homes.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/tests/coordination/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/coordination/test_local_jobs.py` | tooling | tooling | - | - | conformance, jobs | - | - | - |
| `governance/tests/coordination/test_local_jobs_claim.py` | tooling | tooling | - | - | conformance, jobs | - | - | - |
| `governance/tests/coordination/test_local_jobs_concurrency.py` | tooling | tooling | - | - | conformance, jobs | - | - | - |
| `governance/tests/extensions/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/extensions/mission_control/__init__.py` | tooling | tooling | - | - | - | mission-control | - | medium |
| `governance/tests/extensions/mission_control/check.py` | tooling | tooling | - | - | mission-control-ui | mission-control | - | - |
| `governance/tests/extensions/mission_control/fixture_repo.py` | tooling | tooling | - | - | mission-control-ui | mission-control | - | - |
| `governance/tests/extensions/mission_control/test_core.mjs` | tooling | tooling | - | - | mission-control-ui | mission-control | - | - |
| `governance/tests/extensions/mission_control/test_generate.py` | tooling | tooling | - | - | mission-control-generator | mission-control | - | - |
| `governance/tests/extensions/mission_control/test_generate_scope.py` | tooling | tooling | - | - | mission-control-generator | mission-control | - | - |
| `governance/tests/extensions/mission_control/test_serve.py` | tooling | tooling | - | - | mission-control-ui | mission-control | - | - |
| `governance/tests/extensions/test_remove_extension.py` | tooling | tooling | - | - | - | - | - | - |
| `governance/tests/fake_git.py` | tooling | tooling | - | - | - | checkpoint | - | medium |
| `governance/tests/lifecycle/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/lifecycle/test_approval_words.py` | tooling | tooling | - | - | approval-words | checkpoint | - | medium |
| `governance/tests/lifecycle/test_checkpoint.py` | tooling | tooling | - | - | approval | - | - | medium |
| `governance/tests/lifecycle/test_checkpoint_abandon.py` | tooling | tooling | - | - | abandon | checkpoint | - | medium |
| `governance/tests/lifecycle/test_checkpoint_deletion.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/lifecycle/test_checkpoint_evidence.py` | tooling | tooling | - | - | evidence | - | - | medium |
| `governance/tests/lifecycle/test_checkpoint_interpreter_pin.py` | tooling | tooling | - | - | interpreter-pin | checkpoint | - | medium |
| `governance/tests/lifecycle/test_checkpoint_metadata.py` | tooling | tooling | - | - | selector | - | - | medium |
| `governance/tests/lifecycle/test_checkpoint_overlay.py` | tooling | tooling | - | - | classification, selector | checkpoint | - | medium |
| `governance/tests/lifecycle/test_checkpoint_reconcile.py` | tooling | tooling | - | - | reconcile | - | - | medium |
| `governance/tests/lifecycle/test_commit_history_conformance.py` | tooling | tooling | - | - | - | checkpoint | - | - |
| `governance/tests/lifecycle/test_early_approval.py` | tooling | tooling | - | - | early-approval | checkpoint | - | medium |
| `governance/tests/lifecycle/test_review_render.py` | tooling | tooling | - | - | review | checkpoint | - | medium |
| `governance/tests/run_tests.py` | tooling | tooling | - | - | test-runner | checkpoint | - | medium |
| `governance/tests/support/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/support/test_entry_points.py` | tooling | tooling | - | - | - | checkpoint | - | - |
| `governance/tests/support/test_fake_git.py` | tooling | tooling | - | - | - | checkpoint | - | medium |
| `governance/tests/support/test_link_retarget.py` | tooling | tooling | - | - | link-retarget | checkpoint | - | medium |
| `governance/tests/support/test_local_state.py` | tooling | tooling | - | - | - | checkpoint | - | medium |
| `governance/tests/support/test_repo_paths.py` | tooling | tooling | - | - | - | checkpoint | - | - |
| `governance/tests/support/test_run_tests.py` | tooling | tooling | - | - | test-runner | checkpoint | - | medium |
| `governance/tests/support/test_runtime_guard.py` | tooling | tooling | - | - | governance-runtime | - | - | medium |
| `governance/tests/support/test_runtime_guard_adoption.py` | tooling | tooling | - | - | governance-runtime | - | - | - |
| `governance/tests/support/test_timing_record.py` | tooling | tooling | - | - | timings | checkpoint | - | - |
| `governance/tests/worktrees/` | tooling | tooling | - | - | - | checkpoint | - | medium |
| `governance/tests/worktrees/__init__.py` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tests/worktrees/test_checkpoint_claims_script.py` | tooling | tooling | - | - | - | checkpoint | - | - |
| `governance/tests/worktrees/test_checkpoint_worktrees.py` | tooling | tooling | - | - | worktrees | - | - | medium |
| `governance/tools/__init__.py` | tooling | tooling | - | - | - | - | - | high |
| `governance/tools/adopt.py` | tooling | tooling | - | - | governance-runtime | - | - | - |
| `governance/tools/approval_words.py` | tooling | tooling | - | - | approval-words | checkpoint | - | high |
| `governance/tools/checkpoint.py` | tooling | tooling | - | - | symbol-map | checkpoint | - | high |
| `governance/tools/commit_metadata.py` | tooling | tooling | - | - | selector | checkpoint | - | medium |
| `governance/tools/continuity/` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tools/continuity/artifact_classification.py` | tooling | tooling | - | - | classification | checkpoint | - | medium |
| `governance/tools/continuity/show_classification.py` | tooling | tooling | - | - | classification | checkpoint | - | - |
| `governance/tools/continuity/validate_continuity.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/tools/coordination/` | tooling | tooling | - | - | conformance, jobs | - | - | - |
| `governance/tools/lifecycle/` | tooling | tooling | - | - | symbol-map | checkpoint | - | high |
| `governance/tools/lifecycle/__init__.py` | tooling | tooling | - | - | - | checkpoint | - | high |
| `governance/tools/lifecycle/approval_words.py` | tooling | tooling | - | - | approval-words | checkpoint | - | high |
| `governance/tools/lifecycle/checkpoint_evidence.py` | tooling | tooling | - | - | symbol-map | checkpoint | - | high |
| `governance/tools/lifecycle/checkpoint_lifecycle.py` | tooling | tooling | - | - | symbol-map | checkpoint | - | high |
| `governance/tools/lifecycle/checkpoint_selection.py` | tooling | tooling | - | - | symbol-map | checkpoint | - | high |
| `governance/tools/lifecycle/checkpoint_state.py` | tooling | tooling | - | - | symbol-map | checkpoint | - | high |
| `governance/tools/lifecycle/commit_metadata.py` | tooling | tooling | - | - | selector | checkpoint | - | medium |
| `governance/tools/lifecycle/early_approval.py` | tooling | tooling | - | - | early-approval | checkpoint | - | high |
| `governance/tools/lifecycle/review_render.py` | tooling | tooling | - | - | review | checkpoint | - | medium |
| `governance/tools/local_jobs.py` | tooling | tooling | - | - | conformance, jobs | - | - | - |
| `governance/tools/repo_paths.py` | tooling | tooling | - | - | - | checkpoint | - | - |
| `governance/tools/run_tests.py` | tooling | tooling | - | - | test-runner | checkpoint | - | medium |
| `governance/tools/runtime_guard.py` | tooling | tooling | - | - | governance-runtime | - | - | high |
| `governance/tools/show_classification.py` | tooling | tooling | - | - | classification | checkpoint | - | - |
| `governance/tools/support/` | tooling | tooling | - | - | - | - | - | medium |
| `governance/tools/support/adoption.py` | tooling | tooling | - | - | governance-runtime | - | - | - |
| `governance/tools/support/link_retarget.py` | tooling | tooling | - | - | link-retarget | checkpoint | - | medium |
| `governance/tools/support/local_state.py` | tooling | tooling | - | - | - | checkpoint | - | medium |
| `governance/tools/support/timing_record.py` | tooling | tooling | - | - | timings | checkpoint | - | - |
| `governance/tools/timing_record.py` | tooling | tooling | - | - | timings | checkpoint | - | - |
| `governance/tools/validate_continuity.py` | tooling | tooling | - | - | continuity | - | - | medium |
| `governance/tools/worktree.py` | tooling | tooling | - | - | worktrees | - | - | high |
| `governance/tools/worktrees/` | tooling | tooling | - | - | worktrees | - | - | high |
| `governance/tools/worktrees/__init__.py` | tooling | tooling | - | - | - | - | - | high |
| `governance/tools/worktrees/claims.py` | tooling | tooling | - | - | - | checkpoint | - | medium |
| `governance/tools/worktrees/guard.py` | tooling | tooling | - | - | worktrees | - | - | high |
| `governance/tools/worktrees/worktree_carry.py` | tooling | tooling | - | - | worktrees | - | - | high |
| `governance/tools/worktrees/worktree_support.py` | tooling | tooling | - | - | worktrees | - | - | high |
| `project-governance/` | governance | governance | - | - | classification, continuity, early-approval, selector | - | - | high |
