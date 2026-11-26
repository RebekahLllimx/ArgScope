"""Verify the actual portable MVP, browser exports and restart, then stop services."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen, Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "falsifylab/tools"))
from evaluate_kv import SessionServer, snapshot_source


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def stop(process):
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def verify(args):
    delivery = args.delivery.resolve()
    manifest = json.loads((delivery / "manifest.json").read_text())
    for name, expected in manifest["members"].items():
        data = (delivery / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected
        assert b"/Users/" not in data
        assert not any(
            part in name.split("/")
            for part in ("outputs", "data", ".venv", "__pycache__", "node_modules")
        )
    subprocess.run(["zsh", "-n", str(delivery / "Launch.command")], check=True)
    help_run = subprocess.run(
        ["zsh", str(delivery / "Launch.command"), "--help"],
        capture_output=True,
        timeout=15,
    )
    assert help_run.returncode == 0, help_run.stderr.decode()
    before = snapshot_source(args.kv_root)
    args.artifacts.mkdir(parents=True, exist_ok=True)
    args.screenshots.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        session = SessionServer(args.kv_root, temporary / "isolated.aof")
        server = None
        logs = []

        def launch(data_dir, managed=False):
            port = free_port()
            log = (temporary / f"app-{len(logs)}.log").open("w+b")
            logs.append(log)
            command = [
                sys.executable,
                "-I",
                str(delivery / "launch.py"),
                "--provider",
                "demo",
                "--port",
                str(port),
                "--data-dir",
                str(data_dir),
                "--namespace",
                "mvp-browser",
            ]
            command += (
                ["--kv-root", str(args.kv_root)]
                if managed
                else ["--kv-port", str(session.port)]
            )
            process = subprocess.Popen(command, cwd=temporary, stdout=log, stderr=log)
            base = f"http://127.0.0.1:{port}"
            for _ in range(200):
                try:
                    with urlopen(
                        base + "/api/argument-health", timeout=0.2
                    ) as response:
                        health = json.load(response)
                        assert health["kv_available"]
                        assert not health["provider"]["live_model"]
                    return process, base, log
                except OSError:
                    if process.poll() is not None:
                        log.seek(0)
                        raise RuntimeError(log.read().decode())
                    time.sleep(0.05)
            stop(process)
            raise RuntimeError("Packaged MVP did not start")

        def browser(base, paused=None):
            env = {
                **os.environ,
                "REVIEW_TEST_URL": base,
                "REVIEW_SCREENSHOTS": str(args.screenshots.resolve()),
                "REVIEW_ARTIFACTS": str(args.artifacts.resolve()),
            }
            if paused:
                env["REVIEW_RESTART_RUN"] = paused
            result = subprocess.run(
                ["node", str(ROOT / "workbench/tests/browser_review_mvp.cjs")],
                cwd=temporary,
                env=env,
                capture_output=True,
                timeout=90,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.decode())
            return json.loads(result.stdout)

        try:
            ready = session.start()
            server, base, log = launch(temporary / "data")
            first = browser(base)
            shutil.copyfile(
                args.artifacts / "argument-review.json",
                args.artifacts / "before-restart.json",
            )
            stop(server)
            server, base, log = launch(temporary / "data")
            resumed = browser(base, first["paused_run"])
            replays = []
            for name in ("before-restart.json", "argument-review.json"):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        str(delivery / "launch.py"),
                        "verify-report",
                        str((args.artifacts / name).resolve()),
                    ],
                    cwd=temporary,
                    capture_output=True,
                    timeout=15,
                )
                assert result.returncode == 0, result.stderr.decode()
                assert json.loads(result.stdout)["status"] == "snapshot_verified"
                replays.append(name)
            # A real unavailable service must not create a successful shared-profile run.
            session.stop()
            body = {
                "question": "Which premise is being used?",
                "notes": "",
                "sources": [
                    {
                        "id": "main",
                        "title": "Offline control",
                        "text": "One premise. A conclusion.",
                        "kind": "authored_control",
                    }
                ],
            }
            try:
                urlopen(
                    Request(
                        base + "/api/argument-runs",
                        data=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json"},
                    ),
                    timeout=3,
                )
            except __import__(
                "urllib.error", fromlist=["HTTPError"]
            ).HTTPError as error:
                assert error.code == 503
            else:
                raise AssertionError(
                    "Offline KV silently accepted a new shared-profile run"
                )
            stop(server)
            # Exercise the real CLI's own KV lifecycle as well as an externally managed test service.
            server, base, log = launch(temporary / "managed-data", managed=True)
            with urlopen(base + "/api/argument-examples") as response:
                example = json.load(response)[1]["request"]
            with urlopen(
                Request(
                    base + "/api/argument-runs",
                    data=json.dumps(example).encode(),
                    headers={"Content-Type": "application/json"},
                )
            ) as response:
                run_id = json.load(response)["id"]
            for _ in range(200):
                with urlopen(base + "/api/argument-runs/" + run_id) as response:
                    value = json.load(response)
                if value["status"] == "awaiting_review":
                    break
                assert value["status"] not in ("failed", "budget_exhausted"), value
                time.sleep(0.05)
            else:
                raise AssertionError("Managed KV workflow did not pause")
            log.flush()
            log.seek(0)
            startup = json.loads(log.readline())
            managed_port = startup["kv_port"]
            assert startup["managed_kv"]
            stop(server)
            with socket.socket() as sock:
                sock.settimeout(0.2)
                assert (
                    sock.connect_ex(("127.0.0.1", managed_port)) != 0
                ), "Managed KV was left running"
            assert snapshot_source(args.kv_root) == before
            return {
                "status": "passed",
                "version": manifest["version"],
                "provider_mode": "explicit deterministic demo; no live model calls",
                "first_browser": first,
                "restart_browser": resumed,
                "isolated_source_delivery": True,
                "browser_export_cli_replays": replays,
                "offline_kv_explicit_503": True,
                "native_managed_kv_lifecycle": True,
                "external_kv_source_unchanged": True,
                "servers_stopped": True,
                "package_members": len(manifest["members"]) + 1,
                "kv_source_sha256": ready["source_sha256"],
            }
        finally:
            stop(server)
            session.stop()
            for log in logs:
                log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--screenshots", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
