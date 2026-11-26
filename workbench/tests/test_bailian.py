"""Remote-provider boundary checks without network or real credentials."""
import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from workbench.review.bailian import BailianProvider
from workbench.review.providers import DemoProvider, examples


class BailianTests(unittest.TestCase):
    def provider(self):
        return BailianProvider()

    @patch.dict(
        os.environ,
        {"BAILI_API_KEY": "test-secret", "BAILI_API_URL": "http://example.org"},
    )
    def test_reject_untrusted_endpoint(self):
        with self.assertRaises(ValueError):
            self.provider()

    @patch.dict(
        os.environ,
        {
            "BAILI_API_KEY": "test-secret",
            "BAILI_API_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )
    def test_json_and_exact_quote_offsets(self):
        req = examples()[1]["request"]
        draft = DemoProvider().generate("draft", req)
        draft["readings"][0]["nodes"][0]["source_refs"][0]["start"] = 999
        body = {
            "choices": [
                {"finish_reason": "stop", "message": {"content": json.dumps(draft)}}
            ],
            "usage": {"total_tokens": 12},
        }
        with patch(
            "workbench.review.bailian.urlopen",
            return_value=io.BytesIO(json.dumps(body).encode()),
        ) as mocked:
            p = self.provider()
            result = p.generate("draft", req)
        self.assertEqual(
            result["readings"][0]["nodes"][0]["source_refs"][0]["start"], 0
        )
        self.assertNotIn("test-secret", json.dumps(result))
        self.assertEqual(len(p.usage), 1)
        sent = json.loads(mocked.call_args.args[0].data)
        self.assertEqual(sent["response_format"], {"type": "json_object"})

    @patch.dict(
        os.environ,
        {
            "BAILI_API_KEY": "test-secret",
            "BAILI_API_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )
    def test_error_sanitized_and_no_fallback(self):
        with patch(
            "workbench.review.bailian.urlopen",
            side_effect=HTTPError("private", 401, "test-secret", {}, None),
        ):
            with self.assertRaisesRegex(ValueError, "HTTP 401") as error:
                self.provider().generate("draft", examples()[0]["request"])
        self.assertNotIn("test-secret", str(error.exception))

    @patch.dict(
        os.environ,
        {
            "BAILI_API_KEY": "test-secret",
            "BAILI_API_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
    )
    def test_truncated_output_rejected(self):
        with patch(
            "workbench.review.bailian.urlopen",
            return_value=io.BytesIO(
                json.dumps({"choices": [{"finish_reason": "length"}]}).encode()
            ),
        ):
            with self.assertRaisesRegex(ValueError, "incomplete"):
                self.provider().generate("draft", examples()[0]["request"])
