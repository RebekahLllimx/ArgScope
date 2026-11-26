# ArgScope propositional engine design

This component implements bounded propositional checking and inference-DAG analysis. It provides immutable formula ASTs, an exhaustive reference, a Tseitin/DPLL backend and dependency-impact queries. The current review application reuses these deterministic tools; its separate first-order adapter and reader workflow are described in the [application design](../../workbench/docs/ENGINEERING_DESIGN.md). Natural-language reconstruction and global term-sense editing are not responsibilities of this kernel.

## 1. Problem and requirements

An argument can fail because its conclusion does not follow, its assumptions contradict one another, or it relies on an earlier unsupported step. A single valid/invalid label hides these differences. ArgScope lets a reader inspect an inference and trace its consequences through a reasoning chain.

The user supplies a reviewed formalization. Whether a formula represents the author's intended claim and whether the formal inference is valid are separate questions. The current engine evaluates the second.

| Requirement | Observable behavior |
| --- | --- |
| Check an inference | Return validity or an assignment making all premises true and the conclusion false |
| Diagnose contradiction | Return a subset-minimal inconsistent set of declared premises |
| Check a chain | Validate references, order steps by dependency and block steps relying on failed checks |
| Inspect edit impact | List the changed inference and downstream inference descendants in dependency order |
| Explain a bounded check | Return `unknown` when the variable limit is exceeded |
| Produce usable artifacts | Emit JSON and an escaped, standalone HTML report |

Non-functional requirements are deterministic evaluation, bounded input, no execution of formula text, no network dependency and a testable logical core. A database, web service and concurrent scheduler are outside the present requirements.

## 2. System architecture

```text
JSON document
    │
    ├── graph.py: validate nodes and references; reject cycles
    │       └── topological inference order
    │                   │
    └── logic.py: tokenize → parse → immutable AST → exhaustive evaluator
                        │
                 structured results
                        │
          __main__.py: JSON output / HTML rendering
```

`logic.py` knows formulas and assignments, not node identifiers or files. `graph.py` owns dependency semantics and translates premise indices in inconsistency explanations into graph identifiers. The CLI owns input size checks, filesystem errors and presentation. There is no global mutable argument graph.

`analyze_document(..., backend="dpll", max_variables=64)` selects the implemented SAT backend without changing dependency semantics. The exhaustive backend remains the default and the small-instance oracle. CLI `--backend dpll` selects a 64-variable default, with explicit resource limits.

## 3. Interfaces and data design

### Argument document

```json
{
  "title": "A policy argument",
  "premises": [
    {"id": "P1", "formula": "p -> q", "label": "Conditional claim"},
    {"id": "P2", "formula": "p"}
  ],
  "steps": [
    {"id": "S1", "formula": "q", "depends_on": ["P1", "P2"]},
    {"id": "S2", "formula": "q | r", "depends_on": ["S1"]}
  ]
}
```

Identifiers are unique, nonempty strings of at most 80 characters. Dependencies are unique identifiers and may point forward in the input, but must resolve to an acyclic graph. Premises cannot have dependencies. A step with no dependencies checks a tautology.

Each inference uses **only its declared immediate dependencies**. Other premises in the document are not silently added. A valid step can support later steps, conditional on the assumptions of the chain; the real-world truth of those assumptions is not evaluated.

Optional `symbols` metadata is accepted, but does not change formula interpretation and is not displayed by the present HTML renderer.

### Logical interface

| Function | Contract |
| --- | --- |
| `parse(source) -> Formula` | Parse a complete formula or raise `FormulaError` |
| `Formula.variables() -> set[str]` | Collect propositional variables |
| `Formula.evaluate(assignment) -> bool` | Evaluate a complete assignment |
| `check_argument(premises, conclusion, max_variables=12)` | Classify an inference and return its witness or diagnostic |
| `analyze_document(document, max_variables=12, backend="exhaustive")` | Validate the graph and check inference steps |
| `check_argument_dpll(premises, conclusion, limits=Limits())` | Tseitin/DPLL classification, projected witness, bounds and deterministic observations |
| `affected_steps(document, changed_id)` | List inference descendants, including the changed node if it is an inference |

`Formula` is a frozen dataclass with tuple children: an immutable syntax tree, not an executable expression.

### Result states

| State | Meaning | Usable dependency? |
| --- | --- | --- |
| `assumed` | A supplied premise | Yes |
| `valid` | No counterexample in the checked propositional space | Yes |
| `invalid` | A returned assignment makes the inference fail | No |
| `inconsistent_premises` | No assignment satisfies the declared premises | No |
| `unknown` | The variable bound prevents a check | No |
| `blocked` | A declared dependency is unusable | No |

Premises have internal `assumed` results. The document report contains inference results, topological order and `all_steps_valid`. An empty step list does not count as a completed valid argument.

## 4. Algorithms and technical decisions

### Explicit parsing

Recursive descent implements precedence from strongest to weakest: negation (`~`, `!`), conjunction (`&`), disjunction (`|`), implication (`->`) and biconditional (`<->`). Implication is right-associative; biconditional chains require parentheses.

Parentheses, `TRUE` and `FALSE` are supported. Unicode connectives are normalized before tokenization. Variables use `[A-Za-z][A-Za-z0-9_]*` and are case-sensitive; constants are uppercase. Unexpected characters, missing operands and trailing tokens are rejected. Python `eval` is never used.

### Consistency before entailment

The checker first searches for an assignment satisfying all premises. If none exists, it reports inconsistency instead of presenting classical explosion as useful support for an arbitrary conclusion. This is a reporting decision, not a change to connective truth tables.

An inconsistent core is reduced by deleting each premise when inconsistency remains. Every remaining member is necessary for that returned core. Another deletion order may yield a different core: the result is subset-minimal, not necessarily smallest by cardinality.

In the exhaustive backend, consistent-premise assignments are enumerated until a counterexample is found or the space is exhausted. Sorted variable names and false-before-true enumeration make witnesses deterministic.

### Implemented Tseitin/DPLL backend

`Formula` is the original immutable AST, not a new natural-language parser.
Tseitin assigns original names stable integer IDs and fresh auxiliary IDs for
connective equivalences, then asserts all root formulas. Identity memoization
avoids repeated traversal of shared AST objects. The CNF size is linear in AST
size. DPLL repeatedly propagates units, prunes empty-clause conflicts and stores
unexplored branches on an explicit stack; original-name projection is checked
against the original AST before a model is returned.

Limits are 64 original variables, 2048 AST nodes, 8192 clauses per query, 100000
decisions and 1000000 literal inspections per argument, shared across consistency,
countermodel search and core reduction. Bounds produce unknown; an already known
inconsistency can retain an explicitly not-minimized core after a reduction bound.
No external UNSAT certificate is produced. Worst-case search remains exponential.
The prototype separately supervises calculation children with a wall-clock bound.

### Graph operations and complexity

Kahn's algorithm validates acyclicity and produces an order in `O(V + E)`. Breadth-first traversal finds edit impact in `O(V + E)`. A failed inference blocks downstream checks rather than becoming an established premise. Impact analysis identifies work to rerun; it is not a persistent incremental cache.

For `n` variables and total formula size `L`, one truth-table pass costs `O(2^n L)`. Inconsistency reduction can require one additional pass per premise. The default 12-variable limit and explicit maximum of 16 make this tradeoff visible. Input bounds are not wall-clock deadlines; hosted checks would also need cancellation and process limits.

## 5. Testing and validation

Run from the project root:

```bash
python -m unittest discover -s tests -v
python -m argscope examples/valid_chain.json --changed P1
python -m argscope examples/invalid_chain.json --html outputs/review.html
```

The HTML destination must be new; existing files are not overwritten.

| Test group | What it establishes |
| --- | --- |
| Standard forms | Expected valid/invalid classifications |
| Independent Boolean functions | Operator semantics checked against separately expressed truth functions |
| Counterexamples | Reported assignments make all premises true and the conclusion false |
| Inconsistent cores | Removing any returned member restores satisfiability |
| Parser and limits | Unicode, constants, precedence, malformed text and `unknown` at the bound |
| Graph integrity | Cycles, missing references, duplicate IDs and forward references |
| Propagation and impact | Downstream blocking and transitive descendants |
| Presentation | Escaping of user-controlled HTML text |

The suite now has 21 test methods: the original 13 plus eight SAT/CLI methods, including 1000 fixed-seed differential arguments and independent tiny-CNF extension checks. It validates local contracts, not external first-order benchmark performance. See [Related work](RELATED_WORK.md) for the distinctions between this kernel, first-order tasks and defeasible argumentation.

## 6. Deployment and operations

Python 3.10+ is sufficient. No external packages, credentials or network are required. JSON goes to stdout; malformed input and filesystem failures go to stderr with exit code 2. An invalid argument is a successful analysis, not a command failure.

The CLI caps documents at 100 KB. Graph validation allows 80 nodes and 32 direct dependencies per inference. Parsing allows 1000 source characters and 160 tokens per formula. Variable bounds are also enforced when the checker is called without the CLI.

HTML is a static artifact with escaped text and exclusive file creation. Reports include the supplied formalization, so publication is a separate decision. There is no telemetry collector or background monitor.

## 7. Limitations and extension boundaries

- Propositional validity does not establish soundness, factual accuracy or faithful text interpretation.
- Quantifiers, identity, modal worlds and defeasible reasoning require new semantics, not extra parser keywords.
- A public service would need authentication, quotas and process-level resource limits.
- A SAT/SMT backend should preserve witness, inconsistency and `unknown` contracts and be differentially tested against the reference evaluator.
- The sibling workbench prototype now stores source-linked reconstruction snapshots, invalidates content-bound reviews on edits and provides a local editor. General semantic interpretation and a persistent incremental solver cache remain future work.
