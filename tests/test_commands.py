from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from debriefgc.commands import (
    command_cooldown_message,
    parse_chat_command,
    poll_chat_commands,
    starts_with_mention,
)
from debriefgc.config import Config
from debriefgc.memory import MemoryStore
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

    def test_parse_help_command(self):
        parsed = parse_chat_command("@debrief help")

        self.assertEqual(parsed.name, "help")
        self.assertIsNone(parsed.window)

    def test_parse_rejects_unsupported_command(self):
        with self.assertRaisesRegex(ValueError, "Unsupported command"):
            parse_chat_command("@debrief roast everyone")

    def test_invalid_command_response_explains_reason(self):
        command_message = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief roast everyone",
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
            store = MemoryStore(config.memory_db_path)

            response = build_command_response(config, store, command_message)

        self.assertIn('Unsupported command "roast".', response)
        self.assertIn(
            "DebriefGC supports: @debrief summarize the past 6 hours of this chat",
            response,
        )
        self.assertIn("@debrief summarize last 45 minutes", response)

    def test_summarize_response_respects_invocation_cooldown(self):
        command_message = ChatCommandMessage(
            rowid=124,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 5),
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
                command_invocation_cooldown_minutes=10,
            )
            store = MemoryStore(config.memory_db_path)
            store.mark_command_processed(
                "chat-test",
                123,
                "@debrief summarize the past 6 hours of this chat",
                "summary response",
                datetime(2026, 5, 3, 12, 0),
            )

            with patch("debriefgc.commands.fetch_messages_between") as fetch_mock:
                response = build_command_response(
                    config,
                    store,
                    command_message,
                    datetime(2026, 5, 3, 12, 5),
                )

        self.assertIn(
            "DebriefGC can only summarize this chat once every 10 minutes.",
            response,
        )
        self.assertIn("Try again in about 5 minutes.", response)
        fetch_mock.assert_not_called()

    def test_mention_requires_word_boundary(self):
        self.assertTrue(starts_with_mention("@debrief summarize", "@debrief"))
        self.assertFalse(starts_with_mention("@debriefing summarize", "@debrief"))

    def test_cooldown_message_reports_remaining_time(self):
        message = command_cooldown_message(
            timedelta(minutes=10),
            timedelta(minutes=4, seconds=1),
        )

        self.assertEqual(
            message,
            "DebriefGC can only summarize this chat once every 10 minutes. "
            "Try again in about 5 minutes.",
        )

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
        self.assertEqual(first.failed, 0)
        self.assertEqual(second.processed, 0)
        self.assertEqual(second.skipped, 1)
        self.assertEqual(send_mock.call_count, 2)
        self.assertEqual(send_mock.call_args_list[0].args[1], "DebriefGC is working...")
        self.assertEqual(send_mock.call_args_list[1].args[1], "summary response")

    def test_poll_invalid_command_sends_helper_message_without_working_reply(self):
        command_message = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief roast everyone",
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
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                result = poll_chat_commands(config)

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.skipped, 0)
        build_mock.assert_not_called()
        send_mock.assert_called_once()
        self.assertEqual(
            send_mock.call_args.args[1],
            "invalid command: type '@debrief help' for help",
        )

    def test_poll_help_command_sends_help_without_working_reply(self):
        command_message = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief help",
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
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                result = poll_chat_commands(config)

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.skipped, 0)
        build_mock.assert_not_called()
        send_mock.assert_called_once()
        self.assertEqual(
            send_mock.call_args.args[1],
            "DebriefGC supports: @debrief summarize the past 6 hours of this chat",
        )

    def test_poll_invalid_command_does_not_start_summarize_cooldown(self):
        invalid_command = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief roast everyone",
            is_from_me=True,
        )
        summarize_command = ChatCommandMessage(
            rowid=124,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 1),
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
                command_retry_cooldown_minutes=10,
            )

            with patch(
                "debriefgc.commands.fetch_recent_command_messages",
                return_value=[invalid_command, summarize_command],
            ), patch(
                "debriefgc.commands.build_command_response",
                return_value="summary response",
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                result = poll_chat_commands(config)

        self.assertEqual(result.processed, 2)
        self.assertEqual(result.skipped, 0)
        build_mock.assert_called_once()
        self.assertEqual(
            [call.args[1] for call in send_mock.call_args_list],
            [
                "invalid command: type '@debrief help' for help",
                "DebriefGC is working...",
                "summary response",
            ],
        )

    def test_poll_skips_same_chat_command_inside_cooldown(self):
        first_command = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief summarize the past 6 hours of this chat",
            is_from_me=True,
        )
        second_command = ChatCommandMessage(
            rowid=124,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 5),
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
                command_retry_cooldown_minutes=10,
            )

            with patch(
                "debriefgc.commands.fetch_recent_command_messages",
                return_value=[first_command, second_command],
            ), patch(
                "debriefgc.commands.build_command_response",
                return_value="summary response",
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                first = poll_chat_commands(config)
                second = poll_chat_commands(config)

        self.assertEqual(first.processed, 1)
        self.assertEqual(first.skipped, 1)
        self.assertEqual(second.processed, 0)
        self.assertEqual(second.skipped, 2)
        build_mock.assert_called_once()
        self.assertEqual(send_mock.call_count, 3)
        self.assertEqual(send_mock.call_args_list[0].args[1], "DebriefGC is working...")
        self.assertEqual(send_mock.call_args_list[1].args[1], "summary response")
        self.assertEqual(
            send_mock.call_args_list[2].args[1],
            "DebriefGC can only summarize this chat once every 10 minutes. "
            "Try again in about 5 minutes.",
        )

    def test_poll_cooldown_reply_does_not_extend_cooldown(self):
        first_command = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief summarize the past 6 hours of this chat",
            is_from_me=True,
        )
        second_command = ChatCommandMessage(
            rowid=124,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 5),
            sender_handle="me",
            sender_name="Me",
            text="@debrief summarize the past 6 hours of this chat",
            is_from_me=True,
        )
        third_command = ChatCommandMessage(
            rowid=125,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 11),
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
                command_retry_cooldown_minutes=10,
            )

            with patch(
                "debriefgc.commands.fetch_recent_command_messages",
                return_value=[first_command, second_command, third_command],
            ), patch(
                "debriefgc.commands.build_command_response",
                return_value="summary response",
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                result = poll_chat_commands(config)

        self.assertEqual(result.processed, 2)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(build_mock.call_count, 2)
        self.assertEqual(send_mock.call_count, 5)

    def test_poll_allows_same_chat_command_after_cooldown(self):
        first_command = ChatCommandMessage(
            rowid=123,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 0),
            sender_handle="me",
            sender_name="Me",
            text="@debrief summarize the past 6 hours of this chat",
            is_from_me=True,
        )
        second_command = ChatCommandMessage(
            rowid=124,
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            sent_at=datetime(2026, 5, 3, 12, 10),
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
                command_retry_cooldown_minutes=10,
            )

            with patch(
                "debriefgc.commands.fetch_recent_command_messages",
                return_value=[first_command, second_command],
            ), patch(
                "debriefgc.commands.build_command_response",
                return_value="summary response",
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                result = poll_chat_commands(config)

        self.assertEqual(result.processed, 2)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(build_mock.call_count, 2)
        self.assertEqual(send_mock.call_count, 4)

    def test_poll_retries_failed_commands_after_cooldown(self):
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
                command_retry_cooldown_minutes=5,
            )
            with patch(
                "debriefgc.commands.fetch_recent_command_messages",
                return_value=[command_message],
            ), patch(
                "debriefgc.commands.current_time",
                side_effect=[
                    datetime(2026, 5, 3, 12, 0),
                    datetime(2026, 5, 3, 12, 1),
                    datetime(2026, 5, 3, 12, 6),
                ],
            ), patch(
                "debriefgc.commands.build_command_response",
                side_effect=[RuntimeError("OpenAI timeout"), "summary response"],
            ) as build_mock, patch(
                "debriefgc.commands.send_summary"
            ) as send_mock:
                first = poll_chat_commands(config)
                second = poll_chat_commands(config)
                third = poll_chat_commands(config)

        self.assertEqual(first.failed, 1)
        self.assertEqual(first.processed, 0)
        self.assertEqual(second.failed, 0)
        self.assertEqual(second.skipped, 1)
        self.assertEqual(third.processed, 1)
        self.assertEqual(build_mock.call_count, 2)
        send_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
