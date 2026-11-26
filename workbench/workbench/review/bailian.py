"""Operator-configured Bailian JSON provider; no credentials in artifacts."""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from .schemas import Draft
from .providers import Critique
from .prompt_policy import instructions


class BailianProvider:
    live = True

    def __init__(self, config_file=None, model=None):
        config = {}
        if config_file:
            for line in Path(config_file).read_text().splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    config[key.strip()] = value.strip().strip("\"'")

        def setting(key, default=""):
            return os.environ.get(key, config.get(key, default)).strip()

        self._key = setting("BAILI_API_KEY") or setting("DASHSCOPE_API_KEY")
        base = setting(
            "BAILI_API_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        parts = urlsplit(base)
        host = parts.hostname or ""
        if (
            parts.scheme != "https"
            or not (
                host == "dashscope.aliyuncs.com"
                or host.endswith(".maas.aliyuncs.com")
                or host == "dashscope-intl.aliyuncs.com"
            )
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Bailian requires an official HTTPS endpoint")
        if not self._key:
            raise ValueError("Configure BAILI_API_KEY or DASHSCOPE_API_KEY locally")
        self.url = base.rstrip("/") + "/chat/completions"
        self.model = model or setting("BAILI_MODEL", "qwen-plus")
        self.name = "bailian:" + self.model + ":review-v5"
        self.usage = []

    def generate(self, operation, request, draft=None, issues=None, evidence=None):
        schema = (
            Critique.model_json_schema()
            if operation == "critique"
            else Draft.model_json_schema()
        )
        system = (
            instructions("dictionary")
            + "\n\nJSON schema: "
            + json.dumps(schema)
        )
        payload = {
            "model": self.model,
            "stream": False,
            "temperature": 0,
            "max_tokens": 6000,
            "enable_thinking": False,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
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
        http = Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": "Bearer " + self._key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(http, timeout=60) as response:
                raw = response.read(600001)
        except HTTPError as exc:
            code = ""
            try:
                candidate = json.loads(exc.read(4096)).get("error", {}).get("code", "")
                if isinstance(candidate, str) and re.fullmatch(
                    r"[A-Za-z0-9_.-]{1,80}", candidate
                ):
                    code = " (" + candidate + ")"
            except Exception:
                pass
            raise ValueError(
                "Bailian HTTP "
                + str(exc.code)
                + code
                + "; check local service configuration"
            ) from None
        except (URLError, TimeoutError):
            raise ValueError("Bailian network request failed") from None
        if len(raw) > 600000:
            raise ValueError("Bailian response exceeded 600 KB")
        packet = json.loads(raw)
        self.usage.append(
            {
                "operation": operation,
                "model": self.model,
                "usage": packet.get("usage", {}),
            }
        )
        choice = packet["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Bailian response incomplete; no partial draft accepted")
        result = json.loads(choice["message"]["content"])
        if operation != "critique":
            result["provider"] = self.name
            result[
                "provenance"
            ] = "Live Bailian interpretation; exact quotations are checked, interpretation requires human review."
            # Canonicalize uniquely located exact quotes, never alter their wording.
            sources = {s["id"]: s["text"] for s in request["sources"]}
            for reading in result.get("readings", []):
                for node in reading.get("nodes", []):
                    for ref in node.get("source_refs", []):
                        text = sources.get(ref.get("source_id"), "")
                        quote = ref.get("quote", "")
                        if quote and text.count(quote) == 1:
                            ref["start"] = text.index(quote)
                            ref["end"] = ref["start"] + len(quote)
        parsed = (
            Critique.model_validate(result)
            if operation == "critique"
            else Draft.model_validate(result)
        )
        return parsed.model_dump()
