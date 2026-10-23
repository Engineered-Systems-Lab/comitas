# Progress

Status: template placeholder. Replace this document with the verified current state of your project.

Progress records what is verified now, with no task lists and no history; history is in [devlog entries](../devlog/README.md) and in Git, direction is in [Plan](PLAN.md).

## Current state

Comitas has been adopted and no project work has landed yet.

## Recovery facts

These current-state fields keep the exact table format consumed by [continuity verification](../governance/COMMAND-CONTINUITY.md) and the [v1 reconstruction contract](../governance/config/reconstruction-contract-v1.json).

| Fact | Value |
| --- | --- |
| service | None yet: the project has no runtime component |
| manual_acceptance | Not recorded |
| next_authorized_action | None recorded here: each action requires its own applicable checkpoint authorization |

A project that declares a runtime boundary in the classification table also adds an `implementation_checkpoint` row (a full Git SHA) and the matching fact in the reconstruction contract; see [Command continuity](../governance/COMMAND-CONTINUITY.md#adopting-the-recovery-chain). Record, governance and tooling transactions do not advance the implementation checkpoint.
