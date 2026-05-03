from __future__ import annotations

from datetime import datetime, timezone
import unittest

from debriefgc.messages import (
    applescript_chat_id,
    apple_time_to_datetime,
    datetime_to_apple_nanoseconds,
)


class MessagesTests(unittest.TestCase):
    def test_apple_time_roundtrip_nanoseconds(self):
        original = datetime(2026, 5, 3, 23, 59, tzinfo=timezone.utc)
        encoded = datetime_to_apple_nanoseconds(original)
        decoded = apple_time_to_datetime(encoded)

        self.assertIsNotNone(decoded)
        self.assertEqual(int(decoded.timestamp()), int(original.timestamp()))

    def test_apple_time_handles_none_and_zero(self):
        self.assertIsNone(apple_time_to_datetime(None))
        self.assertIsNone(apple_time_to_datetime(0))

    def test_applescript_chat_id_adds_group_prefix(self):
        self.assertEqual(
            applescript_chat_id("chat782401933118362511"),
            "any;+;chat782401933118362511",
        )
        self.assertEqual(
            applescript_chat_id("any;+;chat782401933118362511"),
            "any;+;chat782401933118362511",
        )


if __name__ == "__main__":
    unittest.main()
