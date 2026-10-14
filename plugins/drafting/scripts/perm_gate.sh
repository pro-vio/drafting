#!/usr/bin/env bash
# One-time per-project permission gate for the drafting skills.
# Emits a SessionStart additionalContext instruction ONLY until the project records
# a decision in .claude/.perm-decision (then it stays silent).
dir="${CLAUDE_PROJECT_DIR:-$PWD}"
marker="$dir/.claude/.perm-decision"
[ -f "$marker" ] && exit 0
printf '%s' '{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "[Permission setup] This project has no recorded skill-permission decision yet. Before doing other work, ask the user via AskUserQuestion whether THIS project contains sensitive data, and explain the consequence of each choice: if NOT sensitive, you will add frictionless allow rules to .claude/settings.local.json (Bash python:*, python3:*, pandoc:*, pip install lxml, and curl to http://127.0.0.1:23119 / http://localhost:23119) so the scriere and zotero-citations skills run without per-command prompts; if sensitive, you will leave permissions at default so every command still prompts, preserving control. After the user answers, apply the choice and create the marker file .claude/.perm-decision (write the decision text into it) so this is not asked again in this project."}}'
exit 0
