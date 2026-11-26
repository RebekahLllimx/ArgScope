"""Window-independent trajectory execution, shared by numerical experiments."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import platform
import sys
import threading

import numpy as np
import scipy

from .model import _load_model
from .records import decode_record, measure_pair, validate_input, validate_record
from .result_store import StoreError, canonical, fingerprint


def grid_hash(grid):
    return hashlib.sha256(grid.tobytes(order="C")).hexdigest()


class TrajectoryCache:
    def __init__(self, store, spec, settings):
        self.store, self.spec, self.settings = store, spec, settings
        self.computed = self.hits = 0  # Pair accounting, not trajectory accounting.
        self.computed_trajectories = self.cached_trajectories = 0
        self.store_failures = 0
        self.warnings = []
        self.lock = threading.Lock()

    def failure(self, error):
        with self.lock:
            self.store_failures += 1
            if len(self.warnings) < 8:
                self.warnings.append(str(error))

    def trajectory(self, b, p, seed, intervention_time):
        self.spec.verify()
        model = _load_model(self.spec)(
            grid_size=self.settings["grid_size"],
            b=b,
            p=p,
            leviathan_time=intervention_time,
            seed=seed,
        )
        value = {
            "schema": "trajectory-input-v1",
            "model": self.spec.provenance(),
            "environment": {
                "python": platform.python_version(),
                "implementation": sys.implementation.name,
                "system": platform.system(),
                "machine": platform.machine(),
                "numpy": np.__version__,
                "scipy": scipy.__version__,
                "grid_dtype": str(model.grid.dtype),
                "rng": type(model.rng.bit_generator).__name__,
                "trajectory_adapter": 1,
            },
            "dynamics": {
                "grid_size": self.settings["grid_size"],
                "steps": self.settings["steps"],
                "b": float(b),
                "p": float(p),
                "seed": seed,
                "intervention_time": intervention_time,
            },
            "initial_state_sha256": grid_hash(model.grid),
            "observation": "cooperation-and-grid-sha256-state-0-through-steps-v1",
        }
        validate_input(value)
        key = fingerprint(value)
        try:
            saved = self.store.get(key)
        except StoreError as error:
            self.failure(error)
            saved = None
        if saved is not None:
            record = decode_record(saved, key, value)
            with self.lock:
                self.cached_trajectories += 1
            return record, False
        series, states = [float(model.grid.mean())], [value["initial_state_sha256"]]
        for _ in range(self.settings["steps"]):
            model.step()
            series.append(float(model.grid.mean()))
            states.append(grid_hash(model.grid))
        self.spec.verify()
        result = {"cooperation": series, "state_sha256": states}
        record = {
            "schema": "trajectory-record-v1",
            "key": key,
            "input": value,
            "result": result,
            "result_sha256": fingerprint(result),
        }
        validate_record(record, key, value)
        try:
            self.store.put(key, canonical(record))
        except StoreError as error:
            self.failure(error)
        with self.lock:
            self.computed_trajectories += 1
        return record, True

    def pair(self, point, seed):
        control, first = self.trajectory(point[0], 0, seed, None)
        treated, second = self.trajectory(
            point[0], point[1], seed, self.settings["intervention_time"]
        )
        result = measure_pair(control, treated, self.settings["final_window"])
        with self.lock:
            if first or second:
                self.computed += 1
            else:
                self.hits += 1
        return result

    def evaluate(self, point, seeds, workers):
        if workers == 1:
            return [self.pair(point, seed) for seed in seeds]
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return list(executor.map(lambda seed: self.pair(point, seed), seeds))

    def accounting(self):
        return {
            "backend": self.store.backend,
            "computed_trajectories": self.computed_trajectories,
            "cached_trajectories": self.cached_trajectories,
            "store_failures": self.store_failures,
            "warnings": self.warnings,
            "miss_policy": "recompute; concurrent misses may duplicate work",
        }
