"""Configuration parsing tests for automatic scanning."""

import os
import unittest
from unittest.mock import patch

from app.config import Settings


class SettingsTests(unittest.TestCase):
    def test_scan_defaults_are_thirty_minutes_and_enabled(self) -> None:
        environment = {"DISCORD_TOKEN": "test-discord-token"}
        with patch("app.config.load_dotenv"), patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.scan_interval_minutes, 30)
        self.assertTrue(settings.scan_enabled)
        self.assertNotIn("test-discord-token", repr(settings))

    def test_scan_interval_and_disabled_setting_are_loaded(self) -> None:
        environment = {
            "DISCORD_TOKEN": "test-discord-token",
            "SCAN_INTERVAL_MINUTES": "45",
            "SCAN_ENABLED": "false",
        }
        with patch("app.config.load_dotenv"), patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.scan_interval_minutes, 45)
        self.assertFalse(settings.scan_enabled)

    def test_invalid_scan_settings_are_rejected(self) -> None:
        for values in (
            {"SCAN_INTERVAL_MINUTES": "0"},
            {"SCAN_ENABLED": "sometimes"},
        ):
            environment = {"DISCORD_TOKEN": "test-discord-token", **values}
            with patch("app.config.load_dotenv"), patch.dict(os.environ, environment, clear=True):
                with self.assertRaises(ValueError):
                    Settings.from_env()


if __name__ == "__main__":
    unittest.main()
