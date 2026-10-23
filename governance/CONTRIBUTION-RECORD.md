# Contribution record up to template commit a7ab3f2

Status: frozen record, written on 2026-10-20. It covers the 60 commits on `main` from template commit `9c8446e`, the first, to template commit `a7ab3f2`, the last before the [contribution process](CONTRIBUTING.md) took effect. Later work does not edit it; a correction is a new dated entry in the [contribution register](CONTRIBUTION-REGISTER.md) that names what it corrects. It is a record, not a rule, and it establishes no ownership.

**The licensor** is the person named in the `Required Notice:` line of [NOTICE](NOTICE).

## Origin

The first commit, template commit `9c8446e`, extracted the framework from an earlier private project of the licensor and adapted it. The history of that project records work done with two AI tools, Anthropic Claude Code and OpenAI Codex, so text written with either tool there was carried into the framework. The exact source revision, how closely it is known, and the details of that history are kept in a private provenance record outside this repository. The commit ids that the [rationale](RATIONALE.md) cites as evidence refer to that project, as the [governance index](README.md#historical-records) says.

## Identities in the history

| Commits | Count | Author identity | `Worker:` trailer | `Co-Authored-By:` trailer |
| --- | --- | --- | --- | --- |
| template commit `9c8446e` to template commit `bf183c6` | 5 | the licensor | none | Claude Sonnet 5.5 |
| template commit `976f9ee` | 1 | the licensor | the licensor (a human worker) | none |
| template commit `a97e9fd` to template commit `4b08f91` | 12 | the licensor | Claude (template builder) | Claude Sonnet 5.5 on template commit `2170878` and template commit `f4a3329` only |
| template commit `d05a206` to template commit `c4259b1` | 25 | Template builder | Claude (template builder) | Claude Sonnet 5.5 |
| template commit `320fa75` to template commit `a7ab3f2` | 17 | the licensor | Anthropic Claude Code or Claude (template builder), with Claude Opus 5.5 or Claude Sonnet 5.5 | none |

The ranges are consecutive commits of `main`, oldest first. Every committer identity equals the author identity.

## What these identities show

- **The author field** is the Git identity recorded for a commit. It is evidence of recorded attribution, not of who wrote each line and not of legal ownership. Commits whose `Worker:` trailer names an AI tool carry the licensor's identity as author. For ten of the twelve commits from template commit `a97e9fd` to template commit `4b08f91`, earlier copies with the same subjects under the Template builder identity remain in the local repository: the author was changed when the messages were rewritten.
- **Template builder** is a neutral identity (`noreply@example.invalid`) used for commits made by AI worker sessions. It is not a person and not a contributor of its own; its work is covered by the entries for the licensor and the tool in the register.
- **The `Worker:` and `Co-Authored-By:` trailers** name the tool and model as declared when each commit was made. They are not authenticated. The convention then changed: `Co-Authored-By:` is refused from template commit `a936dd2` on, and `Worker:` alone names the agent.
- **The human part.** The history does not show which lines a human wrote, revised, selected or arranged. Commit messages and the rationale record the lead developer's decisions, and quote their words where they were recorded. The lead developer's approval of a commit is not evidence of authorship.

## Tools and agreements

Recorded in the [contribution register](CONTRIBUTION-REGISTER.md), entries R2 and R3, which state the account type and the provider agreements as of 2026-10-20. Codex appears in this history only through the material carried in from the earlier project; no commit of this repository up to template commit `a7ab3f2` names Codex.

## Third-party material at the cutoff

None was identified. This is the result of an inspection with stated limits, not a statement that there is none.

- **Inspected:** every file Git tracks at template commit `a7ab3f2`: the Python imports (the standard library and the framework's own modules only), the JavaScript imports (the framework's own modules, and Node.js built-in modules in the tests), the HTML and CSS (no external scripts, stylesheets, images or fonts; fonts are named by family and none is shipped), the shell scripts (they call Git, Python and standard shell utilities, and ship none of them), the dependency declarations (none: the tools need only Python and Git), attribution comments and license or copyright notices (none apart from LICENSE), and the files added and removed in this repository's history.
- **Found:** the checkpoint tool reference told the reader to install an optional dependency from a requirements file that the framework does not ship and that no tool uses. The instruction was removed by the commit that added this record.
- **Not done:** no comparison of the material with outside code or text, and no search for similar material. The earlier project's history was read only to trace the extraction.

## Unknowns

- Which parts of the material are human-authored, and to what extent.
- Whether any of the material resembles third-party work.

Stated by the licensor on 2026-10-20 and recorded as the licensor's statements (register entry R1): no employment, client, contract or confidentiality obligation applied to the material; nobody else contributed; the licensor was resident in the United States; the extraction was taken from a commit of the earlier project, and that project's first commits, which name no tool, were written by the licensor with OpenAI Codex.
