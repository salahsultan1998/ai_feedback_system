from unittest.mock import patch
from feedback.cleaning import clean_feedback
from feedback.processing import process_feedback, validate_result
from tests.helpers import BaseTest, fake_request


class ProcessingTests(BaseTest):
    def test_reuses_results_and_invalidates_prompt_changes(self):
        rows, _ = clean_feedback(self.raw.iloc[:2], self.config)
        path = self.root / "processed.csv"
        with patch("feedback.ai_client.request_json", side_effect=fake_request) as request:
            process_feedback(rows,self.config,path,lambda **values: None)
            process_feedback(rows,self.config,path,lambda **values: None)
            self.assertEqual(request.call_count,2)
            config = {**self.config,"classification_prompt":self.config["classification_prompt"]+" changed"}
            process_feedback(rows,config,path,lambda **values: None)
            self.assertEqual(request.call_count,4)

    def test_invalid_model_labels_rejected(self):
        with self.assertRaises(ValueError):
            validate_result({"translation_zh":"text","label":"invented","theme":""},self.config)

    def test_stop_preserves_pending_checkpoint(self):
        rows, _ = clean_feedback(self.raw,self.config)
        path = self.root / "processed.csv"
        with patch("feedback.ai_client.request_json") as request:
            with self.assertRaises(InterruptedError):
                process_feedback(rows,self.config,path,lambda **v:None,lambda:True)
            request.assert_not_called()
        self.assertTrue(path.exists())
