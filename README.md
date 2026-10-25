# Comitas

*A governance framework for AI-assisted Git repositories.*

> Governed work for AI agents, landed only on your sealed approval.

Comitas is Latin for courtesy and friendliness: the spirit in which people and AI agents work together here.

A reusable governance framework for projects that humans and AI agents work on together. It is the process, the rules and the tooling, with no project in it:

- a **checkpoint workflow**: explicit scope, a sealed transaction, validation chosen from what changed, and a commit only after the exact approval of one person (the lead developer);
- **transaction-owned Git worktrees**, so several workers can run in parallel without sharing a checkout or an index, with guarded integration;
- **local job coordination**: JSON records of the work in flight, hand-offs between workers, and an execution status that shows overlaps before they collide;
- **one classification table**, in two files read as one (the framework's rows in `governance/ARTIFACT-CLASSIFICATION.md`, yours in `project-governance/ARTIFACT-CLASSIFICATION.md`), that says what every path is and which validation a change to it needs, read by every tool;
- a **continuity and recovery chain**: a deterministic check of the documents, links and logs, and a versioned way for a fresh agent to reconstruct the project state from the repository alone;
- **Mission Control**: a read-only local terminal over the decisions, rationale, logs, documents and commits;
- the **decision registry and rationale** for each of these rules, so you know why they are there before you change them.

**What approval guarantees, and its limits.** Human approval binds to the exact reviewed transaction: its candidate and its validation evidence. A change that invalidates that binding requires a fresh approval, except through the optional guarded carry. When enabled, the guarded carry preserves an approval after main advances only when all its conditions hold: the change merges cleanly, the approved change and commit message are preserved under the documented comparison rules, the validation selection (framework and project checks alike) remains unchanged, and every selected check passes again. This verifies preservation and passing checks; it does not prove that the combined result is semantically correct or that parallel changes are independent ([approval](governance/CHECKPOINT-WORKFLOW.md#exact-approval-and-execution), [guarded carry](governance/CHECKPOINT-TOOL.md#the-carry)).

The documents are written as rules: MUST and MUST NOT mark requirements, the lead developer is the person who holds final approval, and the Coordinator is the role that plans the work and hands out Jobs.

## Layout

`governance/` is the framework: do not edit it, because an upgrade replaces it wholesale. `project-governance/` is yours: the files the tools read beside the framework's, listed in [Project governance files](governance/PROJECT-GOVERNANCE.md); an upgrade never touches it. The project's own files are `AGENTS.md`, `docs/` and `devlog/`; every agent starts at `governance/AGENT-ORIENTATION.md`, which `AGENTS.md` points to.

| Folder | What it holds | Yours to edit? |
| --- | --- | --- |
| `AGENTS.md` | the project's own guidance: a pointer to the framework entry, what the project is and its authority map | yes |
| `governance/` | the rules, workflow, decisions and rationale, with the folders below inside it | no: it is the framework, and an upgrade replaces it; your own rules and decisions go in `project-governance/` |
| `governance/tools/` | the commands (checkpoint, worktree guard, local jobs, continuity, test runner, runtime guard and the adoption helper, each a script at this level) with their implementation in the packages `lifecycle/`, `worktrees/`, `coordination/`, `support/` and `continuity/` | no: this is what you update from Comitas |
| `governance/tests/` | the tests of the tools, in folders named like the packages they test (`lifecycle/`, `worktrees/`, `coordination/`, `support/`, `continuity/`) | no: they come with the tools |
| `governance/extensions/` | the optional pieces of the framework, each in its own folder (today Mission Control, a read-only local terminal), with the script that removes one; the core never depends on them | no: remove an extension you do not want with `python -B governance/extensions/remove_extension.py <name>` |
| `governance/tests/extensions/` | the tests of the extensions, one folder each | no: they come with the extensions |
| `governance/config/` | what the framework tells the tools: policy, profiles, the framework's classification golden pin, the framework part of the reconstruction contract, and the conformance tests that pin that content (`conformance/`); framework content only | no: it comes with the framework |
| `project-governance/` | your side of the governance: your classification table and its golden pin, your rules, decisions and rationale, your feature switches, devlog settings, Mission Control inputs and port, and the optional additions to the policy, profiles and reconstruction contract; Comitas ships a starter for each, and [Project governance files](governance/PROJECT-GOVERNANCE.md) lists them with their keys and defaults | yes: it is yours, and an upgrade never touches it |
| `docs/` | your architecture, plan and progress (placeholders here) | yes |
| `devlog/` | the historical record of project-state transitions: your conventions (the style of an entry, yours to rewrite) and the index, no entries yet; the folder name and the date format are settings | yes |
| `governance/scripts/` | the thin shell entry point for the claims status (committed executable, mode 100755) | rarely |
| `governance/VERSION`, `governance/CHANGELOG.md` | the Comitas version and what changed in it | no: they come with Comitas |

"Yours" in this README, and "the project's" or "owns" in the governance documents, say who maintains a file. They do not say who holds the rights in it: every file Comitas ships, the starter files included, is licensed material ([License](#license)).

Python 3.13 or later and Git are the only requirements of the tools; Node.js is needed only for the Mission Control terminal checks. On Windows run the shell commands from Git Bash.

## Adopting and running a project

[Project lifecycle](governance/PROJECT-LIFECYCLE.md) is the one page for the lead developer, in six phases, each with the commands you run yourself and links to the sections that own the details:

1. **Adopt.** Either click "Use this template" on GitHub (leave "Include all branches" off) and clone the new repository, which needs no adoption helper, or export the tracked files of `main` into a new empty folder (do not copy Comitas, which would take its `.git` history), run `git init -b main` and `git add -A`, run the adoption helper `python -B governance/tools/adopt.py` once, and make the first commit.
2. **Set up.** Make the files yours (`AGENTS.md`, `docs/`, this README and `project-governance/`), run the continuity check and the test suite, and claim the Coordinator role with `local_jobs.py claim-coordinator`.
3. **Run.** Give orders to the Coordinator, watch the work with `governance/scripts/claims.sh` and Mission Control, give the go and the Approve in the workers' own sessions, and push.
4. **Repair.** Clear stale claims, check the claims database and remove a stale lock.
5. **Change.** Turn a feature switch off or remove an extension.
6. **Upgrade.** Replace `governance/` with a new release, read the Migration notes of the [changelog](governance/CHANGELOG.md) and run the checks.

This README is yours to rewrite and an upgrade never replaces it, so the steps are kept in that page and not here. The export carries `LICENSE` and `governance/NOTICE`; keep both ([License](#license)).

## License

Comitas is licensed under the PolyForm Noncommercial License 1.0.0, with the Required Notice in the first line of [LICENSE](LICENSE). This summary does not replace the license text:

- **Permitted purposes.** You may use, copy, change and distribute Comitas, and make new works based on it, for a permitted purpose: any noncommercial purpose; personal use for research, experiment and testing for the benefit of public knowledge, personal study, private entertainment, hobby projects, amateur pursuits or religious observance, without any anticipated commercial application; and use by a charitable organization, an educational institution, a public research organization, a public safety or health organization, an environmental protection organization or a government institution, whatever the source of its funding.
- **Other uses** need a separate permission from the licensor. The license gives none, and this repository makes no offer of one.
- **Notices.** Anyone who gets a copy of any part of Comitas from you must also get the license terms or their URL, and every `Required Notice:` line. `LICENSE` carries both for the whole of Comitas, and [governance/NOTICE](governance/NOTICE) carries them for a copy of `governance/` or of any part of it, an extension included.
- **Your project.** These terms do not cover material you write yourself. They keep covering what you retain from Comitas, whether or not you changed it, and owning your project does not change that.
- **The rest of the license** includes a patent license that ends if you make a written claim that the software infringes a patent, no right to sublicense, a 32-day period to come into compliance after a first written notice of a violation, and no warranty and no liability as far as the law allows.
- **What the license does not say.** It licenses the rights the licensor holds in the material. It does not protect the governance method or the ideas, it is not a statement that every part of the material is protected by copyright (parts were written with AI tools, as the [contribution record](governance/CONTRIBUTION-RECORD.md) says), and it is not a statement that the material is free of third-party rights. Third-party material would keep its own license and notice; none has been identified ([contribution register](governance/CONTRIBUTION-REGISTER.md)).

How contributions to the framework are handled is in [Contributing](governance/CONTRIBUTING.md). The framework was extracted from an earlier private project of the licensor.

## What was left out

Comitas carries the governance framework only: it holds no project logs, services, datasets, design or plan documents, component validation gates or service scripts, and nothing machine-local. Those are yours to add. The framework's decisions are numbered with the prefix G, and the numbers that are not listed in its decisions document were retired before release; your project's decisions are D decisions in `project-governance/DECISIONS.md`.
