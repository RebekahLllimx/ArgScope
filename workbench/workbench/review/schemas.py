"""Strict prose-first contracts; a formula is optional, never a substitute for prose."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from workbench.contracts import digest


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LibraryBinding(Strict):
    document_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    chunk_id: str = Field(pattern=r"^[a-f0-9]{64}$")
    upload_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Source(Strict):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=180)
    version: str = Field(default="v1", min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=18000)
    kind: Literal[
        "user_text", "authored_control", "authored_paraphrase", "original_excerpt"
    ] = "user_text"
    reference_url: str | None = Field(default=None, max_length=1000)
    binding: LibraryBinding | None = None

    @model_validator(mode="after")
    def content(self):
        if not self.text.strip():
            raise ValueError("The source passage cannot be empty")
        if self.reference_url:
            from urllib.parse import urlsplit

            url = urlsplit(self.reference_url)
            if (
                url.scheme not in ("http", "https")
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ValueError("Use an HTTP(S) reference without credentials")
        return self


class Request(Strict):
    question: str = Field(min_length=5, max_length=1000)
    sources: list[Source] = Field(min_length=1, max_length=3)
    notes: str = Field(default="", max_length=2000)
    workflow_mode: Literal["bounded_review", "single_draft"] = "bounded_review"

    @model_validator(mode="after")
    def limits(self):
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("Duplicate source ID")
        if (
            len(self.sources[0].text.split()) > 1500
            or sum(len(s.text.split()) for s in self.sources) > 3000
        ):
            raise ValueError("Use at most 1500 main-passage words and 3000 total words")
        return self


class Reference(Strict):
    source_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=4000)


class Node(Strict):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    content: str = Field(min_length=1, max_length=1600)
    role: Literal["premise", "inference"]
    speaker: str = Field(min_length=1, max_length=180)
    commitment: Literal[
        "author_position", "opponent_position", "hypothetical", "proposed_assumption"
    ]
    source_refs: list[Reference] = Field(max_length=6)
    depends_on: list[str] = Field(max_length=20)
    rationale: str = Field(default="", max_length=1600)


class Qualification(Strict):
    kind: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=1600)
    representation: Literal[
        "unencoded", "propositional_abstraction", "first_order"
    ] = "unencoded"
    rationale: str = Field(default="", max_length=1600)


class FormalMapping(Strict):
    formulas: dict[str, str]
    rationale: str = Field(min_length=1, max_length=1600)


class FirstOrderMapping(Strict):
    semantics: Literal["classical-first-order-v1"]
    formulas: dict[str, str]
    rationale: str = Field(min_length=1, max_length=1600)


class FormalScope(Strict):
    mode: Literal["declared_formulas"] = "declared_formulas"
    rationale: str = Field(min_length=20, max_length=2000)

    @model_validator(mode="after")
    def explicit_scope(self):
        if len(self.rationale.strip()) < 20:
            raise ValueError("Explain the restricted formula scope explicitly")
        return self


class Reading(Strict):
    id: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=2000)
    nodes: list[Node] = Field(min_length=1, max_length=20)
    target: str
    qualifications: list[Qualification] = Field(default_factory=list, max_length=20)
    formal_mapping: FormalMapping | FirstOrderMapping | None = None
    formal_scope: FormalScope | None = None


class Draft(Strict):
    schema_version: Literal["argument-draft-v1"] = "argument-draft-v1"
    title: str = Field(min_length=1, max_length=180)
    summary: str = Field(min_length=1, max_length=3000)
    readings: list[Reading] = Field(min_length=1, max_length=2)
    provider: str = Field(min_length=1, max_length=100)
    provenance: str = Field(min_length=1, max_length=1200)


class HumanReview(Strict):
    expected_draft: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=180)
    rationale: str = Field(min_length=5, max_length=2000)
    fidelity: Literal["accepted", "disputed"]
    attribution: Literal["accepted", "disputed"]
    interpretation: Literal["accepted", "disputed"]
    approve_formal_mapping: bool = False
    approve_formula_scope: bool = False
    edited_draft: Draft | None = None


class RevisionFeedback(Strict):
    action: Literal["request_revision"] = "request_revision"
    expected_draft: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewer: str = Field(min_length=1, max_length=180)
    feedback: str = Field(min_length=5, max_length=2000)
    edited_draft: Draft | None = None

    @model_validator(mode="after")
    def nonblank(self):
        if not self.reviewer.strip() or len(self.feedback.strip()) < 5:
            raise ValueError("Supply a reviewer label and specific feedback")
        return self


def validate_draft(value, request):
    draft = Draft.model_validate(value)
    request = Request.model_validate(request)
    sources = {s.id: s for s in request.sources}
    issues = []
    if len({r.id for r in draft.readings}) != len(draft.readings):
        raise ValueError("Duplicate reading ID")
    for reading in draft.readings:
        nodes = {node.id: node for node in reading.nodes}
        if len(nodes) != len(reading.nodes):
            raise ValueError("Duplicate node ID")
        if reading.target not in nodes or nodes[reading.target].role != "inference":
            raise ValueError("Select an inference target")

        def visit(key, active, done):
            if key in active:
                raise ValueError("Cyclic argument dependencies")
            if key in done:
                return
            active.add(key)
            for ref in nodes[key].depends_on:
                if ref not in nodes:
                    raise ValueError("Missing argument dependency")
                visit(ref, active, done)
            active.remove(key)
            done.add(key)

        done = set()
        for node in reading.nodes:
            if len(set(node.depends_on)) != len(node.depends_on):
                raise ValueError("Duplicate dependency")
            if node.role == "premise" and node.depends_on:
                raise ValueError("A premise cannot have inference dependencies")
            visit(node.id, set(), done)
            for ref in node.source_refs:
                source = sources.get(ref.source_id)
                if (
                    source is None
                    or not ref.start < ref.end <= len(source.text)
                    or source.text[ref.start : ref.end] != ref.quote
                ):
                    raise ValueError(
                        "A quotation must exactly match its frozen source and Unicode span"
                    )
            if (
                node.commitment in ("author_position", "opponent_position")
                and not node.source_refs
            ):
                issues.append(
                    issue(
                        "attribution_without_reference",
                        reading.id,
                        node.id,
                        "An attributed statement has no source reference. Mark it as a proposed assumption or supply exact evidence.",
                        "revise",
                    )
                )
            if node.commitment == "proposed_assumption" and not node.rationale.strip():
                issues.append(
                    issue(
                        "assumption_without_rationale",
                        reading.id,
                        node.id,
                        "Explain why this additional assumption is being proposed.",
                        "revise",
                    )
                )
        if reading.formal_mapping:
            mapping = reading.formal_mapping.formulas
            if set(mapping) - set(nodes) or len(mapping) > 20:
                raise ValueError("Formula mappings must reference existing nodes")
            if isinstance(reading.formal_mapping, FirstOrderMapping):
                from .first_order import parse, signatures

                signatures([parse(formula) for formula in mapping.values()])
            else:
                from argscope.logic import parse

                for formula in mapping.values():
                    parse(formula)
        for qualifier in reading.qualifications:
            if qualifier.representation == "unencoded":
                issues.append(
                    issue(
                        "unsupported_semantics",
                        reading.id,
                        reading.target,
                        qualifier.description,
                        "human",
                    )
                )
            elif not qualifier.rationale.strip():
                raise ValueError("An encoded mapping needs a scope rationale")
    normalized = draft.model_dump(mode="json")
    # Preserve legacy normalized draft identities when the new scope is absent.
    for reading in normalized["readings"]:
        if reading.get("formal_scope") is None:
            reading.pop("formal_scope", None)
    return normalized, issues


def issue(kind, reading, node, message, action):
    return {
        "id": digest([kind, reading, node])[:16],
        "kind": kind,
        "reading_id": reading,
        "node_id": node,
        "message": message,
        "action": action,
        "status": "open",
    }
