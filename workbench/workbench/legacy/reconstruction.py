"""Bounded alternative readings, node attribution and content-bound self-review."""
from copy import deepcopy
import uuid

from argscope.graph import _index
from ..contracts import digest, fields, text, validate_case

ASPECTS = ("fidelity", "attribution", "interpretation")


def core(document):
    return {key: value for key, value in document.items() if key != "reviews"}


def revision_hash(document):
    return digest(core(document))


def reading_hash(document, reading):
    return digest(
        {
            "reconstruction_id": document["reconstruction_id"],
            "source": document["source"],
            "reading": reading,
        }
    )


def case_for(document, reading):
    nodes = {node["id"]: node for node in reading["nodes"]}
    target = nodes[reading["target"]]
    # Direct dependencies only: no unrelated premises or opponent commitments.
    premises = [nodes[key]["formula"] for key in target["depends_on"]]
    return {
        "schema_version": "research-case-v2"
        if "passage" in document["source"]
        else "research-case-v1",
        "source": document["source"],
        "reading": {
            "id": reading["id"],
            "speaker": target["speaker"],
            "commitment": target["commitment"],
            "summary": reading["summary"],
            "review": {
                "status": "draft",
                "reviewer": "unassigned",
                "rationale": "Content-bound reviews required",
            },
        },
        "claim": {
            "id": reading["target"],
            "origin": reading["origin"],
            "semantics": reading["semantics"],
            "premises": premises,
            "conclusion": target["formula"],
        },
    }


def validate(document):
    fields(
        document,
        (
            "schema_version",
            "reconstruction_id",
            "parent_revision",
            "source",
            "readings",
            "reviews",
        ),
    )
    if document["schema_version"] not in ("reconstruction-v1", "reconstruction-v2"):
        raise ValueError("Unsupported reconstruction schema")
    text(document["reconstruction_id"], 100)
    if document["parent_revision"] is not None:
        check_hash(document["parent_revision"])
    readings = document["readings"]
    if not isinstance(readings, list) or not 1 <= len(readings) <= 8:
        raise ValueError("Use 1-8 explicit alternative readings")
    seen = set()
    for reading in readings:
        reading_fields = ("id", "summary", "origin", "semantics", "nodes", "target")
        if document["schema_version"] == "reconstruction-v2":
            reading_fields += ("senses", "occurrences", "qualifications", "bridges")
        fields(reading, reading_fields)
        text(reading["id"], 100)
        text(reading["summary"])
        if reading["id"] in seen:
            raise ValueError("Duplicate reading ID")
        seen.add(reading["id"])
        nodes = reading["nodes"]
        if not isinstance(nodes, list) or not 1 <= len(nodes) <= 80:
            raise ValueError("Use 1-80 nodes per reading")
        graph = {"premises": [], "steps": []}
        ids = set()
        for node in nodes:
            fields(
                node,
                (
                    "id",
                    "formula",
                    "role",
                    "speaker",
                    "commitment",
                    "scope",
                    "depends_on",
                ),
            )
            text(node["id"], 80)
            text(node["formula"], 1000)
            text(node["speaker"], 180)
            text(node["scope"], 1000)
            if node["commitment"] not in (
                "user_assumption",
                "author_position",
                "opponent_position",
                "mutation_control",
                "hypothetical",
            ):
                raise ValueError("Unknown node commitment")
            if node["role"] not in ("premise", "inference") or node["id"] in ids:
                raise ValueError("Invalid or duplicate node role/ID")
            ids.add(node["id"])
            refs = node["depends_on"]
            if (
                not isinstance(refs, list)
                or any(not isinstance(r, str) for r in refs)
                or len(refs) != len(set(refs))
                or len(refs) > 32
            ):
                raise ValueError("Invalid node dependencies")
            if node["role"] == "premise" and refs:
                raise ValueError("Premises cannot have inference dependencies")
            graph["premises" if node["role"] == "premise" else "steps"].append(
                {"id": node["id"], "formula": node["formula"], "depends_on": refs}
            )
        if not isinstance(reading["target"], str) or reading["target"] not in ids:
            raise ValueError("Missing target")
        if (
            next(n for n in nodes if n["id"] == reading["target"])["role"]
            != "inference"
        ):
            raise ValueError("Target must be an inference")
        # Graph shape must be valid even when the formulas use unsupported semantics.
        def visit(key, active, done):
            if key in active:
                raise ValueError("Cyclic inference dependencies")
            if key in done:
                return
            active.add(key)
            for ref in by_id[key]["depends_on"]:
                if ref not in by_id:
                    raise ValueError("Missing inference dependency")
                visit(ref, active, done)
            active.remove(key)
            done.add(key)

        by_id = {node["id"]: node for node in nodes}
        done = set()
        for key in by_id:
            visit(key, set(), done)
        if reading["semantics"] == "classical-propositional-v1":
            _index(graph)
        if document["schema_version"] == "reconstruction-v2":
            from .semantic_ir import validate_reading

            validate_reading(reading, document["source"])
        projected = case_for(document, reading)
        # Hypothetical is an explicit scope annotation; the legacy envelope calls it an assumption.
        if projected["reading"]["commitment"] == "hypothetical":
            projected["reading"]["commitment"] = "user_assumption"
        validate_case(projected)
    if not isinstance(document["reviews"], list) or len(document["reviews"]) > 200:
        raise ValueError("Use at most 200 review records")
    review_ids = set()
    for review in document["reviews"]:
        fields(
            review,
            (
                "id",
                "reading_id",
                "target_hash",
                "aspect",
                "decision",
                "reviewer",
                "rationale",
            ),
        )
        for key in ("id", "reading_id", "reviewer", "rationale"):
            text(review[key])
        check_hash(review["target_hash"])
        if (
            review["id"] in review_ids
            or review["aspect"] not in ASPECTS
            or review["decision"] not in ("self_reviewed", "disputed")
        ):
            raise ValueError("Invalid review record")
        review_ids.add(review["id"])
    return revision_hash(document)


def check_hash(value):
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise ValueError("Invalid revision hash")


def upgrade(case, reconstruction_id):
    validate_case(case)
    nodes = [
        {
            "id": "P" + str(i),
            "formula": formula,
            "role": "premise",
            "speaker": case["reading"]["speaker"],
            "commitment": "user_assumption",
            "scope": "Explicit assumption of this reconstruction",
            "depends_on": [],
        }
        for i, formula in enumerate(case["claim"]["premises"], 1)
    ]
    nodes.append(
        {
            "id": "S1",
            "formula": case["claim"]["conclusion"],
            "role": "inference",
            "speaker": case["reading"]["speaker"],
            "commitment": case["reading"]["commitment"],
            "scope": "Selected inference only",
            "depends_on": [n["id"] for n in nodes],
        }
    )
    document = {
        "schema_version": "reconstruction-v1",
        "reconstruction_id": reconstruction_id,
        "parent_revision": None,
        "source": deepcopy(case["source"]),
        "readings": [
            {
                "id": case["reading"]["id"],
                "summary": case["reading"]["summary"],
                "origin": case["claim"]["origin"],
                "semantics": case["claim"]["semantics"],
                "nodes": nodes,
                "target": "S1",
            }
        ],
        "reviews": [],
    }
    validate(document)
    return document


def review(document, reading_id, aspect, decision, reviewer, rationale):
    validate(document)
    reading = next((r for r in document["readings"] if r["id"] == reading_id), None)
    if reading is None:
        raise ValueError("Unknown reading")
    result = deepcopy(document)
    result["reviews"].append(
        {
            "id": uuid.uuid4().hex,
            "reading_id": reading_id,
            "target_hash": reading_hash(document, reading),
            "aspect": aspect,
            "decision": decision,
            "reviewer": reviewer,
            "rationale": rationale,
        }
    )
    validate(result)
    return result


def review_status(document, reading):
    latest = {}
    target = reading_hash(document, reading)
    for record in document["reviews"]:
        if record["reading_id"] == reading["id"] and record["target_hash"] == target:
            latest[record["aspect"]] = record
    return {
        aspect: latest.get(aspect, {}).get("decision", "review_required")
        for aspect in ASPECTS
    }


def execute(document, source_db=None, snapshot_only=False, *, backend="exhaustive"):
    from .runner import run
    from argscope.graph import analyze_document

    if backend not in ("exhaustive", "dpll"):
        raise ValueError("Unknown formal backend")
    validate(document)
    results = []
    for reading in document["readings"]:
        case = case_for(document, reading)
        if case["reading"]["commitment"] == "hypothetical":
            case["reading"]["commitment"] = "user_assumption"
        status = review_status(document, reading)
        reviewed = all(value == "self_reviewed" for value in status.values())
        semantic = None
        if document["schema_version"] == "reconstruction-v2":
            from .semantic_ir import assess

            semantic = assess(reading)
        if reviewed and (semantic is None or semantic["status"] == "executable"):
            case["reading"]["review"] = {
                "status": "self_reviewed",
                "reviewer": "content-bound review records",
                "rationale": "See reconstruction reviews for individual judgments",
            }
        bundle = run(case, source_db, snapshot_only=snapshot_only, backend=backend)
        if reviewed and semantic is not None and semantic["status"] != "executable":
            bundle["result"] = {
                "outcome": semantic["status"],
                "semantic_issues": semantic,
            }
            bundle["result_hash"] = digest(bundle["result"])
        # Validate prerequisite inferences rather than treating their formulas as assumptions.
        graph_result = None
        if (
            bundle["result"]["outcome"]
            not in ("review_required", "unsupported_semantics")
            and reading["semantics"] == "classical-propositional-v1"
        ):
            graph = {"premises": [], "steps": []}
            for node in reading["nodes"]:
                graph["premises" if node["role"] == "premise" else "steps"].append(
                    {k: node[k] for k in ("id", "formula", "depends_on")}
                )
            graph_result = analyze_document(
                graph, backend=backend, max_variables=64 if backend == "dpll" else 12
            )
            target = next(
                s for s in graph_result["steps"] if s["id"] == reading["target"]
            )
            if target["status"] == "blocked":
                bundle["result"] = {
                    "outcome": "blocked",
                    "blocked_by": target["blocked_by"],
                }
                bundle["result_hash"] = digest(bundle["result"])
        result = {
            "reading_id": reading["id"],
            "reading_hash": reading_hash(document, reading),
            "review_status": status,
            "analysis": bundle,
            "graph": graph_result,
        }
        if semantic is not None:
            result["semantic_scope"] = semantic
        results.append(result)
    bundle = {
        "schema_version": "reconstruction-bundle-v2"
        if document["schema_version"] == "reconstruction-v2"
        else "reconstruction-bundle-v1",
        "document": document,
        "revision_hash": revision_hash(document),
        "results": results,
        "comparison": {
            "reported_outcomes": {
                r["reading_id"]: r["analysis"]["result"]["outcome"] for r in results
            },
            "same_reported_outcome": len(
                {r["analysis"]["result"]["outcome"] for r in results}
            )
            == 1,
        },
        "scope": "Only the explicitly supplied readings; review annotations are not certified interpretation",
    }
    if backend == "dpll":
        bundle["schema_version"] = "reconstruction-bundle-v3"
        bundle["execution"] = {"backend": "dpll-v1"}
    return bundle


def replay_reconstruction(bundle, source_db=None):
    if not isinstance(bundle, dict) or bundle.get("schema_version") not in (
        "reconstruction-bundle-v1",
        "reconstruction-bundle-v2",
        "reconstruction-bundle-v3",
    ):
        raise ValueError("Unsupported reconstruction bundle version")
    backend = "exhaustive"
    if bundle.get("schema_version") == "reconstruction-bundle-v3":
        if bundle.get("execution") != {"backend": "dpll-v1"}:
            raise ValueError("Unsupported execution version")
        backend = "dpll"
    expected = execute(bundle["document"], snapshot_only=True, backend=backend)
    if digest(expected) != digest(bundle):
        raise ValueError("Reconstruction bundle differs from recomputation")
    checks = []
    if source_db is not None:
        from .sources import verify_source

        for result in expected["results"]:
            checks.append(verify_source(result["analysis"]["case"], source_db))
    return {
        "status": "replay_verified",
        "revision_hash": expected["revision_hash"],
        "source_checks": checks,
        "outcomes": {
            r["reading_id"]: r["analysis"]["result"]["outcome"]
            for r in expected["results"]
        },
    }


def revise(previous, proposed):
    validate(previous)
    validate(proposed)
    if previous["reconstruction_id"] != proposed["reconstruction_id"]:
        raise ValueError("Cannot change reconstruction identity in a revision")
    result = deepcopy(proposed)
    result["parent_revision"] = revision_hash(previous)
    result["reviews"] = deepcopy(previous["reviews"])
    validate(result)
    return result
