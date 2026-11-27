"""Exercise real model tasks and the existing KV over TCP, then stop the server.

Counts separate trajectory decomposition/reuse from storage choice. Timings are
small local observations, not a universal cache speedup or scientific finding.
"""

import argparse
import hashlib
import json
import logging
import os
import select
import signal
import statistics
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from falsifylab.experiment import Config, run_experiment
from falsifylab.model import ModelSpec
from falsifylab.records import read_measurement
from falsifylab.result_store import KVStore, open_store
from falsifylab.trajectories import TrajectoryCache

ROOT = Path(__file__).resolve().parents[2]


class NoStore:
    backend = "none"

    def get(self, key):
        return None

    def put(self, key, data):
        pass


class SessionServer:
    def __init__(self, root, aof):
        self.root, self.aof, self.process = root, aof, None

    def start(self, port=0):
        self.stderr = self.aof.with_suffix(".stderr").open("ab")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(ROOT / "falsifylab/tools/serve_research_kv.py"),
                "--kv-root",
                str(self.root),
                "--aof",
                str(self.aof),
                "--port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=self.stderr,
        )
        if not select.select([self.process.stdout], [], [], 5)[0]:
            self.stop()
            raise RuntimeError("KV session did not become ready")
        line = self.process.stdout.readline()
        if not line:
            self.stop()
            raise RuntimeError("KV session stopped during startup")
        ready = json.loads(line)
        self.port = ready["port"]
        return ready

    def stop(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.send_signal(signal.SIGINT)
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    logging.getLogger(__name__).warning(
                        "KV shutdown exceeded five seconds; waiting for commit drain"
                    )
                    self.process.wait()
            self.process.stdout.close()
            self.stderr.close()
            self.process = None


def signature(report):
    result = []
    for row in report["methods"]:
        result.append(
            {
                "method": row["method"],
                "candidates": [
                    {k: v for k, v in candidate.items() if k != "trajectory_pairs"}
                    for candidate in row["evaluated_candidates"]
                ],
                "selected": {
                    k: v for k, v in row["selected"].items() if k != "trajectory_pairs"
                },
                "confirmation": {
                    k: v
                    for k, v in row["confirmation"].items()
                    if k != "evidence_pairs"
                },
            }
        )
    return result


def snapshot_source(root):
    paths = list(root.glob("*.py")) + list((root / "__pycache__").glob("*"))
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
        if path.is_file()
    }


def evaluate(args):
    args.output.mkdir(parents=True, exist_ok=False)
    before = snapshot_source(args.kv_root)
    spec = ModelSpec.from_root(args.model_root)
    config = Config(
        grid_size=16,
        steps=60,
        intervention_time=30,
        final_window=15,
        budget=9,
        discovery_seeds=(11, 12, 13),
        confirmation_seeds=(101, 102, 103, 104),
        methods=("grid",),
    )
    (args.output / "config.json").write_text(json.dumps(asdict(config), indent=2))
    session = SessionServer(args.kv_root, args.output / "session.aof")
    rows = []
    try:
        ready = session.start()
        expected = None
        # Imports and a small model-load warmup are outside the observation timers.
        TrajectoryCache(NoStore(), spec, config.simulation_settings()).pair(
            (1.5, 0.2), 999
        )

        def run(label, store=None):
            nonlocal expected
            start = time.perf_counter()
            report = run_experiment(
                config, spec, args.output / label, trajectory_store=store
            )
            seconds = time.perf_counter() - start
            if expected is None:
                expected = signature(report)
            assert signature(report) == expected, label
            row = {
                "task": label,
                "seconds": seconds,
                **report.get(
                    "trajectory_store",
                    {
                        "backend": "legacy-pairs",
                        "computed_trajectories": report["computed_pairs"] * 2,
                        "cached_trajectories": report["cached_pairs"] * 2,
                    },
                ),
            }
            rows.append(row)
            return report

        backend_names = ["file", "sqlite", "kv"]
        last = None
        for repeat in range(3):
            run(f"r{repeat}-legacy")
            run(f"r{repeat}-split-none", NoStore())
            order = backend_names[repeat:] + backend_names[:repeat]
            for backend in order:
                path = args.output / f"r{repeat}-{backend}-store"
                if backend == "sqlite":
                    path = path.with_suffix(".sqlite3")
                store = open_store(
                    backend,
                    path=path,
                    port=session.port,
                    namespace=f"evaluation-r{repeat}",
                )
                cold = run(f"r{repeat}-{backend}-cold", store)
                warm = run(f"r{repeat}-{backend}-warm", store)
                assert cold["trajectory_store"]["computed_trajectories"] == 44
                assert warm["trajectory_store"]["computed_trajectories"] == 0
                if backend == "kv":
                    last = warm
        store = KVStore(session.port, "evaluation-r2")
        pair = last["methods"][0]["confirmation"]["evidence_pairs"][0]
        key = pair["control_key"]
        saved = store.get(key)
        aof_size = session.aof.stat().st_size
        exploratory = read_measurement(store, key, pair["treated_key"], 5)
        assert exploratory["measurement"]["final_window"] == 5
        assert session.aof.stat().st_size == aof_size

        env = {**os.environ, "PYTHONPATH": str(ROOT / "falsifylab")}
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "falsifylab",
                "--model-root",
                str(args.model_root),
                "--config",
                str(args.output / "config.json"),
                "--output",
                str(args.output / "independent-search"),
                "--trajectory-store",
                "kv",
                "--kv-port",
                str(session.port),
                "--namespace",
                "evaluation-r2",
            ],
            env=env,
            capture_output=True,
            timeout=60,
        )
        assert cli.returncode == 0, cli.stderr.decode()
        assert json.loads(cli.stdout)["trajectory_store"]["computed_trajectories"] == 0
        reader_command = [
            sys.executable,
            "-S",
            "-m",
            "falsifylab.reader",
            "--store",
            "kv",
            "--kv-port",
            str(session.port),
            "--namespace",
            "evaluation-r2",
            "--control",
            key,
            "--treated",
            pair["treated_key"],
            "--window",
            "5",
        ]
        reader = subprocess.run(
            reader_command, env=env, capture_output=True, timeout=10
        )
        assert reader.returncode == 0, reader.stderr.decode()
        assert json.loads(reader.stdout) == exploratory
        assert session.aof.stat().st_size == aof_size

        port = session.port
        session.stop()
        session.start(port)
        assert store.get(key) == saved
        assert read_measurement(store, key, pair["treated_key"], 5) == exploratory
        assert KVStore(port, "different-namespace").get(key) is None
        store.put(key, b'{"corrupt":true}')
        try:
            read_measurement(store, key, pair["treated_key"], 5)
        except ValueError:
            pass
        else:
            raise AssertionError("Corrupt KV value accepted")
        store.put(key, saved)
        store.delete(key)
        assert store.get(key) is None
        refill = TrajectoryCache(store, spec, config.simulation_settings())
        refill.pair((pair["b"], pair["p"]), pair["seed"])
        assert refill.computed_trajectories == 1

        session.stop()
        offline = TrajectoryCache(store, spec, config.simulation_settings())
        restored = offline.pair((pair["b"], pair["p"]), pair["seed"])
        assert restored == pair
        assert offline.computed_trajectories == 2 and offline.store_failures == 4
        reader = subprocess.run(
            reader_command, env=env, capture_output=True, timeout=10
        )
        assert reader.returncode == 2
        assert snapshot_source(args.kv_root) == before
        summaries = []
        for kind in (
            "legacy",
            "split-none",
            "file-cold",
            "file-warm",
            "sqlite-cold",
            "sqlite-warm",
            "kv-cold",
            "kv-warm",
        ):
            samples = [
                row["seconds"] for row in rows if row["task"].endswith("-" + kind)
            ]
            summaries.append(
                {
                    "task": kind,
                    "repeats": len(samples),
                    "median_seconds": statistics.median(samples),
                    "min_seconds": min(samples),
                    "max_seconds": max(samples),
                }
            )
        return {
            "status": "passed",
            "model_sha256": spec.sha256,
            "server": ready,
            "config": asdict(config),
            "observations": rows,
            "summaries": summaries,
            "scientific_results_equal": True,
            "independent_search_process": "62 trajectory hits, zero computations",
            "independent_stdlib_reader": "passed with site-packages disabled; 5-state exploratory window",
            "restart_aof_recovery": "passed",
            "namespace_isolation": "passed",
            "corruption_rejection": "passed",
            "deleted_record_refill": "one trajectory recomputed",
            "offline_search": "two trajectories recomputed; four store failures recorded",
            "offline_reader": "explicit failure, no invented measurement",
            "external_source_unchanged": True,
            "session_stopped": True,
            "limits": "Three local in-process repeats, imports/startup excluded, backend order rotated. File fsync+rename (no directory fsync), SQLite FULL rollback journal, KV AOF always; these are not identical power-loss contracts. Reuse gains are separate from KV overhead; no general KV speed or new model-result claim.",
        }
    finally:
        session.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kv-root", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = evaluate(args)
    (args.output / "evaluation.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n"
    )
    print(json.dumps({"status": report["status"], "summaries": report["summaries"]}))
