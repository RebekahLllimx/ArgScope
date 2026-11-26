"""Build a portable standard-library zipapp and a self-contained delivery folder."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipapp
import zipfile

ROOT = Path(__file__).resolve().parents[2]
BOOTSTRAP = """import sys
if sys.version_info < (3, 10):
    raise SystemExit("ArgScope Prototype requires Python 3.10+. Try python3.10 ArgScopePrototype.pyz")
from workbench.legacy.prototype import main
raise SystemExit(main())
"""
LAUNCH = """#!/bin/zsh
set -e
cd -- "${0:A:h}"
for runtime in python3.14 python3.13 python3.12 python3.11 python3.10 python3 /Library/Frameworks/Python.framework/Versions/3.10/bin/python3.10; do
  if command -v "$runtime" >/dev/null 2>&1 && "$runtime" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>/dev/null; then
    exec "$runtime" ArgScopePrototype.pyz --open-browser --data-dir ./data "$@"
  fi
done
print 'Python 3.10 or newer is required. No Python packages are needed.'
read '?Press Enter to close.'
"""
LAUNCH_KV = """#!/bin/zsh
set -e
cd -- "${0:A:h}"
exec ./Launch.command --trajectory-store kv --kv-port "${RESEARCH_KV_PORT:-6379}" --namespace "${RESEARCH_KV_NAMESPACE:-research}" "$@"
"""
ARCHIVE_README = """# ArgScope portable formal editor

This archive contains the standalone formal editor and its example reconstructions.
It requires Python 3.10 or newer and no third-party Python packages. The full
Research Workbench, source-library interface and live model providers are separate
from this dependency-free editor.

## Start

On macOS, open `Launch.command`, or run:

```bash
python3.10 ArgScopePrototype.pyz --data-dir ./data --open-browser
```

The service listens on http://127.0.0.1:8766. Add `--port 8767` if that port is
occupied. Stop it with Control+C; saved snapshots remain in the data directory.

## Review and replay

Open a draft, inspect its source and formulas, record the reading's review, and
run the formal check. Editing the draft invalidates approvals for its old content.
The editor can save snapshots and export JSON results and HTML reports.

```bash
python3.10 ArgScopePrototype.pyz replay result.json
python3.10 ArgScopePrototype.pyz --help
```

Formal results concern the declared formulas and assumptions. The archive does
not generate interpretations with a model or authenticate reviewer identities.

## Optional shared numerical records

The trajectory reader can inspect records produced by the separate FalsifyLab
CLI. To use an existing trusted local KV service:

```bash
python3.10 ArgScopePrototype.pyz --open-browser --trajectory-store kv \\
  --kv-port 16379 --namespace research
```

Use the producer's port and namespace. The reader remeasures completed records;
it does not run the numerical simulation. File and SQLite stores are also
available through `--trajectory-store` and `--store-path`.
"""


def build(output):
    output = Path(output)
    if output.exists():
        raise ValueError(
            "Choose a new output directory; existing deliveries are not overwritten"
        )
    output.mkdir(parents=True)
    hashes = {}
    with tempfile.TemporaryDirectory() as folder:
        stage = Path(folder)
        for package in ("argscope", "falsifylab"):
            destination = stage / package
            destination.mkdir()
            for source in (ROOT / package / package).glob("*.py"):
                shutil.copyfile(source, destination / source.name)
        destination = stage / "workbench"
        destination.mkdir()
        for name in ("__init__.py", "contracts.py"):
            shutil.copyfile(ROOT / "workbench/workbench" / name, destination / name)
        shutil.copytree(
            ROOT / "workbench/workbench/legacy", destination / "legacy"
        )
        shutil.copytree(ROOT / "workbench/examples", stage / "workbench/demo")
        (stage / "__main__.py").write_text(BOOTSTRAP)
        for source in sorted(stage.rglob("*")):
            if source.is_file():
                hashes[source.relative_to(stage).as_posix()] = hashlib.sha256(
                    source.read_bytes()
                ).hexdigest()
        zipapp.create_archive(
            stage,
            output / "ArgScopePrototype.pyz",
            interpreter="/usr/bin/env python3.10",
            compressed=True,
        )
    (output / "Launch.command").write_text(LAUNCH)
    (output / "Launch.command").chmod(0o755)
    (output / "Launch KV Reader.command").write_text(LAUNCH_KV)
    (output / "Launch KV Reader.command").chmod(0o755)
    (output / "README.md").write_text(ARCHIVE_README)
    manifest = {
        "prototype_version": "0.3.0",
        "requires": "Python 3.10+; standard library only for the prototype",
        "archive_sha256": hashlib.sha256(
            (output / "ArgScopePrototype.pyz").read_bytes()
        ).hexdigest(),
        "members": hashes,
        "scope": "Local formal prototype and read-only exploratory trajectory measurement; numerical execution is a separate CLI requiring NumPy/SciPy",
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    archive = output.parent / (output.name + ".zip")
    if archive.exists():
        raise ValueError("Delivery ZIP already exists")
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zipped:
        for source in sorted(output.iterdir()):
            zipped.write(source, output.name + "/" + source.name)
    return {
        "directory": str(output),
        "zip": str(archive),
        "archive_sha256": manifest["archive_sha256"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output)))
