#!/usr/bin/env python3
"""Install / uninstall the memory-agent Claude Code hooks and baseline the transcript manifest.

Idempotent — re-run any time to repair. `--uninstall` removes only memory-agent's hook blocks.
This is the automated form of the manual steps in README.md."""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HOOKS = ROOT / "adapters" / "agent" / "claude_code" / "hooks"
COMMANDS_SRC = ROOT / "adapters" / "agent" / "claude_code" / "commands"
COMMANDS_DST = Path.home() / ".claude" / "commands"
SETTINGS = Path.home() / ".claude" / "settings.json"
MARKER = str(HOOKS)  # used to find/replace our own blocks on re-run


def _cmd(name):
    return {"type": "command", "command": f"python3 {HOOKS}/{name}", "timeout": 10}


def install():
    s = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    if SETTINGS.exists():
        bak = SETTINGS.with_name("settings.json.bak-memagent")
        shutil.copy(SETTINGS, bak)
        print(f"backed up {SETTINGS} -> {bak}")
    hooks = s.setdefault("hooks", {})
    for event, matcher, script in [("SessionStart", "startup|resume", "session_start.py"),
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
    plist_src = ROOT / "adapters" / "agent" / "claude_code" / "launchd" / "com.memory-agent.refresh.plist"
    if plist_src.exists():
        agents = Path.home() / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        plist_dst = agents / plist_src.name
        plist_dst.write_text(plist_src.read_text().replace("/ABS/PATH/TO/memory-agent", str(ROOT)))
        subprocess.run(["launchctl", "unload", str(plist_dst)], capture_output=True)
        subprocess.run(["launchctl", "load", str(plist_dst)], capture_output=True)
        print("launchd agent installed: com.memory-agent.refresh (hourly background capture+extract)")
    print("baselining transcript manifest (only NEW sessions will be captured)...")
    subprocess.run([sys.executable, str(ROOT / "mem.py"), "capture", "--baseline"])
    print("done — memory flows on your next session. Keep it fresh with: /loop 45m /memory-refresh")


def uninstall():
    if not SETTINGS.exists():
        return
    s = json.loads(SETTINGS.read_text())
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
    plist = Path.home() / "Library" / "LaunchAgents" / "com.memory-agent.refresh.plist"
    if plist.exists():
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True)
        plist.unlink()
    print("memory-agent hooks + commands + launchd agent removed.")


if __name__ == "__main__":
    uninstall() if "--uninstall" in sys.argv else install()
