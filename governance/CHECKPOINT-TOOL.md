# Checkpoint tool v1

Implementation notes for [the authoritative workflow](CHECKPOINT-WORKFLOW.md) and G018. This tool does not interpret authorization, compose messages, update records, or grant permission. Nothing in this document authorizes a real transaction.

The notes are split by purpose into the seven files below. The [reading map](READING-MAP.md#by-step) says which file and section to read for which step; a rule that asks for the checkpoint tool in full means this index and all seven files.

## Files by purpose

| Purpose | File |
| --- | --- |
| Inputs, the runtime guard, the adoption helper and the bootstrap pass. | [CHECKPOINT-TOOL-COMMANDS.md](CHECKPOINT-TOOL-COMMANDS.md) |
| The conversation consent interface, approval words, early conditional approval, feature switches and the sealed review. | [CHECKPOINT-TOOL-CONSENT.md](CHECKPOINT-TOOL-CONSENT.md) |
| Diff-bound approval, the sensitivity of a path, the carry, append-only lists and the go carry. | [CHECKPOINT-TOOL-CARRY.md](CHECKPOINT-TOOL-CARRY.md) |
| The state machine and the store, the packet, the timing record, the classification table, the project overlay, link retarget, runtime profiles and validation before sealing. | [CHECKPOINT-TOOL-EVIDENCE.md](CHECKPOINT-TOOL-EVIDENCE.md) |
| The claims database, execution status and the commands that read them. | [CHECKPOINT-TOOL-CLAIMS.md](CHECKPOINT-TOOL-CLAIMS.md) |
| The test runner, the components of the checkpoint tool, the layout of the tools and the test layout. | [CHECKPOINT-TOOL-INTERNALS.md](CHECKPOINT-TOOL-INTERNALS.md) |
| Recovery after an interruption, scratch completion and recovering blocked post-commit verification. | [CHECKPOINT-TOOL-RECOVERY.md](CHECKPOINT-TOOL-RECOVERY.md) |

## Where the old anchors went

Every heading kept its text, so its fragment is unchanged and only the file differs. A link written to `CHECKPOINT-TOOL.md` with a fragment before the split now needs the file named here.

| Old link | Now |
| --- | --- |
| `CHECKPOINT-TOOL.md#inputs-and-commands` | [CHECKPOINT-TOOL-COMMANDS.md#inputs-and-commands](CHECKPOINT-TOOL-COMMANDS.md#inputs-and-commands) |
| `CHECKPOINT-TOOL.md#runtime-guard` | [CHECKPOINT-TOOL-COMMANDS.md#runtime-guard](CHECKPOINT-TOOL-COMMANDS.md#runtime-guard) |
| `CHECKPOINT-TOOL.md#the-adoption-helper` | [CHECKPOINT-TOOL-COMMANDS.md#the-adoption-helper](CHECKPOINT-TOOL-COMMANDS.md#the-adoption-helper) |
| `CHECKPOINT-TOOL.md#one-bootstrap-pass` | [CHECKPOINT-TOOL-COMMANDS.md#one-bootstrap-pass](CHECKPOINT-TOOL-COMMANDS.md#one-bootstrap-pass) |
| `CHECKPOINT-TOOL.md#conversation-consent-interface-agent-execution-contract` | [CHECKPOINT-TOOL-CONSENT.md#conversation-consent-interface-agent-execution-contract](CHECKPOINT-TOOL-CONSENT.md#conversation-consent-interface-agent-execution-contract) |
| `CHECKPOINT-TOOL.md#approval-words` | [CHECKPOINT-TOOL-CONSENT.md#approval-words](CHECKPOINT-TOOL-CONSENT.md#approval-words) |
| `CHECKPOINT-TOOL.md#early-conditional-approval` | [CHECKPOINT-TOOL-CONSENT.md#early-conditional-approval](CHECKPOINT-TOOL-CONSENT.md#early-conditional-approval) |
| `CHECKPOINT-TOOL.md#feature-switches` | [CHECKPOINT-TOOL-CONSENT.md#feature-switches](CHECKPOINT-TOOL-CONSENT.md#feature-switches) |
| `CHECKPOINT-TOOL.md#diff-bound-approval` | [CHECKPOINT-TOOL-CARRY.md#diff-bound-approval](CHECKPOINT-TOOL-CARRY.md#diff-bound-approval) |
| `CHECKPOINT-TOOL.md#sensitivity-of-a-path` | [CHECKPOINT-TOOL-CARRY.md#sensitivity-of-a-path](CHECKPOINT-TOOL-CARRY.md#sensitivity-of-a-path) |
| `CHECKPOINT-TOOL.md#the-carry` | [CHECKPOINT-TOOL-CARRY.md#the-carry](CHECKPOINT-TOOL-CARRY.md#the-carry) |
| `CHECKPOINT-TOOL.md#append-only-lists` | [CHECKPOINT-TOOL-CARRY.md#append-only-lists](CHECKPOINT-TOOL-CARRY.md#append-only-lists) |
| `CHECKPOINT-TOOL.md#the-go-carry` | [CHECKPOINT-TOOL-CARRY.md#the-go-carry](CHECKPOINT-TOOL-CARRY.md#the-go-carry) |
| `CHECKPOINT-TOOL.md#test-runner` | [CHECKPOINT-TOOL-INTERNALS.md#test-runner](CHECKPOINT-TOOL-INTERNALS.md#test-runner) |
| `CHECKPOINT-TOOL.md#sealed-review` | [CHECKPOINT-TOOL-CONSENT.md#sealed-review](CHECKPOINT-TOOL-CONSENT.md#sealed-review) |
| `CHECKPOINT-TOOL.md#state-and-evidence` | [CHECKPOINT-TOOL-EVIDENCE.md#state-and-evidence](CHECKPOINT-TOOL-EVIDENCE.md#state-and-evidence) |
| `CHECKPOINT-TOOL.md#timing-record` | [CHECKPOINT-TOOL-EVIDENCE.md#timing-record](CHECKPOINT-TOOL-EVIDENCE.md#timing-record) |
| `CHECKPOINT-TOOL.md#artifact-classification-table` | [CHECKPOINT-TOOL-EVIDENCE.md#artifact-classification-table](CHECKPOINT-TOOL-EVIDENCE.md#artifact-classification-table) |
| `CHECKPOINT-TOOL.md#the-project-overlay` | [CHECKPOINT-TOOL-EVIDENCE.md#the-project-overlay](CHECKPOINT-TOOL-EVIDENCE.md#the-project-overlay) |
| `CHECKPOINT-TOOL.md#link-retarget` | [CHECKPOINT-TOOL-EVIDENCE.md#link-retarget](CHECKPOINT-TOOL-EVIDENCE.md#link-retarget) |
| `CHECKPOINT-TOOL.md#runtime-profiles` | [CHECKPOINT-TOOL-EVIDENCE.md#runtime-profiles](CHECKPOINT-TOOL-EVIDENCE.md#runtime-profiles) |
| `CHECKPOINT-TOOL.md#claims-and-execution-status` | [CHECKPOINT-TOOL-CLAIMS.md#claims-and-execution-status](CHECKPOINT-TOOL-CLAIMS.md#claims-and-execution-status) |
| `CHECKPOINT-TOOL.md#components-of-the-checkpoint-tool` | [CHECKPOINT-TOOL-INTERNALS.md#components-of-the-checkpoint-tool](CHECKPOINT-TOOL-INTERNALS.md#components-of-the-checkpoint-tool) |
| `CHECKPOINT-TOOL.md#layout-of-the-tools-commands-and-packages` | [CHECKPOINT-TOOL-INTERNALS.md#layout-of-the-tools-commands-and-packages](CHECKPOINT-TOOL-INTERNALS.md#layout-of-the-tools-commands-and-packages) |
| `CHECKPOINT-TOOL.md#test-layout` | [CHECKPOINT-TOOL-INTERNALS.md#test-layout](CHECKPOINT-TOOL-INTERNALS.md#test-layout) |
| `CHECKPOINT-TOOL.md#validation-before-sealing` | [CHECKPOINT-TOOL-EVIDENCE.md#validation-before-sealing](CHECKPOINT-TOOL-EVIDENCE.md#validation-before-sealing) |
| `CHECKPOINT-TOOL.md#recovery-and-limits` | [CHECKPOINT-TOOL-RECOVERY.md#recovery-and-limits](CHECKPOINT-TOOL-RECOVERY.md#recovery-and-limits) |
| `CHECKPOINT-TOOL.md#scratch-completion` | [CHECKPOINT-TOOL-RECOVERY.md#scratch-completion](CHECKPOINT-TOOL-RECOVERY.md#scratch-completion) |
| `CHECKPOINT-TOOL.md#recovering-blocked-post-commit-verification` | [CHECKPOINT-TOOL-RECOVERY.md#recovering-blocked-post-commit-verification](CHECKPOINT-TOOL-RECOVERY.md#recovering-blocked-post-commit-verification) |
