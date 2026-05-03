from __future__ import annotations

from pathlib import Path
import sys


def launchd_plist(label: str, config_path: Path, log_dir: Path) -> str:
    python_path = sys.executable
    out_log = log_dir / "debriefgc.out.log"
    err_log = log_dir / "debriefgc.err.log"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_path}</string>
    <string>-m</string>
    <string>debriefgc.cli</string>
    <string>run</string>
    <string>--config</string>
    <string>{config_path}</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>23</integer>
    <key>Minute</key>
    <integer>59</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>{out_log}</string>
  <key>StandardErrorPath</key>
  <string>{err_log}</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
"""
