"""Explicitly authored paper demonstrations, independent of model availability."""
from .providers import DemoProvider
from .research_examples import research_examples
from .schemas import Request


class PaperExamplesProvider:
    name = "authored-paper-examples-v1"
    live = False
    supports_research_examples = True

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        if operation == "revise" and any(i.get("kind") == "reader_feedback" for i in issues or []):
            return DemoProvider().generate(operation, request, draft, issues, evidence)
        cases = {
            c["id"]: Request.model_validate(c["request"]).model_dump(mode="json")
            for c in research_examples()
        }
        request = Request.model_validate(request).model_dump(mode="json")
        case = next(
            (
                key
                for key, value in cases.items()
                if value["question"] == request["question"]
                and value["sources"] == request["sources"]
            ),
            None,
        )
        if case not in (
            "quine-description-first-order",
            "chalmers-singularity-original",
        ):
            return DemoProvider().generate(operation, request, draft, issues, evidence)
        if operation == "critique":
            return {
                "summary": "Authored example: deterministic contract checks only; no independent semantic critique or model call.",
                "needs_revision": False,
                "issues": issues or [],
            }
        source = request["sources"][0]
        text = source["text"]

        def refs(quote):
            start = text.index(quote)
            return [
                {
                    "source_id": source["id"],
                    "start": start,
                    "end": start + len(quote),
                    "quote": quote,
                }
            ]

        if case == "quine-description-first-order":
            reading = {
                "id": "negative-description",
                "summary": "Failure of unique authorship permits zero or multiple authors. The historical denial is a supposition, not an author assertion.",
                "nodes": [
                    {
                        "id": "P1",
                        "content": "Suppose there is not exactly one Waverley author.",
                        "role": "premise",
                        "speaker": "Conditional reconstruction of Quine’s analysis",
                        "commitment": "hypothetical",
                        "source_refs": refs("The author of Waverley is not"),
                        "depends_on": [],
                        "rationale": "Assumed for a conditional equivalence check; not asserted as a historical fact.",
                    },
                    {
                        "id": "C",
                        "content": "Then either no object wrote Waverley or two distinct objects did.",
                        "role": "inference",
                        "speaker": "Quine explaining Russell",
                        "commitment": "hypothetical",
                        "source_refs": refs(
                            "Either each thing failed to write Waverley or two or more things wrote Waverley"
                        ),
                        "depends_on": ["P1"],
                        "rationale": "Checks the local implication from negated unique existence; source interpretation and meaningfulness remain separate.",
                    },
                ],
                "target": "C",
                "formal_mapping": {
                    "semantics": "classical-first-order-v1",
                    "formulas": {
                        "P1": "~(exists x. (W(x) & forall y. (W(y) -> y = x)))",
                        "C": "(forall x. ~W(x)) | (exists x. (W(x) & exists y. (W(y) & ~(x = y))))",
                    },
                    "rationale": "W(x) means x wrote Waverley. A classical nonempty domain with equality; no name constant denoting a presumed author.",
                },
                "qualifications": [
                    {
                        "kind": "local_first_order_translation",
                        "description": "Checks one direction of the local zero-or-multiple equivalence; does not prove meaningfulness or Quine’s full ontology.",
                        "representation": "first_order",
                        "rationale": "All variables are bound. Meaning of W and translation fidelity need reader review.",
                    }
                ],
            }
        else:
            lines = [
                ("P1", "There will be AI+.", "p"),
                ("P2", "If there is AI+, there will be AI++.", "p -> q"),
                ("C", "There will be AI++.", "q"),
            ]
            reading = {
                "id": "intelligence-explosion",
                "summary": "The displayed two-premise inference is modus ponens. Premise truth and later temporal/defeater qualifications are not established.",
                "nodes": [
                    {
                        "id": key,
                        "content": quote,
                        "role": "inference" if key == "C" else "premise",
                        "speaker": "Chalmers’s displayed argument",
                        "commitment": "author_position",
                        "source_refs": refs(quote),
                        "depends_on": ["P1", "P2"] if key == "C" else [],
                        "rationale": "One of the explicitly numbered lines, not an empirical verification.",
                    }
                    for key, quote, formula in lines
                ],
                "target": "C",
                "formal_mapping": {
                    "formulas": {key: formula for key, quote, formula in lines},
                    "rationale": "p = AI+ will occur; q = AI++ will occur. Only the displayed p,p→q therefore q structure.",
                },
                "qualifications": [
                    {
                        "kind": "local_propositional_scope",
                        "description": "No proof of prediction premises or later temporal/defeater argument.",
                        "representation": "propositional_abstraction",
                        "rationale": "Whole prediction statements are atoms for this explicitly displayed inference.",
                    }
                ],
            }
        return {
            "schema_version": "argument-draft-v1",
            "title": source["title"],
            "summary": reading["summary"],
            "readings": [reading],
            "provider": self.name,
            "provenance": "Explicitly authored source-backed demonstration; no live model, semantic evaluation, or human approval. The reader must check this candidate reconstruction.",
        }
