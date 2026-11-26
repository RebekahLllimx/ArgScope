"""Verify the delivered archive from a temporary working directory, then stop it."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def verify(delivery):
    delivery = Path(delivery).resolve()
    archive = delivery / "ArgScopePrototype.pyz"
    manifest = json.loads((delivery / "manifest.json").read_text())
    assert (
        hashlib.sha256(archive.read_bytes()).hexdigest() == manifest["archive_sha256"]
    )
    with zipfile.ZipFile(archive) as zipped:
        assert set(zipped.namelist()) >= {
            "__main__.py",
            "workbench/legacy/prototype.py",
            "argscope/sat.py",
            "workbench/legacy/web/app.js",
        }
        for name, expected in manifest["members"].items():
            data = zipped.read(name)
            assert hashlib.sha256(data).hexdigest() == expected
            assert "/Users/" not in data.decode("utf-8")
            assert not any(
                part in name.split("/")
                for part in (
                    "outputs",
                    ".venv",
                    "__pycache__",
                    "WORKING.md",
                    "PROJECT_PLAN.md",
                )
            )
    subprocess.run(["zsh", "-n", str(delivery / "Launch.command")], check=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as folder:
        server = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                str(archive),
                "--port",
                str(port),
                "--data-dir",
                str(Path(folder) / "data"),
            ],
            cwd=folder,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(base + "/api/health", timeout=0.2) as response:
                        assert json.load(response)["status"] == "ok"
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError(server.stderr.read().decode())
                    time.sleep(0.05)
            else:
                raise RuntimeError("Prototype did not start")
            env = {
                **os.environ,
                "WORKBENCH_TEST_URL": base,
                "WORKBENCH_SCREENSHOT_DIR": str(ROOT / "workbench/docs/images"),
            }
            run = subprocess.run(
                ["node", str(ROOT / "workbench/tests/browser_prototype.cjs")],
                env=env,
                cwd=folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
            )
            if run.returncode:
                raise RuntimeError(run.stderr.decode())
            browser = json.loads(run.stdout)
            exported = ROOT / "workbench/outputs/prototype-browser/result-bundle.json"
            replay = subprocess.run(
                [sys.executable, "-I", "-S", str(archive), "replay", str(exported)],
                cwd=folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10,
            )
            assert replay.returncode == 0, replay.stderr.decode()
            assert json.loads(replay.stdout)["status"] == "replay_verified"
            return {
                "status": "passed",
                "archive_sha256": manifest["archive_sha256"],
                "isolated_python": True,
                "temporary_working_directory": True,
                "browser": browser,
                "export_replay": "verified",
            }
        finally:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.delivery)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
