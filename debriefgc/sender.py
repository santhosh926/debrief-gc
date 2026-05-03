from __future__ import annotations

import subprocess

from .config import Config
from .messages import resolve_chat_send_identifier


def send_summary(config: Config, text: str) -> None:
    if config.dry_run or config.send_mode == "dry-run":
        print(text)
        return
    if config.send_mode == "messages":
        send_with_messages_applescript(config, text)
        return
    raise ValueError(f"Unsupported send mode: {config.send_mode}")


def send_with_messages_applescript(config: Config, text: str) -> None:
    escaped_text = text.replace("\\", "\\\\").replace('"', '\\"')
    chat_identifier = resolve_chat_send_identifier(config)
    escaped_chat = chat_identifier.replace("\\", "\\\\").replace('"', '\\"')
    escaped_service = config.sender_service.replace("\\", "\\\\").replace('"', '\\"')
    script = f'''
    tell application "Messages"
      set targetService to 1st service whose service type = {escaped_service}
      set targetChat to chat id "{escaped_chat}" of targetService
      send "{escaped_text}" to targetChat
    end tell
    '''
    subprocess.run(["osascript", "-e", script], check=True)
