# Project artifact classification

The project's own table: what its paths are and which validation a change to them needs. It has the format of the framework's [artifact classification](../governance/ARTIFACT-CLASSIFICATION.md), which defines the columns and the rules, and the tools read the two tables as one: the framework's rows for everything under `governance/` and for the few files the tools own at the root, these rows for everything else. This table may not classify a path under `governance/` or under `project-governance/`: the loader refuses it, because the framework's table classifies all of both, and the project folder is graded `high` there since its files decide what a go may carry. A row may name a profile of the framework or one the project added in `config/checkpoint-profiles.json`.

Comitas ships the rows of its own documents. A project that starts from it keeps what applies and adds one row for each path of its own; every file Git tracks needs a row in one of the two tables, and a new file needs its row in the commit that adds it. An upgrade never touches this file.

## Rows

| Path | Class | Profile | Boundary | Bound | Invariants | Subclass | Reachable | Approval-sensitivity |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `.github/FUNDING.yml` | tooling | tooling | - | - | guidance | - | - | - |
| `LICENSE` | record | record-only | - | - | - | - | - | - |
| `README.md` | record | record-only | - | - | guidance | - | yes | - |
| `devlog/*.md` | record | record-only | - | - | - | devlog | - | - |
| `devlog/CONVENTIONS.md` | governance | governance | - | yes | guidance | - | yes | - |
| `devlog/README.md` | record | record-only | - | - | - |  | yes | - |
| `docs/ARCHITECTURE.md` | governance | governance | - | - | guidance | architecture | yes | - |
| `docs/PLAN.md` | record | record-only | - | yes | guidance | plan | yes | - |
| `docs/PROGRESS.md` | record | record-only | - | yes | - | progress | yes | - |
| `docs/timings/*.jsonl` | record | record-only | - | - | - | - | - | - |

## Historical paths

A deleted file keeps its Class and Subclass here, as the framework's [table](../governance/ARTIFACT-CLASSIFICATION.md#historical-paths) describes.

| Path | Class | Subclass |
| --- | --- | --- |
| `REFACTORING-NOTES.md` | record | - |
