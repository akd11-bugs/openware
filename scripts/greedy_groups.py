"""Score each candidate feature group alone on top of a base set (validation only), then combine the winners.

  scripts/greedy_groups.py --base card_hist,roll_counts,amt_cents --cands amt_stats,newness --extra experiments/a1/groups.py --params '{...}'
"""
import argparse
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentshield.txn import experiment as E  # noqa: E402
import agentshield.txn.fintech_features  # noqa: E402,F401
from experiment import load_df  # noqa: E402

MIN_GAIN = 0.0007


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--cands", required=True)
    ap.add_argument("--extra", action="append", default=[])
    ap.add_argument("--params", default="{}")
    ap.add_argument("--n-jobs", type=int, default=8)
    ap.add_argument("--portable", action="store_true")
    ap.add_argument("--agent", default="greedy")
    a = ap.parse_args()
    for path in a.extra:
        spec = importlib.util.spec_from_file_location("extra_" + Path(path).stem, path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    df, base, params = load_df(), a.base.split(","), json.loads(a.params)
    log = open(ROOT / f"docs/results/experiments_{a.agent}.jsonl", "a")

    def run(groups):
        r = E.evaluate(df, groups, params, "lgbm", a.n_jobs, portable=a.portable)
        log.write(json.dumps({**r, "agent": a.agent}) + "\n")
        log.flush()
        return r

    ref = run(base)
    print(f"base: auc {ref['val_auc']} ll {ref['val_logloss']} r1 {ref['val_recall@1%']}", flush=True)
    winners = []
    for g in a.cands.split(","):
        r = run(base + [g])
        gain = r["val_auc"] - ref["val_auc"]
        print(f"+{g}: auc {r['val_auc']} ({gain:+.5f}) ll {r['val_logloss']} r1 {r['val_recall@1%']}", flush=True)
        if gain >= MIN_GAIN:
            winners.append(g)
    if len(winners) > 1:
        r = run(base + winners)
        print(f"+{'+'.join(winners)}: auc {r['val_auc']} ({r['val_auc'] - ref['val_auc']:+.5f}) ll {r['val_logloss']} r1 {r['val_recall@1%']}", flush=True)
    print("WINNERS", winners, flush=True)


if __name__ == "__main__":
    main()
