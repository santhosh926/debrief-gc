from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import tomllib


DEFAULT_CONFIG_PATH = Path("~/.config/debriefgc/config.toml").expanduser()
DEFAULT_MEMORY_PATH = Path("~/.local/share/debriefgc/memory.sqlite3").expanduser()
DEFAULT_MESSAGES_DB_PATH = Path("~/Library/Messages/chat.db").expanduser()


@dataclass(frozen=True)
class Config:
    chat_identifier: str
    chat_display_name: str
    participant_names: dict[str, str]
    memory_db_path: Path = DEFAULT_MEMORY_PATH
    messages_db_path: Path = DEFAULT_MESSAGES_DB_PATH
    retention_days: int = 90
    model: str = "gpt-4.1-mini"
    openai_api_key: str | None = None
    dry_run: bool = True
    send_mode: str = "dry-run"
    timezone: str | None = None
    max_messages_chars: int = 90_000
    max_memory_days: int = 90
    sender_service: str = "iMessage"
    contacts_enabled: bool = True
    commands_enabled: bool = True
    command_mention: str = "@debrief"
    command_poll_lookback_minutes: int = 360
    command_retry_cooldown_minutes: int = 5
    command_invocation_cooldown_minutes: int = 10
    command_default_summary_hours: int = 6
    command_chat_identifiers: tuple[str, ...] = ()


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found at {path}. Run `debriefgc init-config` first."
        )

    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    group = raw.get("group", {})
    ai = raw.get("ai", {})
    storage = raw.get("storage", {})
    runtime = raw.get("runtime", {})
    sending = raw.get("sending", {})
    contacts = raw.get("contacts", {})
    commands = raw.get("commands", {})

    chat_identifier = str(group.get("chat_identifier", "")).strip()
    if not chat_identifier:
        raise ValueError("Config [group].chat_identifier is required.")

    retention_days = int(storage.get("retention_days", 90))
    if retention_days < 1:
        raise ValueError("Config [storage].retention_days must be at least 1.")

    send_mode = str(sending.get("mode", "dry-run")).strip()
    if send_mode not in {"dry-run", "messages"}:
        raise ValueError("Config [sending].mode must be either 'dry-run' or 'messages'.")

    dry_run = bool(runtime.get("dry_run", send_mode == "dry-run"))
    command_poll_lookback_minutes = int(commands.get("poll_lookback_minutes", 360))
    if command_poll_lookback_minutes < 1:
        raise ValueError("Config [commands].poll_lookback_minutes must be at least 1.")
    command_retry_cooldown_minutes = int(commands.get("retry_cooldown_minutes", 5))
    if command_retry_cooldown_minutes < 1:
        raise ValueError("Config [commands].retry_cooldown_minutes must be at least 1.")
    command_invocation_cooldown_minutes = int(
        commands.get("invocation_cooldown_minutes", 10)
    )
    if command_invocation_cooldown_minutes < 1:
        raise ValueError(
            "Config [commands].invocation_cooldown_minutes must be at least 1."
        )
    command_default_summary_hours = int(commands.get("default_summary_hours", 6))
    if command_default_summary_hours < 1:
        raise ValueError("Config [commands].default_summary_hours must be at least 1.")

    return Config(
        chat_identifier=chat_identifier,
        chat_display_name=str(group.get("display_name", chat_identifier)),
        participant_names={
            str(k): str(v) for k, v in group.get("participant_names", {}).items()
        },
        memory_db_path=Path(
            storage.get("memory_db_path", str(DEFAULT_MEMORY_PATH))
        ).expanduser(),
        messages_db_path=Path(
            storage.get("messages_db_path", str(DEFAULT_MESSAGES_DB_PATH))
        ).expanduser(),
        retention_days=retention_days,
        model=str(ai.get("model", os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"))),
        openai_api_key=normalize_openai_api_key(
            os.environ.get("OPENAI_API_KEY") or ai.get("openai_api_key")
        ),
        dry_run=dry_run,
        send_mode=send_mode,
        timezone=runtime.get("timezone"),
        max_messages_chars=int(ai.get("max_messages_chars", 90_000)),
        max_memory_days=int(ai.get("max_memory_days", retention_days)),
        sender_service=str(sending.get("service", "iMessage")),
        contacts_enabled=bool(contacts.get("enabled", True)),
        commands_enabled=bool(commands.get("enabled", True)),
        command_mention=str(commands.get("mention", "@debrief")).strip() or "@debrief",
        command_poll_lookback_minutes=command_poll_lookback_minutes,
        command_retry_cooldown_minutes=command_retry_cooldown_minutes,
        command_invocation_cooldown_minutes=command_invocation_cooldown_minutes,
        command_default_summary_hours=command_default_summary_hours,
        command_chat_identifiers=tuple(
            str(item).strip()
            for item in commands.get("chat_identifiers", [])
            if str(item).strip()
        ),
    )


def normalize_openai_api_key(value: object) -> str | None:
    if value is None:
        return None
    key = str(value).strip()
    if not key or key == "sk-your-openai-api-key-here":
        return None
    return key


def sample_config() -> str:
    return """# DebriefGC local config
# Create with: debriefgc init-config
# Find possible group identifiers with: debriefgc list-chats

[group]
chat_identifier = "chat1234567890"
display_name = "The Group Chat"

# Optional handle-to-name mapping. Handles are usually phone numbers or emails.
[group.participant_names]
"+15555550123" = "Avery"
"friend@example.com" = "Jordan"
"me" = "Me"

[contacts]
# Uses the local macOS Contacts app to resolve phone numbers/emails to first names.
enabled = true

[ai]
model = "gpt-4.1-mini"
max_messages_chars = 90000
max_memory_days = 90

[storage]
memory_db_path = "~/.local/share/debriefgc/memory.sqlite3"
messages_db_path = "~/Library/Messages/chat.db"
retention_days = 90

[runtime]
dry_run = true
timezone = "America/New_York"

[sending]
# Use "dry-run" until extraction and generation look right.
# Use "messages" to attempt automatic posting through macOS Messages automation.
mode = "dry-run"
service = "iMessage"

[commands]
# `debriefgc poll-commands` watches these chats for messages like:
# @debrief summarize the past 6 hours of this chat
enabled = true
mention = "@debrief"
poll_lookback_minutes = 360
retry_cooldown_minutes = 5
invocation_cooldown_minutes = 10
default_summary_hours = 6

# Optional. If omitted, DebriefGC watches [group].chat_identifier.
chat_identifiers = []
"""
