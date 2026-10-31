"""LightGBM on IEEE-CIS with three feature sets, evaluated on a later time period.

Split by time: fit on first ~55%, early-stop on next ~15%, report on last 30% (touched once per variant).
"""
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentshield.txn.features import add_history_features, add_uid  # noqa: E402
from agentshield.txn.loaders import ieee_cis_to_standard  # noqa: E402
from agentshield.txn.metrics import auc, precision_at_k, recall_at_fpr  # noqa: E402
from agentshield.txn.split import time_split  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
IEEE = ROOT / "data/.kagglehub/competitions/ieee-fraud-detection"
OUT = ROOT / "docs/results"
GAP = 7 * 86400  # label delay: chargebacks arrive late, so no training data right before the test period
# Headline (production-realistic) variant is declared up front: B_history.
# A_raw is the floor; C_uid is winner-style (needs D1/addr1) and is reported separately.
PARAMS = dict(
    objective="binary", learning_rate=0.05, num_leaves=128, min_child_samples=60,
    feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
    n_jobs=10, verbose=-1, seed=7,
)


def base_matrix(df: pd.DataFrame) -> pd.DataFrame:
    X = df.drop(columns=["txn_id", "label", "source", "sender_id", "receiver_id", "uid"], errors="ignore").copy()
    X["card1_num"] = df["sender_id"].astype("int64")
    X["hour"] = (df["ts"] // 3600) % 24
    X["dow"] = (df["ts"] // 86400) % 7
    X = X.drop(columns=["ts"])  # raw time cannot generalise to a later period
    for c in X.columns:
        if not (pd.api.types.is_numeric_dtype(X[c]) or pd.api.types.is_bool_dtype(X[c])):
            X[c] = X[c].astype("category")
    return X


def build(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    if variant == "A_raw":
        return base_matrix(df)
    d = add_history_features(df, "sender_id", "card")
    if variant == "C_uid":
        d = add_history_features(add_uid(d), "uid", "uid")
    X = base_matrix(d)
    X = X.drop(columns=[c for c in X.columns if c == "uid"], errors="ignore")
    return X.replace([np.inf, -np.inf], np.nan)


def run(df: pd.DataFrame, variant: str) -> dict:
    X = build(df, variant)
    y = df["label"].to_numpy()
    tr_all, te = time_split(df, 0.7, gap=GAP)
    fit, val = time_split(tr_all, 0.7857, gap=GAP)
    idx = {"fit": fit.index, "val": val.index, "test": te.index}
    t0 = time.perf_counter()
    dfit = lgb.Dataset(X.loc[idx["fit"]], y[idx["fit"]])
    dval = lgb.Dataset(X.loc[idx["val"]], y[idx["val"]], reference=dfit)
    model = lgb.train(
        PARAMS, dfit, num_boost_round=3000, valid_sets=[dval],
        callbacks=[lgb.early_stopping(100, verbose=False)],
    )
    train_s = time.perf_counter() - t0
    Xte, yte = X.loc[idx["test"]], y[idx["test"]]
    s = model.predict(Xte, num_iteration=model.best_iteration)
    k = max(1, int(len(yte) * 0.01))
    res = {
        "variant": variant, "features": X.shape[1], "best_iter": model.best_iteration,
        "test_rows": len(yte), "auc": round(auc(yte, s), 4),
        "recall@1%": round(recall_at_fpr(yte, s, 0.01), 4),
        "recall@0.1%": round(recall_at_fpr(yte, s, 0.001), 4),
        "precision@top1%": round(precision_at_k(yte, s, k), 4), "train_s": round(train_s, 1),
    }
    row = Xte.iloc[:500]
    lat = []
    for i in range(500):
        t = time.perf_counter()
        model.predict(row.iloc[[i]], num_iteration=model.best_iteration)
        lat.append((time.perf_counter() - t) * 1000)
    res["single_row_ms_p50"] = round(float(np.percentile(lat, 50)), 2)
    res["single_row_ms_p99"] = round(float(np.percentile(lat, 99)), 2)
    return res


def main() -> None:
    tx = pd.read_csv(IEEE / "train_transaction.csv")
    ident = pd.read_csv(IEEE / "train_identity.csv")
    df = ieee_cis_to_standard(tx, ident)
    del tx, ident
    results = []
    for variant in sys.argv[1:] or ["A_raw", "B_history", "C_uid"]:
        r = run(df, variant)
        print(json.dumps(r), flush=True)
        results.append(r)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ieee_cis_models.json").write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
