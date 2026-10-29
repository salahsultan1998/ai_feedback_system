import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import threading

from openai import APIStatusError

from feedback.cleaning import clean_feedback
from feedback.ai_client import ModelRequestError
from feedback.processing import fingerprint, make_batches, process_feedback, validate_batch, validate_result
from feedback.storage import atomic_csv, read_csv
from tests.helpers import BaseTest, fake_request


class ProcessingTests(BaseTest):
    def model_result(self, feedback_id):
        return {"feedback_id": feedback_id, "translation_zh": f"反馈 {feedback_id} 的翻译",
                "label": "游戏卡顿", "theme": "", "subtheme": "",
                "is_valid": True, "exclusion_reason": ""}

    def run_model(self, response, rows, config=None, progress=None, should_stop=None):
        client = MagicMock()
        def create(**kwargs):
            value = response(json.loads(kwargs["messages"][1]["content"]))
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])
        client.chat.completions.create.side_effect = create
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), patch(
                "feedback.ai_client.OpenAI") as factory:
            factory.return_value.__enter__.return_value = client
            return process_feedback(rows, config or {**self.config, "concurrency": 1, "max_retries": 1, "retry_delay": 0},
                                    self.root / "processed.csv", progress or (lambda **v: None), should_stop)

    def test_reuses_results_and_invalidates_prompt_changes(self):
        rows, _ = clean_feedback(self.raw.iloc[:2], self.config)
        path = self.root / "processed.csv"
        with patch("feedback.ai_client.request_json", side_effect=fake_request) as request:
            process_feedback(rows,self.config,path,lambda **values: None)
            process_feedback(rows,self.config,path,lambda **values: None)
            self.assertEqual(request.call_count,1)
            config = {**self.config,"classification_prompt":self.config["classification_prompt"]+" changed"}
            process_feedback(rows,config,path,lambda **values: None)
            self.assertEqual(request.call_count,2)

    def test_invalid_model_labels_rejected(self):
        with self.assertRaises(ValueError):
            validate_result({"translation_zh":"text","label":"invented","theme":""},self.config)

    def test_global_request_failure_stops_new_batches_and_keeps_checkpoint(self):
        rows, _ = clean_feedback(self.raw, self.config)
        config = {**self.config, "classification_batch_size": 1, "concurrency": 2}
        error = ModelRequestError("模型请求失败（HTTP 401）：密钥无效。", 401, stops_processing=True)
        with patch("feedback.ai_client.request_json", side_effect=error) as request, self.assertLogs(
                "feedback.processing", level="WARNING") as logs:
            with self.assertRaisesRegex(ValueError, "有 8 条反馈.*HTTP 401"):
                process_feedback(rows, config, self.root / "processed.csv", lambda **v: None)
        self.assertEqual(request.call_count, 2)
        saved = read_csv(self.root / "processed.csv")
        self.assertEqual(len(saved), 8)
        self.assertEqual(saved["错误"].eq("待处理").sum(), 6)
        self.assertTrue(all("HTTP 401" in message for message in logs.output))

    def test_403_batch_failure_surfaces_reason_and_does_not_skip_other_batches(self):
        rows, _ = clean_feedback(self.raw.iloc[:3], self.config)
        error = ModelRequestError("模型请求失败（HTTP 403）：Provider policy rejected the request.", 403)
        config = {**self.config, "classification_batch_size": 1, "concurrency": 1}
        with patch("feedback.ai_client.request_json", side_effect=error) as request, self.assertLogs(
                "feedback.processing", level="WARNING"):
            with self.assertRaisesRegex(ValueError, "HTTP 403.*Provider policy"):
                process_feedback(rows, config, self.root / "processed.csv", lambda **v: None)
        self.assertEqual(request.call_count, 3)
        self.assertTrue(read_csv(self.root / "processed.csv")["错误"].str.contains("HTTP 403").all())

    def test_stop_preserves_pending_checkpoint(self):
        rows, _ = clean_feedback(self.raw,self.config)
        path = self.root / "processed.csv"
        with patch("feedback.ai_client.request_json") as request:
            with self.assertRaises(InterruptedError):
                process_feedback(rows,self.config,path,lambda **v:None,lambda:True)
            request.assert_not_called()
        self.assertTrue(path.exists())

    def test_batch_size_last_batch_and_unordered_response_match_ids(self):
        rows, _ = clean_feedback(self.raw, self.config)
        submitted = []
        def response(payload):
            ids = [item["feedback_id"] for item in payload["feedbacks"]]
            submitted.append(ids)
            return {"results": [self.model_result(fid) for fid in reversed(ids)]}
        config = {**self.config, "classification_batch_size": 3}
        result = self.run_model(response, rows, config)
        self.assertEqual(sorted(map(len, submitted)), [2, 3, 3])
        self.assertEqual(set(sum(submitted, [])), set(rows["反馈ID"]))
        for row in result.to_dict("records"):
            self.assertEqual(row["中文翻译"], f"反馈 {row['反馈ID']} 的翻译")
        self.assertTrue(result["错误"].eq("").all())

    def test_partial_success_is_saved_and_only_invalid_rows_are_retried(self):
        rows, _ = clean_feedback(self.raw.iloc[:3], self.config)
        submitted = []
        progress = []
        def response(payload):
            ids = [item["feedback_id"] for item in payload["feedbacks"]]
            submitted.append(ids)
            if len(submitted) == 1:
                invalid = {**self.model_result(ids[1]), "label": "invented"}
                return {"results": [self.model_result(ids[0]), invalid]}
            # Checkpoint must exist before request 2, not just after all retries.
            saved = read_csv(self.root / "processed.csv").set_index("反馈ID")
            self.assertEqual(saved.loc["000", "错误"], "")
            self.assertEqual(saved.loc["000", "中文翻译"], "反馈 000 的翻译")
            return {"results": [self.model_result(fid) for fid in ids]}
        result = self.run_model(response, rows, progress=lambda **v: progress.append(v["done"]))
        self.assertEqual(submitted, [["000", "001", "002"], ["001", "002"]])
        self.assertEqual(progress, [0, 1, 3])
        self.assertTrue(result["错误"].eq("").all())

    def test_request_rejection_after_partial_success_keeps_latest_failure_reason(self):
        rows, _ = clean_feedback(self.raw.iloc[:2], self.config)
        client = MagicMock()
        answer = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {"results": [self.model_result("000")]})))])
        response = SimpleNamespace(status_code=403, headers={}, request=SimpleNamespace())
        client.chat.completions.create.side_effect = [answer, APIStatusError("Request rejected",
            response=response, body={"message": "Provider access denied"})]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-only"}), patch(
                "feedback.ai_client.OpenAI") as factory, self.assertLogs("feedback.processing", level="WARNING"):
            factory.return_value.__enter__.return_value = client
            with self.assertRaisesRegex(ValueError, "HTTP 403.*Provider access denied"):
                process_feedback(rows, {**self.config, "max_retries": 1, "retry_delay": 0},
                                 self.root / "processed.csv", lambda **v: None)
        saved = read_csv(self.root / "processed.csv").set_index("反馈ID")
        self.assertEqual(saved.loc["000", "错误"], "")
        self.assertIn("HTTP 403", saved.loc["001", "错误"])

    def test_duplicate_numeric_and_unknown_ids_cannot_be_misassigned(self):
        response = {"results": [self.model_result("000"), self.model_result("000"),
            self.model_result(1), self.model_result("unknown"), self.model_result("002")]}
        answers, errors = validate_batch(response, ["000", "001", "002"], self.config)
        self.assertEqual(set(answers), {"002"})
        self.assertEqual(set(errors), {"000", "001"})

    def test_retry_exhaustion_keeps_valid_rows_for_next_run(self):
        rows, _ = clean_feedback(self.raw.iloc[:3], self.config)
        calls = []
        def response(payload):
            calls.append([item["feedback_id"] for item in payload["feedbacks"]])
            return {"results": [self.model_result("000")]}
        with self.assertRaisesRegex(ValueError, "有 2 条反馈"):
            self.run_model(response, rows)
        self.assertEqual(calls, [["000", "001", "002"], ["001", "002"]])
        saved = read_csv(self.root / "processed.csv")
        self.assertEqual(saved["错误"].eq("").sum(), 1)
        with patch("feedback.ai_client.request_json", side_effect=fake_request) as request:
            result = process_feedback(rows, self.config, self.root / "processed.csv", lambda **v: None)
        self.assertEqual([item["feedback_id"] for item in request.call_args.args[1]["feedbacks"]], ["001", "002"])
        self.assertTrue(result["错误"].eq("").all())

    def test_current_row_cache_survives_batch_size_change(self):
        rows, _ = clean_feedback(self.raw.iloc[:3], self.config)
        cached = rows.copy()
        cached["中文翻译"], cached["最终标签"], cached["具体主题"], cached["错误"] = "已有翻译", "游戏卡顿", "", ""
        cached["是否建议"], cached["是否有效"], cached["排除原因"] = "否", "是", ""
        cached["三级主题"] = ""
        cached["处理指纹"] = [fingerprint(row, self.config) for _, row in rows.iterrows()]
        path = self.root / "processed.csv"
        atomic_csv(path, cached)
        with patch("feedback.ai_client.request_json") as request:
            result = process_feedback(rows, {**self.config, "classification_batch_size": 1}, path, lambda **v: None)
            request.assert_not_called()
        self.assertTrue(result["中文翻译"].eq("已有翻译").all())

    def test_long_feedback_splits_batches_without_truncation(self):
        items = [{"feedback_id": str(i), "feedback": "玩家反馈 العربية " * (1 if i != 1 else 100),
                  "original_label": ""} for i in range(5)]
        batches = list(make_batches(items, 3, 250))
        self.assertEqual([item for batch in batches for item in batch], items)
        self.assertTrue(any(batch == [items[1]] for batch in batches))
        for batch in batches:
            self.assertLessEqual(len(batch), 3)
            if len(batch) > 1:
                self.assertLessEqual(len(json.dumps({"feedbacks": batch}, ensure_ascii=False)), 250)

    def test_stop_between_partial_response_and_retry_preserves_success(self):
        rows, _ = clean_feedback(self.raw.iloc[:3], self.config)
        stopped = threading.Event()
        calls = []
        def response(payload):
            calls.append(payload)
            return {"results": [self.model_result("000")]}
        def progress(**values):
            if values["done"]:
                stopped.set()
        with self.assertRaises(InterruptedError):
            self.run_model(response, rows, progress=progress, should_stop=stopped.is_set)
        self.assertEqual(len(calls), 1)
        saved = read_csv(self.root / "processed.csv").set_index("反馈ID")
        self.assertEqual(saved.loc["000", "错误"], "")
        self.assertEqual(saved.loc["001", "错误"], "待处理")

    def test_concurrency_counts_requests_and_progress_counts_feedback(self):
        rows, _ = clean_feedback(self.raw, self.config)
        barrier, lock = threading.Barrier(2), threading.Lock()
        active, peak, counts, progress = 0, 0, [], []
        def request(prompt, payload, config, validate, should_stop):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                counts.append(len(payload["feedbacks"]))
            barrier.wait(timeout=5)
            try:
                return validate({"results": [self.model_result(item["feedback_id"]) for item in payload["feedbacks"]]})
            finally:
                with lock:
                    active -= 1
        config = {**self.config, "classification_batch_size": 2, "concurrency": 2}
        with patch("feedback.ai_client.request_json", side_effect=request):
            process_feedback(rows, config, self.root / "processed.csv", lambda **v: progress.append(v["done"]))
        self.assertEqual(peak, 2)
        self.assertEqual(counts, [2, 2, 2, 2])
        self.assertEqual(progress, [0, 2, 4, 6, 8])
