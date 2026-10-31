#!/bin/sh
# PreToolUse: block edits to datasets, secrets and credentials. Exit 2 = block and tell Claude why.
path=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("file_path",""))')
case "$path" in
  */data/*|*/.env|*/.env.*|*/.kaggle/*)
    echo "Blocked: $path is protected (datasets and secrets are never edited by Claude)." >&2
    exit 2
    ;;
esac
exit 0
