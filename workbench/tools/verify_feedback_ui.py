"""Isolated current-source feedback UI and actual process restart; no model calls."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
from verify_review_mvp import ROOT, SessionServer, snapshot_source, free_port, stop
from workbench.version import __version__
from workbench.review.provenance import source_snapshot, environment, object_hash


def main(out, kv_root):
    out.mkdir(parents=True, exist_ok=False)
    before, kv_before = source_snapshot(ROOT), snapshot_source(kv_root)
    provenance = {"source": before, "kv_source": kv_before, "environment": environment(), "provider": "demo-rules-v1", "human_review": "synthetic fixtures only", "model_calls": 0}
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2))
    session = SessionServer(kv_root, out / "isolated.aof")
    server = None
    logs = []
    result = {}
    try:
        session.start()
        def launch():
            port = free_port()
            log = (out / f"app-{len(logs)}.log").open("wb"); logs.append(log)
            process = subprocess.Popen([sys.executable, "-m", "workbench.review_app", "--provider", "demo", "--port", str(port), "--kv-port", str(session.port), "--namespace", "feedback-ui", "--data-dir", str(out / "data")], cwd=ROOT, stdout=log, stderr=log)
            for _ in range(200):
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/argument-health", timeout=.2) as response:
                        assert json.load(response)["version"] == __version__
                    return process, f"http://127.0.0.1:{port}"
                except OSError:
                    if process.poll() is not None: raise RuntimeError("App stopped; inspect log")
                    time.sleep(.05)
            stop(process)
            raise RuntimeError("Startup deadline")
        def browser(base, run_id=""):
            env = {**os.environ, "REVIEW_TEST_URL": base, "REVIEW_ARTIFACTS": str(out), "FEEDBACK_RESTART_ID": run_id}
            completed = subprocess.run(["node", str(ROOT / "workbench/tests/browser_feedback.cjs")], env=env, capture_output=True, text=True, timeout=90)
            (out / ("browser-restart.log" if run_id else "browser-first.log")).write_text(completed.stdout + completed.stderr)
            if completed.returncode: raise RuntimeError(completed.stderr)
            return json.loads(completed.stdout)
        server, base = launch()
        result["first"] = browser(base)
        stop(server); server = None
        server, base = launch()
        result["restart"] = browser(base, result["first"]["run_id"])
        result["status"] = "passed"
    except Exception as error:
        result.update(status="failed", error=str(error))
    finally:
        stop(server); session.stop()
        for log in logs: log.close()
    result.update(source_unchanged=before["sha256"] == source_snapshot(ROOT)["sha256"], kv_source_unchanged=kv_before == snapshot_source(kv_root), provenance_sha256=object_hash(provenance))
    (out / "result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "passed" and result["source_unchanged"] and result["kv_source_unchanged"] else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", required=True, type=Path)
    p.add_argument("--kv-root", required=True, type=Path)
    a = p.parse_args()
    raise SystemExit(main(a.output.resolve(), a.kv_root))
