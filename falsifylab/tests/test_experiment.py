from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from falsifylab import Config, ExperimentError, run_experiment
from falsifylab.experiment import PairCache, candidates, summarize, atomic_json
from falsifylab.model import ModelSpec


def fake_pair(spec, settings, b, p, seed):
    first = [0.5] * (settings["steps"] + 1)
    second = first.copy()
    for time in range(settings["intervention_time"]+1, settings["steps"]+1):
        second[time] += 0.1 if b < 1.5 else 0.0
    control_mean = sum(first[-settings["final_window"]:]) / settings["final_window"]
    treated_mean = sum(second[-settings["final_window"]:]) / settings["final_window"]
    return {"b": b, "p": p, "seed": seed, "control_mean": control_mean, "treated_mean": treated_mean,
            "cooperation_at_intervention": 0.5, "pre_intervention_regime": "mixed",
            "effect": treated_mean-control_mean, "control_series": first, "treated_series": second}


class ConfigTests(unittest.TestCase):
    def test_default_config(self):
        Config().validate()

    def test_invalid_configs(self):
        cases = [{"steps": True}, {"steps": 0}, {"final_window": 60}, {"temptation": [1,2]},
                 {"penalty": [0,1]}, {"budget": 0}, {"methods": ["random","random"]},
                 {"methods": ["bayes"]}, {"search_seed": -1}, {"minimum_effect": float("nan")},
                 {"discovery_seeds": [1,1]}, {"confirmation_seeds": [11,101]},
                 {"discovery_seeds": "12"}, {"unknown": 1}, {"grid_size": 256, "steps": 10000}]
        for item in cases:
            with self.subTest(item=item), self.assertRaises(ExperimentError):
                Config.from_dict(item)

    def test_candidate_budget_bounds_and_reproducibility(self):
        config = Config(budget=7)
        for method in config.methods:
            points = candidates(config, method)
            self.assertEqual(len(points), 7)
            self.assertEqual(points, candidates(config, method))
            self.assertTrue(all(1.1 <= b <= 1.95 and 0.05 <= p <= 0.5 for b,p in points))

    def test_single_grid_candidate_uses_domain_midpoint(self):
        self.assertEqual(candidates(Config(budget=1), "grid"), [(1.525, 0.275)])

    def test_interval_and_raw_outcomes(self):
        result = summarize([0,0,0], alpha=0.05)
        self.assertGreater(result["upper_bound"], 0)
        self.assertEqual(result["student_t_diagnostic_upper"], 0)
        noisy = summarize([-0.1,0,0.1], alpha=0.05)
        self.assertGreater(noisy["upper_bound"], noisy["mean_effect"])
        with self.assertRaises(ExperimentError):
            summarize([1], alpha=0.05)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "model" / "final-project" / "src" / "ca_model.py"
        source.parent.mkdir(parents=True)
        source.write_text("# Trusted test fixture; execution is mocked.\n")
        self.spec = ModelSpec.from_root(self.root / "model")
        self.config = Config(grid_size=4, steps=6, intervention_time=3, final_window=2, budget=4,
                             discovery_seeds=(1,2), confirmation_seeds=(10,11,12))

    @patch("falsifylab.experiment.run_pair", side_effect=fake_pair)
    def test_search_then_fresh_seed_confirmation_and_resume(self, mock):
        output = self.root / "experiment"
        report = run_experiment(self.config, self.spec, output)
        count = mock.call_count
        for method in report["methods"]:
            self.assertEqual(method["confirmation"]["outcome"], "not_confirmed_below_floor")
            self.assertEqual(method["confirmation"]["one_sided_alpha"], 0.025)
        self.assertEqual(count, report["computed_pairs"])
        replay = run_experiment(self.config, self.spec, output, resume=True)
        self.assertEqual(mock.call_count, count)
        self.assertEqual(replay["computed_pairs"], 0)
        self.assertEqual(replay["methods"], report["methods"])

    @patch("falsifylab.experiment.run_pair", side_effect=fake_pair)
    def test_strong_enough_evidence_can_confirm_a_declared_floor(self, mock):
        config = replace(self.config, methods=("grid",), budget=1, temptation=(1.6,1.9),
                         confirmation_seeds=tuple(range(10,110)), minimum_effect=0.5)
        report = run_experiment(config, self.spec, self.root / "large_confirmation")
        self.assertEqual(report["methods"][0]["confirmation"]["outcome"], "held_out_effect_below_floor")

    @patch("falsifylab.experiment.run_pair", side_effect=fake_pair)
    def test_parallel_results_match_serial(self, mock):
        serial = run_experiment(self.config, self.spec, self.root / "serial")
        parallel = run_experiment(self.config, self.spec, self.root / "parallel", workers=2)
        self.assertEqual(serial["methods"], parallel["methods"])

    @patch("falsifylab.experiment.run_pair", side_effect=fake_pair)
    def test_refuse_overwrite_or_changed_resume_configuration(self, mock):
        output = self.root / "experiment"
        run_experiment(self.config, self.spec, output)
        with self.assertRaises(ExperimentError):
            run_experiment(self.config, self.spec, output)
        with self.assertRaises(ExperimentError):
            run_experiment(replace(self.config, minimum_effect=0.1), self.spec, output, resume=True)

    def test_model_changes_are_detected(self):
        Path(self.spec.source).write_text("# Changed fixture\n")
        with self.assertRaises(ValueError):
            self.spec.verify()

    @patch("falsifylab.experiment.run_pair", side_effect=fake_pair)
    def test_corrupt_cached_summary_is_rejected(self, mock):
        output = self.root / "experiment"
        run_experiment(self.config, self.spec, output)
        cache_file = next((output / "pairs").glob("*.json"))
        data = json.loads(cache_file.read_text())
        data["result"]["effect"] = 0.8
        cache_file.write_text(json.dumps(data))
        with self.assertRaises(ExperimentError):
            run_experiment(self.config, self.spec, output, resume=True)

    def test_atomic_json_rejects_nan_and_leaves_original(self):
        path = self.root / "artifact.json"
        atomic_json(path, {"valid": True})
        with self.assertRaises(ValueError):
            atomic_json(path, {"invalid": float("nan")})
        self.assertEqual(json.loads(path.read_text()), {"valid": True})
        self.assertEqual([p.name for p in self.root.iterdir() if p.is_file()], ["artifact.json"])


if __name__ == "__main__":
    unittest.main()
