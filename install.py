#!/usr/bin/env python3
"""Install / uninstall the memory-agent Claude Code integration: hooks, the /memory-refresh
command, the macOS launchd background extractor, and the transcript-manifest baseline.

Idempotent — re-run any time to repair (after moving the repo, re-run to fix paths).
`--uninstall` removes only memory-agent's pieces. Linux/Windows: hooks + commands install fine;
the launchd step is skipped (run `mem.py capture && mem.py refresh` on your own scheduler)."""
from __future__ import annotations
import datetime
import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOOKS = ROOT / "adapters" / "agent" / "claude_code" / "hooks"
COMMANDS_SRC = ROOT / "adapters" / "agent" / "claude_code" / "commands"
COMMANDS_DST = Path.home() / ".claude" / "commands"
SETTINGS = Path.home() / ".claude" / "settings.json"
PLIST_SRC = ROOT / "adapters" / "agent" / "claude_code" / "launchd" / "com.memory-agent.refresh.plist"
PLIST_DST = Path.home() / "Library" / "LaunchAgents" / "com.memory-agent.refresh.plist"
# stable marker: survives repo moves (old blocks are matched by suffix, not absolute path)
MARKER = "memory-agent/adapters/agent/claude_code/hooks"


def _cmd(name):
    return {"type": "command", "command": f"python3 {shlex.quote(str(HOOKS / name))}", "timeout": 10}


def _load_settings():
    if not SETTINGS.exists():
        return {}
    try:
        return json.loads(SETTINGS.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {SETTINGS} is not valid JSON ({e}). Fix it manually, then re-run — "
                 f"no changes were made.")


def install():
    s = _load_settings()
    if SETTINGS.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = SETTINGS.with_name(f"settings.json.bak-memagent-{stamp}")
        shutil.copy(SETTINGS, bak)
        print(f"backed up {SETTINGS} -> {bak}")
    hooks = s.setdefault("hooks", {})
    for event, matcher, script in [("SessionStart", "startup|resume|clear", "session_start.py"),
                                   ("SessionEnd", None, "session_end.py"),
                                   ("PreCompact", None, "pre_compact.py")]:
        block = {"hooks": [_cmd(script)]}
        if matcher:
            block["matcher"] = matcher
        kept = [b for b in hooks.get(event, []) if MARKER not in json.dumps(b)]
        hooks[event] = kept + [block]
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2) + "\n")
    print("hooks installed:", ", ".join(hooks))

    COMMANDS_DST.mkdir(parents=True, exist_ok=True)
    for cmd in COMMANDS_SRC.glob("*.md"):
        body = cmd.read_text().replace("/ABS/PATH/TO/memory-agent", str(ROOT))
        (COMMANDS_DST / cmd.name).write_text(body)
        print(f"command installed: /{cmd.stem}")

    if sys.platform == "darwin" and PLIST_SRC.exists():
        PLIST_DST.parent.mkdir(parents=True, exist_ok=True)
        PLIST_DST.write_text(PLIST_SRC.read_text().replace("/ABS/PATH/TO/memory-agent", str(ROOT)))
        subprocess.run(["launchctl", "unload", str(PLIST_DST)], capture_output=True)
        subprocess.run(["launchctl", "load", str(PLIST_DST)], capture_output=True)
        print("launchd agent installed: com.memory-agent.refresh (hourly background capture+extract)")
    elif sys.platform != "darwin":
        print("non-macOS: launchd step skipped — schedule "
              "'mem.py capture && mem.py refresh --min-growth 75000' yourself (cron/systemd timer)")

    print("baselining transcript manifest (only NEW sessions will be captured)...")
    subprocess.run([sys.executable, str(ROOT / "mem.py"), "capture", "--baseline"])
    print("done — memory flows on your next session. Keep it consolidating with: "
          "/loop 4h /memory-refresh   (and bootstrap your history with the backfill, see README)")


def uninstall():
    if SETTINGS.exists():
        s = _load_settings()
        hooks = s.get("hooks", {})
        for event in list(hooks):
            hooks[event] = [b for b in hooks[event] if MARKER not in json.dumps(b)]
            if not hooks[event]:
                del hooks[event]
        if not hooks:
            s.pop("hooks", None)
        SETTINGS.write_text(json.dumps(s, indent=2) + "\n")
    for cmd in COMMANDS_SRC.glob("*.md"):
        installed = COMMANDS_DST / cmd.name
        if installed.exists():
            installed.unlink()
    if sys.platform == "darwin" and PLIST_DST.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_DST)], capture_output=True)
        PLIST_DST.unlink()
    print("memory-agent hooks + commands + launchd agent removed.")


if __name__ == "__main__":
    uninstall() if "--uninstall" in sys.argv else install()
