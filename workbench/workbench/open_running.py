"""Reuse the already-running local paper demo on repeated Finder launches."""
import json
import sys
from urllib.request import urlopen
import webbrowser


def open_existing(port, provider="papers"):
    if type(port) is not int or not 1024 <= port <= 65535:
        return False
    url = f"http://127.0.0.1:{port}"
    try:
        with urlopen(url + "/api/argument-health", timeout=1) as response:
            value = json.loads(response.read(8192))
        name = value.get("provider", {}).get("name", "")
        matches = (
            name == "authored-paper-examples-v1"
            if provider == "papers"
            else name.startswith("codex-cli:")
            if provider == "codex"
            else False
        )
        if value.get("framework") != "LangGraph" or not matches:
            return False
    except (OSError, ValueError):
        return False
    webbrowser.open(url)
    return True


if __name__ == "__main__":
    raise SystemExit(
        0
        if open_existing(
            int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "papers"
        )
        else 1
    )
