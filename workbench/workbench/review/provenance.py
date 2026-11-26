"""Portable provenance for new experiments; never backfill historical identities."""
import hashlib
from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def object_hash(value):
    return sha(encoded(value))


def source_snapshot(root):
    """Hash explicit project code/config roots, including uncommitted source.

    No recursive workspace walk: private outputs, env files and account config
    are excluded. Built UI is included because it is what the server serves.
    """
    root = Path(root)
    files = {}
    directories = [
        "argscope/argscope", "falsifylab/falsifylab", "sourcelens/sourcelens",
        "workbench/workbench", "workbench/tools", "workbench/tests",
        "workbench/review_web/src", "workbench/studies", "falsifylab/tools",
        "vendor/kv_server", ".github/workflows",
    ]
    suffixes = {".py", ".json", ".js", ".cjs", ".jsx", ".css", ".html", ".md", ".yml", ".yaml"}
    for directory in directories:
        for p in sorted((root / directory).rglob("*")):
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts and p.suffix in suffixes:
                files[p.relative_to(root).as_posix()] = sha(p.read_bytes())
    for pattern in ["*/requirements*.txt", "*/pyproject.toml", "package*.json", "sourcelens/web/package*.json", "workbench/review_web/*.mjs", "workbench/review_web/index.html", "workbench/docs/EXPERIMENT_PROTOCOL.md", "workbench/*.command", "workbench/*.app/Contents/Info.plist", "workbench/*.app/Contents/MacOS/*"]:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and not p.is_symlink():
                files[p.relative_to(root).as_posix()] = sha(p.read_bytes())
    if not files:
        raise ValueError("No project source found")
    git = None
    # Do not accidentally attribute this project to an enclosing unrelated repo.
    if (root / ".git").exists():
        def command(*args):
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
        git = {"commit": command("rev-parse", "HEAD"), "dirty": bool(command("status", "--porcelain"))}
    return {"policy": "argscope-source-manifest-v4", "files": files,
            "sha256": object_hash(files), "git": git}


def environment():
    packages = {d.metadata["Name"]: d.version for d in metadata.distributions() if d.metadata.get("Name")}
    return {"python": platform.python_version(), "system": platform.system(),
            "machine": platform.machine(), "installed_distributions": dict(sorted(packages.items()))}
