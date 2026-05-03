from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from debriefgc.commands import parse_chat_command, poll_chat_commands, starts_with_mention
from debriefgc.config import Config
from debriefgc.models import ChatCommandMessage


class CommandTests(unittest.TestCase):
    def test_parse_summarize_hours_window(self):
        parsed = parse_chat_command(
            "@debrief summarize the past 6 hours of this chat"
        )

        self.assertEqual(parsed.name, "summarize")
        self.assertEqual(parsed.window, timedelta(hours=6))

    def test_parse_summarize_minutes_window(self):
        parsed = parse_chat_command("@debrief summarize last 45 minutes")

        self.assertEqual(parsed.window, timedelta(minutes=45))

    def test_parse_summarize_uses_default_window(self):
        parsed = parse_chat_command("@debrief summarize", default_summary_hours=12)

        self.assertEqual(parsed.window, timedelta(hours=12))

    def test_parse_rejects_unsupported_command(self):
        with self.assertRaisesRegex(ValueError, "Unsupported command"):
            parse_chat_command("@debrief roast everyone")

    def test_mention_requires_word_boundary(self):
        self.assertTrue(starts_with_mention("@debrief summarize", "@debrief"))
        self.assertFalse(starts_with_mention("@debriefing summarize", "@debrief"))

    def test_poll_marks_command_processed_once(self):
        command_message = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief summarize the past 6 hours of this chat",
            is_from_me=True,
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            config = Config(
                chat_identifier="chat-test",
                chat_display_name="Test Chat",
                participant_names={},
                memory_db_path=Path(tmp_dir) / "memory.sqlite3",
                dry_run=True,
            )

            with patch(
                "debriefgc.commands.fetch_recent_command_messages",
                return_value=[command_message],
            ), patch(
                "debriefgc.commands.build_command_response",
                return_value="summary response",
            ), patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                first = poll_chat_commands(config)
                second = poll_chat_commands(config)

        self.assertEqual(first.processed, 1)
        self.assertEqual(first.skipped, 0)
        self.assertEqual(second.processed, 0)
        self.assertEqual(second.skipped, 1)
        send_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
