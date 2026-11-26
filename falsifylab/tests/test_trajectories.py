from contextlib import closing
from copy import deepcopy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from falsifylab.experiment import Config, run_experiment
from falsifylab.model import ModelSpec, run_pair
from falsifylab.records import decode_record, measure_pair, read_measurement
from falsifylab.result_store import (
    FileStore,
    KVStore,
    MAX_RECORD_BYTES,
    SQLiteStore,
    StoreError,
    canonical,
    fingerprint,
)
from falsifylab.trajectories import TrajectoryCache


class TinyModel:
    """Deterministic fixture, not a scientific model or timing workload."""

    def __init__(self, grid_size, b, p, leviathan_time=None, seed=None):
        self.rng = np.random.default_rng(seed)
        self.grid = (self.rng.random((grid_size, grid_size)) < 0.5).astype(np.int8)
        self.time, self.p, self.intervention = 0, p, leviathan_time

    def step(self):
        self.grid = np.roll(self.grid, 1, axis=0)
        if (
            self.intervention is not None
            and self.time >= max(1, self.intervention)
            and self.p > 0
        ):
            self.grid[0, 0] = 1
        self.time += 1


class SocketFixture:
    def __init__(self, response):
        self.response, self.sent = response, None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        self.sent = data

    def makefile(self, mode):
        return io.BytesIO(self.response)


class StoreTests(unittest.TestCase):
    def test_file_sqlite_roundtrip_and_unrelated_database_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            key = "a" * 64
            for store in (
                FileStore(Path(folder) / "files"),
                SQLiteStore(Path(folder) / "cache.sqlite3"),
            ):
                self.assertIsNone(store.get(key))
                store.put(key, b"{}")
                self.assertEqual(store.get(key), b"{}")
                store.put(key, b"replacement")
                self.assertEqual(store.get(key), b"replacement")
                store.delete(key)
                self.assertIsNone(store.get(key))
                with self.assertRaises(StoreError):
                    store.put(key, b"x" * (MAX_RECORD_BYTES + 1))
                with self.assertRaises(ValueError):
                    store.get("../escape")
            path = Path(folder) / "history.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE heads(id TEXT)")
            with self.assertRaisesRegex(ValueError, "separate"):
                SQLiteStore(path)

    def test_resp_framing_and_reply_guards(self):
        store, key = KVStore(16379, "fixture"), "a" * 64
        sock = SocketFixture(b"$5\r\na\r\nbc\r\n")
        with patch("socket.create_connection", return_value=sock):
            self.assertEqual(store.get(key), b"a\r\nbc")
        self.assertIn(b"falsifylab:trajectory-v1:fixture:", sock.sent)
        for response in (b"$-1\r\n", b"$0\r\n\r\n"):
            with patch(
                "socket.create_connection", return_value=SocketFixture(response)
            ):
                self.assertEqual(
                    store.get(key), None if response.startswith(b"$-1") else b""
                )
        for response in (
            b"$99999999\r\n",
            b"$3\r\nx\r\n",
            b"-ERR broken\r\n",
            b"+PONG\r\n",
            b"$-2\r\n",
        ):
            with self.subTest(response=response), patch(
                "socket.create_connection", return_value=SocketFixture(response)
            ):
                with self.assertRaises(StoreError):
                    store.get(key)
        with patch("socket.create_connection", return_value=SocketFixture(b"+OK\r\n")):
            store.put(key, b"{}")
        with self.assertRaises(ValueError):
            KVStore(1)
        with self.assertRaises(ValueError):
            KVStore(6379, "not:a:namespace")


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / "final-project/src/ca_model.py"
        source.parent.mkdir(parents=True)
        source.write_text("# Fixture, model execution patched.\n")
        self.spec = ModelSpec.from_root(self.root)
        self.settings = {
            "grid_size": 4,
            "steps": 8,
            "intervention_time": 3,
            "final_window": 2,
        }
        for name in (
            "falsifylab.trajectories._load_model",
            "falsifylab.model._load_model",
        ):
            patcher = patch(name, return_value=TinyModel)
            patcher.start()
            self.addCleanup(patcher.stop)

    def cache(self, name="files", settings=None):
        return TrajectoryCache(
            FileStore(self.root / name), self.spec, settings or self.settings
        )

    def test_split_pair_matches_existing_runner_and_zero_penalty(self):
        cache = self.cache()
        for p in (0, 0.5):
            actual = cache.pair((1.5, p), 11)
            original = run_pair(self.spec, self.settings, 1.5, p, 11)
            for key, value in original.items():
                self.assertEqual(actual[key], value)

    def test_control_reuse_across_penalties_and_window_change(self):
        cache = self.cache()
        first = cache.pair((1.5, 0.2), 11)
        second = cache.pair((1.5, 0.5), 11)
        self.assertEqual(
            (cache.computed_trajectories, cache.cached_trajectories), (3, 1)
        )
        self.assertEqual(first["control_key"], second["control_key"])
        changed = self.cache(settings={**self.settings, "final_window": 4})
        measured = changed.pair((1.5, 0.2), 11)
        self.assertEqual(
            (changed.computed_trajectories, changed.cached_trajectories), (0, 2)
        )
        self.assertEqual(measured["treated_key"], first["treated_key"])
        direct = read_measurement(
            changed.store, first["control_key"], first["treated_key"], 4
        )
        self.assertEqual(direct["measurement"], measured)

    def test_dynamics_model_and_environment_changes_do_not_hit(self):
        cache = self.cache()
        initial = cache.pair((1.5, 0.2), 11)
        for point, seed, settings in (
            ((1.6, 0.2), 11, self.settings),
            ((1.5, 0.2), 12, self.settings),
            ((1.5, 0.2), 11, {**self.settings, "steps": 9}),
        ):
            other = self.cache(settings=settings)
            result = other.pair(point, seed)
            self.assertNotEqual(result["treated_key"], initial["treated_key"])
        with patch("platform.machine", return_value="other-architecture"):
            other = self.cache()
            self.assertNotEqual(
                other.pair((1.5, 0.2), 11)["control_key"], initial["control_key"]
            )
        source = Path(self.spec.source)
        source.write_text("# Changed fixture\n")
        other = TrajectoryCache(
            cache.store, ModelSpec.from_root(self.root), self.settings
        )
        self.assertNotEqual(
            other.pair((1.5, 0.2), 11)["control_key"], initial["control_key"]
        )

    def test_corrupt_hash_identity_and_preintervention_mismatch_rejected(self):
        cache = self.cache()
        pair = cache.pair((1.5, 0.2), 11)
        key = pair["treated_key"]
        original = json.loads(cache.store.get(key))
        broken = deepcopy(original)
        broken["result"]["cooperation"][-1] = 0.9
        cache.store.put(key, canonical(broken))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            cache.pair((1.5, 0.2), 11)
        broken["result_sha256"] = fingerprint(broken["result"])
        broken["result"]["state_sha256"][1] = "b" * 64
        broken["result_sha256"] = fingerprint(broken["result"])
        control = decode_record(
            cache.store.get(pair["control_key"]), pair["control_key"]
        )
        with self.assertRaisesRegex(ValueError, "before"):
            measure_pair(control, broken, 2)
        broken = deepcopy(original)
        broken["input"]["dynamics"]["seed"] = 99
        with self.assertRaisesRegex(ValueError, "key"):
            decode_record(canonical(broken), key)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            decode_record(b'{"schema":1,"schema":2}', key)
        with self.assertRaises(ValueError):
            measure_pair(control, original, 6)

    def test_missing_store_recomputes_and_reader_does_not_invent(self):
        cache = self.cache()
        cache.store.get = lambda key: (_ for _ in ()).throw(StoreError("offline"))
        cache.store.put = lambda key, data: (_ for _ in ()).throw(StoreError("offline"))
        result = cache.pair((1.5, 0.2), 11)
        self.assertEqual(cache.computed_trajectories, 2)
        self.assertEqual(cache.store_failures, 4)
        with self.assertRaises(StoreError):
            read_measurement(
                cache.store, result["control_key"], result["treated_key"], 2
            )

    def test_experiment_v3_preserves_v2_statistics_and_archives_evidence(self):
        config = Config(
            grid_size=4,
            steps=8,
            intervention_time=3,
            final_window=2,
            budget=4,
            discovery_seeds=(11, 12),
            confirmation_seeds=(101, 102),
        )
        original = run_experiment(config, self.spec, self.root / "original")
        store = FileStore(self.root / "shared")
        shared = run_experiment(
            config,
            self.spec,
            self.root / "shared-run",
            trajectory_store=store,
            workers=2,
        )
        self.assertEqual(shared["experiment_version"], 3)
        for a, b in zip(original["methods"], shared["methods"]):
            self.assertEqual(
                a["confirmation"],
                {k: v for k, v in b["confirmation"].items() if k != "evidence_pairs"},
            )
            self.assertEqual(
                a["selected"],
                {k: v for k, v in b["selected"].items() if k != "trajectory_pairs"},
            )
            self.assertTrue(b["confirmation"]["evidence_pairs"][0]["control_series"])
        resumed = run_experiment(
            config,
            self.spec,
            self.root / "shared-run",
            resume=True,
            trajectory_store=store,
        )
        self.assertEqual(resumed["trajectory_store"]["computed_trajectories"], 0)
        self.assertFalse((self.root / "shared-run/pairs").exists())
        self.assertEqual(resumed["methods"], shared["methods"])


if __name__ == "__main__":
    unittest.main()
