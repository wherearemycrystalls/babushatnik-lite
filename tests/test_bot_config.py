from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from bot.config import BotConfig


class BotConfigTests(unittest.TestCase):
    def test_uses_lite_bot_token(self) -> None:
        with patch.dict(os.environ, {"LITE_BOT_TOKEN": "lite-token"}, clear=True):
            self.assertEqual(BotConfig.from_env().bot_token, "lite-token")

    def test_uses_finder_bot_token_as_deprecated_fallback(self) -> None:
        with patch.dict(os.environ, {"FINDER_BOT_TOKEN": "finder-token"}, clear=True):
            self.assertEqual(BotConfig.from_env().bot_token, "finder-token")

    def test_prefers_lite_bot_token_over_finder_fallback(self) -> None:
        env = {"LITE_BOT_TOKEN": "lite-token", "FINDER_BOT_TOKEN": "finder-token"}
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(BotConfig.from_env().bot_token, "lite-token")


if __name__ == "__main__":
    unittest.main()
