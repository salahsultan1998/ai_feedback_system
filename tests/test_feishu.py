import base64
import hashlib
import hmac
import json
import os
import ssl
import subprocess
from urllib.error import URLError
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from feedback.feishu import FORMAT, MAX_BODY_BYTES, card_payload, report_parts, report_text, push, state


class FeishuTests(TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "report.md").write_text("# 测试反馈报告\n建议 &amp; 问题\n[下载](feedback_details.xlsx)", encoding="utf-8")
        self.config = {"feishu": {"enabled": True, "webhook_env": "TEST_FEISHU_URL", "secret_env": "TEST_FEISHU_SECRET"}}
        self.env = patch.dict(os.environ, {"TEST_FEISHU_URL": "https://open.feishu.cn/open-apis/bot/v2/hook/test", "TEST_FEISHU_SECRET": "secret"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def opener(self, result):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(result).encode()
        return opener

    def test_signed_send_and_duplicate(self):
        opener = self.opener({"code": 0})
        with patch("feedback.feishu.build_opener", return_value=opener), patch("feedback.feishu.time.time", return_value=123):
            result = push(self.folder, self.config)
            self.assertEqual(result["status"], "sent")
            self.assertEqual(push(self.folder, self.config)["status"], "sent")
        opener.open.assert_called_once()
        payload = json.loads(opener.open.call_args.args[0].data)
        expected = base64.b64encode(hmac.new(b"123\nsecret", b"", hashlib.sha256).digest()).decode()
        self.assertEqual(payload["sign"], expected)
        self.assertEqual(payload["msg_type"], "interactive")
        self.assertEqual(payload["card"]["schema"], "2.0")
        self.assertEqual(payload["card"]["header"]["title"]["tag"], "plain_text")
        markdown = payload["card"]["body"]["elements"][0]
        self.assertEqual(markdown["tag"], "markdown")
        self.assertNotIn("feedback_details.xlsx", markdown["content"])
        self.assertIn("建议 &amp; 问题", markdown["content"])
        self.assertNotIn("content", payload)
        self.assertNotIn("secret", (self.folder / "feishu_push.json").read_text())

    def test_business_failure_can_retry(self):
        with patch("feedback.feishu.build_opener", return_value=self.opener({"code": 19024})):
            with self.assertRaisesRegex(ValueError, "拒绝"):
                push(self.folder, self.config)
        self.assertEqual(state(self.folder, self.config)["status"], "failed")

    def test_rejection_preserves_actual_service_message_without_webhook(self):
        detail = "unsupported tag md " + os.environ["TEST_FEISHU_URL"]
        with patch("feedback.feishu.send_payload", return_value={"code": 10002, "msg": detail}):
            with self.assertRaisesRegex(ValueError, "unsupported tag md") as caught:
                push(self.folder, self.config)
        self.assertNotIn(os.environ["TEST_FEISHU_URL"], str(caught.exception))
        saved = state(self.folder, self.config)
        self.assertEqual(saved["error_code"], "10002")
        self.assertIn("unsupported tag md", saved["last_error"])
        self.assertNotIn(os.environ["TEST_FEISHU_URL"], saved["last_error"])

    def test_timeout_and_interruption_do_not_resend(self):
        opener = MagicMock()
        opener.open.side_effect = TimeoutError()
        with patch("feedback.feishu.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "未知"):
                push(self.folder, self.config)
            with self.assertRaisesRegex(ValueError, "未知"):
                push(self.folder, self.config)
        opener.open.assert_called_once()
        (self.folder / "feishu_push.json").write_text('{"status":"sending"}')
        self.assertEqual(state(self.folder, self.config)["status"], "unknown")

    def test_missing_config_and_wrong_host(self):
        self.assertFalse(state(self.folder, {})["available"])
        with patch.dict(os.environ, {"TEST_FEISHU_URL": "https://example.com/hook/test"}):
            with self.assertRaises(ValueError):
                push(self.folder, self.config)

    def test_certificate_failure_uses_verified_curl_without_signing(self):
        self.config["feishu"].pop("secret_env")
        opener = MagicMock()
        opener.open.side_effect = URLError(ssl.SSLCertVerificationError("untrusted chain"))
        response = subprocess.CompletedProcess([], 0, '{"code":0}\n200', '')
        with patch("feedback.feishu.build_opener", return_value=opener), patch("feedback.feishu.shutil.which", return_value="/usr/bin/curl"), patch("feedback.feishu.subprocess.run", return_value=response) as run:
            self.assertEqual(push(self.folder, self.config)["status"], "sent")
        args = run.call_args.args[0]
        self.assertNotIn("--insecure", args)
        self.assertNotIn(os.environ["TEST_FEISHU_URL"], args)
        self.assertNotIn('\\"sign\\"', run.call_args.kwargs["input"])

    def test_curl_certificate_failure_is_retryable(self):
        opener = MagicMock()
        opener.open.side_effect = URLError(ssl.SSLCertVerificationError("untrusted chain"))
        response = subprocess.CompletedProcess([], 60, '', 'certificate failure')
        with patch("feedback.feishu.build_opener", return_value=opener), patch("feedback.feishu.shutil.which", return_value="/usr/bin/curl"), patch("feedback.feishu.subprocess.run", return_value=response):
            with self.assertRaisesRegex(ValueError, "证书"):
                push(self.folder, self.config)
        self.assertEqual(state(self.folder, self.config)["status"], "failed")

    def test_explicit_retry_of_unknown_delivery(self):
        (self.folder / "feishu_push.json").write_text('{"status":"unknown"}')
        with patch("feedback.feishu.build_opener", return_value=self.opener({"code": 0})):
            self.assertEqual(push(self.folder, self.config, retry_unknown=True)["status"], "sent")

    def test_full_markdown_tables_and_notes_are_preserved(self):
        text = "# 报告\n\n| 模块 | 说明 |\n| --- | --- |\n| 支付 | 第一行<br>第二行 |\n\n## 统计说明\n\n- 完整统计说明。"
        (self.folder / "report.md").write_text(text, encoding="utf-8")
        markdown = report_text(self.folder)
        self.assertIn("| 支付 | 第一行；第二行 |", markdown)
        self.assertIn("## 统计说明", markdown)
        self.assertIn("完整统计说明。", markdown)
        self.assertEqual(report_parts(markdown, "报告"), [markdown])

    def test_large_table_splits_by_row_with_headers_and_no_missing_rows(self):
        rows = [f"| row-{index:03d} | {'内容' * 130} |\n" for index in range(80)]
        text = "| 模块 | 说明 |\n| --- | --- |\n" + "".join(rows)
        parts = report_parts(text, "反馈报告")
        self.assertGreater(len(parts), 1)
        for part in parts:
            self.assertTrue(part.startswith("| 模块 | 说明 |\n| --- | --- |"))
            payload = card_payload("反馈报告（999999/999999）", part)
            payload.update(timestamp="9" * 20, sign="x" * 44)
            self.assertLessEqual(len(json.dumps(payload, ensure_ascii=False).encode()), MAX_BODY_BYTES)
        for row in rows:
            self.assertEqual("\n".join(parts).count(row.strip()), 1)

    def test_oversized_unicode_paragraph_is_not_truncated(self):
        text = '阿拉伯语 العربية 中文 " \\ ' * 4000
        parts = report_parts(text, "报告")
        # Boundary whitespace may normalize, but no words or characters vanish.
        self.assertEqual(''.join(''.join(parts).split()), ''.join(text.split()))

    def test_resume_after_partial_rejection_does_not_repeat_sent_parts(self):
        (self.folder / "report.md").write_text("# 报告\n\n" + "\n\n".join(f"段落{i} " + "反馈" * 2000 for i in range(4)), encoding="utf-8")
        with patch("feedback.feishu.send_payload", side_effect=[{"code": 0}, {"code": 11232}]), patch("feedback.feishu.time.sleep"):
            with self.assertRaisesRegex(ValueError, "11232"):
                push(self.folder, self.config)
        checkpoint = state(self.folder, self.config)
        self.assertEqual(checkpoint["sent_parts"], 1)
        with patch("feedback.feishu.send_payload", return_value={"code": 0}) as sender, patch("feedback.feishu.time.sleep"):
            result = push(self.folder, self.config)
        self.assertEqual(result["status"], "sent")
        self.assertEqual(sender.call_count, checkpoint["total_parts"] - 1)
        self.assertIn("（2/", sender.call_args_list[0].args[1]["card"]["header"]["title"]["content"])

    def test_legacy_plain_text_send_can_be_upgraded(self):
        (self.folder / "feishu_push.json").write_text('{"status":"sent","sent_at":"old"}')
        self.assertEqual(state(self.folder, self.config)["status"], "ready")
        with patch("feedback.feishu.send_payload", return_value={"code": 0}) as sender:
            self.assertEqual(push(self.folder, self.config)["status"], "sent")
        sender.assert_called_once()

    def test_old_post_checkpoint_does_not_skip_card_parts(self):
        parts = report_parts(report_text(self.folder), "测试反馈报告")
        old = {"status": "failed", "format": "markdown-v1", "sent_parts": 1,
               "total_parts": 1, "digest": hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()}
        (self.folder / "feishu_push.json").write_text(json.dumps(old))
        with patch("feedback.feishu.send_payload", return_value={"code": 0}) as sender:
            result = push(self.folder, self.config)
        self.assertEqual(result["format"], FORMAT)
        sender.assert_called_once()
        self.assertEqual(sender.call_args.args[1]["msg_type"], "interactive")

    def test_legacy_post_success_can_be_sent_as_card(self):
        (self.folder / "feishu_push.json").write_text('{"status":"sent","format":"markdown-v1"}')
        self.assertEqual(state(self.folder, self.config)["status"], "ready")
        with patch("feedback.feishu.send_payload", return_value={"code": 0}) as sender:
            push(self.folder, self.config)
        sender.assert_called_once()
