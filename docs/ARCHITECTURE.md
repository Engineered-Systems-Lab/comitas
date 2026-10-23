# Architecture

Status: template placeholder. Replace this document with the architecture of your project.

This is the document the governance rules call the architecture: the current, authoritative description of what the project is made of, which parts own which facts, and where the boundaries between parts lie. [Constraints](../governance/CONSTRAINTS.md) and [Decisions](../governance/DECISIONS.md) are read with it, and historical [devlog entries](../devlog/README.md) never override it.

## What to write here

- What the project is and what it is not, in a few sentences.
- The parts (components, services, libraries) with one line each on their responsibility and their owner.
- The boundaries that matter: what may depend on what, which parts are frozen and which may change, and which paths form the runtime boundary that the [artifact classification table](../governance/ARTIFACT-CLASSIFICATION.md) marks.
- Anything a fresh worker must know before touching a part, linked to the part's own specification.

Keep it current. History belongs in devlog entries and in Git, plans in [Plan](PLAN.md), and verified state in [Progress](PROGRESS.md).
