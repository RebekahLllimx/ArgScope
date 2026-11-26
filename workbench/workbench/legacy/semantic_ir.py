"""Explicit semantic annotations and conservative execution boundaries.

Sense identifiers record a reader's modeling choice. They never rewrite formulas
or establish synonymy. Unsupported operators require an explicit abstraction.
"""
from copy import deepcopy

from ..contracts import fields, text

KINDS = (
    "belief",
    "justification",
    "knowledge",
    "perspective",
    "temporal",
    "modal",
    "quantifier",
    "reference",
    "quotation",
    "substitution",
)
CONTEXTS = ("object_language", "quotation", "belief_report")


def unique_records(records, keys, limit):
    if not isinstance(records, list) or len(records) > limit:
        raise ValueError(f"Expected at most {limit} annotation records")
    seen = set()
    for record in records:
        fields(record, keys)
        text(record["id"], 100)
        if record["id"] in seen:
            raise ValueError("Duplicate annotation ID")
        seen.add(record["id"])
    return seen


def validate_reading(reading, source):
    nodes = {node["id"]: node for node in reading["nodes"]}
    senses = unique_records(
        reading["senses"], ("id", "label", "description", "grounds"), 80
    )
    for sense in reading["senses"]:
        for key in ("label", "description", "grounds"):
            text(sense[key], 2000)
    unique_records(
        reading["occurrences"],
        ("id", "node_id", "start", "end", "surface", "sense_id", "context"),
        160,
    )
    for occurrence in reading["occurrences"]:
        start, end = occurrence["start"], occurrence["end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(source["text"])
        ):
            raise ValueError("Invalid term occurrence offsets")
        text(occurrence["surface"], 2000)
        if source["text"][start:end] != occurrence["surface"]:
            raise ValueError("Term occurrence does not match the selected source text")
        if (
            not isinstance(occurrence["node_id"], str)
            or occurrence["node_id"] not in nodes
        ):
            raise ValueError("Unknown occurrence node")
        sense = occurrence["sense_id"]
        if sense is not None and (not isinstance(sense, str) or sense not in senses):
            raise ValueError("Unknown proposed sense")
        if occurrence["context"] not in CONTEXTS:
            raise ValueError("Unknown occurrence context")
    unique_records(
        reading["qualifications"],
        (
            "id",
            "node_id",
            "kind",
            "description",
            "representation",
            "abstraction_rationale",
        ),
        80,
    )
    for qualifier in reading["qualifications"]:
        if (
            not isinstance(qualifier["node_id"], str)
            or qualifier["node_id"] not in nodes
        ):
            raise ValueError("Unknown qualification node")
        if qualifier["kind"] not in KINDS:
            raise ValueError("Unknown qualification kind")
        text(qualifier["description"], 2000)
        if qualifier["representation"] == "propositional_abstraction":
            text(qualifier["abstraction_rationale"], 2000)
        elif (
            qualifier["representation"] != "unencoded"
            or qualifier["abstraction_rationale"] is not None
        ):
            raise ValueError(
                "Unencoded qualifications must have a null abstraction rationale"
            )
    unique_records(
        reading["bridges"], ("id", "node_id", "rationale", "source_basis"), 32
    )
    for bridge in reading["bridges"]:
        if not isinstance(bridge["node_id"], str) or bridge["node_id"] not in nodes:
            raise ValueError("Unknown bridge node")
        node = nodes[bridge["node_id"]]
        if node["role"] != "premise" or node["commitment"] != "user_assumption":
            raise ValueError("Bridge premises must be explicit user assumptions")
        text(bridge["rationale"], 2000)
        text(bridge["source_basis"], 2000)


def assess(reading):
    """Conservatively gate the entire annotated reading, not only its target."""
    unresolved = [o["id"] for o in reading["occurrences"] if o["sense_id"] is None]
    unencoded = [
        q["id"] for q in reading["qualifications"] if q["representation"] == "unencoded"
    ]
    # A quotation or belief context cannot vanish just because its qualifier was omitted.
    for occurrence in reading["occurrences"]:
        kind = {"quotation": "quotation", "belief_report": "belief"}.get(
            occurrence["context"]
        )
        if kind and not any(
            q["node_id"] == occurrence["node_id"] and q["kind"] == kind
            for q in reading["qualifications"]
        ):
            unencoded.append("context:" + occurrence["id"])
    if unencoded:
        status = "unsupported_semantics"
    elif unresolved:
        status = "review_required"
    else:
        status = "executable"
    abstractions = [
        q["id"]
        for q in reading["qualifications"]
        if q["representation"] == "propositional_abstraction"
    ]
    return {
        "status": status,
        "unresolved_occurrences": unresolved,
        "unencoded_qualifications": unencoded,
        "abstractions": abstractions,
        "level": "unencoded_semantics"
        if unencoded
        else "unresolved_interpretation"
        if unresolved
        else "propositional_abstraction_only"
        if abstractions
        else "supplied_formalization",
        "scope": "Sense mappings are proposed interpretations. When executable, only the supplied propositional formulas are checked.",
    }


def annotate(document):
    """Upgrade with empty annotation sets; never carry approval across the change."""
    from .reconstruction import validate, revision_hash

    validate(document)
    result = deepcopy(document)
    if document["schema_version"] == "reconstruction-v1":
        result["schema_version"] = "reconstruction-v2"
        result["parent_revision"] = revision_hash(document)
        for reading in result["readings"]:
            reading.update(senses=[], occurrences=[], qualifications=[], bridges=[])
    validate(result)
    return result


def changes(previous, current):
    """Explain content changes and conservatively identify inference descendants."""
    from .reconstruction import validate, reading_hash, revision_hash

    validate(previous)
    validate(current)
    if previous["reconstruction_id"] != current["reconstruction_id"]:
        raise ValueError("Cannot compare different reconstruction identities")
    before = {r["id"]: r for r in previous["readings"]}
    after = {r["id"]: r for r in current["readings"]}
    results = []
    for key in sorted(before.keys() | after.keys()):
        if key not in before or key not in after:
            results.append(
                {"reading_id": key, "change": "added" if key in after else "removed"}
            )
            continue
        old, new = before[key], after[key]
        old_nodes, new_nodes = ({n["id"]: n for n in r["nodes"]} for r in (old, new))
        node_changes = []
        affected = set()
        for node_id in sorted(old_nodes.keys() | new_nodes.keys()):
            a, b = old_nodes.get(node_id, {}), new_nodes.get(node_id, {})
            changed_fields = sorted(
                k for k in a.keys() | b.keys() if a.get(k) != b.get(k)
            )
            if changed_fields:
                node_changes.append({"node_id": node_id, "fields": changed_fields})
                affected.add(node_id)
        reading_fields = sorted(
            k
            for k in old.keys() | new.keys()
            if k != "nodes" and old.get(k) != new.get(k)
        )
        source_changed = previous["source"] != current["source"]
        if source_changed or reading_fields:
            affected.update(new_nodes)
        # Traverse the union so that removing a dependency cannot hide an old descendant.
        outgoing = {}
        for node in [*old["nodes"], *new["nodes"]]:
            for dependency in node["depends_on"]:
                outgoing.setdefault(dependency, set()).add(node["id"])
        from collections import deque

        queue = deque(affected)
        while queue:
            for child in outgoing.get(queue.popleft(), ()):
                if child not in affected:
                    affected.add(child)
                    queue.append(child)
        same = reading_hash(previous, old) == reading_hash(current, new)
        results.append(
            {
                "reading_id": key,
                "change": "unchanged" if same else "changed",
                "source_changed": source_changed,
                "reading_fields": reading_fields,
                "node_changes": node_changes,
                "affected_inferences": [
                    n["id"]
                    for n in new["nodes"]
                    if n["role"] == "inference" and n["id"] in affected
                ],
                "review_content_unchanged": same,
            }
        )
    return {
        "before_revision": revision_hash(previous),
        "after_revision": revision_hash(current),
        "readings": results,
        "scope": "Conservative impact report; no persisted solver cache or semantic equivalence proof",
    }
