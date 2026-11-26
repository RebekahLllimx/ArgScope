"""Budgeted candidate search, held-out confirmation and replayable artifacts."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

import numpy as np
from scipy.stats import t

from .model import ModelSpec, run_pair


class ExperimentError(ValueError):
    """An experiment cannot be executed under its declared contract."""


@dataclass(frozen=True)
class Config:
    grid_size: int = 20
    steps: int = 80
    intervention_time: int = 40
    final_window: int = 20
    temptation: tuple = (1.1, 1.95)
    penalty: tuple = (0.05, 0.5)
    budget: int = 8
    discovery_seeds: tuple = (11, 12, 13)
    confirmation_seeds: tuple = (101, 102, 103, 104, 105, 106, 107, 108)
    minimum_effect: float = 0.05
    search_seed: int = 42
    family_alpha: float = 0.05
    methods: tuple = ("grid", "random")

    @classmethod
    def from_dict(cls, value: dict):
        allowed = {field.name for field in fields(cls)}
        if not isinstance(value, dict) or set(value) - allowed:
            raise ExperimentError("Unknown experiment configuration field")
        tuples = {
            "temptation",
            "penalty",
            "discovery_seeds",
            "confirmation_seeds",
            "methods",
        }
        converted = {}
        for key, item in value.items():
            if key in tuples:
                if not isinstance(item, (list, tuple)):
                    raise ExperimentError(f"{key} must be a sequence")
                item = tuple(item)
            converted[key] = item
        config = cls(**converted)
        config.validate()
        return config

    def validate(self):
        for key in (
            "grid_size",
            "steps",
            "intervention_time",
            "final_window",
            "budget",
            "search_seed",
        ):
            if type(getattr(self, key)) is not int:
                raise ExperimentError(f"{key} must be an integer")
        if not 2 <= self.grid_size <= 256 or not 2 <= self.steps <= 10000:
            raise ExperimentError("Use grid_size in [2,256] and steps in [2,10000]")
        if (
            not 0 <= self.intervention_time < self.steps
            or not 1 <= self.final_window <= self.steps - self.intervention_time
        ):
            raise ExperimentError(
                "The final window must lie entirely after intervention"
            )
        if not 1 <= self.budget <= 1000 or self.search_seed < 0:
            raise ExperimentError("Invalid candidate budget or search seed")
        for key, lower, upper in (("temptation", 1.0, 2.0), ("penalty", 0.0, 10.0)):
            pair = getattr(self, key)
            if len(pair) != 2 or any(
                type(v) not in (int, float) or not math.isfinite(v) for v in pair
            ):
                raise ExperimentError(f"{key} must contain two finite numbers")
            if not lower < pair[0] <= pair[1] <= upper:
                raise ExperimentError(f"{key} range is outside the adapter domain")
        for seeds in (self.discovery_seeds, self.confirmation_seeds):
            if (
                not 2 <= len(seeds) <= 100
                or any(type(s) is not int or s < 0 for s in seeds)
                or len(set(seeds)) != len(seeds)
            ):
                raise ExperimentError(
                    "Use 2-100 distinct nonnegative integer seeds per stage"
                )
        if set(self.discovery_seeds) & set(self.confirmation_seeds):
            raise ExperimentError("Search and confirmation seeds must be disjoint")
        if (
            not self.methods
            or len(set(self.methods)) != len(self.methods)
            or set(self.methods) - {"grid", "random"}
        ):
            raise ExperimentError(
                "Methods must be a nonempty subset of grid and random"
            )
        if (
            type(self.minimum_effect) not in (int, float)
            or not math.isfinite(self.minimum_effect)
            or not -1 <= self.minimum_effect <= 1
        ):
            raise ExperimentError("minimum_effect must be a finite fraction in [-1,1]")
        if (
            type(self.family_alpha) not in (int, float)
            or not 0 < self.family_alpha < 0.5
        ):
            raise ExperimentError("family_alpha must lie between 0 and 0.5")
        pairs = len(self.methods) * (
            self.budget * len(self.discovery_seeds) + len(self.confirmation_seeds)
        )
        if 2 * pairs * self.grid_size**2 * self.steps > 200_000_000:
            raise ExperimentError(
                "Experiment exceeds the 200-million cell-transition budget; reduce its size"
            )

    def simulation_settings(self):
        return {
            key: getattr(self, key)
            for key in ("grid_size", "steps", "intervention_time", "final_window")
        }


def candidates(config: Config, method: str):
    if method == "random":
        rng = np.random.default_rng(config.search_seed)
        b_values = rng.uniform(*config.temptation, size=config.budget)
        p_values = rng.uniform(*config.penalty, size=config.budget)
        return [(float(b), float(p)) for b, p in zip(b_values, p_values)]
    if method != "grid":
        raise ExperimentError("Unknown search method")
    if config.budget == 1:
        return [(float(np.mean(config.temptation)), float(np.mean(config.penalty)))]
    side = math.ceil(math.sqrt(config.budget))
    lattice = [
        (float(b), float(p))
        for b in np.linspace(*config.temptation, side)
        for p in np.linspace(*config.penalty, side)
    ]
    return [
        lattice[index]
        for index in np.linspace(0, len(lattice) - 1, config.budget, dtype=int)
    ]


def summarize(effects, *, alpha: float) -> dict:
    values = np.asarray(effects, dtype=float)
    if (
        values.ndim != 1
        or len(values) < 2
        or not np.isfinite(values).all()
        or not 0 < alpha < 0.5
    ):
        raise ExperimentError(
            "An interval requires at least two finite effects and a valid alpha"
        )
    mean = float(values.mean())
    se = float(values.std(ddof=1) / math.sqrt(len(values)))
    if np.any((values < -1) | (values > 1)):
        raise ExperimentError("Cooperation effects must lie in [-1,1]")
    # Effects lie in [-1,1]. A distribution-free bound avoids interpreting a
    # small, zero-variance sample as certainty about the population mean.
    upper = min(1.0, mean + math.sqrt(2 * math.log(1 / alpha) / len(values)))
    student_t = mean + float(t.ppf(1 - alpha, len(values) - 1)) * se
    return {
        "n": len(values),
        "mean_effect": mean,
        "standard_error": se,
        "upper_bound": upper,
        "one_sided_alpha": alpha,
        "raw_effects": values.tolist(),
        "interval": "hoeffding_one_sided_upper",
        "student_t_diagnostic_upper": student_t,
    }


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, delete=False, encoding="utf-8"
        ) as file:
            temporary = Path(file.name)
            json.dump(value, file, indent=2, sort_keys=True, allow_nan=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


class PairCache:
    def __init__(self, directory: Path, spec: ModelSpec, settings: dict):
        self.directory, self.spec, self.settings = directory, spec, settings
        self.hits = 0
        self.computed = 0

    def payload(self, point, seed):
        return {
            "model": self.spec.provenance(),
            "settings": self.settings,
            "b": point[0],
            "p": point[1],
            "seed": seed,
        }

    def path(self, payload):
        key = hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        return self.directory / (key + ".json")

    def validate(self, payload, result):
        expected_length = self.settings["steps"] + 1
        for field in ("b", "p", "seed"):
            if result[field] != payload[field]:
                raise ExperimentError("Cached simulation does not match its key")
        series = []
        for field in ("control_series", "treated_series"):
            values = np.asarray(result[field], dtype=float)
            if (
                values.shape != (expected_length,)
                or not np.isfinite(values).all()
                or np.any((values < 0) | (values > 1))
            ):
                raise ExperimentError("Invalid cached cooperation trajectory")
            series.append(values)
        before = self.settings["intervention_time"] + 1
        if not np.array_equal(series[0][:before], series[1][:before]):
            raise ExperimentError("Cached pair violates pre-intervention matching")
        at_intervention = float(series[0][before - 1])
        regime = (
            "all_defectors"
            if at_intervention == 0
            else "all_cooperators"
            if at_intervention == 1
            else "mixed"
        )
        if (
            result["cooperation_at_intervention"] != at_intervention
            or result["pre_intervention_regime"] != regime
        ):
            raise ExperimentError("Cached regime does not agree with its trajectory")
        window = self.settings["final_window"]
        first, second = (float(values[-window:].mean()) for values in series)
        if not all(
            math.isclose(float(result[key]), value, abs_tol=1e-12)
            for key, value in (
                ("control_mean", first),
                ("treated_mean", second),
                ("effect", second - first),
            )
        ):
            raise ExperimentError("Cached summary does not agree with its trajectories")

    def evaluate(self, point, seeds, workers):
        payloads = [self.payload(point, seed) for seed in seeds]
        results = [None] * len(payloads)
        pending = []
        for index, payload in enumerate(payloads):
            path = self.path(payload)
            if path.exists():
                try:
                    saved = json.loads(path.read_text())
                    if saved["input"] != payload:
                        raise ExperimentError("Cached input metadata mismatch")
                    self.validate(payload, saved["result"])
                    results[index] = saved["result"]
                except (KeyError, ValueError, TypeError) as error:
                    raise ExperimentError(
                        f"Invalid cache entry {path.name}: {error}"
                    ) from error
                self.hits += 1
            else:
                pending.append((index, payload))

        def execute(item):
            index, payload = item
            result = run_pair(
                self.spec, self.settings, payload["b"], payload["p"], payload["seed"]
            )
            return index, payload, result

        if workers == 1:
            completed = map(execute, pending)
            for index, payload, result in completed:
                self.validate(payload, result)
                atomic_json(self.path(payload), {"input": payload, "result": result})
                results[index] = result
                self.computed += 1
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                for index, payload, result in executor.map(execute, pending):
                    self.validate(payload, result)
                    atomic_json(
                        self.path(payload), {"input": payload, "result": result}
                    )
                    results[index] = result
                    self.computed += 1
        return results


def render_markdown(report):
    lines = [
        "# FalsifyLab experiment",
        "",
        report["hypothesis"],
        "",
        "Effects are treated minus control, measured as fractions of the population over the final window.",
        "",
        "| Search method | b | Penalty | Search mean | Fresh-seed mean | Upper bound | Outcome |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in report["methods"]:
        check = row["confirmation"]
        lines.append(
            f"| {row['method']} | {row['selected']['b']:.4f} | {row['selected']['p']:.4f} | "
            f"{row['selected']['mean_effect']:.4f} | {check['mean_effect']:.4f} | {check['upper_bound']:.4f} | {check['outcome']} |"
        )
    lines += [
        "",
        "## Interpretation",
        "",
        "The parameter pair was selected on search seeds and then evaluated on disjoint confirmation seeds. "
        "One-sided Hoeffding bounds for effects in [-1,1] use a Bonferroni-adjusted alpha across the declared search methods. "
        "They assume independent initial-state samples and are deliberately wide for small seed counts. "
        "A Student-t diagnostic is also retained in JSON but does not decide confirmation. "
        "A confirmed effect below the floor concerns this configuration and model, not a real-world policy or a global proof.",
        "",
        "The effect-floor hypothesis is declared for this experiment; it is not attributed to the original paper.",
        "",
        "## Reproduction",
        "",
        "Model SHA-256: `" + report["model"]["source_sha256"] + "`.",
        "",
        "The JSON report contains all candidate results, independent seed sets, exact settings, package versions and cache accounting.",
    ]
    return "\n".join(lines) + "\n"


def run_experiment(
    config: Config,
    spec: ModelSpec,
    output: Path,
    *,
    workers=1,
    resume=False,
    trajectory_store=None,
):
    config.validate()
    spec.verify()
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ExperimentError("workers must be an integer in [1,4]")
    config_json = json.loads(json.dumps(asdict(config)))
    identity = {
        "config": config_json,
        "model": spec.provenance(),
        "experiment_version": 3 if trajectory_store is not None else 2,
    }
    if trajectory_store is not None:
        identity["trajectory_contract"] = "trajectory-record-v1"
    if output.exists() and not resume:
        raise ExperimentError(
            "Output directory exists; choose a new directory or use --resume"
        )
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "experiment.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != identity:
            raise ExperimentError(
                "Cannot resume with different configuration or model provenance"
            )
    else:
        if any(output.iterdir()):
            raise ExperimentError("Output directory is not an initialized experiment")
        atomic_json(manifest, identity)
    if trajectory_store is None:
        cache = PairCache(output / "pairs", spec, config.simulation_settings())
    else:
        from .trajectories import TrajectoryCache

        cache = TrajectoryCache(trajectory_store, spec, config.simulation_settings())
    methods = []
    for method in config.methods:
        search = []
        for b, p in candidates(config, method):
            pairs = cache.evaluate((b, p), config.discovery_seeds, workers)
            effects = [pair["effect"] for pair in pairs]
            row = {
                "b": b,
                "p": p,
                "mean_effect": float(np.mean(effects)),
                "raw_effects": effects,
            }
            if trajectory_store is not None:
                row["trajectory_pairs"] = [
                    {key: pair[key] for key in ("seed", "control_key", "treated_key")}
                    for pair in pairs
                ]
            search.append(row)
        selected = min(search, key=lambda row: (row["mean_effect"], row["b"], row["p"]))
        pairs = cache.evaluate(
            (selected["b"], selected["p"]), config.confirmation_seeds, workers
        )
        confirmation = summarize(
            [pair["effect"] for pair in pairs],
            alpha=config.family_alpha / len(config.methods),
        )
        confirmation["pre_intervention_regimes"] = {
            regime: sum(pair["pre_intervention_regime"] == regime for pair in pairs)
            for regime in ("all_defectors", "all_cooperators", "mixed")
        }
        confirmation["outcome"] = (
            "held_out_effect_below_floor"
            if confirmation["upper_bound"] < config.minimum_effect
            else "not_confirmed_below_floor"
        )
        if trajectory_store is not None:
            # Final evidence is archived in the report even if the disposable store disappears.
            # These window-specific pairs are not a second live trajectory cache.
            confirmation["evidence_pairs"] = pairs
        methods.append(
            {
                "method": method,
                "evaluated_candidates": search,
                "selected": selected,
                "confirmation": confirmation,
            }
        )
    report = {
        **identity,
        "hypothesis": f"Across the declared parameter domain, the expected final-window punishment effect is at least {config.minimum_effect:.4f}.",
        "methods": methods,
        "computed_pairs": cache.computed,
        "cached_pairs": cache.hits,
        "confirmation_family_alpha": config.family_alpha,
        "time_axis": "states t=0 through steps, inclusive",
    }
    if trajectory_store is not None:
        report["trajectory_store"] = cache.accounting()
    spec.verify()
    atomic_json(output / "report.json", report)
    with (output / "report.md").open("w", encoding="utf-8") as file:
        file.write(render_markdown(report))
    return report
