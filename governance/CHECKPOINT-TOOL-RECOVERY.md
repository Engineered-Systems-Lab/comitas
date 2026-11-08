# Checkpoint tool: recovery and limits

Part of the [checkpoint tool notes](CHECKPOINT-TOOL.md), which list the files by purpose. Recovery after an interruption, scratch completion and recovering blocked post-commit verification.

## Recovery and limits

Restart with `inspect`; approval and evidence survive restarts. After interruption around commit, use `verify`, which accepts only an exact HEAD/parent/tree/message match, empty post-commit index and preserved working/residual state. Residual comparisons use the sealed `prepared_status` after selective staging, excluding exact manifest paths. Pre-staging `residual` remains historical evidence: Git may collapse a new transaction directory into an untracked directory entry before staging, which is why `reconcile` judges unrelated dirty state from the sealed `prepared_status` instead. Unrelated paths remaining after staging and validated working-content hashes must still match; existing packets are not rewritten to correct that classification. It never retries a commit. Repeated successful verification is idempotent and recreates the same handoff bytes if the handoff write was interrupted. Unexpected HEAD or missing/tampered packets/key require operator review, not reconstruction of approval.

There is one transaction slot. A stale owned base is a normal lifecycle state eligible for explicit [bounded reconciliation](WORKTREE-WORKFLOW.md#bounded-stale-base-reconciliation), preserving the original authenticated packet and refreshing parent/index/tree bindings without clearing owned staging. Other failed/stale transactions require operator inspection; `abandon` withdraws one that has not begun to commit, so re-preparation needs no manual unstaging. Never discard unrelated staging or working content. After successful verification and required durable record promotion, use `finish` below; failed/stale transactions cannot use cleanup. Reconciliation audits remain in scratch until verified completion and are included in restart-safe cleanup. Record prose and Step-3 worker integration remain outside the tool.

Evidence reuse is transaction-local and command-specific. The replayable suites that read only tooling, configuration and authored inputs use `content-v1` bindings over manifest bytes, validation files, declared governance inputs and checkout rules, together with exact policy/profile, environment, configuration, driver and interpreter/Git identity. Parent, full tree/index and refs are excluded only for these reviewed content-only suites. Continuity regressions use `base-v3` for packets prepared under the [protected-set ref policy](WORKTREE-WORKFLOW.md#protected-set-ref-policy): the base/history facts bind the protected refs, including `main`, and nothing tolerated. `base-v2` packets keep the [same-tree ref-name policy](WORKTREE-WORKFLOW.md#same-tree-transient-ref-name-policy), whose transient names/targets remain separately audited and whose surviving-name retargeting is refused. Legacy packets retain `base-v1` and exact all-ref names; no packet or historical result is migrated. Driver/input changes invalidate old evidence under the existing rules. All other checks execute fresh. Results require authenticated exact input/output bindings and successful exit; ambiguous, changed or failed results rerun. `validation_runs` records `executed` or `reused`. Final `seal` always refreshes cheap continuity/whitespace and checks candidate integrity, and supplies no approval. A changed parent always requires fresh G018 exact approval.

No push, amend, destructive cleanup, branch switching, credential handling or Git configuration mutation is implemented. Hooks are rejected rather than trusted to preserve approved bytes. Signed commits remain deferred. Linked worktrees retain private checkpoint scratch/indexes and bind private/common Git directories plus the native ownership marker in packet identity. The separate [worktree lifecycle guard](WORKTREE-WORKFLOW.md) handles creation, fast-forward integration and removal without granting G018 authority. Tests create their own temporary repositories and identities; production never configures Git identity. Run `python -B -m unittest discover -s governance/tools -p 'test_checkpoint.py' -v` for isolated checkpoint regression coverage.


## Scratch completion

The [disposable-state workflow](CHECKPOINT-WORKFLOW.md#disposable-operational-state-2026-10-02)
governs scratch.

`finish` rechecks the exact approval receipt, immutable commit parent/tree/message,
HEAD/branch/index, configuration, validated content and unrelated status. It accepts
only a verified result. A sealed, temporary deletion journal binds those facts and
the exact packet/handoff hashes; interrupted deletion resumes only if they still
match. Cleanup deletes only the named scratch packets, handoff and journal. It is
idempotent; it neither creates a historical archive nor removes unknown files or
the receipt key. A pending runtime is rejected until its continuity records are
committed and verified. A successful tooling transaction with no new mandatory
record obligation needs no redundant durable packet: its Git commit is the result.
Normal completion additionally requires Progress and the matching runtime log to
match their committed versions, and that log to be linked from the committed
index using the existing same-directory Markdown link convention. Unrelated
uncommitted index entries need not be included.
A working-copy continuity PASS alone is insufficient durable promotion.

### Recovering blocked post-commit verification

`verify` also accepts an authenticated `verification-blocked` packet for an explicit recheck after inspection.
It reconstructs the original approval without the post-commit error field and verifies the original immutable commit,
the interpreter pin, branch, configuration, empty index, sealed working bytes and unrelated state again. HEAD must still
equal the blocked result. It reruns post-commit continuity and all independent runtime-pending checks. A continuing
failure stays blocked; success clears the obsolete error and enters only the ordinary verified or follow-up-pending
state. It never repeats the commit, changes an approval, repairs records or accepts changed candidate bytes.

A project blocked by the framework-only reconstruction-contract lookup in 0.6.1 can recover across a governed upgrade:

1. Inspect the original mission, packet, exact approved runtime commit and execution status. Preserve its worktree,
   branch, key, packets and review inputs. Do not abandon it, edit its packets or replace its working `governance/`.
2. Take the complete fixed framework through a separate, normally previewed and approved upgrade mission on clean main,
   then finish and integrate that upgrade. A blocked packet with no running operation or lock is not a ref window.
   Its path claims can report an overlap; they do not prohibit the upgrade. Read execution status at each required step.
3. In Git Bash, use the same Python interpreter as the blocked transaction. Change the current directory to the
   main checkout and run its upgraded, committed driver against the unchanged original mission:

   ```sh
   python -B governance/tools/checkpoint.py --repo '<original-mission>' inspect
   python -B governance/tools/checkpoint.py --repo '<original-mission>' verify
   python -B governance/tools/checkpoint.py --repo '<original-mission>' continue-records
   ```

   Run only inspected, committed fixed tooling. This explicit driver choice leaves the old mission's sealed working
   bytes intact; copying fixed files into it would invalidate verification. Main's movement does not change its commit.
4. Delegate the continuity-record work separately, with the required fresh-worker assessment and exact approval.
   Edit the Progress and indexed devlog records in the original mission. From the main checkout, prepare those changes
   with the upgraded driver and `--repo '<original-mission>'`, then approve, commit, verify and finish with that driver. The records cite the
   original runtime commit and preserve its anchored completion report. Full committed continuity must pass.
5. The finished branch now diverges from upgraded main. When the committed `diff_bound_approval` switch is true, use
   [the carry](CHECKPOINT-TOOL-CARRY.md#the-carry) from upgraded main for the finished runtime/records pair, then guarded integration and owned
   cleanup. The carry proves the approved changes, substitutes the carried runtime identity in the records and reruns
   every selected check. It can refuse on changed selection, conflicts, changed evidence or any other existing gate.
   If it is off or refuses, preserve the mission and obtain a fresh reviewed reconciliation mission and approvals;
   neither `reconcile` of an unfinished packet nor a forced merge is a recovery shortcut.

The same-interpreter and original-receipt requirements remain. Missing approval evidence, changed working bytes,
unexpected HEAD, an unrelated continuity failure or a failed carry needs inspection and the applicable fresh review;
this procedure supplies no authority to repair those conditions. No packet migration, copying or hand editing is needed.

`continue-records` accepts a verified `follow-up-pending` runtime, atomically moves
its authenticated packet to scratch `pending.json`, and permits only explicit
Progress/devlog paths in the next normal preparation. It does not imply lifecycle
completion or continuity PASS. The records may already be drafted in the worktree
when it runs: it tolerates differences from the sealed runtime only at continuity
records, and refuses any other difference, a staged change and a moved HEAD, naming
the paths (`Working copy differs from the sealed transaction at: ...`). `verify` and
`finish` stay strict and name the paths too, and add that a records worker runs
`continue-records` first. A path is a continuity record when it is `docs/PROGRESS.md`,
or lies under the devlog folder and the artifact classification table calls it class
`record`; the folder and the table are read as committed at HEAD, never from the
working copy, so `devlog/CONVENTIONS.md` (governance) never qualifies and a candidate
cannot move the folder or reclassify a path. The same rule limits the next
preparation. The subsequent transaction uses ordinary exact
approval and all normal checks; `finish` then checks the predecessor and clears both
packets. No unrelated transaction can bypass stale committed runtime continuity,
including after scratch loss. Do not delete scratch before completion.

Continuity evidence paths explicitly reject `tmp/`, including existing files;
operational handoffs cannot become authoritative reconstruction evidence.
