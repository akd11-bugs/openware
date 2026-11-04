import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
from shadow_report import summarize  # noqa: E402


def test_summary_counts_decisions_reasons_and_unreachable(tmp_path):
    rows = [{"would_do": "block", "reasons": ["A", "B"], "reached": True}, {"would_do": "allow", "reasons": [], "reached": True},
            {"would_do": "challenge", "reasons": [], "reached": False}]
    p = tmp_path / "log.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n\nnot json\n")
    out = summarize(p)
    assert out["total"] == 3 and out["would_do"] == {"block": 1, "allow": 1, "challenge": 1}
    assert out["reasons"] == {"A": 1, "B": 1} and out["unreachable"] == 1 and out["skipped_lines"] == 1
