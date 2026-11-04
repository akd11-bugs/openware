"""Summarise a shadow-mode log: python scripts/shadow_report.py agentshield_shadow.jsonl"""
import json
import sys
from collections import Counter


def summarize(path) -> dict:
    rows, bad = [], 0
    for line in open(path):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                bad += 1
    return {"total": len(rows), "would_do": dict(Counter(r["would_do"] for r in rows)),
            "reasons": dict(Counter(c for r in rows for c in r["reasons"])),
            "unreachable": sum(not r["reached"] for r in rows), "skipped_lines": bad}


if __name__ == "__main__":
    print(json.dumps(summarize(sys.argv[1]), indent=2))
