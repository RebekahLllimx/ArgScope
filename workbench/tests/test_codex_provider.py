"""Codex transport, schema and launch reuse checks with no model/network calls."""
import io
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
from workbench.review.codex_provider import CodexProvider, output_schema, resolve_cli
from workbench.review.providers import DemoProvider, examples
from workbench.open_running import open_existing


class CodexTests(unittest.TestCase):
    def test_cli_defaults_to_codex_and_bailian_requires_explicit_selection(self):
        from workbench.review_app import main
        for argv, chosen in [([], "CodexProvider"), (["--provider", "bailian"], "BailianProvider"),
                             (["--provider", "demo"], "DemoProvider")]:
            with self.subTest(argv=argv), patch("workbench.review_app.CodexProvider") as codex, \
                 patch("workbench.review_app.BailianProvider") as bailian, \
                 patch("workbench.review_app.DemoProvider") as demo, \
                 patch("workbench.review_app.create_app", side_effect=RuntimeError("selected")):
                with self.assertRaisesRegex(RuntimeError, "selected"):
                    main(argv)
                calls = {"CodexProvider": codex.call_count, "BailianProvider": bailian.call_count,
                         "DemoProvider": demo.call_count}
                self.assertEqual(calls, {name: int(name == chosen) for name in calls})

    def test_feedback_trial_codex_does_not_read_bailian_config(self):
        import importlib.util
        import sys
        from types import SimpleNamespace
        tools_dir = Path(__file__).resolve().parents[1] / "tools"
        with patch.dict(sys.modules), patch.object(sys, "path", [str(tools_dir), *sys.path]):
            spec = importlib.util.spec_from_file_location("feedback_trial_selection", tools_dir / "run_feedback_trial.py")
            trial = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(trial)
            with patch.object(trial, "CodexProvider") as codex, patch.object(trial, "BailianProvider") as bailian:
                args = SimpleNamespace(provider="codex", config=None, model=None)
                self.assertIs(trial.selected_provider(args), codex.return_value)
                bailian.assert_not_called()
                args.config = Path("private.env")
                with self.assertRaisesRegex(ValueError, "explicit"):
                    trial.selected_provider(args)
                bailian.assert_not_called()

    def test_invalid_explicit_binary_is_not_silently_replaced(self):
        with self.assertRaises(ValueError):
            resolve_cli("/missing/codex")

    def test_transport_schema_is_closed_and_requires_all_object_fields(self):
        def visit(value):
            if isinstance(value, dict):
                self.assertNotIn("default", value)
                if value.get("type") == "object":
                    self.assertFalse(value["additionalProperties"])
                    self.assertEqual(set(value["required"]), set(value["properties"]))
                for v in value.values():
                    visit(v)
            elif isinstance(value, list):
                for v in value:
                    visit(v)

        for op in ["draft", "critique"]:
            visit(output_schema(op))

    def test_official_cli_arguments_and_formula_transport(self):
        req = examples()[1]["request"]
        draft = DemoProvider().generate("draft", req)
        mapping = draft["readings"][0]["formal_mapping"]
        mapping["formulas"] = [
            {"node_id": k, "formula": v} for k, v in mapping["formulas"].items()
        ]

        def complete(command, **kwargs):
            self.assertIn("--ephemeral", command)
            self.assertIn("--ignore-user-config", command)
            self.assertIn("read-only", command)
            self.assertIn("shell_tool", command)
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            Path(command[command.index("--output-last-message") + 1]).write_text(
                json.dumps(draft)
            )
            kwargs["stdout"].write(
                json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5}})
                + "\n"
            )
            kwargs["stdout"].flush()
            return subprocess.CompletedProcess(command, 0)

        with patch(
            "workbench.review.codex_provider.subprocess.run", side_effect=complete
        ):
            provider = CodexProvider("/bin/echo")
            actual = provider.generate("draft", req)
        self.assertEqual(actual["readings"][0]["formal_mapping"]["formulas"]["C"], "q")
        self.assertEqual(len(provider.usage), 1)
        self.assertIn("codex-cli:", actual["provider"])

    def test_failed_cli_does_not_turn_into_demo(self):
        with patch(
            "workbench.review.codex_provider.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1),
        ):
            with self.assertRaisesRegex(ValueError, "inference failed"):
                CodexProvider("/bin/echo").generate("draft", examples()[1]["request"])

    def test_repeated_launch_opens_only_matching_paper_service(self):
        body = {
            "framework": "LangGraph",
            "provider": {"name": "authored-paper-examples-v1"},
        }
        with patch(
            "workbench.open_running.urlopen",
            return_value=io.BytesIO(json.dumps(body).encode()),
        ), patch("workbench.open_running.webbrowser.open") as opened:
            self.assertTrue(open_existing(8767))
            opened.assert_called_once_with("http://127.0.0.1:8767")
        body["provider"]["name"] = "codex-cli:test:client-default:review-v1"
        with patch("workbench.open_running.urlopen", return_value=io.BytesIO(json.dumps(body).encode())), patch("workbench.open_running.webbrowser.open"):
            self.assertTrue(open_existing(8768, "codex"))
        body["provider"]["name"] = "other-service"
        with patch(
            "workbench.open_running.urlopen",
            return_value=io.BytesIO(json.dumps(body).encode()),
        ), patch("workbench.open_running.webbrowser.open") as opened:
            self.assertFalse(open_existing(8767))
            opened.assert_not_called()

    def test_unavailable_or_invalid_port_is_not_reused(self):
        self.assertFalse(open_existing(0))
        with patch("workbench.open_running.urlopen", side_effect=OSError):
            self.assertFalse(open_existing(8767))
