from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_CONFIG_PATH, load_config, sample_config
from .contacts import ContactsLookupError, load_contact_names
from .llm import generate_summary
from .memory import MemoryStore
from .messages import fetch_daily_messages, fetch_messages_between, list_chats
from .messages import connect_messages_db
from .scheduler import launchd_plist
from .sender import send_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="debriefgc")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init-config")
    subparsers.add_parser("doctor")

    list_parser = subparsers.add_parser("list-chats")
    list_parser.add_argument("--limit", type=int, default=30)
    list_parser.add_argument("--search", help="Filter chats by name, identifier, or participant handle")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    run_parser.add_argument(
        "--hours",
        type=int,
        help="Summarize the rolling window ending now, e.g. --hours 24",
    )
    run_parser.add_argument("--send", action="store_true", help="Override dry_run for this run")
    run_parser.add_argument("--dry-run", action="store_true", help="Print only")

    launchd_parser = subparsers.add_parser("print-launchd-plist")
    launchd_parser.add_argument("--label", default="com.local.debriefgc")
    launchd_parser.add_argument(
        "--log-dir", type=Path, default=Path("~/Library/Logs/debriefgc").expanduser()
    )

    args = parser.parse_args(argv)

    if args.command == "init-config":
        init_config(args.config)
        return 0

    config = load_config(args.config)

    if args.command == "doctor":
        doctor(config)
        return 0

    if args.command == "list-chats":
        for chat in list_chats(config, limit=args.limit, search=args.search):
            participants = ", ".join(chat.participants[:6])
            if len(chat.participants) > 6:
                participants += ", ..."
            print(
                f"{chat.rowid} | {chat.display_name or '(no display name)'} | "
                f"identifier={chat.chat_identifier or chat.guid} | "
                f"last={chat.last_message_at} | participants={participants}"
            )
        return 0

    if args.command == "print-launchd-plist":
        args.log_dir.mkdir(parents=True, exist_ok=True)
        print(launchd_plist(args.label, args.config.expanduser(), args.log_dir))
        return 0

    if args.command == "run":
        if args.date and args.hours:
            raise SystemExit("Use either --date or --hours, not both.")
        if args.hours is not None and args.hours < 1:
            raise SystemExit("--hours must be at least 1.")
        if args.send and args.dry_run:
            raise SystemExit("Use either --send or --dry-run, not both.")
        if args.send:
            object.__setattr__(config, "dry_run", False)
            object.__setattr__(config, "send_mode", "messages")
        if args.dry_run:
            object.__setattr__(config, "dry_run", True)
            object.__setattr__(config, "send_mode", "dry-run")

        target_date = date.fromisoformat(args.date) if args.date else date.today()
        local_tz = ZoneInfo(config.timezone) if config.timezone else None
        if args.hours:
            now = datetime.now(local_tz).astimezone()
            target_date = now.date()
            messages = fetch_messages_between(
                config,
                now - timedelta(hours=args.hours),
                now,
            )
        else:
            messages = fetch_daily_messages(config, target_date, local_tz)
        store = MemoryStore(config.memory_db_path)
        prior = store.recent_summaries(
            config.chat_identifier, target_date, config.max_memory_days
        )
        try:
            summary = generate_summary(config, target_date, messages, prior)
        except RuntimeError as exc:
            raise SystemExit(f"FAIL: {exc}") from exc
        store.upsert_summary(summary)
        pruned = store.prune(config.chat_identifier, config.retention_days, target_date)
        send_summary(config, summary.final_message)
        print(
            f"\nSaved summary for {target_date.isoformat()} "
            f"with {len(messages)} messages. Pruned {pruned} old summaries."
        )
        return 0

    raise SystemExit(f"Unknown command: {args.command}")


def init_config(path: Path) -> None:
    path = path.expanduser()
    if path.exists():
        print(f"Config already exists: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(sample_config(), encoding="utf-8")
    print(f"Wrote sample config to {path}")


def doctor(config) -> None:
    print("DebriefGC doctor")
    print(f"Config chat identifier: {config.chat_identifier}")
    print(f"Messages DB: {config.messages_db_path}")
    print(f"Memory DB: {config.memory_db_path}")
    print(f"Send mode: {config.send_mode}")
    print(f"Dry run: {config.dry_run}")
    print(f"Contacts lookup: {'enabled' if config.contacts_enabled else 'disabled'}")

    if not config.messages_db_path.exists():
        print("\nFAIL: Messages DB does not exist at the configured path.")
        return

    try:
        with connect_messages_db(config.messages_db_path) as conn:
            chat_count = conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0]
        print(f"\nOK: Messages DB opened. Found {chat_count} chats.")
    except Exception as exc:
        print(f"\nFAIL: {exc}")

    if config.contacts_enabled:
        try:
            load_contact_names(config)
            print("OK: Contacts lookup succeeded.")
        except ContactsLookupError as exc:
            print(f"FAIL: Contacts lookup failed. {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
