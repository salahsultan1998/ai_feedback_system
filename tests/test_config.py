from unittest import TestCase
from unittest.mock import patch

from feedback.config import load_config, load_settings, read_json


class ConfigurationTests(TestCase):
    def test_module_growth_thresholds_require_all_roots_and_finite_nonnegative_numbers(self):
        original = load_config("baloot")["report"]["module_growth_thresholds"]
        invalid = [None, [], "0.1", {key: value for key, value in original.items() if key != "账号问题"},
                   {**original, "不存在的类别": .1}]
        invalid += [{**original, "账号问题": value} for value in (-.1, True, "0.1", None, float("nan"), float("inf"))]
        for thresholds in invalid:
            def read(path):
                value = read_json(path)
                if str(path).endswith("baloot/report_rules.json"):
                    value["module_growth_thresholds"] = thresholds
                return value
            with self.subTest(thresholds=thresholds), patch("feedback.config.read_json", side_effect=read):
                with self.assertRaisesRegex(ValueError, "增长率"):
                    load_config("baloot")

    def test_growth_thresholds_can_exceed_one_hundred_percent(self):
        def read(path):
            value = read_json(path)
            if str(path).endswith("baloot/report_rules.json"):
                value["module_growth_thresholds"]["账号问题"] = 2
            return value
        with patch("feedback.config.read_json", side_effect=read):
            self.assertEqual(load_config("baloot")["report"]["module_growth_thresholds"]["账号问题"], 2)

    def test_suggestion_source_labels_must_be_explicit_unique_names(self):
        for labels in ("建议", [], [""], ["建议", "建议"], [" 建议"], [True]):
            def read(path):
                value = read_json(path)
                if str(path).endswith("baloot/settings.json"):
                    value["suggestion_labels"] = labels
                return value
            with self.subTest(labels=labels), patch("feedback.config.read_json", side_effect=read):
                with self.assertRaisesRegex(ValueError, "suggestion_labels"):
                    load_config("baloot")

    def test_game_batch_settings_override_global_defaults(self):
        def read(path):
            value = read_json(path)
            if str(path).endswith("baloot/settings.json"):
                value["classification_batch_size"] = 3
            return value
        with patch("feedback.config.read_json", side_effect=read):
            config = load_config("baloot")
        self.assertEqual(config["classification_batch_size"], 3)
        self.assertEqual(config["classification_batch_max_chars"], load_settings()["classification_batch_max_chars"])

    def test_batch_settings_must_be_positive_integers(self):
        settings = load_settings()
        for key in ("classification_batch_size", "classification_batch_max_chars"):
            for value in (0, -1, True, 1.5, "10"):
                with self.subTest(key=key, value=value), patch("feedback.config.load_settings", return_value={**settings, key: value}):
                    with self.assertRaisesRegex(ValueError, key):
                        load_config("baloot")
