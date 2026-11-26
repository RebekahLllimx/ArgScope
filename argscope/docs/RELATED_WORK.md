# Related work

Inspected on 2026-11-23. These are related systems, not an exact existing implementation of ArgScope. No third-party source or benchmark data was copied into this release.

| Project | Relevant contribution | Difference from this release |
| --- | --- | --- |
| [LogicSkills](https://github.com/brianrabern/LogicSkills) | Formal symbolization, validity assessment and countermodel construction; a two-variable first-order benchmark verified with Z3 | Best candidate for later first-order evaluation. Its benchmark cannot be claimed as covered by a propositional engine. Data is CC-BY-4.0; code has a separate license file to inspect before reuse. |
| [PyArg](https://github.com/DaphneOdekerken/PyArg) / [documentation](https://daphneodekerken.github.io/PyArg/) | Computational argumentation, including abstract frameworks and ASPIC+ | Acceptance under attack/defeat semantics is a different question from classical entailment. Useful if defeasible reasoning becomes a separate extension. |
| [Arguinas](https://github.com/GyoukChu/Arguinas) | Argument reconstruction and LLM critical-thinking evaluation | Related to formalization proposals and reconstruction, not a replacement for a deterministic validity kernel. Reuse permission has not been established. |
| [Carnap](https://github.com/Carnap/Carnap) | Browser-based formal logic framework | Useful reference for logic interfaces and exercises; not a lightweight Python foundation for this implementation. |
| [Programming Z3](https://z3prover.github.io/papers/programmingz3.html) | Solver interfaces, models, assumptions and unsatisfiable cores | Candidate production backend. This release first establishes a transparent exhaustive oracle. |

The product contribution being explored is a reviewable argument document with dependencies, explicit formalization assumptions, local counterexamples and change impact. Existing logical solvers should be reused for larger fragments rather than reimplemented without a reason.
