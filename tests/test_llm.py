from __future__ import annotations

from datetime import date, datetime
import tempfile
from pathlib import Path
import ssl
import unittest
from unittest.mock import patch
from urllib import error

from debriefgc.config import Config
from debriefgc.llm import call_openai_json, generate_summary, openai_ssl_context
from debriefgc.models import ChatMessage


class LlmTests(unittest.TestCase):
    def test_generate_summary_without_api_key_uses_local_fallback(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = Config(
                chat_identifier="chat-test",
                chat_display_name="Test Chat",
                participant_names={},
                memory_db_path=Path(tmp_dir) / "memory.sqlite3",
                openai_api_key=None,
            )
            messages = [
                ChatMessage(
                    sent_at=datetime(2026, 5, 3, 12, 0),
                    sender_handle="a",
                    sender_name="Avery",
                    text="hello",
                    is_from_me=False,
                ),
                ChatMessage(
                    sent_at=datetime(2026, 5, 3, 12, 1),
                    sender_handle="a",
                    sender_name="Avery",
                    text="again",
                    is_from_me=False,
                ),
            ]

            summary = generate_summary(config, date(2026, 5, 3), messages, [])

            self.assertEqual(summary.roast_target, "Avery")
            self.assertEqual(summary.activity_ranking[0]["message_count"], 2)
            self.assertIn("DebriefGC", summary.final_message)

    def test_call_openai_json_explains_certificate_errors(self):
        with patch(
            "debriefgc.llm.request.urlopen",
            side_effect=error.URLError(ssl.SSLCertVerificationError()),
        ):
            with self.assertRaisesRegex(RuntimeError, "Install Certificates"):
                call_openai_json("sk-test", "gpt-test", "prompt")

    def test_openai_ssl_context_builds(self):
        self.assertIsInstance(openai_ssl_context(), ssl.SSLContext)


if __name__ == "__main__":
    unittest.main()
