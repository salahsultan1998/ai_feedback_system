import json
import os
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from openai import APIConnectionError, APIStatusError

from feedback.ai_client import ModelRequestError, request_json


class ModelErrorTests(TestCase):
    config = {"model": "test-model", "timeout_seconds": 1, "max_retries": 1, "retry_delay": 0}

    def status_error(self, status, body):
        response = SimpleNamespace(status_code=status, headers={}, request=SimpleNamespace())
        return APIStatusError("SDK error should not be dumped", response=response, body=body)

    def call_model(self, responses, payload=None):
        client = MagicMock()
        client.chat.completions.create.side_effect = responses
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-test-secret"}), patch(
                "feedback.ai_client.OpenAI") as factory:
            factory.return_value.__enter__.return_value = client
            try:
                return request_json("private system prompt", payload or {}, self.config, lambda value: value)
            finally:
                self.calls = client.chat.completions.create.call_count

    def test_403_preserves_reason_but_hides_keys_inputs_and_moderation_metadata(self):
        error = self.status_error(403, {"message": "Permission denied: sk-test-secret; private feedback body; "
            "private system prompt; https://example.com/private", "metadata": {"flagged_input": "private moderation excerpt"}})
        with self.assertRaises(ModelRequestError) as raised:
            self.call_model([error], {"feedbacks": [{"feedback": "private feedback body"}]})
        message = str(raised.exception)
        self.assertIn("HTTP 403", message)
        self.assertIn("Permission denied", message)
        for hidden in ("sk-test-secret", "private feedback body", "private system prompt", "private moderation excerpt", "https://"):
            self.assertNotIn(hidden, message)
        self.assertFalse(raised.exception.stops_processing)
        self.assertEqual(self.calls, 1)

    def test_non_json_403_does_not_expose_html_response(self):
        with self.assertRaisesRegex(ModelRequestError, "HTTP 403") as raised:
            self.call_model([self.status_error(403, "<html>private upstream response</html>")])
        self.assertNotIn("<html>", str(raised.exception))
        self.assertIn("非 JSON", str(raised.exception))

    def test_nested_provider_reason_is_kept_without_dumping_raw_metadata(self):
        body = {"message": "Provider returned error", "metadata": {"raw": json.dumps({
            "error": {"message": "Model access denied"}, "request": "private provider payload"})}}
        with self.assertRaises(ModelRequestError) as raised:
            self.call_model([self.status_error(403, body)])
        self.assertIn("Model access denied", str(raised.exception))
        self.assertNotIn("private provider payload", str(raised.exception))

    def test_global_credential_credit_and_model_errors_stop_without_retries(self):
        for status in (401, 402, 404):
            with self.subTest(status=status), self.assertRaises(ModelRequestError) as raised:
                self.call_model([self.status_error(status, {"error": {"message": "Request rejected"}})])
            self.assertTrue(raised.exception.stops_processing)
            self.assertIn("Request rejected", str(raised.exception))
            self.assertEqual(self.calls, 1)

    def test_rate_limit_can_retry_and_succeed(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"ok": True})))])
        self.assertEqual(self.call_model([self.status_error(429, {"message": "Too many requests"}), response]), {"ok": True})
        self.assertEqual(self.calls, 2)

    def test_exhausted_server_error_keeps_status_and_reason(self):
        error = self.status_error(503, {"message": "No providers available"})
        with self.assertRaisesRegex(ModelRequestError, "HTTP 503.*No providers available"):
            self.call_model([error, error])
        self.assertEqual(self.calls, 2)

    def test_connection_failure_does_not_log_sdk_request_data(self):
        error = APIConnectionError(message="private request body", request=SimpleNamespace())
        with self.assertLogs("feedback.ai_client", level="WARNING") as logs:
            with self.assertRaisesRegex(ValueError, "无法连接 OpenRouter") as raised:
                self.call_model([error, error])
        self.assertNotIn("private request body", str(raised.exception) + " ".join(logs.output))
