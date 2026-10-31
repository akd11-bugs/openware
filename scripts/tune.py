"""Coordinate search over LightGBM params on validation only. Same keep/stop rules as the agent brief.

  scripts/tune.py --groups card_hist,roll_counts,amt_cents --start '{...}' --max-exps 10
Appends to docs/results/experiments_tune.jsonl and prints the best params at the end.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentshield.txn import experiment as E  # noqa: E402
import agentshield.txn.fintech_features  # noqa: E402,F401
from experiment import load_df  # noqa: E402

SPACE = [
    ("min_child_samples", [120, 250]),
    ("num_leaves", [64, 256]),
    ("lambda_l2", [5.0, 20.0]),
    ("feature_fraction", [0.2, 0.4]),
    ("learning_rate", [0.02]),
    ("min_gain_to_split", [0.01]),
]
MIN_GAIN, STOP_GAIN = 0.0007, 0.001


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", required=True)
    ap.add_argument("--start", default="{}")
    ap.add_argument("--max-exps", type=int, default=10)
    ap.add_argument("--n-jobs", type=int, default=3)
    ap.add_argument("--portable", action="store_true")
    a = ap.parse_args()
    df, groups = load_df(), a.groups.split(",")
    best_p = json.loads(a.start)
    best = E.evaluate(df, groups, best_p, "lgbm", a.n_jobs, portable=a.portable)
    n, bad_rounds = 1, 0
    log = open(ROOT / "docs/results/experiments_tune.jsonl", "a")
    log.write(json.dumps({**best, "agent": "tune"}) + "\n")
    print("start", best_p, best["val_auc"], flush=True)
    for name, values in SPACE:
        round_gain = 0.0
        for v in values:
            if n >= a.max_exps:
                break
            cand_p = {**best_p, name: v}
            r = E.evaluate(df, groups, cand_p, "lgbm", a.n_jobs, portable=a.portable)
            n += 1
            gain = r["val_auc"] - best["val_auc"]
            log.write(json.dumps({**r, "agent": "tune"}) + "\n")
            log.flush()
            print(f"{name}={v}: auc {r['val_auc']} ({gain:+.5f}) ll {r['val_logloss']}", flush=True)
            if gain >= MIN_GAIN:
                best, best_p, round_gain = r, cand_p, max(round_gain, gain)
        bad_rounds = 0 if round_gain >= STOP_GAIN else bad_rounds + 1
        if bad_rounds >= 2 or n >= a.max_exps:
            break
    print("BEST", json.dumps(best_p), best["val_auc"], best["val_logloss"], best["val_recall@1%"], flush=True)


if __name__ == "__main__":
    main()
