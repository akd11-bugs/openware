#!/bin/sh
# PostToolUse: after editing code or tests, run the fast suite. Exit 2 feeds failures back to Claude.
path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
case "$path" in
  */agentshield/*.py|*/tests/*.py|*/scripts/*.py)
    cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
    out=$(.venv/bin/python -m pytest -q -x 2>&1) || { echo "$out" | tail -20 >&2; exit 2; }
    ;;
esac
exit 0
