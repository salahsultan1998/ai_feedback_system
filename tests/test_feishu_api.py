from io import BytesIO
import json
import ssl
import subprocess
from unittest import TestCase
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from feedback.feishu_api import ApiError, request_json


class ApiTransportTests(TestCase):
    def test_signed_in_get_and_put_use_correct_methods(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.status = 200
        response.read.return_value = b'{"code":0,"data":{}}'
        with patch("feedback.feishu_api.build_opener", return_value=opener):
            request_json("GET", "/sheets/v3/spreadsheets/example", token="test-access-token")
            request_json("PUT", "/sheets/v2/spreadsheets/example/values", {"valueRange": {}}, "test-access-token")
        first, second = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(first.get_method(), "GET")
        self.assertIsNone(first.data)
        self.assertEqual(second.get_method(), "PUT")
        self.assertEqual(json.loads(second.data), {"valueRange": {}})

    def test_tls_fallback_keeps_credentials_out_of_process_arguments(self):
        opener = MagicMock()
        opener.open.side_effect = URLError(ssl.SSLCertVerificationError("certificate chain"))
        result = subprocess.CompletedProcess([], 0, '{"code":0,"data":{}}\n200', '')
        with patch("feedback.feishu_api.build_opener", return_value=opener), patch(
                "feedback.feishu_api.shutil.which", return_value="/usr/bin/curl"), patch(
                "feedback.feishu_api.subprocess.run", return_value=result) as run:
            request_json("PUT", "/example", {"value": "中文"}, "test-access-token")
        self.assertNotIn("test-access-token", " ".join(run.call_args.args[0]))
        self.assertNotIn("--insecure", run.call_args.args[0])
        self.assertIn("Bearer test-access-token", run.call_args.kwargs["input"])
        self.assertIn('request = "PUT"', run.call_args.kwargs["input"])

    def test_http_rejection_exposes_code_and_redacts_token(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("https://open.feishu.cn/example", 403, "Denied", {},
            BytesIO(b'{"code":99991672,"msg":"missing scope for test-access-token"}'))
        with patch("feedback.feishu_api.build_opener", return_value=opener):
            with self.assertRaises(ApiError) as caught:
                request_json("GET", "/example", token="test-access-token")
        self.assertEqual(caught.exception.code, 99991672)
        self.assertNotIn("test-access-token", str(caught.exception))

    def test_missing_curl_or_invalid_json_has_actionable_error(self):
        opener = MagicMock()
        opener.open.side_effect = URLError(ssl.SSLCertVerificationError("certificate chain"))
        with patch("feedback.feishu_api.build_opener", return_value=opener), patch(
                "feedback.feishu_api.shutil.which", return_value=None):
            with self.assertRaisesRegex(ApiError, "证书"):
                request_json("GET", "/example")
