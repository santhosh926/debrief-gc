from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import re
import sys
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
COMMAND_COOLDOWN_RESPONSE_PREFIX = (
    "DebriefGC can only summarize this chat once every "
)


@dataclass(frozen=True)
class CommandPollResult:
    seen: int
    processed: int
    skipped: int
    failed: int = 0


def parse_chat_command(
    text: str,
    mention: str = "@debrief",
    default_summary_hours: int = 6,
    max_lookback_days: int = 7,
) -> ParsedCommand:
    stripped = text.strip()
    if not starts_with_mention(stripped, mention):
        raise ValueError(f"Message must start with {mention}.")

    command_text = stripped[len(mention) :].strip()
    if not command_text:
        raise ValueError(f"Missing command after {mention}.")

    parts = command_text.split(maxsplit=1)
    command_name = parts[0].lower()
    if command_name != "summarize":
        raise ValueError(f'Unsupported command "{parts[0]}".')

    window = parse_summary_window(
        command_text,
        default_summary_hours,
        max_lookback_days,
    )
    return ParsedCommand(name=command_name, raw_text=command_text, window=window)


def parse_summary_window(
    command_text: str,
    default_summary_hours: int = 6,
    max_lookback_days: int = 7,
) -> timedelta:
    match = WINDOW_RE.search(command_text)
    if match is None:
        return validate_summary_window(
            timedelta(hours=default_summary_hours), max_lookback_days
        )

    amount = int(match.group(1))
    if amount < 1:
        raise ValueError("Summary window must be at least 1 unit.")

    unit = match.group(2).lower()
    if unit.startswith(("minute", "min")):
        return validate_summary_window(timedelta(minutes=amount), max_lookback_days)
    if unit.startswith(("hour", "hr")):
        return validate_summary_window(timedelta(hours=amount), max_lookback_days)
    if unit.startswith("day"):
        return validate_summary_window(timedelta(days=amount), max_lookback_days)
    raise ValueError(f"Unsupported summary window unit: {unit}")


def validate_summary_window(window: timedelta, max_lookback_days: int) -> timedelta:
    if window > timedelta(days=max_lookback_days):
        raise ValueError(
            f"Summary window cannot be more than {max_lookback_days} days."
        )
    return window


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
    failed = 0
    retry_cooldown = timedelta(minutes=config.command_retry_cooldown_minutes)
    for command_message in command_messages:
        if store.has_processed_command(
            command_message.chat_identifier, command_message.rowid
        ):
            skipped += 1
            continue

        if not store.should_retry_command(
            command_message.chat_identifier,
            command_message.rowid,
            retry_cooldown,
            now,
        ):
            skipped += 1
            continue

        try:
            response = build_command_response(config, store, command_message, now)
            chat_config = config_for_command_chat(config, command_message)
            send_summary(chat_config, response)
        except Exception as exc:
            store.mark_command_failed(
                command_message.chat_identifier,
                command_message.rowid,
                command_message.text,
                str(exc),
                now,
            )
            print(
                "Command failed: "
                f"chat={command_message.chat_identifier} "
                f"rowid={command_message.rowid}: {exc}",
                file=sys.stderr,
            )
            failed += 1
            continue

        store.mark_command_processed(
            command_message.chat_identifier,
            command_message.rowid,
            command_message.text,
            response,
            now,
        )
        store.clear_command_failure(
            command_message.chat_identifier,
            command_message.rowid,
        )
        processed += 1

    return CommandPollResult(
        seen=len(command_messages),
        processed=processed,
        skipped=skipped,
        failed=failed,
    )


def build_command_response(
    config: Config,
    store: MemoryStore,
    command_message: ChatCommandMessage,
    now: datetime | None = None,
) -> str:
    chat_config = config_for_command_chat(config, command_message)
    try:
        parsed = parse_chat_command(
            command_message.text,
            mention=config.command_mention,
            default_summary_hours=config.command_default_summary_hours,
            max_lookback_days=config.command_max_lookback_days,
        )
    except ValueError as exc:
        return invalid_command_message(config.command_mention, str(exc))

    if parsed.name == "summarize" and parsed.window is not None:
        if now is None:
            now = current_time(config)
        cooldown = timedelta(minutes=config.command_invocation_cooldown_minutes)
        cooldown_remaining = store.command_cooldown_remaining(
            chat_config.chat_identifier,
            f"{config.command_mention} summarize",
            (COMMAND_COOLDOWN_RESPONSE_PREFIX,),
            cooldown,
            now,
        )
        if cooldown_remaining > timedelta(0):
            return command_cooldown_message(cooldown, cooldown_remaining)

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


def invalid_command_message(mention: str, reason: str) -> str:
    return (
        f"DebriefGC couldn't understand that command: {reason}\n\n"
        f"{supported_commands_message(mention)}\n"
        "You can change the window with minutes, hours, or days, like: "
        f"{mention} summarize last 45 minutes"
    )


def command_cooldown_message(cooldown: timedelta, remaining: timedelta) -> str:
    cooldown_minutes = max(1, int((cooldown.total_seconds() + 59) // 60))
    minutes = max(1, int((remaining.total_seconds() + 59) // 60))
    cooldown_plural = "" if cooldown_minutes == 1 else "s"
    plural = "" if minutes == 1 else "s"
    return (
        f"{COMMAND_COOLDOWN_RESPONSE_PREFIX}{cooldown_minutes} "
        f"minute{cooldown_plural}. "
        f"Try again in about {minutes} minute{plural}."
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
