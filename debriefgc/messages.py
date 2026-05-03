from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, time, timezone
from pathlib import Path
import sqlite3

from .config import Config
from .contacts import ContactsLookupError, load_contact_names, resolve_sender_name
from .models import ChatCommandMessage, ChatInfo, ChatMessage

APPLE_EPOCH_OFFSET_SECONDS = 978_307_200


def apple_time_to_datetime(value: int | float | None) -> datetime | None:
    if value is None:
        return None
    value = float(value)
    if value == 0:
        return None

    absolute_value = abs(value)
    if absolute_value > 1e17:
        seconds = value / 1_000_000_000
    elif absolute_value > 1e14:
        seconds = value / 1_000_000
    elif absolute_value > 1e11:
        seconds = value / 1_000
    else:
        seconds = value

    return datetime.fromtimestamp(
        APPLE_EPOCH_OFFSET_SECONDS + seconds, tz=timezone.utc
    ).astimezone()


def datetime_to_apple_nanoseconds(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.astimezone()
    seconds = dt.timestamp() - APPLE_EPOCH_OFFSET_SECONDS
    return int(seconds * 1_000_000_000)


def connect_messages_db(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(
            f"Messages database not found at {path}. Make sure Messages is enabled on this Mac."
        )
    uri = f"file:{path}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError as exc:
        raise RuntimeError(
            "Could not open the macOS Messages database.\n\n"
            f"Path: {path}\n"
            f"SQLite error: {exc}\n\n"
            "Most likely fix:\n"
            "1. Open System Settings > Privacy & Security > Full Disk Access.\n"
            "2. Enable Full Disk Access for the terminal app you are using "
            "(Terminal, iTerm, VS Code, or Codex).\n"
            "3. Quit and reopen that terminal app.\n"
            "4. Run `debriefgc doctor`, then `debriefgc list-chats` again."
        ) from exc
    conn.row_factory = sqlite3.Row
    return conn


def list_chats(config: Config, limit: int = 30, search: str | None = None) -> list[ChatInfo]:
    with connect_messages_db(config.messages_db_path) as conn:
        params: list[object] = []
        where = ""
        if search:
            pattern = f"%{search}%"
            where = """
            WHERE chat.display_name LIKE ?
               OR chat.chat_identifier LIKE ?
               OR chat.guid LIKE ?
               OR EXISTS (
                 SELECT 1
                 FROM chat_handle_join
                 JOIN handle ON handle.ROWID = chat_handle_join.handle_id
                 WHERE chat_handle_join.chat_id = chat.ROWID
                   AND handle.id LIKE ?
               )
            """
            params.extend([pattern, pattern, pattern, pattern])
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT
              chat.ROWID AS rowid,
              chat.guid AS guid,
              COALESCE(chat.display_name, '') AS display_name,
              COALESCE(chat.chat_identifier, '') AS chat_identifier,
              MAX(message.date) AS last_message_date
            FROM chat
            LEFT JOIN chat_message_join ON chat.ROWID = chat_message_join.chat_id
            LEFT JOIN message ON message.ROWID = chat_message_join.message_id
            {where}
            GROUP BY chat.ROWID
            ORDER BY last_message_date DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        chats: list[ChatInfo] = []
        for row in rows:
            participants = [
                item["id"]
                for item in conn.execute(
                    """
                    SELECT DISTINCT handle.id
                    FROM chat_handle_join
                    JOIN handle ON handle.ROWID = chat_handle_join.handle_id
                    WHERE chat_handle_join.chat_id = ?
                    ORDER BY handle.id
                    """,
                    (row["rowid"],),
                ).fetchall()
            ]
            chats.append(
                ChatInfo(
                    rowid=int(row["rowid"]),
                    guid=row["guid"],
                    display_name=row["display_name"],
                    chat_identifier=row["chat_identifier"],
                    last_message_at=apple_time_to_datetime(row["last_message_date"]),
                    participants=participants,
                )
            )
        return chats


def resolve_chat_send_identifier(config: Config) -> str:
    with connect_messages_db(config.messages_db_path) as conn:
        row = conn.execute(
            """
            SELECT chat_identifier, guid
            FROM chat
            WHERE guid = ? OR chat_identifier = ? OR display_name = ?
            ORDER BY ROWID DESC
            LIMIT 1
            """,
            (
                config.chat_identifier,
                config.chat_identifier,
                config.chat_identifier,
            ),
        ).fetchone()
    if row is None:
        raise ValueError(
            f"Could not find chat '{config.chat_identifier}'. Run `debriefgc list-chats`."
        )
    return applescript_chat_id(row["chat_identifier"] or row["guid"])


def applescript_chat_id(identifier: str) -> str:
    if identifier.startswith(("any;", "iMessage;", "SMS;")):
        return identifier
    if identifier.startswith("chat"):
        return f"any;+;{identifier}"
    return identifier


def fetch_daily_messages(
    config: Config, target_date: date, local_tz=None
) -> list[ChatMessage]:
    tz = local_tz
    start = datetime.combine(target_date, time.min, tzinfo=tz).astimezone()
    end = datetime.combine(target_date, time.max, tzinfo=tz).astimezone()
    return fetch_messages_between(config, start, end)


def fetch_messages_between(
    config: Config,
    start: datetime,
    end: datetime,
    exclude_message_rowids: Sequence[int] = (),
) -> list[ChatMessage]:
    if start.tzinfo is None:
        start = start.astimezone()
    if end.tzinfo is None:
        end = end.astimezone()
    start_ns = datetime_to_apple_nanoseconds(start)
    end_ns = datetime_to_apple_nanoseconds(end)

    with connect_messages_db(config.messages_db_path) as conn:
        chat_row = conn.execute(
            """
            SELECT ROWID
            FROM chat
            WHERE guid = ? OR chat_identifier = ? OR display_name = ?
            LIMIT 1
            """,
            (
                config.chat_identifier,
                config.chat_identifier,
                config.chat_identifier,
            ),
        ).fetchone()
        if chat_row is None:
            raise ValueError(
                f"Could not find chat '{config.chat_identifier}'. Run `debriefgc list-chats`."
            )

        exclude_clause = ""
        exclude_params: list[object] = []
        if exclude_message_rowids:
            placeholders = ", ".join("?" for _ in exclude_message_rowids)
            exclude_clause = f"AND message.ROWID NOT IN ({placeholders})"
            exclude_params.extend(int(rowid) for rowid in exclude_message_rowids)

        rows = conn.execute(
            """
            SELECT
              message.ROWID AS rowid,
              message.date,
              COALESCE(handle.id, 'me') AS sender_handle,
              message.is_from_me,
              message.text,
              message.attributedBody
            FROM message
            JOIN chat_message_join ON chat_message_join.message_id = message.ROWID
            LEFT JOIN handle ON handle.ROWID = message.handle_id
            WHERE chat_message_join.chat_id = ?
              AND message.date >= ?
              AND message.date <= ?
              {exclude_clause}
            ORDER BY message.date ASC
            """.format(exclude_clause=exclude_clause),
            (chat_row["ROWID"], start_ns, end_ns, *exclude_params),
        ).fetchall()

    messages: list[ChatMessage] = []
    try:
        contact_names = load_contact_names(config)
    except ContactsLookupError:
        contact_names = {}
    for row in rows:
        text = message_text_from_row(row)
        if not text:
            continue
        sent_at = apple_time_to_datetime(row["date"])
        if sent_at is None:
            continue
        sender_handle = "me" if row["is_from_me"] else row["sender_handle"]
        sender_name = resolve_sender_name(sender_handle, config, contact_names)
        messages.append(
            ChatMessage(
                sent_at=sent_at,
                sender_handle=sender_handle,
                sender_name=sender_name,
                text=text,
                is_from_me=bool(row["is_from_me"]),
            )
        )
    return messages


def fetch_recent_command_messages(
    config: Config,
    chat_identifiers: Sequence[str],
    since: datetime,
    mention: str,
) -> list[ChatCommandMessage]:
    if since.tzinfo is None:
        since = since.astimezone()
    since_ns = datetime_to_apple_nanoseconds(since)
    command_messages: list[ChatCommandMessage] = []
    try:
        contact_names = load_contact_names(config)
    except ContactsLookupError:
        contact_names = {}

    with connect_messages_db(config.messages_db_path) as conn:
        for chat_identifier in chat_identifiers:
            chat_row = conn.execute(
                """
                SELECT ROWID, guid, COALESCE(display_name, '') AS display_name,
                       COALESCE(chat_identifier, '') AS chat_identifier
                FROM chat
                WHERE guid = ? OR chat_identifier = ? OR display_name = ?
                ORDER BY ROWID DESC
                LIMIT 1
                """,
                (chat_identifier, chat_identifier, chat_identifier),
            ).fetchone()
            if chat_row is None:
                continue

            rows = conn.execute(
                """
                SELECT
                  message.ROWID AS rowid,
                  message.date,
                  COALESCE(handle.id, 'me') AS sender_handle,
                  message.is_from_me,
                  message.text,
                  message.attributedBody
                FROM message
                JOIN chat_message_join ON chat_message_join.message_id = message.ROWID
                LEFT JOIN handle ON handle.ROWID = message.handle_id
                WHERE chat_message_join.chat_id = ?
                  AND message.date >= ?
                ORDER BY message.date ASC
                """,
                (chat_row["ROWID"], since_ns),
            ).fetchall()

            resolved_chat_identifier = (
                chat_row["chat_identifier"] or chat_row["guid"] or chat_identifier
            )
            display_name = chat_row["display_name"] or resolved_chat_identifier
            for row in rows:
                text = message_text_from_row(row)
                if not starts_with_mention(text, mention):
                    continue
                sent_at = apple_time_to_datetime(row["date"])
                if sent_at is None:
                    continue
                sender_handle = "me" if row["is_from_me"] else row["sender_handle"]
                sender_name = resolve_sender_name(sender_handle, config, contact_names)
                command_messages.append(
                    ChatCommandMessage(
                        rowid=int(row["rowid"]),
                        chat_identifier=resolved_chat_identifier,
                        chat_display_name=display_name,
                        sent_at=sent_at,
                        sender_handle=sender_handle,
                        sender_name=sender_name,
                        text=text,
                        is_from_me=bool(row["is_from_me"]),
                    )
                )
    return command_messages


def message_text_from_row(row: sqlite3.Row) -> str:
    text = row["text"]
    if text is not None and str(text).strip():
        return str(text)
    return decode_attributed_body(row["attributedBody"])


def decode_attributed_body(value: object) -> str:
    if value is None:
        return ""
    data = bytes(value)
    marker = b"NSString"
    marker_index = data.find(marker)
    if marker_index < 0:
        return ""
    text_marker = b"\x94\x84\x01+"
    text_index = data.find(text_marker, marker_index)
    if text_index < 0:
        return ""
    length_index = text_index + len(text_marker)
    if length_index >= len(data):
        return ""
    length = data[length_index]
    start = length_index + 1
    end = start + length
    if end > len(data):
        return ""
    return data[start:end].decode("utf-8", errors="replace").strip()


def starts_with_mention(text: str, mention: str) -> bool:
    stripped = text.strip()
    if not stripped.lower().startswith(mention.lower()):
        return False
    if len(stripped) == len(mention):
        return True
    return stripped[len(mention)].isspace()


def format_messages_for_prompt(messages: list[ChatMessage], max_chars: int) -> str:
    lines = [
        f"[{message.sent_at.strftime('%H:%M')}] {message.sender_name}: {message.text}"
        for message in messages
    ]
    joined = "\n".join(lines)
    if len(joined) <= max_chars:
        return joined
    return joined[-max_chars:]
