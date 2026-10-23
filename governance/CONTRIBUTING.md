# Contributing to the framework

This document is about contributions to the framework itself, in Comitas's own repository: how a contribution is disclosed and recorded, and why the license alone does not settle what the licensor may do with it. A project that adopts Comitas keeps its own contribution rules, if it has any, in `project-governance/`; nothing here binds a project's own work. The binding lines are in [Constraints](CONSTRAINTS.md#licensing-and-provenance) and the decision is [G052](DECISIONS.md#g052---notices-travel-with-the-framework-and-contributions-are-recorded-in-files-2026-10-20-accepted). Nothing in this document is legal advice, and no statement in it has been reviewed by a lawyer.

**The licensor** below is the person named in the `Required Notice:` line of the repository's [LICENSE](../LICENSE) and of [NOTICE](NOTICE).

## The license and what it does not do

The framework is licensed under the PolyForm Noncommercial License 1.0.0, whose text is the repository's root LICENSE, under the Required Notice line. The license grants permissions under the copyright and patent rights that the licensor holds in the material; it says nothing about rights the licensor does not hold. In particular:

- It does not protect the governance method, the ideas or the workflow the documents describe; anyone may describe or build their own.
- It does not establish that every part of the material is protected by copyright. Material produced by an AI tool without enough human authorship may not be protected (see [AI-assisted contributions](#ai-assisted-contributions)).
- It does not establish who owns the material, and it is not a statement that the material is free of third-party rights.

The licensor may give separate permissions, such as commercial use, for the material the licensor holds the rights to. This repository makes no such offer and states no terms for one.

## Who contributes today

Today the only contributor is the licensor, working alone or through AI coding tools on the licensor's own accounts, as the [contribution register](CONTRIBUTION-REGISTER.md) records. Contributions from other people are not accepted until contributor terms are in effect: no contributor terms have been accepted by anyone, and none are in effect.

### Why the license alone is not enough for a contribution

A contribution offered under the framework's own license gives the licensor only what that license gives everyone: use for permitted purposes, with no right to sublicense. The licensor could then not offer anyone a commercial permission that covers the contributor's part, and could not use that part commercially either. Keeping that possibility needs a separate grant from each contributor, such as a license to the licensor that includes the right to sublicense, or an assignment.

A Developer Certificate of Origin sign-off does not provide that grant. It is a statement about where a contribution came from and that the contributor may submit it; it assigns nothing, and it grants no license beyond the one the project already uses, which its text describes as an open source license. The framework's commit messages also refuse a `Signed-off-by:` trailer ([the commit-message convention](CHECKPOINT-WORKFLOW.md#commit-message-convention)), and the records below are files, not trailers.

## What a contribution discloses

Every contribution is disclosed through the [contribution register](CONTRIBUTION-REGISTER.md), which holds standing entries. An entry is added only when something new enters:

- a new contributor: who is responsible for the work, and on what basis they may submit it;
- a new AI tool, a new kind of account (personal or organizational) or a new provider agreement: the tool, the account type, the agreement and its version or date when established;
- third-party material: what it is, where it came from, its license and notice, and what the license requires when the material is distributed.

Routine work by a registered contributor with a registered tool needs no entry: the commit is its record, and its `Worker:` trailer names the tool. An entry states what is known and lists what is not known as unknowns; it never fills a gap with a guess. An entry never holds a transcript, a credential, an account id, a payment record or other private account detail.

The [contribution record](CONTRIBUTION-RECORD.md) is a one-time record of the history up to the point where this process took effect. It is frozen: later work does not edit it, and a correction to it is a new dated entry in the register that says what it corrects.

## Third-party material

Material from anyone other than a registered contributor, including code, text, images or fonts, enters only with its license and notice in the same commit, kept beside it unchanged, and with its entry in the register. Its license must allow it to be distributed as part of the framework; material whose license is unknown does not enter. Its own notices stay with it in every copy, next to the framework's notice.

Software that the tools run but do not ship, such as Git, Python, Node.js or a shell, is not distributed with the framework and needs no entry.

## AI-assisted contributions

Three questions are separate, and none of them is answered by the others, by the lead developer's approval of a commit, or by the register:

1. **The provider's contract.** Each provider's terms say who receives the provider's rights in the output. The register records which agreement applied, with its version or date.
2. **Copyrightability.** Whether output is protected depends on human authorship, under the law of the place where protection is claimed. As of 2026-10-20, in the United States, the Thaler v. Perlmutter decision (D.C. Circuit, March 18, 2025; certiorari denied March 2, 2026) holds that the Copyright Act requires a human author, and the Copyright Office's report on copyrightability (Part 2 of its report on copyright and artificial intelligence, January 29, 2025) concludes that AI output is protected only where a human author determined sufficient expressive elements, which can include creative arrangement or modification of the output but not the mere provision of prompts. Other jurisdictions were not reviewed.
3. **Third-party rights.** Output may resemble existing material. No check of the framework's material against other works has been made.

The register says where the human part of the work lies (writing, substantive revision, selection and arrangement) only as far as the evidence shows it, and claims no more than that.
