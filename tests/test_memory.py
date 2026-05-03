from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import tempfile
from pathlib import Path
import unittest

from debriefgc.memory import MemoryStore, build_running_lore
from debriefgc.models import DailySummary


def make_summary(day: date, title: str) -> DailySummary:
    return DailySummary(
        chat_identifier="chat-test",
        summary_date=day,
        episode_title=title,
        factual_recap=f"Recap for {title}",
        top_topics=["topic"],
        activity_ranking=[{"member": "Avery", "message_count": 5}],
        notable_moments=["moment"],
        roast_target="Avery",
        roast_text="Avery caught a stray.",
        recurring_bits=[f"{title} bit"],
        final_message=f"Final {title}",
        raw_json={"episode_title": title},
    )


class MemoryTests(unittest.TestCase):
    def test_memory_upsert_recent_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(Path(tmp_dir) / "memory.sqlite3")
            old = make_summary(date(2026, 1, 1), "Old")
            recent = make_summary(date(2026, 5, 1), "Recent")
            store.upsert_summary(old)
            store.upsert_summary(recent)

            summaries = store.recent_summaries("chat-test", date(2026, 5, 3), 90)

            self.assertEqual([summary.episode_title for summary in summaries], ["Recent"])
            self.assertEqual(store.prune("chat-test", 90, date(2026, 5, 3)), 1)
            self.assertEqual(
                store.recent_summaries("chat-test", date(2026, 5, 3), 365)[
                    0
                ].episode_title,
                "Recent",
            )

    def test_build_running_lore_uses_recaps_and_bits(self):
        summary = make_summary(date(2026, 5, 1), "The One With Tests")

        lore = build_running_lore([summary])

        self.assertIn("The One With Tests", lore)
        self.assertIn("The One With Tests bit", lore)

    def test_processed_commands_are_deduped_by_chat_and_message(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(Path(tmp_dir) / "memory.sqlite3")

            self.assertFalse(store.has_processed_command("chat-test", 123))
            store.mark_command_processed("chat-test", 123, "@debrief summarize", "ok")

            self.assertTrue(store.has_processed_command("chat-test", 123))
            self.assertFalse(store.has_processed_command("chat-other", 123))

    def test_command_cooldown_ignores_cooldown_responses(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = MemoryStore(Path(tmp_dir) / "memory.sqlite3")
            store.mark_command_processed(
                "chat-test",
                123,
                "@debrief summarize the past 6 hours of this chat",
                "summary response",
                datetime(2026, 5, 3, 12, 0, tzinfo=timezone.utc),
            )
            store.mark_command_processed(
                "chat-test",
                124,
                "@debrief summarize the past 6 hours of this chat",
                "DebriefGC can only summarize this chat once every 10 minutes.",
                datetime(2026, 5, 3, 12, 5, tzinfo=timezone.utc),
            )

            remaining = store.command_cooldown_remaining(
                "chat-test",
                "@debrief summarize",
                ("DebriefGC can only summarize this chat once every ",),
                timedelta(minutes=10),
                datetime(2026, 5, 3, 12, 9, tzinfo=timezone.utc),
            )

        self.assertEqual(remaining, timedelta(minutes=1))


if __name__ == "__main__":
    unittest.main()
