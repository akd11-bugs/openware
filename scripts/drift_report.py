"""Drift report for the portable serving model: validation period (what thresholds were calibrated on) vs test period."""
import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentshield.monitoring.drift import alert_rate_check, feature_drift  # noqa: E402
from agentshield.txn import experiment as E  # noqa: E402
import agentshield.txn.fintech_features  # noqa: E402,F401
from agentshield.txn.split import time_split  # noqa: E402
from experiment import load_df  # noqa: E402
from train_serving import DEVICE_CODES  # noqa: E402


def main() -> None:
    d = ROOT / "models/portable_v1"
    card = json.loads((d / "model_card.json").read_text())
    booster = lgb.Booster(model_file=str(d / "model.txt"))
    df = load_df("ieee")
    X, _ = E.build_matrix(df, card["groups"], True)
    X["x_DeviceType"] = X["x_DeviceType"].astype(object).map(DEVICE_CODES).fillna(-1).astype("float64")
    X = X[card["feature_names"]]
    tr, te = time_split(df, 0.7, gap=E.GAP)
    _fit, val = time_split(tr, 0.7857, gap=E.GAP)
    print(f"reference = validation period ({len(val):,} rows)   current = test period ({len(te):,} rows)\n")
    t = feature_drift(X.loc[val.index], X.loc[te.index])
    print(t.round(4).to_string())
    thr = card["score_thresholds_by_val_fpr"]["0.05"]
    for name, idx in (("validation", val.index), ("test", te.index)):
        s = booster.predict(X.loc[idx])
        print(f"\nshare of ALL traffic above the 5%-FPR threshold, {name}: {(s > thr).mean():.1%}")
    s_te = booster.predict(X.loc[te.index])
    s_va = booster.predict(X.loc[val.index])
    print("alert-rate check:", alert_rate_check(float((s_te > thr).mean()), float((s_va > thr).mean())))


if __name__ == "__main__":
    main()
