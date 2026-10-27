# Project governance files

This document is the one place that says what a project keeps in `project-governance/`: every file the tools read there, its keys, what it means when it is missing, how the tools combine it with the framework's, and what they refuse. It is framework-owned, so an upgrade replaces it; the files it describes are the project's and an upgrade never touches them. It states no rule of its own: the reasons are in [G045](DECISIONS.md#g045---the-project-overlay-is-read-by-every-tool-and-the-settings-override-code-defaults-2026-10-18-accepted) and [G050](DECISIONS.md#g050---the-project-keeps-its-own-rules-decisions-and-half-of-the-reconstruction-contract-2026-10-19-accepted), the loading mechanics in [the project overlay](CHECKPOINT-TOOL.md#the-project-overlay), and a conformance test pins the defaults below to the code.

`governance/` is the framework and is never edited by a project. `project-governance/` is the project's. A project starts from the files Comitas ships there (a minimal valid starter of each) and from then on owns them. Owning a file here means maintaining it; the starters remain licensed material of Comitas like the rest ([licensing and provenance](CONSTRAINTS.md#licensing-and-provenance)). The [framework's classification table](ARTIFACT-CLASSIFICATION.md) gives each supported file its own row: the project's constraints and decisions are `medium`, explanatory rationale and the optional extension's input and port settings are `-` (low), and the classification table and core configuration remain `high`. The directory fallback remains `high`, and a project cannot classify its own folder. A high path always takes the sealed Approve; lower paths use the committed reach and the higher-of-HEAD/candidate rule. This replaces the earlier blanket grading of G045/G050 ([G026 extension](DECISIONS.md#g026-project-artifact-classification-extension-2026-10-25)).

## How the tools combine the two sides

- **Overlay (the union).** The classification table, the policy, the profiles and the reconstruction contract each have a framework file and an optional project file of the same shape. The tools read both and take the union; the project's file may only add. A name the project reuses from the framework, a path it classifies under `governance/` or `project-governance/`, or a key it may not set is a refusal, never a silent choice, and the tool stops with one message naming the file.
- **Override of code defaults.** The feature switches, the devlog settings and the Mission Control port are one file each, with no framework copy. The framework's code holds the default of every key: a key the file leaves out takes it. How a file that is missing or does not parse is treated is stated per file below.
- **Registries and documents.** The project's rules, decisions and rationale are documents beside the framework's. Each is optional, and an absent one means none.

With every project file absent, the tools behave exactly as with the framework alone.

## The files

| File (in `project-governance/`) | Kind | Missing means |
| --- | --- | --- |
| `ARTIFACT-CLASSIFICATION.md` | overlay of the framework's table | no project rows, so every tracked file outside `governance/` is unclassified and blocked |
| `config/artifact-classification-golden.json` | the independent pin of the project's rows | no pin for the project's rows (the conformance tests of Comitas expect one) |
| `config/checkpoint-policy.json` | overlay of the framework's policy | the framework's policy alone |
| `config/checkpoint-profiles.json` | overlay of the framework's profiles | the framework's profiles alone |
| `config/checkpoint-features.toml` | override of code defaults | every feature off |
| `config/citations.json` | override of code defaults | no label (the adoption helper writes the label `template` into a new project's file) |
| `config/devlog.json` | override of code defaults | the defaults below |
| `config/frozen-manifest.json` | the checksums of the frozen files | no frozen file may exist: every file under a `frozen` row needs an entry ([the manifest](#the-frozen-manifest)) |
| `config/mission-control-inputs.json` | the project's part of the inventory of the Mission Control extension | the terminal shows the framework alone |
| `config/mission-control.json` | override of code defaults of the Mission Control extension | the default port and no tagline |
| `config/reconstruction-contract.json` | overlay of the framework's reconstruction contract | the framework's part alone |
| `config/selector-expectations.json` | the project's expectations of which checks its paths select | none ([selector expectations](#selector-expectations)) |
| `CONSTRAINTS.md` | the project's rules | no project rules |
| `DECISIONS.md` | the project's decision registry (prefix D) | no project decisions |
| `RATIONALE.md` | the rationale of the project's decisions | none |

## Defaults

The code supplies these. The table is read by a conformance test that compares every row with the code, so a default that changes in the code changes here in the same transaction. A key is always optional; the cell is the JSON value, and a TOML key takes the same value.

| File | Key | Default |
| --- | --- | --- |
| `config/citations.json` | `external_repositories` | `[]` |
| `config/devlog.json` | `folder` | `"devlog"` |
| `config/devlog.json` | `date_format` | `"%Y-%m-%dT%H:%MZ"` |
| `config/devlog.json` | `enabled` | `true` |
| `config/checkpoint-features.toml` | `early_approval_reach` | `"off"` |
| `config/checkpoint-features.toml` | `diff_bound_approval` | `false` |
| `config/checkpoint-features.toml` | `parallel_tests` | `false` |
| `config/checkpoint-features.toml` | `test_workers` | `0` |
| `config/mission-control.json` | `port` | `8765` |
| `config/mission-control.json` | `tagline` | `""` |

The starter files Comitas ships hold the values Comitas itself runs with, which are not always the defaults (for example `early_approval_reach` is `"medium"` and `parallel_tests` is `true` there). A project keeps the values it wants. An upgrade that adds an optional key announces it in the [changelog](CHANGELOG.md), and the default in the code keeps old project files working.

## The project's classification table

`ARTIFACT-CLASSIFICATION.md` has the format of the [framework's table](ARTIFACT-CLASSIFICATION.md), which defines the columns and the rules: the same header, rows sorted by plain ASCII ordinal Path, one backtick-quoted path per row, and the optional Historical paths table. Its own rules in addition:

- It may classify no path under `governance/` or under `project-governance/`, and not a historical path there either: the framework's table classifies all of both. A project row for a path the framework's table also names is a duplicate. The loader refuses the table at once.
- A row may name a profile of the framework or one the project added in `config/checkpoint-profiles.json`.
- Every file Git tracks needs a row in one of the two tables, and a new file needs its row in the commit that adds it. Continuity fails with the list of paths no row covers.
- Because the framework's table classifies the root files the tools own (`AGENTS.md`, `.gitignore`, `.gitattributes`), a project does not repeat them. A project that commits an export of the timing record (`docs/timings/<label>.jsonl`) adds that row; Comitas ships it.
- Change class, validation profile, boundary and the other columns mean what the framework's document says. A project with a runtime boundary marks its runtime and frozen paths in the Boundary column here.

`python -B governance/tools/show_classification.py <path> --checks` shows how any path resolves across the two tables and which checks it would select.

## The golden pin

`config/artifact-classification-golden.json` pins rows of the project's table independently of the document, so that a table edit alone cannot reclassify a path the project cares about. It is `{"version": 1, "paths": {...}}` with the keys of `paths` sorted by ASCII ordinal. Each entry is either the resolved row, with the keys `class`, `profile`, `boundary`, `bound`, `invariants`, `subclass`, `reachable`, `approval_sensitivity` and a `why`, or a refused path, `{"refused": true, "boundary": [...], "why": "..."}`, for a path that must stay unclassified or blocked. A path is pinned on one side only: a path the framework's golden pins is not pinned here. The conformance test of Comitas, which reads this file against the union of the tables, wants at least five entries. Change the pin in the same transaction as the row it pins; a project's own conformance tests may add lists of sensitive paths beside it.

## Selector expectations

`config/selector-expectations.json` lets a project assert which checks its paths select, so that a new row with the wrong profile fails instead of passing together with its matching golden pin. It is optional: only an absent file means no expectation. Anything at that name that is not a readable, good file (a directory, a link to nothing, an empty or unparsable file) fails the conformance test, and the conformance test refuses it otherwise, because a guard that is silently off guards nothing.

```json
{"version": 1, "expectations": [
  {"paths": "docs/api/*.md", "selects": ["continuity", "docs-lint"], "never": ["docs-build"], "why": "api pages are linted, not built"}
]}
```

- `paths`: a [git pathspec](https://git-scm.com/docs/gitglossary#def_pathspec) on one line, matched against `git ls-files` from the repository root. In the default syntax `*` also crosses `/`; use `:(glob)` for the strict form. It must match at least one tracked file.
- `selects`, `never`: lists of distinct check ids, each a command of the merged policy (the framework's or the project's), at least one in the two together and none in both. `selects` is a floor: each matched file must select at least those checks, and may select others. `never` is a ban: no matched file may select any of the named checks. Both are assertions about each file on its own, so one file that differs fails the expectation.
- `why`: optional text for the reader.
- `version`: `1` when present. Any other key is refused.

Each tracked file is judged by the selection the checkpoint tool makes for a change to that file alone, so its row, the profile, `narrowable` and the impact invariants of the project all count; the mandatory checks (`continuity`) are in it. A file with no usable row fails the expectation. A row whose Invariants cell is `symbol-map` is always judged with the whole envelope of its profile, whatever the working tree holds: the test switches the comparison with HEAD that picks mapped symbols off, so an edit never changes the verdict. A failure is one message per expectation naming each file with its profile and its selection (the first twenty, then a count). The test is `governance/config/conformance/test_selector_expectations.py`; it runs with the conformance folder, which any change under `governance/` or `project-governance/` selects, the project's table and profiles included. See [G056](DECISIONS.md#g056---a-project-may-declare-which-checks-its-paths-select-2026-10-23-accepted).

## The policy and the profiles

`config/checkpoint-policy.json` may add commands, required checks and prohibited components. `config/checkpoint-profiles.json` may add validation profiles and, under `impact.invariants`, impact invariants (`{"groups": [...], "requires": [...]}`, new names only, every group a command of the merged policy); a project profile may list under `narrowable` the checks of its own that the groups of a row's invariants replace, never a required check or a runtime gate. A row of the project's table may name such an invariant. Both are `{"version": N, ...}` with the same `version` as the framework's file of that name; a different version is refused.

| Key | In the file | Rule |
| --- | --- | --- |
| `commands` | policy | an object of command name to argument array (`{python}` stands for the interpreter); a name that the framework's policy uses is a clash and is refused |
| `required_checks` | policy | a list of command names that every transaction runs, added after the framework's |
| `prohibited_components` | policy | a list of path components no transaction may name, added after the framework's |
| `profiles` | profiles | an object of profile name to profile (`limitations`, `checks` as a list of `{id, argv}`, and for a runtime component its `runtime_checks`, each an exact policy command); a name the framework's profiles use (`record-only`, `tooling`, `governance`) is a clash and is refused |

Any other key is refused, so an overlay never changes a framework setting: the impact mapping, the commit class and subclass vocabulary and the policy keys `commit_metadata_required`, `link_retarget` and `timing_record` stay the framework's. A runtime profile declares its own gates; see [runtime profiles](CHECKPOINT-TOOL.md#runtime-profiles).

## The feature switches

`config/checkpoint-features.toml` holds the switches of the checkpoint tool, one key each, with a comment above it saying what it does. The tool reads it with `tomllib` from the tip of `main`, never from a working tree, so the first commit comes before the first governed work. The file is the project's, and a key it leaves out takes the default in the table above. Only the exact values below are on; a missing key, any other value or type, a duplicate key, or a file that is missing or does not parse means the feature is off, and off means the sealed Approve and fresh previews as before.

| Key | Value | What it does |
| --- | --- | --- |
| `early_approval_reach` | `"off"`, `"low"` or `"medium"` | how far a go at an eligible preview may approve the exact candidate: `"low"` carries only low-sensitivity paths, `"medium"` also medium ones; a high path never rides on a go |
| `diff_bound_approval` | `true` | an approval already given survives a moved `main` when every gate of the carry holds |
| `parallel_tests` | `true` | the test runner fans one check's tests out over worker processes |
| `test_workers` | a whole number of zero or more | the number of worker processes; `0` is one per core, and any other value runs the tests sequentially |

The file is of `high` approval sensitivity, so turning a feature on takes the sealed Approve; turning one off is the lead developer's manual commit, with the recipe in [feature switches](CHECKPOINT-TOOL.md#feature-switches).

## The devlog

A devlog is the project's historical, non-normative record of project-state transitions ([the project's conventions](../devlog/CONVENTIONS.md) say how an entry reads). This section is the one place that states what the tools enforce about it and how it is set.

### Settings

`config/devlog.json` is optional and so is every key in it: `{"version": 1, "folder": "devlog", "date_format": "%Y-%m-%dT%H:%MZ", "enabled": true}` is the same as no file. A file that is missing, unreadable or not a JSON object means the defaults. A key that is set must hold a good value, or the tools stop; a key they do not know is refused too, so a misspelt `enabled` cannot silently keep the log on.

- `version`: `1` when present.
- `folder`: one plain folder name at the repository root, the home of the entries, the conventions and the index.
- `date_format`: a `strftime` pattern in UTC that contains the year (`%Y`), reads back what it writes and leaves something to name a file by. The default is ISO-8601 to the minute with a literal `Z`: `2026-10-16T13:03Z`.
- `enabled`: a boolean. `false` means the project keeps no devlog: the continuity check then requires no entry beside the `implementation_checkpoint` pointer in the progress record and validates neither the folder nor its index, `folder` and `date_format` are ignored, and the Mission Control terminal shows an empty devlog that says so. Everything else the check verifies is unchanged.

To rename the folder, change `folder`, move the folder, change the paths of its rows in the project's classification table (and the pins of its golden file), the log documents that the Mission Control inputs file declares, and the links to the folder in the project's documents, all in one transaction.

### What the continuity check enforces

For every Markdown file directly in the folder other than `README.md` (the index) and `CONVENTIONS.md` (the project's conventions), when the devlog is enabled:

1. the entry is listed in the index, `README.md` of the folder, which must exist;
2. its first line is a title, `# ` and text;
3. it carries the metadata lines `Date:`, `Subject:`, `Status: Historical / non-normative` and `Provenance:`;
4. the value of `Date:` reads back exactly as the pattern writes it, and the file name starts with that date with every character other than letters, digits, dot, underscore and hyphen removed, followed by a hyphen (`Date: 2026-10-16T13:03Z` is the file `2026-10-16T1303Z-<slug>.md`);
5. it cites at least one commit, a full 40-character hash in backticks that exists in the repository or a [labeled commit](#citationsjson) of a declared repository, and every full hash it cites exists in the repository;
6. when the reconstruction contract declares an `implementation_checkpoint` fact, exactly one indexed entry carries the line ``Repository checkpoint: `<full hash>` `` for that commit, and its file name starts with the committer date of that commit in UTC written with the pattern.

The check also requires the index and the conventions to resolve their links like any document. Style, grouping and wording of entries are the project's own conventions; making any of the enforced keys configurable is not offered.

## Commit citations

### citations.json

`config/citations.json` is optional and so is every key in it: `{"version": 1, "external_repositories": []}` is the same as no file. A file that is missing, unreadable or not a JSON object means no label. A key that is set must hold a good value, or the tools stop; a key they do not know is refused too.

- `version`: `1` when present.
- `external_repositories`: a list of distinct labels, each one word of letters, digits, dots, underscores and hyphens. A declared label identifies repository citations checked for form only. The code default is no labels; the [adoption helper](CHECKPOINT-TOOL.md#the-adoption-helper) writes `template` into the file of a project made from Comitas, and a project adds its own labels beside it. See [the meaning of `template`](#the-template-label).

A commit of another repository is cited as the label, one space, the lowercase word `commit`, one space and the id in backticks, on one source line (a table row works): ``template commit `<id>` ``. The id is 7 to 40 lowercase hexadecimal characters and the label is matched exactly as declared. The rule has no special case:

- a labeled id whose label is declared is checked for its form only;
- a declared label followed by a backticked token that is not 7 to 40 lowercase hexadecimal characters fails ("label without a valid id");
- a labeled id whose label is not declared is checked like a local one: it must resolve in this repository, as must a bare id;
- a labeled commit counts as a citation of a devlog entry, but an implementation checkpoint entry still needs the ``Repository checkpoint: `<full hash>` `` line of a commit of this repository, and the Mission Control generator refuses a labeled id on that line.

### The template label

`template` names the framework's own repository by role. In Comitas it covers Comitas's own commits and, for history before Comitas, the private template archive holding the commits of 0.2.3 and earlier. In an adopting project it names the framework that project adopted. It does not mean only the archive, and a labeled id may also resolve to a commit in the current repository.

The framework writes its own commit citations as ``template commit `<id>` `` and a conformance test requires it. A project made from Comitas declares `template`, which the adoption helper writes before the project's first commit, so those citations are checked for their form without requiring the framework's history in the project. The declaration controls lookup as described [above](#citationsjson); whether a labeled id also exists locally does not change the label's meaning. See [G051](DECISIONS.md#g051---the-commit-of-another-repository-is-cited-by-a-declared-label-2026-10-20-accepted) and its [later explanatory context](RATIONALE.md#labeled-commit-citations-g051).

## The frozen manifest

`config/frozen-manifest.json` is optional: a project with no `frozen` row in its classification table needs none. It holds `{"version": 1, "files": {"<path>": "<sha256>"}}`, where each path is a file under a `frozen` row and the checksum is the SHA-256 of its bytes with CRLF read as LF, so a checkout's line-ending rules never change an entry. The continuity check requires that the files under the `frozen` rows are exactly the files of the manifest, in the working tree and in the index, and that each has the content its entry records. It does so whenever the classification table has a `frozen` row, whether or not the reconstruction contract declares the fact `frozen_contracts_and_fixtures_unchanged` and whether or not the project has a runtime boundary: the fact only reports the check in the reconstruction and is not what turns it on, so a project cannot leave frozen files unprotected by leaving it out. The `PASS` line of the check names `frozen invariants` only when the comparison ran. It reads no Git history, so a shallow clone gives the same answer. The file holds exactly the two keys and any other shape is refused; the paths are written in ascending order, with LF only.

Freezing is always allowed and takes the content as it is. `python -B governance/tools/validate_continuity.py --freeze` writes an entry, from the current content, for every frozen file that has none, whatever the history of the file, and prints what it added. It never changes or removes an existing entry; it names the entries that disagree with their file and leaves them. Only `--refreeze <path>`, given together with `--freeze` and repeated for each path, re-reads the named file into its entry, or removes the entry of a path that is no longer frozen. A path that is neither frozen nor in the manifest is refused. The manifest is a file of `project-governance/`, so the change that adds, changes or removes an entry is high approval sensitivity and takes the sealed Approve, however the command is run. Run the command in your own worktree, stage the manifest with the files it covers, and expect the check to refuse a frozen file that is staged differently from its working copy. See [G054](DECISIONS.md#g054---freezing-is-always-allowed-and-only-changing-a-freeze-is-restricted-2026-10-21-accepted).

## Mission Control inputs and port

These two files belong to the optional Mission Control [extension](extensions/README.md): without the extension they are not read, and removing the extension removes them.

`config/mission-control-inputs.json` is `{"version": 1, "datasets": {...}, "groups": [...]}` and lists the project's own documents that the terminal shows, with the same selectors as the extension's framework part: for each of the datasets `documents`, `decisions` and `rationale`, a `paths` list of exact documents, a `markdownDirectories` list of folders of Markdown documents and an `excludePaths` list. The two parts are merged as sets. The project may declare its own `project-governance/DECISIONS.md` as a decisions input and `project-governance/RATIONALE.md` as a rationale input; the decisions dataset then lists them beside the framework's registry. The devlog dataset comes from the devlog settings, not from this file. Unknown datasets and selectors are refused.

The optional `groups` key, `[{"label": "<group name>", "paths": ["<exact document path>", ...]}, ...]`, names the display groups of the Status briefing for the project's own documents; without it they all show under Other implementation. A group holds the documents whose exact path it lists, in the order the groups are declared, and only changes how the briefing is arranged. The groups of the project follow those of Mission Control and of the other installed extensions, which each declare theirs in their own manifest (the `group` of `extension.json`). Mission Control refuses, with one message and until the file is corrected, a label used twice in any of these sources, a path in two groups, a path that is not a relative forward-slash repository path and a path of the project that is not among the inputs of Mission Control (a document in a declared folder counts), and no group is shown meanwhile. The version stays 1.

`config/mission-control.json` may hold `{"version": 1, "port": 8765, "tagline": "Governed work for AI agents"}`. Every key is optional. Several projects on one host need distinct ports, because the server binds exclusively and stops with one line when the port is taken, without trying another. `--port` overrides the file. `tagline` is an optional single-line string, trimmed for display after the primary checkout folder name and a middle dot. A missing, empty or whitespace-only tagline shows only the folder name. The tagline is read from the primary checkout's setting, including when the server runs from a linked worktree; it is not extracted from the README or fetched from GitHub. A file that cannot be read, or that holds another key, an invalid port or a tagline of another type or containing a line break, means the defaults (port 8765 and no tagline), with the problem reported. The version stays 1.

## Rules, decisions and rationale

`CONSTRAINTS.md` holds the project's own MUST and MUST NOT rules. They add to the framework's [Constraints](CONSTRAINTS.md) and never override them; where the two disagree on the governed workflow, the framework's prevail.

`DECISIONS.md` is the project's decision registry. Its decisions take the prefix D and any free three-digit number, and need no relation to the framework's G-series ([G049](DECISIONS.md#g049---the-framework-numbers-its-decisions-with-g-and-a-project-numbers-its-own-with-d-2026-10-19-accepted)). A registry takes only its own prefix: a G decision in the project's file, or a D decision in the framework's, is refused with one message naming the file and the numbers. `RATIONALE.md` explains the decisions, with one audit row and one section per decision, whose heading names it after a colon (for example `## Checkpoints: D001`).

Each of the three is optional; a present one is read by the continuity check like any document, so its links must resolve and it must be reachable by links from `AGENTS.md`. A project that keeps them makes sure `AGENTS.md` points to them, as Comitas's does.

## The reconstruction contract

The contract defines what a fresh agent must recover from the repository ([Command continuity](COMMAND-CONTINUITY.md#layer-2-repeatable-reconstruction)). The framework's [part](config/reconstruction-contract-v1.json) holds the version, the bootstrap prompt, the required narrative sections and the evidence about the framework's own files. `config/reconstruction-contract.json` holds the project's part, `{"version": 1, ...}` with the same `version` as the framework's, and may set only:

| Key | Rule |
| --- | --- |
| `facts` | an object of fact name to `{"source": "docs/PROGRESS.md#recovery-facts", "pattern": "<regular expression with one group>", "type": "string"}`; the type is `string`, `integer` or `sum`, and a source `git:frozen-invariants` names the frozen-artifact check. A fact name the framework's part uses is a clash and is refused |
| `governance_evidence` | a list of documents the response must cite, added after the framework's |
| `completion_evidence_fact` | for a project with a runtime boundary, the name of the fact whose report section must stay anchored at the checkpoint commit; refused if the framework's part names one |

The bootstrap prompt and the required sections are the framework's alone. A project with a runtime boundary adds an `implementation_checkpoint` fact; [Adopting the recovery chain](COMMAND-CONTINUITY.md#adopting-the-recovery-chain) lists the steps.

## What the tools refuse

| Condition | Where | Result |
| --- | --- | --- |
| a project table row for a path under `governance/` or `project-governance/`, or a duplicate of a framework row | classification table | the loader refuses the table |
| a project profile or command that reuses a framework name | profiles, policy | refused, naming the clashing names |
| a key the overlay may not set, or a `version` that differs from the framework's file | policy, profiles, contract | refused, naming the file and the keys |
| a fact that reuses a framework fact name, or a second completion fact | contract | refused |
| a G-prefixed decision in the project's registry, or a D-prefixed one in the framework's | `DECISIONS.md` | refused with one message |
| an unknown key or a bad value in the devlog settings | `config/devlog.json` | the tools stop |
| an unknown key or a bad value in the citation settings | `config/citations.json` | the tools stop |
| a frozen file with no entry, an entry with no frozen file, a changed byte, or an index that differs from the manifest | `config/frozen-manifest.json` | continuity fails |
| a manifest that does not parse, or holds more than `version` (1) and `files`, or an entry that is not a path with a SHA-256 | `config/frozen-manifest.json` | continuity fails |
| a declared label followed by a backticked token that is not 7 to 40 lowercase hexadecimal characters | continuity | continuity fails |
| a labeled commit on a `Repository checkpoint:` line | Mission Control generator | refused with one message |
| a project document not reachable from `AGENTS.md`, or with a link that does not resolve | continuity | continuity fails |
| a selector expectation that is unreadable, has an unknown key or check, lists a check twice or in both lists, matches no tracked file, or is not met by a matched file | `config/selector-expectations.json` | the conformance test fails |
| a feature value that is not exactly one of the listed ones, or a file that does not parse | feature switches | the feature is off |
