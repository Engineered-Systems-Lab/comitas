# Devlog conventions

These authoring rules implement [G029](../governance/DECISIONS.md#g029-readme-and-normative-document-extension-2026-10-07). They are the project's own style: rewrite them freely to suit how your project writes its history. What the continuity check enforces about the devlog, and the settings that name its folder and its date format or switch it off, belong to the framework and are written once in [the devlog section of Project governance files](../governance/PROJECT-GOVERNANCE.md#the-devlog); an entry that follows this style and meets that minimum passes. The [devlog index](README.md) remains the historical entry point; current project state belongs to [Progress](../docs/PROGRESS.md).

## Entries

An entry records a meaningful project-state transition. Git records individual changes. Worker threads are implementation mechanics, not historical milestones.

Each entry should explain what changed in project state, why it mattered, notable discoveries or reviews, and the evidence and checkpoints establishing it. Do not create one entry per thread or review invocation. Group activity around the state transition it produced; commits are preferred evidence anchors, not a requirement for every event. Preserve contemporary mistakes, uncertainty and conclusions; label later context explicitly, following [Historical Truth](../governance/CONSTRAINTS.md#first-duty--historical-truth).

Every entry is one Markdown file in the devlog folder, listed in the index. Its first line is a title (`# ...`) that states the event, and it carries the metadata lines of the minimum, each on its own line: `Date:`, `Subject:`, `Status: Historical / non-normative` and `Provenance:`. Name each commit it cites as a full 40-character hash in backticks, usually on a `Repository checkpoint:` line when the entry anchors an implementation checkpoint. A commit of another repository that the project declared in `config/citations.json` is named `<label> commit` and its id in backticks (7 to 40 characters); it counts as a citation but never anchors a checkpoint.

## The date

The date is UTC and never local time. With the default pattern it reads `Date: 2026-10-16T13:03Z`, and the filename starts with the date and describes the event: `2026-10-16T1303Z-first-release.md`. Use the event or report time when evidence supports it, not the later migration time. Record timestamp provenance and uncertainty. If the date is unknown, do not fabricate one or substitute the current time: keep an undated descriptive draft outside the folder, explain the date evidence available, and report the missing precision before giving the entry a dated filename.

## Follow-up

Recording or committing the historical record of an already documented transition does not itself require another entry. Create another entry only for a distinct meaningful project-state transition.

For separately delegated post-commit record preparation and the runtime continuity-follow-up-pending boundary, follow the authoritative [checkpoint workflow](../governance/CHECKPOINT-WORKFLOW.md). The [index](README.md) remains historical and non-normative; it does not grant staging or commit authority.
