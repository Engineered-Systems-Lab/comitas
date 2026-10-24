# Extensions

This folder is the home of the optional pieces of the framework. An extension is shipped by default, and a project that does not want it removes it without refactoring anything. Today there are two: [Mission Control](mission_control/README.md), the read-only local terminal over the decisions, rationale, logs, documents and commits, and the [diff viewer](diff_viewer/README.md), which writes the exact preview or sealed candidate as one readable HTML page beside the review.

## The rule

**The core never depends on an extension.** An extension may use the core: Mission Control reads the classification table, the policy, the profiles, the devlog setting and the job helper. Nothing in the core may import, require or name an extension: no tool, no test of a tool, no configuration value the core needs to start. The two states of an extension are not the same thing:

- **Disabled** is free. An extension only runs when someone starts it (Mission Control runs only when its script is started), so not using it needs no step.
- **Removed** must leave the core consistent. The extension's folder, its tests and the names it owns in the configuration go, and every test of the core still passes.

The decision is [G042](../DECISIONS.md#g042---the-core-never-depends-on-an-extension-2026-10-18-accepted). This is level 1 of the work: an extension is a folder that can be removed by a list. Fragments that let an extension add its own documents, rows and commands without any line in the core's files are a later level, and the rule above is what that level will be held to.

## What an extension owns

| Part | Where |
| --- | --- |
| Code, scripts, user interface, its own README and design | `governance/extensions/<name>/` |
| Its tests | `governance/tests/extensions/<name>/`, a package like the other test folders |
| Its manifest | `governance/extensions/<name>/extension.json`: the folder, the test folder, the policy commands, impact invariants and commit subclasses it owns, the project files it reads, and the names that mark it in files (`markers`) and in prose (`prose`); optionally a `group`, `{"label": "<name>", "paths": ["<exact document path>", ...]}`, that names the extension's documents for a document browser such as Mission Control, which shows them as one group and reads them as inputs, and loses both when the manifest goes |
| Its rows | the rows of the [artifact classification table](../ARTIFACT-CLASSIFICATION.md) under its two folders, with their entries in the golden pin; the commands in the policy; the invariants and the subclass in the profiles |
| Its project part, if any | a file in `project-governance/` that the extension reads when it is present |

An extension is part of the framework's licensed material. A copy of an extension made or distributed on its own goes with a copy of [governance/NOTICE](../NOTICE), which carries the Required Notice line and the license's URL ([licensing and provenance](../CONSTRAINTS.md#licensing-and-provenance)).

The core may carry the extension's invariant or subclass name in a few rows of its own (the rows of the files that the extension reads, such as the decisions). That is allowed because the manifest lists the name and the removal strips it; it is not a dependency.

## Removal list

Run the script from the repository root, with the extension layer still in place:

```sh
python -B governance/extensions/remove_extension.py mission_control
```

It does these steps, which are the mechanical part of the list:

1. Deletes the extension's folder (`governance/extensions/mission_control/`), its test folder (`governance/tests/extensions/mission_control/`) and the project files its manifest names (`project-governance/config/mission-control-inputs.json`).
2. Deletes its rows from the artifact classification table, and its entries from the golden pin (`governance/config/artifact-classification-golden.json`).
3. Deletes its policy commands (`mission-control-generator`, `mission-control-ui`) from `governance/config/checkpoint-policy.json`, its impact invariants of the same names and the commit subclass `mission-control` from `governance/config/checkpoint-profiles.json`.
4. Strips the invariant and subclass names from the rows of the core that carry them (the decisions and the rationale rows of the table, and their golden entries).
5. Turns every Markdown link into the removed folders into plain text, so no link breaks.
6. Fails, naming each line, if a row, an entry or a name of the extension is still in one of those four configuration files.

`--all` removes every extension and then the extension layer itself: `governance/extensions/` (this README and the script) and `governance/tests/extensions/`.

Then edit by hand the document lines that name the extension (the script prints where they are). The decisions and the rationale keep their text as history.

- `README.md`: the Mission Control bullet in the opening list, the layout rows and the sentence about Node.js, and the Mission Control port bullet in the adoption steps.
- `governance/README.md`: the row of the extensions folder and the Mission Control section.
- `governance/AGENT-ORIENTATION.md`: the mention of the queue in "Where each kind of truth lives" and the Mission Control discovery section.
- `governance/ROLE-COORDINATOR.md`: the sentence about reading the Mission Control JSON first for a status request, and the mentions of the queue view.
- `governance/LOCAL-COORDINATION.md`: the two mentions of the queue view.
- `governance/CHECKPOINT-TOOL.md`: the Mission Control gate in the runner paragraph and the extension tests bullet of the test layout.
- `governance/CHECKPOINT-WORKFLOW.md`: the sentence about the generator tests that an edit of the rationale or the decisions runs.
- `governance/CONSTRAINTS.md`: the sentence about the datasets as an example of generated artifacts.
- `governance/WORKER-HAZARDS.md`: H7, the server that loads its code once.
- `governance/ROLE-WORKER.md`: the section about the optional diff viewer.
- `governance/CHANGELOG.md`: add the removal to the note for your release.
- `governance/PROJECT-GOVERNANCE.md`: the section about the Mission Control inputs and port, the two rows of its files and the row of the port in the defaults table, and the clause about the declared log documents in the folder rename paragraph of the devlog section.

## Checking it

`governance/config/conformance/test_extension_removal.py` proves the rule on every run of the suite. It copies the tracked tree into a temporary directory, runs `remove_extension.py --all` there, deletes nothing else by hand, checks that no tracked file of the copy other than a document still names an extension, turns the copy into a Git repository with the environment's identity (no Git configuration is read or written) and runs the tests of the areas most likely to depend on an extension: the classification table and its golden pin, the continuity checker on the copy, the checkpoint scope selector, the shared paths, the commit-metadata vocabulary, the modes of the shipped scripts, link retargeting and the agent pointer. A full suite inside a test would be too slow; these areas are the ones that read the real table, policy and profiles. A file that is new and not yet added to Git is not copied: add it first.

## Adding an extension

Put the code in `governance/extensions/<name>/`, its tests in `governance/tests/extensions/<name>/` (with an `__init__.py` at each level), write the `extension.json` and give every file a row in the table. Anything the core needs must not come from the extension; if the extension needs a new command or invariant, add it to the manifest so that the removal knows it. Run the removal test.
