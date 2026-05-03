from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from debriefgc.config import Config, load_config, normalize_openai_api_key
from debriefgc.contacts import contact_lookup_keys, resolve_sender_name


class ContactsTests(unittest.TestCase):
    def test_phone_lookup_keys_normalize_us_numbers(self):
        self.assertEqual(
            contact_lookup_keys("+1 (609) 770-1722"),
            ["phone:+16097701722", "phone:16097701722", "phone:6097701722"],
        )
        self.assertEqual(
            contact_lookup_keys("609-770-1722"),
            ["phone:+16097701722", "phone:6097701722"],
        )
        self.assertEqual(
            contact_lookup_keys("+16097701722"),
            ["phone:+16097701722", "phone:16097701722", "phone:6097701722"],
        )

    def test_email_lookup_keys_are_case_insensitive(self):
        self.assertEqual(
            contact_lookup_keys("Friend@Example.com"),
            ["email:friend@example.com"],
        )

    def test_resolve_sender_name_precedence(self):
        config = Config(
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            participant_names={"+16097701722": "Custom"},
            memory_db_path=Path("memory.sqlite3"),
        )
        contact_names = {"phone:+16097701722": "Contacts"}

        self.assertEqual(
            resolve_sender_name("+16097701722", config, contact_names),
            "Custom",
        )

    def test_resolve_sender_name_uses_contacts_before_raw_handle(self):
        config = Config(
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            participant_names={},
            memory_db_path=Path("memory.sqlite3"),
        )
        contact_names = {"phone:+16097701722": "Avery"}

        self.assertEqual(
            resolve_sender_name("(609) 770-1722", config, contact_names),
            "Avery",
        )

    def test_resolve_sender_name_keeps_unresolved_handle(self):
        config = Config(
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            participant_names={},
            memory_db_path=Path("memory.sqlite3"),
        )

        self.assertEqual(
            resolve_sender_name("+17326728191", config, {}),
            "+17326728191",
        )

    def test_me_resolves_through_config(self):
        config = Config(
            chat_identifier="chat-test",
            chat_display_name="Test Chat",
            participant_names={"me": "Santhosh"},
            memory_db_path=Path("memory.sqlite3"),
        )

        self.assertEqual(resolve_sender_name("me", config, {}), "Santhosh")

    def test_load_config_defaults_contacts_enabled(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text(
                """
[group]
chat_identifier = "chat-test"
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.contacts_enabled)

    def test_load_config_allows_disabling_contacts(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text(
                """
[group]
chat_identifier = "chat-test"

[contacts]
enabled = false
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertFalse(config.contacts_enabled)

    def test_load_config_defaults_command_settings(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text(
                """
[group]
chat_identifier = "chat-test"
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertTrue(config.commands_enabled)
            self.assertEqual(config.command_mention, "@debrief")
            self.assertEqual(config.command_poll_lookback_minutes, 360)
            self.assertEqual(config.command_retry_cooldown_minutes, 5)
            self.assertEqual(config.command_invocation_cooldown_minutes, 10)
            self.assertEqual(config.command_default_summary_hours, 6)
            self.assertEqual(config.command_chat_identifiers, ())

    def test_load_config_reads_command_chat_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.toml"
            config_path.write_text(
                """
[group]
chat_identifier = "chat-test"

[commands]
chat_identifiers = ["chat-one", "chat-two"]
""",
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.command_chat_identifiers, ("chat-one", "chat-two"))

    def test_openai_placeholder_is_not_treated_as_configured_key(self):
        self.assertIsNone(normalize_openai_api_key("sk-your-openai-api-key-here"))
        self.assertEqual(normalize_openai_api_key("sk-real"), "sk-real")


if __name__ == "__main__":
    unittest.main()
