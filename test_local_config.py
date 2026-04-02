from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import patch

import forward


class LocalConfigTests(unittest.TestCase):
    def test_local_config_has_required_telegram_settings(self):
        config_path = Path(__file__).parent / "config.env"
        self.assertTrue(
            config_path.exists(),
            f"Missing local config file: {config_path}",
        )

        with patch.dict(os.environ, {}, clear=True):
            config = forward.load_config(config_path)

        self.assertTrue(
            config["telegram_bot_token"],
            "TELEGRAM_BOT_TOKEN is missing or empty in config.env",
        )
        self.assertTrue(
            config["telegram_chat_id"],
            "TELEGRAM_CHAT_ID is missing or empty in config.env",
        )


if __name__ == "__main__":
    unittest.main()
