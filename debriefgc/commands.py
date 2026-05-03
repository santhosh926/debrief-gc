from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import re
from zoneinfo import ZoneInfo

from .config import Config
from .llm import generate_summary
from .memory import MemoryStore
from .messages import fetch_messages_between, fetch_recent_command_messages
from .models import ChatCommandMessage, ParsedCommand
from .sender import send_summary


WINDOW_RE = re.compile(
    r"\b(?:past|last)\s+(\d+)\s+"
    r"(minutes?|mins?|hours?|hrs?|days?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CommandPollResult:
    seen: int
    processed: int
    skipped: int


def parse_chat_command(
    text: str,
    mention: str = "@debrief",
    default_summary_hours: int = 6,
) -> ParsedCommand:
    stripped = text.strip()
    if not starts_with_mention(stripped, mention):
        raise ValueError("Message does not start with the configured mention.")

    command_text = stripped[len(mention) :].strip()
    if not command_text:
        raise ValueError("Missing command.")

    parts = command_text.split(maxsplit=1)
    command_name = parts[0].lower()
    if command_name != "summarize":
        raise ValueError(f"Unsupported command: {parts[0]}")

    window = parse_summary_window(command_text, default_summary_hours)
    return ParsedCommand(name=command_name, raw_text=command_text, window=window)


def parse_summary_window(command_text: str, default_summary_hours: int = 6) -> timedelta:
    match = WINDOW_RE.search(command_text)
    if match is None:
        return timedelta(hours=default_summary_hours)

    amount = int(match.group(1))
    if amount < 1:
        raise ValueError("Summary window must be at least 1 unit.")

    unit = match.group(2).lower()
    if unit.startswith(("minute", "min")):
        return timedelta(minutes=amount)
    if unit.startswith(("hour", "hr")):
        return timedelta(hours=amount)
    if unit.startswith("day"):
        return timedelta(days=amount)
    raise ValueError(f"Unsupported summary window unit: {unit}")


def starts_with_mention(text: str, mention: str) -> bool:
    stripped = text.strip()
    if not stripped.lower().startswith(mention.lower()):
        return False
    if len(stripped) == len(mention):
        return True
    return stripped[len(mention)].isspace()


def poll_chat_commands(config: Config) -> CommandPollResult:
    if not config.commands_enabled:
        return CommandPollResult(seen=0, processed=0, skipped=0)

    monitored_chats = (
        config.command_chat_identifiers
        if config.command_chat_identifiers
        else (config.chat_identifier,)
    )
    now = current_time(config)
    since = now - timedelta(minutes=config.command_poll_lookback_minutes)
    store = MemoryStore(config.memory_db_path)
    command_messages = fetch_recent_command_messages(
        config,
        monitored_chats,
        since,
        config.command_mention,
    )

    processed = 0
    skipped = 0
    for command_message in command_messages:
        if store.has_processed_command(
            command_message.chat_identifier, command_message.rowid
        ):
            skipped += 1
            continue

        response = build_command_response(config, store, command_message)
        chat_config = config_for_command_chat(config, command_message)
        send_summary(chat_config, response)
        store.mark_command_processed(
            command_message.chat_identifier,
            command_message.rowid,
            command_message.text,
            response,
        )
        processed += 1

    return CommandPollResult(
        seen=len(command_messages),
        processed=processed,
        skipped=skipped,
    )


def build_command_response(
    config: Config,
    store: MemoryStore,
    command_message: ChatCommandMessage,
) -> str:
    chat_config = config_for_command_chat(config, command_message)
    try:
        parsed = parse_chat_command(
            command_message.text,
            mention=config.command_mention,
            default_summary_hours=config.command_default_summary_hours,
        )
    except ValueError:
        return supported_commands_message(config.command_mention)

    if parsed.name == "summarize" and parsed.window is not None:
        end = command_message.sent_at
        start = end - parsed.window
        messages = fetch_messages_between(
            chat_config,
            start,
            end,
            exclude_message_rowids=(command_message.rowid,),
        )
        prior = store.recent_summaries(
            chat_config.chat_identifier,
            end.date(),
            chat_config.max_memory_days,
        )
        summary = generate_summary(chat_config, end.date(), messages, prior)
        return summary.final_message

    return supported_commands_message(config.command_mention)


def supported_commands_message(mention: str) -> str:
    return (
        "DebriefGC supports: "
        f"{mention} summarize the past 6 hours of this chat"
    )


def config_for_command_chat(
    config: Config,
    command_message: ChatCommandMessage,
) -> Config:
    return replace(
        config,
        chat_identifier=command_message.chat_identifier,
        chat_display_name=command_message.chat_display_name,
    )


def current_time(config: Config) -> datetime:
    if config.timezone:
        return datetime.now(ZoneInfo(config.timezone)).astimezone()
    return datetime.now().astimezone()
