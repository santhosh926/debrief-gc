from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date
from typing import Any


@dataclass(frozen=True)
class ChatMessage:
    sent_at: datetime
    sender_handle: str
    sender_name: str
    text: str
    is_from_me: bool


@dataclass(frozen=True)
class ChatInfo:
    rowid: int
    guid: str
    display_name: str
    chat_identifier: str
    last_message_at: datetime | None
    participants: list[str]


@dataclass(frozen=True)
class DailySummary:
    chat_identifier: str
    summary_date: date
    episode_title: str
    factual_recap: str
    top_topics: list[str]
    activity_ranking: list[dict[str, Any]]
    notable_moments: list[str]
    roast_target: str
    roast_text: str
    recurring_bits: list[str]
    final_message: str
    raw_json: dict[str, Any]
