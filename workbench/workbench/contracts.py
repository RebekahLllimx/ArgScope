"""Strict version-one case records and canonical content identities."""
import hashlib
import json

from argscope.logic import parse


def canonical(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError("Missing or unknown record fields: " + ", ".join(expected))


def text(value, limit=10000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Expected bounded nonempty text")


def validate_case(case):
    fields(case, ("schema_version", "source", "reading", "claim"))
    if case["schema_version"] not in ("research-case-v1", "research-case-v2"):
        raise ValueError("Unsupported case schema")
    source, reading, claim = (case[k] for k in ("source", "reading", "claim"))
    source_fields = ("key", "version", "kind", "text", "locator", "reference_url")
    if case["schema_version"] == "research-case-v2":
        source_fields += ("passage",)
    fields(source, source_fields)
    for key in ("key", "version", "text", "locator"):
        text(source[key])
    if source["kind"] not in (
        "authored_control",
        "authored_paraphrase",
        "original_excerpt",
    ):
        raise ValueError("Unsupported source kind")
    if case["schema_version"] == "research-case-v2":
        from .legacy.sources import validate_passage

        validate_passage(source)
    url = source["reference_url"]
    if url is not None:
        from urllib.parse import urlsplit

        text(url, 1000)
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise ValueError("Expected a public HTTPS reference without credentials")
    fields(reading, ("id", "speaker", "commitment", "summary", "review"))
    for key in ("id", "speaker", "summary"):
        text(reading[key])
    if reading["commitment"] not in (
        "user_assumption",
        "author_position",
        "opponent_position",
        "mutation_control",
    ):
        raise ValueError("Unknown commitment")
    review = reading["review"]
    fields(review, ("status", "reviewer", "rationale"))
    if review["status"] not in ("draft", "self_reviewed", "disputed"):
        raise ValueError("Unknown review status")
    text(review["reviewer"])
    text(review["rationale"])
    fields(claim, ("id", "origin", "semantics", "premises", "conclusion"))
    text(claim["id"])
    if claim["origin"] not in (
        "user_defined",
        "source_reconstruction",
        "deliberate_mutation_control",
    ):
        raise ValueError("Unknown claim origin")
    if (
        claim["origin"] == "deliberate_mutation_control"
        and reading["commitment"] != "mutation_control"
    ):
        raise ValueError("Mutation controls require explicit attribution")
    if claim["semantics"] not in (
        "classical-propositional-v1",
        "conceptual-case-review-v1",
    ):
        raise ValueError("Unknown semantics")
    if not isinstance(claim["premises"], list) or len(claim["premises"]) > 32:
        raise ValueError("Expected at most 32 premises")
    for premise in claim["premises"]:
        text(premise, 1000)
    text(claim["conclusion"], 1000)
    if claim["semantics"] == "classical-propositional-v1":
        for formula in [*claim["premises"], claim["conclusion"]]:
            parse(formula)
    return {
        "source_hash": digest(source),
        "reading_hash": digest(reading),
        "claim_hash": digest(case),
    }
