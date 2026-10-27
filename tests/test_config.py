from unittest import TestCase
from unittest.mock import patch

from feedback.config import load_config, load_settings, read_json


class ConfigurationTests(TestCase):
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
