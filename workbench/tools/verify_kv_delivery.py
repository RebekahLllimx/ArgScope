"""Verify the final independent archive against real KV records, then stop both."""
import argparse
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
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "falsifylab/tools"))
from evaluate_kv import SessionServer, snapshot_source
from verify_delivery import verify


def verify_kv(args):
    # Run the original formal/browser delivery checks on this same final artifact.
    formal = verify(args.delivery)
    archive = args.delivery.resolve() / "ArgScopePrototype.pyz"
    report_file = args.evidence.resolve() / "r2-kv-warm/report.json"
    report = json.loads(report_file.read_text())
    pair = report["methods"][0]["confirmation"]["evidence_pairs"][0]
    before = snapshot_source(args.kv_root)
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        shutil.copyfile(args.evidence / "session.aof", folder / "session.aof")
        session = SessionServer(args.kv_root, folder / "session.aof")
        server = None
        try:
            ready = session.start()
            aof_size = session.aof.stat().st_size
            reader = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(archive),
                    "trajectory-read",
                    "--store",
                    "kv",
                    "--kv-port",
                    str(ready["port"]),
                    "--namespace",
                    "evaluation-r2",
                    "--control",
                    pair["control_key"],
                    "--treated",
                    pair["treated_key"],
                    "--window",
                    "5",
                ],
                cwd=folder,
                capture_output=True,
                timeout=10,
            )
            assert reader.returncode == 0, reader.stderr.decode()
            assert json.loads(reader.stdout)["measurement"]["final_window"] == 5
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            server = subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    str(archive),
                    "--port",
                    str(port),
                    "--data-dir",
                    str(folder / "data"),
                    "--trajectory-store",
                    "kv",
                    "--kv-port",
                    str(ready["port"]),
                    "--namespace",
                    "evaluation-r2",
                ],
                cwd=folder,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            base = f"http://127.0.0.1:{port}"
            for _ in range(100):
                try:
                    with urlopen(base + "/api/health", timeout=0.2) as response:
                        assert json.load(response)["trajectory_store"] == "kv"
                    break
                except OSError:
                    if server.poll() is not None:
                        raise RuntimeError(server.stderr.read().decode())
                    time.sleep(0.05)
            else:
                raise RuntimeError("Packaged reader did not start")
            env = {
                **os.environ,
                "WORKBENCH_TEST_URL": base,
                "WORKBENCH_TRAJECTORY_REPORT": str(report_file),
                "WORKBENCH_SCREENSHOT_DIR": str(ROOT / "workbench/docs/images"),
            }
            browser = subprocess.run(
                ["node", str(ROOT / "workbench/tests/browser_trajectories.cjs")],
                cwd=folder,
                env=env,
                capture_output=True,
                timeout=60,
            )
            assert browser.returncode == 0, browser.stderr.decode()
            assert session.aof.stat().st_size == aof_size, "Reader wrote to the KV AOF"
            assert snapshot_source(args.kv_root) == before
            for name in ("Launch.command", "Launch KV Reader.command"):
                subprocess.run(["zsh", "-n", str(args.delivery / name)], check=True)
            return {
                "status": "passed",
                "archive_sha256": formal["archive_sha256"],
                "formal_delivery": formal,
                "kv_browser": json.loads(browser.stdout),
                "isolated_archive_reader": True,
                "isolated_archive_server": True,
                "no_site_packages_or_model_path": True,
                "kv_aof_unchanged_by_readers": True,
                "external_kv_source_unchanged": True,
                "servers_stopped_on_exit": True,
            }
        finally:
            if server is not None:
                if server.poll() is None:
                    server.send_signal(signal.SIGINT)
                    try:
                        server.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait()
                server.stdout.close()
                server.stderr.close()
            session.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", required=True, type=Path)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = verify_kv(args)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
