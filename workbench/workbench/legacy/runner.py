"""Freeze source, interpretation and claim in a replayable, untrusted JSON bundle."""
from falsifylab.search import search_claim
from ..contracts import digest, fields, validate_case


def run(case, source_db=None, *, snapshot_only=False, backend="exhaustive"):
    if backend not in ("exhaustive", "dpll"):
        raise ValueError("Unknown formal backend")
    identity = validate_case(case)
    if case["schema_version"] == "research-case-v2" and not snapshot_only:
        if source_db is None:
            raise ValueError("A linked case requires --source-db for execution")
        from .sources import verify_source

        verify_source(case, source_db)
    if case["reading"]["review"]["status"] != "self_reviewed":
        result = {"outcome": "review_required"}
    else:
        result = search_claim(case["claim"], backend=backend)
    bundle = {
        "schema_version": "research-bundle-v2"
        if backend == "dpll"
        else "research-bundle-v1",
        "case": case,
        "identity": identity,
        "result": result,
        "result_hash": digest(result),
        "scope": "User-supplied interpretation; source fidelity and premise truth are not mechanically established",
    }
    if backend == "dpll":
        bundle["execution"] = {"backend": "dpll-v1"}
    return bundle


def replay(bundle, source_db=None):
    expected_fields = (
        "schema_version",
        "case",
        "identity",
        "result",
        "result_hash",
        "scope",
    )
    if bundle.get("schema_version") == "research-bundle-v2":
        expected_fields += ("execution",)
    fields(bundle, expected_fields)
    backend = "exhaustive"
    if bundle["schema_version"] == "research-bundle-v2":
        if bundle["execution"] != {"backend": "dpll-v1"}:
            raise ValueError("Unsupported execution version")
        backend = "dpll"
    elif bundle["schema_version"] != "research-bundle-v1":
        raise ValueError("Unsupported bundle schema")
    expected = run(bundle["case"], snapshot_only=True, backend=backend)
    if digest(bundle) != digest(expected):
        raise ValueError("Bundle identity or independently recomputed result mismatch")
    source_check = {}
    if source_db is not None:
        from .sources import verify_source

        source_check = {"source_check": verify_source(bundle["case"], source_db)}
    return {
        **source_check,
        "status": "replay_verified",
        "claim_hash": expected["identity"]["claim_hash"],
        "outcome": expected["result"]["outcome"],
    }
