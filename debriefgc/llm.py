from __future__ import annotations

from datetime import date
import json
import ssl
from typing import Any
from urllib import error, request

from .config import Config
from .memory import build_running_lore
from .messages import format_messages_for_prompt
from .models import ChatMessage, DailySummary


SYSTEM_PROMPT = """You write daily group chat debriefs.

Style:
- Funny, specific, and chaotic like a friend-group recap.
- Generate a sitcom episode-title style name for the day.
- Include callbacks from memory only when the memory supports them.
- Roasts may be sharp, but never target protected traits, medical issues,
  serious trauma, financial hardship, grief, or vulnerabilities that feel
  genuinely harmful.
- Do not invent events, quotes, or prior lore.

Return strict JSON only. No Markdown fences.
"""


def build_user_prompt(
    config: Config,
    target_date: date,
    messages: list[ChatMessage],
    prior_summaries: list[DailySummary],
) -> str:
    lore = build_running_lore(prior_summaries)
    today_messages = format_messages_for_prompt(messages, config.max_messages_chars)
    if messages:
        message_window = (
            f"{messages[0].sent_at.isoformat(timespec='minutes')} to "
            f"{messages[-1].sent_at.isoformat(timespec='minutes')}"
        )
    else:
        message_window = target_date.isoformat()
    schema = {
        "episode_title": "Sitcom-style title for the day",
        "factual_recap": "Short factual summary of what happened",
        "top_topics": ["topic 1", "topic 2"],
        "activity_ranking": [
            {"member": "name", "message_count": 12, "rank_note": "short note"}
        ],
        "notable_moments": ["specific moment"],
        "roast_target": "member name",
        "roast_text": "one roast using today plus prior summary memory",
        "recurring_bits": ["callback or pattern worth remembering"],
        "final_message": "The exact message to send into the group chat",
    }
    return f"""Group chat: {config.chat_display_name}
Date: {target_date.isoformat()}
Message window: {message_window}

Prior 90-day summary memory:
{lore or "No prior memory yet."}

Messages in this run:
{today_messages or "No messages found in this window."}

Required JSON shape:
{json.dumps(schema, indent=2)}

Final message requirements:
- Make it readable as one iMessage post.
- Include title, recap, most active member ranking, recurring callback if relevant, and the roast.
- Keep it under about 1800 characters unless today truly needs more.
"""


def generate_summary(
    config: Config,
    target_date: date,
    messages: list[ChatMessage],
    prior_summaries: list[DailySummary],
) -> DailySummary:
    if not messages:
        payload = fallback_empty_day(config, target_date)
    elif config.openai_api_key:
        payload = call_openai_json(
            api_key=config.openai_api_key,
            model=config.model,
            user_prompt=build_user_prompt(config, target_date, messages, prior_summaries),
        )
    else:
        payload = fallback_local_summary(config, target_date, messages, prior_summaries)

    return DailySummary(
        chat_identifier=config.chat_identifier,
        summary_date=target_date,
        episode_title=str(payload.get("episode_title", "The One With the Group Chat")),
        factual_recap=str(payload.get("factual_recap", "")),
        top_topics=list(payload.get("top_topics", [])),
        activity_ranking=list(payload.get("activity_ranking", [])),
        notable_moments=list(payload.get("notable_moments", [])),
        roast_target=str(payload.get("roast_target", "")),
        roast_text=str(payload.get("roast_text", "")),
        recurring_bits=list(payload.get("recurring_bits", [])),
        final_message=str(payload.get("final_message", "")),
        raw_json=payload,
    )


def call_openai_json(api_key: str, model: str, user_prompt: str) -> dict[str, Any]:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.9,
    }
    req = request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=90, context=openai_ssl_context()) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API error {exc.code}: {detail}") from exc
    except error.URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise RuntimeError(
                "OpenAI API connection failed because this Python install cannot "
                "verify HTTPS certificates. On python.org macOS Python, run "
                "`/Applications/Python 3.12/Install Certificates.command`, then try again."
            ) from exc
        raise RuntimeError(f"OpenAI API connection failed: {exc.reason}") from exc

    content = data["choices"][0]["message"]["content"]
    return json.loads(content)


def openai_ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def fallback_empty_day(config: Config, target_date: date) -> dict[str, Any]:
    title = "The One Where Everyone Forgot the Plot"
    final_message = (
        f"DebriefGC: {title}\n"
        f"{target_date.isoformat()} was quiet. No messages found for this recap, "
        "so the group chat has avoided accountability for one more night."
    )
    return {
        "episode_title": title,
        "factual_recap": "No messages were found for this recap.",
        "top_topics": [],
        "activity_ranking": [],
        "notable_moments": [],
        "roast_target": "the group chat",
        "roast_text": "Somehow even silence had scheduling issues.",
        "recurring_bits": ["quiet day"],
        "final_message": final_message,
    }


def fallback_local_summary(
    config: Config,
    target_date: date,
    messages: list[ChatMessage],
    prior_summaries: list[DailySummary],
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for message in messages:
        counts[message.sender_name] = counts.get(message.sender_name, 0) + 1
    ranking = [
        {"member": name, "message_count": count, "rank_note": "message volume"}
        for name, count in sorted(counts.items(), key=lambda item: item[1], reverse=True)
    ]
    roast_target = ranking[0]["member"] if ranking else "the group chat"
    callback = prior_summaries[0].recurring_bits[0] if prior_summaries and prior_summaries[0].recurring_bits else ""
    title = "The One With the Daily Debrief"
    final_message = (
        f"DebriefGC: {title}\n"
        f"Recap: {len(messages)} messages landed in this window. "
        f"Most active: {', '.join(f'{r['member']} ({r['message_count']})' for r in ranking[:3])}.\n"
        f"Callback: {callback or 'no lore unlocked yet'}.\n"
        f"Roast: {roast_target}, the stats say you carried the chat, "
        "but spiritually this still feels like a cry for help."
    )
    return {
        "episode_title": title,
        "factual_recap": f"{len(messages)} messages were exchanged.",
        "top_topics": ["local fallback summary"],
        "activity_ranking": ranking,
        "notable_moments": [],
        "roast_target": roast_target,
        "roast_text": (
            f"{roast_target}, the stats say you carried the chat, "
            "but spiritually this still feels like a cry for help."
        ),
        "recurring_bits": [callback] if callback else ["first fallback recap"],
        "final_message": final_message,
    }
