# Contribution register

Status: append-only record of the framework's contributors, AI tools, accounts, provider agreements and third-party material. The [contribution process](CONTRIBUTING.md) says what it is for; the rule is in [Constraints](CONSTRAINTS.md#licensing-and-provenance).

**How it changes**, as [the rule](CONSTRAINTS.md#licensing-and-provenance) and the contribution process say: an entry is added, with its date and the next free number, only when a new contributor, a new AI tool, a new kind of account, a new or changed provider agreement, or third-party material enters. Routine work covered by the standing entries adds nothing. An entry is never edited or deleted: a later entry supersedes or corrects it and names it. An entry records what is known, lists what is not known as unknowns, and holds no transcript, credential, account id, payment record or other private account detail.

**The licensor** is the person named in the `Required Notice:` line of [NOTICE](NOTICE). The history up to template commit `a7ab3f2` is described once, in the frozen [contribution record](CONTRIBUTION-RECORD.md).

## R1 - Contributor: the licensor (2026-10-20)

- **Who:** the licensor, the human responsible for every contribution to date, working alone or through the AI tools of R2 and R3.
- **Basis for submitting:** the licensor's own work, and the work of AI tools used on the licensor's own accounts.
- **Stated by the licensor on 2026-10-20:** no employment, client, contract or confidentiality obligation applied to the material; nobody else has contributed to it; the licensor was resident in the United States. These are the licensor's statements; the repository cannot show them either way.

## R2 - Tool: Anthropic Claude Code (2026-10-20)

- **Use:** writes and revises the framework's documents, code and tests in worker sessions; named in the `Worker:` trailer of a commit, and before the current convention also in `Co-Authored-By:`.
- **Account:** a personal consumer subscription, per the licensor's statement of 2026-10-20 that the accounts were personal, bought by the licensor alone, and used without API keys. This register records no plan, account or payment detail.
- **Agreement:** Anthropic's Consumer Terms of Service, effective October 8, 2025, as published at `https://www.anthropic.com/legal/consumer-terms` and read on 2026-10-20. Anthropic's Claude Code documentation (`https://code.claude.com/docs/en/legal-and-compliance`, read the same day) says that use of Claude Code by Free, Pro and Max users is subject to those Consumer Terms. In those terms Anthropic assigns to the user its right, title and interest, if any, in outputs, subject to the user's compliance with the terms, and disclaims any warranty of non-infringement.
- **Configuration:** the model is the one the `Worker:` trailer of each commit names, when it names one.
- **Unknowns:** none recorded.

## R3 - Tool: OpenAI Codex (2026-10-20)

- **Use:** wrote governance text with the licensor in the earlier project from which the framework was extracted (see the [contribution record](CONTRIBUTION-RECORD.md#origin)), including that project's first commits, which name no tool (the licensor's statement of 2026-10-20); registered now so that a later Codex worker on the framework is covered from its first commit.
- **Account:** a personal consumer subscription through a ChatGPT plan, per the licensor's statement of 2026-10-20, without API keys. OpenAI's Codex pricing page (`https://developers.openai.com/codex/pricing`, which led to `https://learn.chatgpt.com/docs/pricing` when read on 2026-10-20) says that Codex is included in the ChatGPT plans, from Free to Enterprise.
- **Agreement:** OpenAI's terms for individuals depend on residence, and the licensor was resident in the United States (R1), so OpenAI's Terms of Use applied: effective January 1, 2026 and still current when read on 2026-10-20 (`https://openai.com/policies/terms-of-use/`, whose text was read through the same page published at `https://openai.com/policies/row-terms-of-use/`), so in force for all the Codex work. They include the Service Terms (`https://openai.com/policies/service-terms/`, updated October 16, 2026), whose section on Codex says that code generation output may be subject to third-party licenses, including open source licenses. Under the Terms of Use the user owns the output and OpenAI assigns its right, title and interest, if any, in it, and output may not be unique.
- **Unknowns:** the text of the Service Terms in force when Codex was used, which was a version before October 16, 2026 and was not read.

## R4 - Third-party material: none identified (2026-10-20)

- **State:** no third-party material was identified in the files tracked at template commit `a7ab3f2`. The inspection, its scope and its limits are in the [contribution record](CONTRIBUTION-RECORD.md#third-party-material-at-the-cutoff).
- **Not distributed:** Git, Python, Node.js and the shell utilities the tools call are run by the tools, not shipped with them.
