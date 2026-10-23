# Project governance

Governance describes how this repository is managed, validated, recorded, decided, recovered and observed. Domain and product designs, architecture, plans, progress and component evidence remain in [docs](../docs/ARCHITECTURE.md); the executable checkpoint, worktree and continuity tools and their tests (the tools, the same in every project that adopts the template) remain in `governance/tools/`, and what the tools are told by the framework (policy, profiles, the framework's classification golden pin and the framework part of the reconstruction contract, with the conformance tests that pin them) remains in `governance/config/`, which holds framework content only. What a project tells the tools lives in `project-governance/`, which [Project governance files](PROJECT-GOVERNANCE.md) describes and an upgrade never touches. The project's [AGENTS](../AGENTS.md) points every agent to the [agent orientation](AGENT-ORIENTATION.md), which is the framework's entry point.

| Document | Purpose |
| --- | --- |
| [Agent orientation](AGENT-ORIENTATION.md) | The short common entry every agent reads first: roles, vocabulary, shortcuts and where each kind of truth lives; no rules of its own |
| [Coordinator role](ROLE-COORDINATOR.md) | What only the Coordinator does: R and SR, delegation, the horizon rule, release, takeover and recovery |
| [Worker role](ROLE-WORKER.md) | What only a worker does: the Job, the mission worktree, validation scope and the preview waiver |
| [Constraints](CONSTRAINTS.md) | Active operating rules and frozen-artifact precedence |
| [Decisions](DECISIONS.md) | Complete standing registry; IDs and history stay together |
| [Rationale](RATIONALE.md) | Explanations and evidence; non-normative |
| [Checkpoint workflow](CHECKPOINT-WORKFLOW.md) | Preparation, exact approval, verification and continuity |
| [Checkpoint tool](CHECKPOINT-TOOL.md) | Tool mechanics, policy and scratch lifecycle |
| [Worktree workflow](WORKTREE-WORKFLOW.md) | Transaction ownership and guarded integration/removal |
| [Local coordination](LOCAL-COORDINATION.md) | Local JSON Jobs, Coordinator responsibilities and handoffs |
| [Worker hazards](WORKER-HAZARDS.md) | Non-normative list of known traps for workers and the Coordinator |
| [Project governance files](PROJECT-GOVERNANCE.md) | Every file a project keeps in `project-governance/`: its keys, defaults, how the tools combine it with the framework's, what they refuse, and the devlog settings and the minimum the continuity check enforces |
| [Project lifecycle](PROJECT-LIFECYCLE.md) | The lead developer's page, in six phases (adopt, set up, run, repair, change, upgrade): the commands a person runs in each, with links to the sections that own the details |
| [Devlog conventions](../devlog/CONVENTIONS.md) | The project's own devlog authoring style and historical-record follow-up |
| [Artifact classification](ARTIFACT-CLASSIFICATION.md) | The single table of what each path is and which validation it needs |
| [Command continuity](COMMAND-CONTINUITY.md) | Repository recovery and evidence chain |
| [Recovery prompt v1](COMMAND-RECOVERY-PROMPT-v1.md) | Versioned reconstruction instructions |
| [Extensions](extensions/README.md) | Optional pieces shipped with the framework, the rule that the core never depends on them and the list for removing one |
| [Contributing](CONTRIBUTING.md) | How a contribution to the framework is disclosed and recorded, and what the license does and does not do |
| [Contribution record](CONTRIBUTION-RECORD.md) | Frozen record of the history, identities and tools up to the point the contribution process took effect |
| [Contribution register](CONTRIBUTION-REGISTER.md) | Append-only, dated register of contributors, AI tools, accounts, provider agreements and third-party material |
| [NOTICE](NOTICE) | The license notice that goes with every copy of this folder or of any part of it |
| [Changelog](CHANGELOG.md) | What an adopter must do or know in each release; the version is in [VERSION](VERSION) |

## Decision identifiers

Decision IDs are permanent, so every cross-reference inside the tools, tests and documents stays valid. The gaps in the numbering (G001 to G017, G020 to G022, G027 to G028, G032 to G034 and G036 to G039) are numbers retired before release. The framework's decisions carry the prefix G; your project's own decisions carry the prefix D with any free number, and need no relation to the ones here ([G049](DECISIONS.md#g049---the-framework-numbers-its-decisions-with-g-and-a-project-numbers-its-own-with-d-2026-10-19-accepted)).

## Historical records

The [devlog](../devlog/README.md) is the historical, non-normative record of project-state transitions. The index provides orientation; authoring and format rules live in the [devlog conventions](../devlog/CONVENTIONS.md), and the folder, the date format and whether a devlog is kept at all are adopter settings in `project-governance/config/devlog.json`. The devlog ships empty. Evidence in the [rationale](RATIONALE.md), such as commit hashes, refers to the repository the framework was extracted from and is kept as the history of why each rule exists; it is not resolvable in a new repository.

## Mission Control

Mission Control is an optional [extension](extensions/README.md): it can be removed without touching the core. [Mission Control v1 design](extensions/mission_control/MISSION-CONTROL-V1-DESIGN.md) records the read-only repository observation design under G030. The [generator](extensions/mission_control/README.md) produces five self-contained derived datasets, which are generated locally and never committed, and the implemented [read-only terminal](extensions/mission_control/ui/README.md) browses the local copy. Neither the datasets nor the UI supplies live transaction state or governing authority.
