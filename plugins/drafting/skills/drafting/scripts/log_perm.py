#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Permission-prompt monitor (hook target).

Wire it in ~/.claude/settings.json under PermissionRequest and PermissionDenied
(see references/runtime-optimization.md). Every time Claude Code is about to show
a permission prompt (= one user accept), the event is appended to
~/.claude/perm-requests.jsonl. At the end of each work iteration, the skill reads
the log, clusters the entries, and proposes one allowlist rule per cluster.

The log is MACHINE-WIDE, shared by every concurrent Claude Code session — the hook
fires per-process, not per-conversation. Each entry carries `session` (the last 8
chars of the hook's session_id) so a monitoring pass can filter to one session
instead of reporting other people's/other projects' concurrent work as if it were
this session's. Discovered for real: "monitor this session's accepts" produced 97
entries from four other coordination projects' sessions running in parallel, none
from the session that asked — session filtering is the fix, not a hypothetical.

Prints nothing to stdout (a PermissionRequest hook that prints JSON could alter
the permission decision — this one only observes).
"""
import sys, json, os, datetime

def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {"parse_error": True}
    event = sys.argv[1] if len(sys.argv) > 1 else "PermissionRequest"
    tool_input = data.get("tool_input")
    # keep log lines compact: Bash/PowerShell -> just the command string
    if isinstance(tool_input, dict) and "command" in tool_input:
        tool_input = tool_input["command"]
    session_id = data.get("session_id") or ""
    entry = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "event": event,
        "session": session_id[-8:] if session_id else None,
        "tool": data.get("tool_name"),
        "input": tool_input,
    }
    log = os.path.join(os.path.expanduser("~"), ".claude", "perm-requests.jsonl")
    try:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never block the prompt over a logging failure

if __name__ == "__main__":
    main()
