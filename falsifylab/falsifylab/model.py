"""Read-only adapter for the existing synchronous HobbesCA implementation."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import importlib.util
from pathlib import Path
import sys
import threading

import numpy as np
import scipy

_IMPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ModelSpec:
    source: str
    sha256: str

    @classmethod
    def from_root(cls, root: Path):
        source = root.resolve() / "final-project" / "src" / "ca_model.py"
        return cls(str(source), hashlib.sha256(source.read_bytes()).hexdigest())

    def verify(self):
        if hashlib.sha256(Path(self.source).read_bytes()).hexdigest() != self.sha256:
            raise ValueError("Model source changed after the experiment was configured")

    def provenance(self):
        return {"adapter": "hobbes_ca_sync_periodic_moore", "source_file": "ca_model.py",
                "source_sha256": self.sha256, "numpy": np.__version__, "scipy": scipy.__version__,
                "adapter_version": 2}


@lru_cache(maxsize=8)
def _load_model(spec: ModelSpec):
    spec.verify()
    with _IMPORT_LOCK:
        name = "falsifylab_hobbes_" + spec.sha256
        if name in sys.modules:
            return sys.modules[name].HobbesCA
        module_spec = importlib.util.spec_from_file_location(name, spec.source)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[name] = module
        previous = sys.dont_write_bytecode
        try:
            # The adapter never writes into the source repository.
            sys.dont_write_bytecode = True
            module_spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        finally:
            sys.dont_write_bytecode = previous
        return module.HobbesCA


def run_pair(spec: ModelSpec, settings: dict, b: float, p: float, seed: int) -> dict:
    """Run the same initial state with and without punishment."""
    model = _load_model(spec)
    control = model(grid_size=settings["grid_size"], b=b, p=0, seed=seed)
    treated = model(grid_size=settings["grid_size"], b=b, p=p,
                    leviathan_time=settings["intervention_time"], seed=seed)
    if not np.array_equal(control.grid, treated.grid):
        raise ValueError("Paired conditions do not share an initial state")
    control_series = [float(control.grid.mean())]
    treated_series = [float(treated.grid.mean())]
    for _ in range(settings["steps"]):
        control.step()
        treated.step()
        if control.time <= settings["intervention_time"] and not np.array_equal(control.grid, treated.grid):
            raise ValueError("Conditions diverged before the intervention transition")
        control_series.append(float(control.grid.mean()))
        treated_series.append(float(treated.grid.mean()))
    window = settings["final_window"]
    control_mean = float(np.mean(control_series[-window:]))
    treated_mean = float(np.mean(treated_series[-window:]))
    at_intervention = control_series[settings["intervention_time"]]
    regime = "all_defectors" if at_intervention == 0 else "all_cooperators" if at_intervention == 1 else "mixed"
    return {"b": b, "p": p, "seed": seed, "control_mean": control_mean,
            "treated_mean": treated_mean, "effect": treated_mean - control_mean,
            "cooperation_at_intervention": at_intervention, "pre_intervention_regime": regime,
            "control_series": control_series, "treated_series": treated_series}
