"""Local Codex CLI structured inference using its supported saved authentication."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from .schemas import Draft, validate_draft
from .providers import Critique
from .prompt_policy import instructions


def resolve_cli(configured=None):
    explicit = configured or os.environ.get("ARGSCOPE_CODEX_BIN")
    if explicit:
        path = Path(explicit)
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ValueError("The explicitly configured Codex CLI is not executable")
        return path.resolve()
    candidates = [
        shutil.which("codex"),
        "/Applications/ChatGPT.app/Contents/Resources/codex-cli/bin/codex",
        str(Path.home() / ".codex/plugins/.plugin-appserver/codex-cli/bin/codex"),
    ]
    for value in candidates:
        if value and Path(value).is_file() and os.access(value, os.X_OK):
            return Path(value).resolve()
    raise ValueError(
        "Codex CLI not found; configure ARGSCOPE_CODEX_BIN or install the official CLI"
    )


def output_schema(operation):
    schema = (
        Critique.model_json_schema()
        if operation == "critique"
        else Draft.model_json_schema()
    )
    if operation == "critique":
        schema["properties"]["issues"]["items"] = {
            "type": "object",
            "properties": {
                k: {"type": "string"}
                for k in [
                    "id",
                    "kind",
                    "reading_id",
                    "node_id",
                    "message",
                    "action",
                    "status",
                ]
            },
            "additionalProperties": False,
        }

    def strict(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object" and "properties" in value:
                if "formulas" in value["properties"]:
                    value["properties"]["formulas"] = {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "node_id": {"type": "string"},
                                "formula": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    }
                value["required"] = list(value["properties"])
                value["additionalProperties"] = False
            for child in value.values():
                strict(child)
        elif isinstance(value, list):
            for child in value:
                strict(child)

    strict(schema)
    return schema


class CodexProvider:
    live = True

    def __init__(self, executable=None, model=None, timeout=90):
        self.executable = resolve_cli(executable)
        self.model = model
        self.timeout = timeout
        self.name = (
            "codex-cli:"
            + hashlib.sha256(self.executable.read_bytes()).hexdigest()[:12]
            + ":"
            + (model or "client-default")
            + ":review-v2"
        )
        self.usage = []

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        policy = instructions("rows")
        prompt = (
            policy
            + "\n"
            + json.dumps(
                {
                    "operation": operation,
                    "request": request,
                    "draft": draft,
                    "issues": issues,
                    "evidence": evidence,
                },
                ensure_ascii=False,
            )
        )
        with tempfile.TemporaryDirectory(prefix="argscope-codex-") as directory:
            directory = Path(directory)
            schema = directory / "schema.json"
            output = directory / "answer.json"
            schema.write_text(json.dumps(output_schema(operation)))
            command = [
                str(self.executable),
                "exec",
                "--ignore-user-config",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--disable",
                "shell_tool",
                "-c",
                'web_search="disabled"',
                "-c",
                'model_reasoning_effort="low"',
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "--json",
                "-C",
                str(directory),
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.append("-")
            # No API key is copied. The official client resolves its own saved login.
            env = dict(os.environ)
            for key in ["OPENAI_API_KEY", "CODEX_API_KEY"]:
                env.pop(key, None)
            try:
                with (directory / "events.jsonl").open("w+") as events, (
                    directory / "stderr.log"
                ).open("w+") as errors:
                    result = subprocess.run(
                        command,
                        input=prompt,
                        text=True,
                        stdout=events,
                        stderr=errors,
                        timeout=self.timeout,
                        env=env,
                    )
                    events.seek(0)
                    for line in events:
                        if len(line) > 100000:
                            continue
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("type") == "turn.completed":
                            self.usage.append(
                                {
                                    "operation": operation,
                                    "provider": self.name,
                                    "usage": event.get("usage", {}),
                                }
                            )
                    if result.returncode:
                        raise ValueError(
                            "Codex CLI inference failed; check login status and model availability locally"
                        )
            except subprocess.TimeoutExpired:
                raise ValueError(
                    "Codex CLI inference timed out; no partial answer accepted"
                ) from None
            if not output.is_file() or output.stat().st_size > 600000:
                raise ValueError("Codex CLI did not return a bounded structured answer")
            value = json.loads(output.read_text())
        if operation == "critique":
            return Critique.model_validate(value).model_dump(mode="json")
        for reading in value.get("readings", []):
            mapping = reading.get("formal_mapping")
            if mapping:
                rows = mapping["formulas"]
                if not isinstance(rows, list) or len(
                    {r["node_id"] for r in rows}
                ) != len(rows):
                    raise ValueError("Codex formula rows require unique node IDs")
                mapping["formulas"] = {r["node_id"]: r["formula"] for r in rows}
            sources = {s["id"]: s["text"] for s in request["sources"]}
            for node in reading.get("nodes", []):
                for ref in node.get("source_refs", []):
                    text = sources.get(ref.get("source_id"), "")
                    quote = ref.get("quote", "")
                    if quote and text.count(quote) == 1:
                        ref.update(
                            start=text.index(quote), end=text.index(quote) + len(quote)
                        )
        value.update(
            provider=self.name,
            provenance="Live structured interpretation via the official local Codex CLI and its saved login; not a Platform API key or human approval.",
        )
        return validate_draft(value, request)[0]
