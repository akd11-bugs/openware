"""Train, evaluate on the held-out test slice ONCE, and save a versioned model + model card.

  scripts/train_serving.py --kind portable   # serving model: only fields/state a real-time store can provide
  scripts/train_serving.py --kind full       # benchmark artifact: tuned model on all IEEE-CIS fields (not servable)

Fit on the early slice, early-stop on validation, calibrate score thresholds on VALIDATION, then score the test
slice a single time. A model card that already holds test metrics blocks a re-run unless --force is given.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentshield.txn import experiment as E  # noqa: E402
import agentshield.txn.fintech_features  # noqa: E402,F401
from agentshield.txn.metrics import auc, precision_at_k, recall_at_fpr  # noqa: E402
from agentshield.txn.split import time_split  # noqa: E402
from experiment import load_df  # noqa: E402

DEVICE_CODES = {"mobile": 0, "desktop": 1}  # unknown / missing -> -1
KINDS = {
    "portable": dict(groups=["velocity_amt", "amt_ratio_30d", "sender_hist", "roll_counts", "amt_cents"], portable=True,
                     params=dict(learning_rate=0.05, num_leaves=64, min_child_samples=60, feature_fraction=0.6)),
    "portable2": dict(groups=["velocity_amt", "amt_ratio_30d", "sender_hist", "roll_counts", "amt_cents", "device_share", "instrument"],
                      portable=True, params=dict(learning_rate=0.05, num_leaves=64, min_child_samples=60, feature_fraction=0.6)),
    "full": dict(groups=["card_hist", "roll_counts", "amt_cents"], portable=False,
                 params=dict(learning_rate=0.03, feature_fraction=0.3, min_child_samples=120, num_leaves=192,
                             min_sum_hessian_in_leaf=1, bagging_fraction=0.8, lambda_l1=1, max_bin=511)),
}


def ci(y, s, n=200):
    rng = np.random.default_rng(0)
    out = {"auc": [], "recall@1%": [], "recall@0.1%": []}
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        out["auc"].append(auc(y[i], s[i]))
        out["recall@1%"].append(recall_at_fpr(y[i], s[i], 0.01))
        out["recall@0.1%"].append(recall_at_fpr(y[i], s[i], 0.001))
    return {k: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)] for k, v in out.items()}


def metrics(y, s):
    return {"auc": round(auc(y, s), 4), "recall@1%": round(recall_at_fpr(y, s, 0.01), 4),
            "recall@0.1%": round(recall_at_fpr(y, s, 0.001), 4), "precision@top1%": round(precision_at_k(y, s, max(1, len(y) // 100)), 4)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=list(KINDS), required=True)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--n-jobs", type=int, default=8)
    a = ap.parse_args()
    cfg = KINDS[a.kind]
    out_dir = ROOT / "models" / f"{a.kind}_{a.version}"
    card_path = out_dir / "model_card.json"
    if card_path.exists() and "test" in json.loads(card_path.read_text()) and not a.force:
        sys.exit(f"{card_path} already holds held-out test metrics; the test slice is used once. Use --force to override.")
    df = load_df("ieee")
    X, prod = E.build_matrix(df, cfg["groups"], cfg["portable"])
    if a.kind.startswith("portable"):
        X["x_DeviceType"] = X["x_DeviceType"].astype(object).map(DEVICE_CODES).fillna(-1).astype("float64")
    y = df["label"].to_numpy()
    tr, te = time_split(df, 0.7, gap=E.GAP)
    fit, val = time_split(tr, 0.7857, gap=E.GAP)
    p = dict(objective="binary", bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, n_jobs=a.n_jobs, verbose=-1, seed=7)
    p.update(cfg["params"])
    dfit = lgb.Dataset(X.loc[fit.index], y[fit.index])
    dval = lgb.Dataset(X.loc[val.index], y[val.index], reference=dfit)
    booster = lgb.train(p, dfit, num_boost_round=3000, valid_sets=[dval], callbacks=[lgb.early_stopping(100, verbose=False)])
    it = booster.best_iteration
    sv, yv = booster.predict(X.loc[val.index], num_iteration=it), y[val.index]
    thresholds = {f"{f:g}": float(np.quantile(sv[yv == 0], 1 - f)) for f in (0.001, 0.005, 0.01, 0.05)}
    st, yt = booster.predict(X.loc[te.index], num_iteration=it), y[te.index]  # the single test-slice pass
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "model.txt"), num_iteration=it)
    card = {
        "name": f"{a.kind}_{a.version}", "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "algorithm": "LightGBM", "params": p, "best_iteration": it, "groups": cfg["groups"], "portable_features_only": cfg["portable"],
        "production_valid_features": prod, "feature_names": list(X.columns), "device_codes": DEVICE_CODES if a.kind.startswith("portable") else None,
        "training": {"dataset": "IEEE-CIS (US card e-commerce, public)", "rows": len(df), "fit_rows": len(fit), "val_rows": len(val),
                     "test_rows": len(te), "gap_days": E.GAP // 86400, "fraud_rate": round(float(y.mean()), 4)},
        "score_thresholds_by_val_fpr": thresholds,
        "validation": {**metrics(yv, sv), "rows": int(len(yv))},
        "test": {**metrics(yt, st), "rows": int(len(yt)), "ci95": ci(yt, st)},
        "notes": (["Later model version: the held-out test slice was also scored for an earlier version. Features and parameters were "
                   "selected on validation only."] if a.kind == "portable2" else []) + ["Held-out test slice scored once for THIS version. Not comparable to the Kaggle private leaderboard.",
                  "Trained on US card data; no Indian traffic was used. Recalibrate per tenant in shadow mode before use."],
    }
    card_path.write_text(json.dumps(card, indent=2))
    print(json.dumps({"saved": str(out_dir), "iters": it, "features": X.shape[1], "validation": card["validation"], "test": card["test"]}, indent=1))


if __name__ == "__main__":
    main()
