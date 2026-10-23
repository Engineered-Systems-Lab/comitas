# Fresh-agent recovery prompt v1

You are joining this repository with no conversational history. Read AGENTS.md first, then follow its documentation authority chain. Reconstruct current project state, governing constraints, latest completed implementation checkpoint, validation evidence, unresolved/pending work, and the next authorized action using repository evidence only. Make no repository changes; do not stage, commit, run builds/services, or inspect transient work/, caches, or generated outputs. Git read-only inspection is allowed. Do not use network or earlier conversations.

Read governance/COMMAND-CONTINUITY.md, governance/config/reconstruction-contract-v1.json and, if the repository has it, project-governance/config/reconstruction-contract.json for the output contract (the two parts together are the contract). Derive facts from the specified authoritative source sections and Git; do not treat historical pre-commit wording as current status. Preserve evidence limitations and distinguish recorded execution from independently rerun checks. Explain any conflict or missing evidence rather than inventing a value. A failed contract is preferable to an unsupported claim.

Return one JSON object with exactly these keys:

- "contract_version": integer 1.
- "facts": every named contract fact, each as {"value": typed value, "evidence": [repository-relative source references]}. Use the contract's source reference for the extracted fact. For frozen invariants cite "git:frozen-invariants"; this denotes comparison of the frozen paths with the frozen manifest by the deterministic verifier.
- "sections": each named required section as {"text": nonempty explanation, "evidence": [repository-relative file or file#heading references]}. Cite all five governance_evidence files across these sections. State pending work and boundaries explicitly; do not infer new authorization from a plan.

Do not include Markdown fences in the JSON response. Natural-language wording is free; facts, types, source references and structural requirements are machine-checked. The operator saves your response outside the repository and validates it. If a fact cannot be established, explain it in limitations and use null for that value; validation will fail visibly.
