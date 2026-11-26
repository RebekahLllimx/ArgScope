"""Isolated packaged authored-paper UI acceptance, with synthetic reviewer records."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from verify_review_mvp import free_port, stop, SessionServer, snapshot_source, ROOT


def main(args):
    delivery = args.delivery.resolve()
    out = args.output.resolve()
    shots = args.screenshots.resolve()
    out.mkdir(parents=True, exist_ok=True)
    shots.mkdir(parents=True, exist_ok=True)
    before = snapshot_source(args.kv_root)
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        session = SessionServer(args.kv_root, temp / "session.aof")
        server = None
        session.start()
        try:
            port = free_port()
            base = f"http://127.0.0.1:{port}"
            with (temp / "app.log").open("w+b") as log:
                server = subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        str(delivery / "launch.py"),
                        "--provider",
                        "papers",
                        "--port",
                        str(port),
                        "--kv-port",
                        str(session.port),
                        "--namespace",
                        "paper-browser-test",
                        "--data-dir",
                        str(temp / "data"),
                    ],
                    cwd=temp,
                    stdout=log,
                    stderr=log,
                )
                for _ in range(200):
                    try:
                        with urlopen(
                            base + "/api/argument-health", timeout=0.2
                        ) as response:
                            health = json.load(response)
                            assert health["kv_available"]
                            assert not health["provider"]["live_model"]
                        break
                    except OSError:
                        if server.poll() is not None:
                            log.seek(0)
                            raise RuntimeError(log.read().decode())
                        time.sleep(0.05)
                else:
                    raise RuntimeError("App startup deadline exceeded")
                env = {
                    **os.environ,
                    "REVIEW_TEST_URL": base,
                    "REVIEW_ARTIFACTS": str(out),
                    "REVIEW_SCREENSHOTS": str(shots),
                }
                browser = subprocess.run(
                    ["node", str(ROOT / "workbench/tests/browser_first_order.cjs")],
                    env=env,
                    capture_output=True,
                    timeout=100,
                )
                if browser.returncode:
                    raise RuntimeError(browser.stderr.decode())
                result = json.loads(browser.stdout)
                for packet in out.glob("*-test-packet.json"):
                    subprocess.run(
                        [
                            sys.executable,
                            "-I",
                            str(delivery / "launch.py"),
                            "verify-report",
                            str(packet),
                        ],
                        cwd=temp,
                        check=True,
                        capture_output=True,
                        timeout=15,
                    )
        finally:
            stop(server)
            session.stop()
    assert snapshot_source(args.kv_root) == before
    result.update(
        package_version=json.loads((delivery / "manifest.json").read_text())["version"],
        isolated_package=True,
        cli_replay=True,
        services_stopped=True,
        kv_source_unchanged=True,
    )
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--kv-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--screenshots", type=Path, required=True)
    main(parser.parse_args())
