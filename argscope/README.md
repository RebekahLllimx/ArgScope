# ArgScope

This is the propositional checking component. The [research workbench](../README.md) adds original-paper review, human judgments and a separate restricted first-order adapter. Commands below run from this component directory.

ArgScope checks explicitly formalized arguments and explains where a chain of reasoning fails. It distinguishes a valid inference, a counterexample, inconsistent premises, a blocked downstream step, and an inconclusive resource-limited check.

## Try it

Python 3.10 or later; no external packages are required for either propositional backend.

```bash
python -m unittest discover -s tests -v
python -m argscope examples/valid_chain.json --changed P1
python -m argscope examples/invalid_chain.json --html outputs/invalid.html
python -m argscope examples/inconsistent.json
python -m argscope examples/valid_chain.json --backend dpll
```

The HTML command creates a new report and refuses to overwrite an existing file. Open the generated HTML in a browser. Invalid arguments are ordinary analysis results, not command failures; malformed documents exit with code 2.

## What is implemented

- An immutable syntax tree and explicit parser for `~`, `!`, `&`, `|`, `->`, `<->`, parentheses, `TRUE`, and `FALSE`; Unicode equivalents are accepted.
- Precedence: negation, conjunction, disjunction, implication, biconditional. Implication is right-associative; biconditional chains require parentheses.
- Exhaustive classical propositional evaluation with independently inspectable counterexamples.
- Tseitin CNF and deterministic DPLL with unit propagation, conflict pruning, an explicit backtracking stack and resource limits; SAT models are checked against the original AST.
- Subset-minimal inconsistent-premise explanations. These need not have minimum cardinality.
- Dependency validation, topological checking, blocked-step propagation, and transitive change impact.
- Escaped HTML explanations and machine-readable JSON results.

Every step is checked against its declared immediate dependencies. Other premises in the document are not silently added. Validity is conditional on those assumptions, not evidence that they are true.

## Scope

The reference engine handles propositional formulas, up to 12 variables by default and an explicit maximum of 16. Explicit `--backend dpll` defaults to 64 original variables and adds encoding, decision and literal-inspection bounds. It does not automatically formalize natural language, evaluate factual truth, or implement first-order/modal semantics. Crossing a configured variable bound returns `unknown`. Dependency impact is implemented; a persistent incremental solver cache is not yet implemented.

The retained DPLL evidence includes 1000 generated small arguments and independent tiny-CNF enumeration. The sibling workbench provides editable source/argument review and a separately implemented [restricted first-order adapter](../workbench/docs/FIRST_ORDER_GUIDE.md). This component remains propositional. The [application report](../workbench/docs/FINAL_REPORT.md) distinguishes formula tests, software acceptance and model interpretation results.

[Engineering design](docs/ENGINEERING_DESIGN.md) · [Related work](docs/RELATED_WORK.md)
