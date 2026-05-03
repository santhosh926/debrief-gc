from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import sqlite3

from .models import DailySummary


SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_identifier TEXT NOT NULL,
  summary_date TEXT NOT NULL,
  episode_title TEXT NOT NULL,
  factual_recap TEXT NOT NULL,
  top_topics_json TEXT NOT NULL,
  activity_ranking_json TEXT NOT NULL,
  notable_moments_json TEXT NOT NULL,
  roast_target TEXT NOT NULL,
  roast_text TEXT NOT NULL,
  recurring_bits_json TEXT NOT NULL,
  final_message TEXT NOT NULL,
  raw_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(chat_identifier, summary_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_summaries_chat_date
ON daily_summaries(chat_identifier, summary_date DESC);

CREATE TABLE IF NOT EXISTS processed_commands (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_identifier TEXT NOT NULL,
  message_rowid INTEGER NOT NULL,
  command_text TEXT NOT NULL,
  response_text TEXT NOT NULL,
  processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(chat_identifier, message_rowid)
);

CREATE INDEX IF NOT EXISTS idx_processed_commands_chat_message
ON processed_commands(chat_identifier, message_rowid);

CREATE TABLE IF NOT EXISTS command_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_identifier TEXT NOT NULL,
  message_rowid INTEGER NOT NULL,
  command_text TEXT NOT NULL,
  error_text TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 1,
  first_failed_at TEXT NOT NULL,
  last_failed_at TEXT NOT NULL,
  UNIQUE(chat_identifier, message_rowid)
);

CREATE INDEX IF NOT EXISTS idx_command_failures_chat_message
ON command_failures(chat_identifier, message_rowid);
"""


class MemoryStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def upsert_summary(self, summary: DailySummary) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO daily_summaries (
                  chat_identifier, summary_date, episode_title, factual_recap,
                  top_topics_json, activity_ranking_json, notable_moments_json,
                  roast_target, roast_text, recurring_bits_json, final_message, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_identifier, summary_date) DO UPDATE SET
                  episode_title = excluded.episode_title,
                  factual_recap = excluded.factual_recap,
                  top_topics_json = excluded.top_topics_json,
                  activity_ranking_json = excluded.activity_ranking_json,
                  notable_moments_json = excluded.notable_moments_json,
                  roast_target = excluded.roast_target,
                  roast_text = excluded.roast_text,
                  recurring_bits_json = excluded.recurring_bits_json,
                  final_message = excluded.final_message,
                  raw_json = excluded.raw_json,
                  created_at = CURRENT_TIMESTAMP
                """,
                (
                    summary.chat_identifier,
                    summary.summary_date.isoformat(),
                    summary.episode_title,
                    summary.factual_recap,
                    json.dumps(summary.top_topics),
                    json.dumps(summary.activity_ranking),
                    json.dumps(summary.notable_moments),
                    summary.roast_target,
                    summary.roast_text,
                    json.dumps(summary.recurring_bits),
                    summary.final_message,
                    json.dumps(summary.raw_json),
                ),
            )

    def recent_summaries(
        self, chat_identifier: str, before: date, limit_days: int
    ) -> list[DailySummary]:
        cutoff = before - timedelta(days=limit_days)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM daily_summaries
                WHERE chat_identifier = ?
                  AND summary_date < ?
                  AND summary_date >= ?
                ORDER BY summary_date DESC
                """,
                (chat_identifier, before.isoformat(), cutoff.isoformat()),
            ).fetchall()
        return [summary_from_row(row) for row in rows]

    def prune(self, chat_identifier: str, keep_days: int, today: date) -> int:
        cutoff = today - timedelta(days=keep_days)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM daily_summaries
                WHERE chat_identifier = ?
                  AND summary_date < ?
                """,
                (chat_identifier, cutoff.isoformat()),
            )
            return cursor.rowcount

    def has_processed_command(self, chat_identifier: str, message_rowid: int) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM processed_commands
                WHERE chat_identifier = ?
                  AND message_rowid = ?
                LIMIT 1
                """,
                (chat_identifier, message_rowid),
            ).fetchone()
        return row is not None

    def mark_command_processed(
        self,
        chat_identifier: str,
        message_rowid: int,
        command_text: str,
        response_text: str,
        processed_at: datetime | None = None,
    ) -> None:
        timestamp = format_utc_timestamp(processed_at) if processed_at else None
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_commands (
                  chat_identifier, message_rowid, command_text, response_text,
                  processed_at
                ) VALUES (?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                ON CONFLICT(chat_identifier, message_rowid) DO UPDATE SET
                  command_text = excluded.command_text,
                  response_text = excluded.response_text,
                  processed_at = excluded.processed_at
                """,
                (
                    chat_identifier,
                    message_rowid,
                    command_text,
                    response_text,
                    timestamp,
                ),
            )

    def command_cooldown_remaining(
        self,
        chat_identifier: str,
        command_text_prefix: str,
        ignored_response_prefixes: tuple[str, ...],
        cooldown: timedelta,
        now: datetime,
    ) -> timedelta:
        normalized_prefix = command_text_prefix.strip().lower()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT command_text, response_text, processed_at
                FROM processed_commands
                WHERE chat_identifier = ?
                ORDER BY processed_at DESC
                LIMIT 50
                """,
                (chat_identifier,),
            ).fetchall()
        for row in rows:
            command_text = str(row["command_text"]).strip().lower()
            if not command_text.startswith(normalized_prefix):
                continue
            response_text = str(row["response_text"])
            if any(
                response_text.startswith(prefix)
                for prefix in ignored_response_prefixes
            ):
                continue

            last_processed_at = parse_stored_timestamp(row["processed_at"])
            elapsed = utc_datetime(now) - last_processed_at
            remaining = cooldown - elapsed
            if remaining <= timedelta(0):
                return timedelta(0)
            return remaining
        return timedelta(0)

    def should_retry_command(
        self,
        chat_identifier: str,
        message_rowid: int,
        cooldown: timedelta,
        now: datetime,
    ) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT last_failed_at
                FROM command_failures
                WHERE chat_identifier = ?
                  AND message_rowid = ?
                LIMIT 1
                """,
                (chat_identifier, message_rowid),
            ).fetchone()
        if row is None:
            return True

        last_failed_at = datetime.fromisoformat(row["last_failed_at"])
        if last_failed_at.tzinfo is None:
            last_failed_at = last_failed_at.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.astimezone()
        return now.astimezone(timezone.utc) - last_failed_at >= cooldown

    def mark_command_failed(
        self,
        chat_identifier: str,
        message_rowid: int,
        command_text: str,
        error_text: str,
        failed_at: datetime,
    ) -> None:
        timestamp = format_utc_timestamp(failed_at)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO command_failures (
                  chat_identifier, message_rowid, command_text, error_text,
                  first_failed_at, last_failed_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_identifier, message_rowid) DO UPDATE SET
                  command_text = excluded.command_text,
                  error_text = excluded.error_text,
                  attempts = attempts + 1,
                  last_failed_at = excluded.last_failed_at
                """,
                (
                    chat_identifier,
                    message_rowid,
                    command_text,
                    error_text,
                    timestamp,
                    timestamp,
                ),
            )

    def clear_command_failure(self, chat_identifier: str, message_rowid: int) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM command_failures
                WHERE chat_identifier = ?
                  AND message_rowid = ?
                """,
                (chat_identifier, message_rowid),
            )


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.astimezone()
    return value.astimezone(timezone.utc)


def format_utc_timestamp(value: datetime) -> str:
    return utc_datetime(value).isoformat(timespec="seconds")


def parse_stored_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summary_from_row(row: sqlite3.Row) -> DailySummary:
    return DailySummary(
        chat_identifier=row["chat_identifier"],
        summary_date=date.fromisoformat(row["summary_date"]),
        episode_title=row["episode_title"],
        factual_recap=row["factual_recap"],
        top_topics=json.loads(row["top_topics_json"]),
        activity_ranking=json.loads(row["activity_ranking_json"]),
        notable_moments=json.loads(row["notable_moments_json"]),
        roast_target=row["roast_target"],
        roast_text=row["roast_text"],
        recurring_bits=json.loads(row["recurring_bits_json"]),
        final_message=row["final_message"],
        raw_json=json.loads(row["raw_json"]),
    )


def build_running_lore(summaries: list[DailySummary], max_chars: int = 12_000) -> str:
    lines: list[str] = []
    for summary in reversed(summaries):
        bits = "; ".join(summary.recurring_bits[:5])
        topics = ", ".join(summary.top_topics[:5])
        lines.append(
            f"{summary.summary_date}: \"{summary.episode_title}\". "
            f"Topics: {topics}. Recap: {summary.factual_recap} "
            f"Roast: {summary.roast_target} - {summary.roast_text} "
            f"Callback candidates: {bits}"
        )
    lore = "\n".join(lines)
    if len(lore) <= max_chars:
        return lore
    return lore[-max_chars:]
