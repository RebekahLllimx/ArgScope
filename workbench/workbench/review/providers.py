"""A replaceable structured provider and an explicitly deterministic demo."""
from copy import deepcopy
import json
import re
from typing import Protocol
from .schemas import Draft, Strict
from pydantic import Field


class Critique(Strict):
    summary: str = Field(min_length=1, max_length=2000)
    needs_revision: bool
    issues: list[dict] = Field(max_length=30)


class StructuredProvider(Protocol):
    """Minimum boundary implemented by live and authored providers."""

    name: str
    live: bool
    usage: list

    def generate(self, operation, request, draft=None, issues=None, evidence=None): ...


def examples():
    texts = [
        (
            "alarm-gap",
            "Affirming the consequent",
            "If the alarm is armed, the indicator is lit. The indicator is lit. Therefore the alarm is armed.",
            "What extra assumption would this conclusion require?",
        ),
        (
            "alarm-valid",
            "A supported inference",
            "If the alarm is armed, the indicator is lit. The alarm is armed. Therefore the indicator is lit.",
            "Does the stated conclusion follow from these premises?",
        ),
        (
            "perspective",
            "Perspective and scope — authored paraphrase",
            "A physical description can tell an observer about a creature's behavior. The experience is accessible from the creature's own perspective. A gap in our present understanding does not by itself establish that physicalism is false.",
            "Which conclusion is qualified, and which stronger conclusion would exceed this passage?",
        ),
        (
            "knowledge",
            "Belief and knowledge — authored paraphrase",
            "A subject has evidence for a belief and draws a valid deduction from it. The deduced proposition happens to be true for a different reason. Whether this justified true belief is knowledge requires an explicit epistemic judgment.",
            "Which parts concern a valid deduction, and which depend on a judgment about knowledge?",
        ),
        (
            "attribution",
            "Author and opponent — authored paraphrase",
            "An opponent argues that using a name commits the speaker to an existing object. The author reports that position in order to question it. Discussing the name does not settle whether the author accepts that commitment.",
            "Whose existential commitment is being reported?",
        ),
    ]
    return [
        {
            "id": key,
            "label": title,
            "request": {
                "question": question,
                "notes": "",
                "sources": [
                    {
                        "id": "main",
                        "title": title,
                        "version": "v1",
                        "kind": "authored_control"
                        if key.startswith("alarm")
                        else "authored_paraphrase",
                        "text": text,
                        "reference_url": None,
                    }
                ],
            },
        }
        for key, title, text, question in texts
    ]


class DemoProvider:
    name = "demo-rules-v1"
    live = False

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        if operation == "critique":
            issues = issues or []
            return {
                "summary": "Deterministic demonstration: explicit record issues are checked; philosophical interpretation is not evaluated by a model.",
                "needs_revision": any(i["action"] == "revise" for i in issues),
                "issues": issues,
            }
        if operation == "revise":
            result = deepcopy(draft)
            if any(i.get("kind") == "reader_feedback" for i in issues or []):
                result["summary"] = "Deterministic feedback demonstration: the supplied argument is preserved. Interpreting free-text feedback requires a configured live model; human review is still pending."
                return result
            for reading in result["readings"]:
                for node in reading["nodes"]:
                    if not node["source_refs"] and node["commitment"] in (
                        "author_position",
                        "opponent_position",
                    ):
                        node.update(
                            commitment="proposed_assumption",
                            speaker="Demo proposal",
                            rationale="This is an additional condition proposed for discussion, not a claim supported by the selected source.",
                        )
            result[
                "summary"
            ] = "The unsupported attribution was relabeled as a proposed assumption. This deterministic demo still requires your interpretation review."
            return result
        source = request["sources"][0]
        text = source["text"]
        sentences = [
            (m.start(), m.end(), m.group())
            for m in re.finditer(r"[^.!?\n]+[.!?]?", text)
            if m.group().strip()
        ][:8]
        if len(sentences) < 2:
            sentences = [(0, len(text), text), (0, len(text), text)]
        nodes = []
        for i, (start, end, quote) in enumerate(sentences):
            leading = len(quote) - len(quote.lstrip())
            start += leading
            quote = quote.strip()
            end = start + len(quote)
            final = i == len(sentences) - 1
            nodes.append(
                {
                    "id": "C" if final else f"P{i+1}",
                    "content": quote,
                    "role": "inference" if final else "premise",
                    "speaker": "Source voice (demo extraction)",
                    "commitment": "author_position",
                    "source_refs": [
                        {
                            "source_id": source["id"],
                            "start": start,
                            "end": end,
                            "quote": quote,
                        }
                    ],
                    "depends_on": [n["id"] for n in nodes] if final else [],
                    "rationale": "Sentence-based demo extraction; human attribution and entailment remain unverified.",
                }
            )
        reading = {
            "id": "reading-1",
            "summary": "One provisional reading extracted from sentences. Review the roles and dependencies.",
            "nodes": nodes,
            "target": "C",
            "qualifications": [],
            "formal_mapping": None,
        }
        known = {
            example["request"]["sources"][0]["text"]: example["id"]
            for example in examples()
        }
        case = known.get(text)
        if case == "attribution":
            nodes[0]["speaker"] = "Reported opponent"
            nodes[0]["commitment"] = "opponent_position"
        if case in ("alarm-gap", "alarm-valid"):
            reading["formal_mapping"] = {
                "formulas": {
                    "P1": "p -> q",
                    "P2": "q" if case == "alarm-gap" else "p",
                    "C": "p" if case == "alarm-gap" else "q",
                },
                "rationale": "Authored propositional control: p means the alarm is armed; q means the indicator is lit.",
            }
            if case == "alarm-gap":
                nodes.insert(
                    2,
                    {
                        "id": "A1",
                        "content": "A lit indicator has no other cause.",
                        "role": "premise",
                        "speaker": "Source voice (deliberate demo error)",
                        "commitment": "author_position",
                        "source_refs": [],
                        "depends_on": [],
                        "rationale": "Deliberately unsupported attribution to exercise a visible revision branch; not a philosophical discovery.",
                    },
                )
        else:
            reading["qualifications"] = [
                {
                    "kind": "unreviewed_interpretation",
                    "description": "This extractive demonstration does not encode the passage's conceptual or perspective-dependent semantics.",
                    "representation": "unencoded",
                    "rationale": "",
                }
            ]
        return {
            "schema_version": "argument-draft-v1",
            "title": source["title"],
            "summary": "Deterministic demo extraction. No live language model was called; sentence roles are provisional.",
            "readings": [reading],
            "provider": self.name,
            "provenance": "Authored controls have predefined mappings. Other text is split into sentences. The alarm-gap control contains a deliberate attribution error that the workflow repairs.",
        }


class OllamaProvider:
    """Optional operator-configured local service; not enabled or live-tested by default."""

    live = True

    def __init__(self, model, base_url="http://127.0.0.1:11434", timeout=30):
        from urllib.parse import urlsplit

        parts = urlsplit(base_url)
        if (
            parts.scheme != "http"
            or parts.hostname not in ("127.0.0.1", "localhost")
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError(
                "The MVP Ollama adapter accepts a configured loopback HTTP service only"
            )
        self.model = model
        self.url = base_url.rstrip("/") + "/api/chat"
        self.timeout = timeout
        self.name = "ollama:" + model + ":review-v2"

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        from urllib.request import Request as HTTPRequest, urlopen

        schema = (
            Critique.model_json_schema()
            if operation == "critique"
            else Draft.model_json_schema()
        )
        from .prompt_policy import instructions

        policy = instructions("dictionary")
        body = {
            "model": self.model,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": policy},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "operation": operation,
                            "request": request,
                            "draft": draft,
                            "issues": issues,
                            "evidence": evidence,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        }
        http = HTTPRequest(
            self.url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(http, timeout=self.timeout) as response:
            raw = response.read(600001)
        if len(raw) > 600000:
            raise ValueError("Model response exceeded 600 KB")
        result = json.loads(json.loads(raw)["message"]["content"])
        if operation != "critique":
            result["provider"] = self.name
            result[
                "provenance"
            ] = "Model-generated interpretation; references and human judgments require separate validation."
        return result
