# DebriefGC

DebriefGC is a local Mac MVP for a daily iMessage group chat recap. It reads one
configured Messages group chat, generates an AI summary at the end of the day,
stores summary-only memory for callbacks, and can post the final debrief back
into the chat through macOS Messages automation.

Apple does not provide a normal personal iMessage bot API. This project is built
as a local Mac automation because that is the practical route for a personal
group chat.

## What It Does

- Reads today’s messages from `~/Library/Messages/chat.db` in read-only mode.
- Stores one structured summary per day in local SQLite memory.
- Keeps a rolling 90-day memory window by default.
- Sends the LLM today’s messages plus prior summaries, not permanent raw logs.
- Generates:
  - sitcom-style episode title
  - short recap
  - top topics
  - most active member ranking
  - notable moments
  - memory-based recurring bits
  - one friend-group roast with guardrails
- Runs manually or on a daily `launchd` schedule.
- Can poll configured chats for `@debrief` commands and reply in-chat.

## Setup

Use Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
debriefgc init-config
```

Edit `~/.config/debriefgc/config.toml`.

To use OpenAI, set:

```bash
export OPENAI_API_KEY="..."
```

If no API key is set, DebriefGC uses a tiny local fallback summary so you can
test extraction, memory, and scheduling without calling an LLM.

## Contact Names

DebriefGC resolves participant names from local macOS Contacts data by default.
It tries the Contacts framework first, then falls back to the local AddressBook
store if macOS scripting cannot enumerate contacts. The first lookup may trigger
a macOS Contacts permission prompt for your terminal app.

Manual mappings still override Contacts and are useful for yourself or custom
nicknames:

```toml
[group.participant_names]
"me" = "Santhosh"
"+15555550123" = "Avery"

[contacts]
enabled = true
```

If Contacts access is disabled or a handle cannot be matched, DebriefGC keeps
the original phone number or email address.

## Find Your Group Chat

macOS may require Full Disk Access for your terminal app before it can read
Messages history.

First check permissions:

```bash
debriefgc doctor
```

If it cannot open `~/Library/Messages/chat.db`, open **System Settings >
Privacy & Security > Full Disk Access**, enable access for the terminal app you
are using, then quit and reopen that app.

```bash
debriefgc list-chats
```

Copy the target chat identifier into `[group].chat_identifier`.

To search for one group chat by name:

```bash
debriefgc list-chats --search Spocks
```

If the group display name is exactly `Spocks`, you can also set:

```toml
[group]
chat_identifier = "Spocks"
display_name = "Spocks"
```

## Dry Run

```bash
debriefgc run --dry-run
```

For a specific date:

```bash
debriefgc run --date 2026-05-03 --dry-run
```

For the last 24 hours:

```bash
debriefgc run --hours 24 --dry-run
```

This prints the final group-chat message and stores the structured daily memory.
Raw messages are not stored.

## Sending To Messages

Automatic sending uses AppleScript against the macOS Messages app:

```toml
[sending]
mode = "messages"
service = "iMessage"
```

Then run:

```bash
debriefgc run --send
```

The first run may trigger macOS Automation permissions. If Messages automation
cannot address the group chat by the configured identifier, keep `mode =
"dry-run"` and use the printed text until the chat identifier is corrected.

## Chat Commands

DebriefGC can also check configured chats for command messages and reply with an
ad hoc summary. This is separate from the existing `debriefgc run` daily recap.

```toml
[commands]
enabled = true
mention = "@debrief"
poll_lookback_minutes = 360
retry_cooldown_minutes = 5
invocation_cooldown_minutes = 10
default_summary_hours = 6

# Optional. If omitted, DebriefGC watches [group].chat_identifier.
chat_identifiers = ["Spocks"]
```

In the chat, send:

```text
@debrief summarize the past 6 hours of this chat
```

Then poll once:

```bash
debriefgc poll-commands --dry-run
```

Use `--send` to post the reply through Messages automation:

```bash
debriefgc poll-commands --send
```

Processed command message IDs are saved in the local memory database so a
scheduled poll does not answer the same command twice. Failed command attempts
are also tracked and retried after the configured retry cooldown. Successful
summary commands are limited by `invocation_cooldown_minutes`; early attempts
receive a try-again-later reply instead of generating another summary.

## Daily 11:59pm Schedule

Generate a launchd plist:

```bash
debriefgc print-launchd-plist > ~/Library/LaunchAgents/com.local.debriefgc.plist
launchctl load ~/Library/LaunchAgents/com.local.debriefgc.plist
```

Logs go to `~/Library/Logs/debriefgc` by default.

## Command Polling Schedule

For chat commands, schedule `debriefgc poll-commands --send` with launchd at a
short interval, such as every minute. Keep the daily `debriefgc run` schedule if
you also want the end-of-day recap.

## Privacy Notes

DebriefGC stores summary memory only:

- episode title
- factual recap
- topics
- ranking
- notable moments
- roast
- recurring bits
- final sent message

It does not store raw messages unless you add that behavior yourself. The default
retention window is 90 days.

## Tests

```bash
python -m unittest discover -s tests
```
