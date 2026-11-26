"""Legacy live checks must use explicit operator configuration."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('paper_validation_cli', ROOT / 'workbench/tools/validate_paper_workflows.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PaperValidationCLITests(unittest.TestCase):
    def test_missing_config_and_output_are_rejected_before_work(self):
        with self.assertRaises(SystemExit) as raised:
            module.parse_args([])
        self.assertEqual(raised.exception.code, 2)

    def test_pinned_runtime_is_default_and_config_stays_explicit(self):
        args = module.parse_args(['--config', 'chosen.env', '--output', 'new-run'])
        self.assertEqual(args.config, Path('chosen.env'))
        self.assertEqual(args.output, Path('new-run'))
        self.assertEqual(args.kv_root, ROOT / 'vendor/kv_server')

    def test_external_runtime_requires_an_explicit_argument(self):
        args = module.parse_args(['--config', 'chosen.env', '--output', 'new-run', '--kv-root', 'selected-kv'])
        self.assertEqual(args.kv_root, Path('selected-kv'))
