from __future__ import annotations

from datetime import date, timedelta
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
