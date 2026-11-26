# Logic guide and worked cases

The application checks a declared mapping, not prose by itself. Source fidelity,
formal validity and premise credibility remain separate judgments. The
[technical report](FINAL_REPORT.md) explains the cases; this guide gives their
syntax, calculations and evidence boundaries.

## 1. Select the representation

A legacy propositional mapping has `formulas` keyed by node IDs and a rationale.
The first-order variant additionally requires
`"semantics": "classical-first-order-v1"`. The corresponding qualification uses
`representation: first_order` and a scope rationale. An `unencoded` qualification
prevents an executable verdict for that reading, even if mapping approval is set.

Model-generated and authored mappings use the same validator. The `papers` mode
contains deliberately curated candidates; these are not unprompted model outputs
or independent reference judgments.

## 2. Propositional path: Chalmers

For the displayed argument in section 2 of *The Singularity*, let p mean AI+
will occur and q mean AI++ will occur:

```text
P1: p
P2: p -> q
C:  q        depends on P1, P2
```

The backend parses immutable formula ASTs and uses Tseitin CNF with bounded,
deterministic DPLL. Unit propagation applies forced literals; conflicts prune
assignments and branches retain alternatives explicitly. Returned assignments
are projected to the original atoms and independently evaluated on the original
AST. Exhaustive checking remains available in the component tools as a small-
instance reference; the review workflow uses DPLL.

For this tiny example the recorded result can be obtained through propagation
without branching. It is not evidence of a measured branching-speed improvement.
Removing P2 creates a labelled damaged control with `p=true, q=false` as a verified
countermodel. Neither calculation proves the prediction premises.

## 3. First-order syntax and semantics

| Construct | Syntax |
| --- | --- |
| Universal / existential quantification | `forall x. F`, `exists x. F` |
| Negation | `~F` |
| Conjunction / disjunction / implication | `F & G`, `F | G`, `F -> G` |
| Unary / binary predicate | `W(x)`, `R(x,y)` |
| Identity | `x = y` |
| Grouping | `(F)` |

Predicates begin uppercase; bound variables are lowercase. Bind every term.
Quantifier bodies extend to the right; implication is right-associative.
Parenthesize quantified subformulas when combining them. The interpretation is
one nonempty classical object domain, ordinary equality, and complete predicate
interpretations. There are no constants, functions, free variables, arithmetic,
modal operators or free-logic semantics in this backend.

```json
{
  "semantics": "classical-first-order-v1",
  "formulas": {
    "P1": "~(exists x. W(x))",
    "C": "forall x. ~W(x)"
  },
  "rationale": "W is a unary predicate; all terms are bound."
}
```

Keys are node IDs, not a dictionary defining predicate meanings. Explain those
meanings in prose/rationale. Editing the mapping changes the reviewed content.

## 4. Quine: a negative unique description

The current example selects two short phrases from Quine's Russell discussion
of Waverley, explicitly joined by an editorial newline. The source record links
to the complete paragraph in the [Colorado transcription, p.4](https://rintintin.colorado.edu/~vancecd/phil375/Quine.pdf).
The denial of unique authorship is a conditional supposition, not Quine asserting
a historically false claim about the book.

Let W(x) mean x wrote Waverley:

```text
A: ~(exists x. (W(x) & forall y. (W(y) -> y = x)))

B: (forall x. ~W(x)) |
   (exists x. (W(x) & exists y. (W(y) & ~(x = y))))
```

A denies exactly one W object. B permits zero W objects or at least two distinct
ones. Q1 checks A entails B; Q2 checks B entails A. Both give UNSAT for premises
and negated conclusion with consistent premises.

Q3 intentionally replaces B with `forall x. ~W(x)`. Two distinct W objects make
A true and that shortcut false. Q4 adds the explicit at-most-one-W assumption:

```text
forall x. forall y. ((W(x) & W(y)) -> x = y)
```

Only with that extra premise does the shortcut follow. It must be labelled and
justified if used in a reconstruction. The demonstration does not assume an
individual name denoting an author, and does not establish Quine's general
account of meaningfulness merely by validating this equivalence.

## 5. First-order execution

The adapter parses a project-owned AST, checks predicate arity across formulas,
and translates to a separate Z3 context. It tests premise consistency before
adding the negated conclusion.

For an UNSAT entailment query it returns trusted solver evidence: backend/version,
input digest, resource limits and a proof digest/preview. The preview is capped
at 16,000 characters; the full proof's digest may outlive its truncated text.
No independent proof checker is implemented.

For SAT, a finite interpretation is materialized from the solver's model. The
export limit is eight objects. Small subdomains may be tried, but are never
assumed to preserve quantified formulas: every original AST is evaluated again
on the complete finite tables. Only a validated countermodel is reported as such.
If none is obtained within the materialization bound, the result remains unresolved.
It does not become valid because a small-model search failed.

Each query has a 1200 ms timeout and 100,000 resource-unit cap. Parsing is bounded
by 4000 characters, 600 tokens and nesting checks; at most twelve predicates are
accepted across a check. The enclosing tool process has its own five-second
limit, so a large dependency graph may reach an outer limit before all per-step
queries finish.

## 6. Interpret outcomes

| Outcome | Meaning |
| --- | --- |
| `no_counterexample_complete` | Declared entailment checked: propositional completion or trusted first-order UNSAT evidence |
| `witness_found` | A propositional assignment or finite first-order model falsifies the original inference and passed its AST validator |
| `inconsistent_assumptions` | Premises are inconsistent; not a substantive endorsement |
| `budget_exhausted` | Unknown/resource limit or no validated bounded model; inspect the reason |
| `blocked` | A prerequisite inference failed or was unresolved |
| `incomplete_mapping` | Not every ancestor of the target has a formula |
| `no_formal_mapping` | The prose reading has no executable mapping |
| `unsupported_semantics` | A declared essential qualification is unencoded |
| `review_required` | The final check lacks required reader judgments/mapping approval |

Audit results remain `human_approved: false`. They are useful feedback before a
reader commits to the interpretation. Final results use content-bound judgments;
a solver outcome cannot substitute for those decisions.

## 7. Reproduce and inspect

From the root after setup:

```bash
export PYTHONPATH="$PWD/workbench:$PWD/argscope:$PWD/falsifylab:$PWD/sourcelens"
.venv/bin/python workbench/tools/check_reference_cases.py
```

[Reference JSON](evidence/reference_checks.json) contains Q1–Q4 and S1–S2. Runtime
report packets retain complete bounded result records and can be replayed with
`python -m workbench.review_app verify-report FILE`. Re-verification is not source
or reviewer authentication and may depend on compatible solver/tool versions.

Backend references: [Z3 quantifiers](https://microsoft.github.io/z3guide/docs/logic/Quantifiers/)
and [resource/proof parameters](https://microsoft.github.io/z3guide/programming/Parameters/).
