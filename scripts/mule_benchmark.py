"""Mule-ring benchmark: do account-graph features beat a plain model (and rules, on PaySim)?

  scripts/mule_benchmark.py --dataset paysim
  scripts/mule_benchmark.py --dataset ibm_aml     (also reports recall by laundering pattern)

Time split with a 1 day label-delay gap. PaySim variants are compared with and without its balance fields, which
nearly give fraud away. A paired bootstrap on the test slice gives a confidence interval on each gain.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentshield.graph.account_features import account_graph_features  # noqa: E402
from agentshield.txn.baseline import PaySimBaseline  # noqa: E402
from agentshield.txn.loaders import ibm_aml_to_standard, paysim_to_standard  # noqa: E402
from agentshield.txn.metrics import auc, precision_at_k, recall_at_fpr  # noqa: E402
from agentshield.txn.split import time_split  # noqa: E402

PAYSIM = ROOT / "data/paysim/PS_20174392719_1491204439457_log.csv"
IBM = ROOT / "data/.kagglehub/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml/versions/8"
TYPES = {"PAYMENT": 0, "TRANSFER": 1, "CASH_OUT": 2, "CASH_IN": 3, "DEBIT": 4}
GAP = 86400
PARAMS = dict(objective="binary", learning_rate=0.1, num_leaves=63, min_child_samples=100, feature_fraction=0.8,
              bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, n_jobs=8, verbose=-1, seed=7)


def metrics(y, s):
    return {"auc": round(auc(y, s), 4), "recall@1%": round(recall_at_fpr(y, s, 0.01), 4),
            "recall@0.1%": round(recall_at_fpr(y, s, 0.001), 4), "precision@top1%": round(precision_at_k(y, s, max(1, len(y) // 100)), 4)}


def pattern_types(raw: pd.DataFrame) -> np.ndarray:
    """Label each laundering row with its ring type (fan-out, cycle ...) from the patterns file; '' for the rest."""
    key = {}
    cur = None
    for line in (IBM / "HI-Small_Patterns.txt").read_text().splitlines():
        if line.startswith("BEGIN LAUNDERING ATTEMPT"):
            cur = line.split(" - ", 1)[1].split(":")[0].strip()
        elif line.startswith("END LAUNDERING ATTEMPT"):
            cur = None
        elif cur and line.count(",") >= 10:
            f = line.split(",")
            key[(f[0], int(f[1]), f[2], int(f[3]), f[4], round(float(f[7]), 2))] = cur
    keys = zip(raw["Timestamp"], raw["From Bank"], raw["Account"], raw["To Bank"], raw["Account.1"], raw["Amount Paid"].round(2))
    return np.array([key.get(k, "") if lab else "" for k, lab in zip(keys, raw["Is Laundering"])], dtype=object)


def prepare(name: str):
    if name == "paysim":
        df = paysim_to_standard(pd.read_csv(PAYSIM))
        base = pd.DataFrame({"type": df["x_type"].map(TYPES).astype("float64"), "amount": df["amount_minor"].astype("float64"),
                             "hour": ((df["ts"] // 3600) % 24).astype("float64")}, index=df.index)
        bal = df[["x_oldbalanceOrg", "x_newbalanceOrig", "x_oldbalanceDest", "x_newbalanceDest"]].astype("float64")
        bal.columns = ["oldbalanceOrg", "newbalanceOrig", "oldbalanceDest", "newbalanceDest"]
        return df, base, bal, None
    raw = pd.read_csv(IBM / "HI-Small_Trans.csv")
    ptype = pattern_types(raw)
    df = ibm_aml_to_standard(raw)
    ptype = ptype[df["txn_id"].to_numpy()]  # txn_id is the raw row number, so this follows the time sort
    base = pd.DataFrame({
        "format": df["x_Payment Format"].astype("category").cat.codes.astype("float64"),
        "currency": df["x_Payment Currency"].astype("category").cat.codes.astype("float64"),
        "self": df["x_self_transfer"].astype("float64"), "cross_ccy": df["x_cross_currency"].astype("float64"),
        "amount": df["amount_minor"].astype("float64"), "hour": ((df["ts"] // 3600) % 24).astype("float64")}, index=df.index)
    return df, base, None, ptype


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["paysim", "ibm_aml"], required=True)
    name = ap.parse_args().dataset
    t0 = time.perf_counter()
    df, base, bal, ptype = prepare(name)
    print(f"{name}: loaded {len(df):,} rows in {time.perf_counter() - t0:.0f}s", flush=True)
    t0 = time.perf_counter()
    g = account_graph_features(df, fraud_delay=GAP)
    print(f"graph features in {time.perf_counter() - t0:.0f}s", flush=True)
    y = df["label"].to_numpy()
    tr, te = time_split(df, 0.7, gap=GAP)
    fit, val = time_split(tr, 0.7857, gap=GAP)
    print(f"fit {len(fit):,} ({y[fit.index].mean():.3%})  val {len(val):,}  test {len(te):,} ({y[te.index].mean():.3%} positive)", flush=True)
    results, scores = {}, {}
    if name == "paysim":
        for ub in (False, True):
            results[f"rules (balance={ub})"] = metrics(y[te.index], PaySimBaseline(use_balance_fields=ub).fit(fit).score(te))
        variants = {"A model, no balance, no graph": [base], "B model, no balance, + graph": [base, g],
                    "C model, balance, no graph": [base, bal], "D model, balance, + graph": [base, bal, g]}
        pairs = [("A model, no balance, no graph", "B model, no balance, + graph"), ("C model, balance, no graph", "D model, balance, + graph")]
    else:
        variants = {"A model, no graph": [base], "B model, + graph": [base, g]}
        pairs = [("A model, no graph", "B model, + graph")]
    for vname, parts in variants.items():
        X = pd.concat(parts, axis=1)
        d = lgb.Dataset(X.loc[fit.index], y[fit.index])
        m = lgb.train(PARAMS, d, 600, valid_sets=[lgb.Dataset(X.loc[val.index], y[val.index], reference=d)],
                      callbacks=[lgb.early_stopping(50, verbose=False)])
        s = m.predict(X.loc[te.index], num_iteration=m.best_iteration)
        scores[vname], results[vname] = s, {**metrics(y[te.index], s), "features": X.shape[1], "iters": m.best_iteration}
        print(f"{vname}: {results[vname]}", flush=True)
    yt = y[te.index]
    rng = np.random.default_rng(0)
    diffs = {}
    for a, b in pairs:
        d_auc, d_r1 = [], []
        for _ in range(100):
            i = rng.integers(0, len(yt), len(yt))
            d_auc.append(auc(yt[i], scores[b][i]) - auc(yt[i], scores[a][i]))
            d_r1.append(recall_at_fpr(yt[i], scores[b][i], 0.01) - recall_at_fpr(yt[i], scores[a][i], 0.01))
        diffs[f"{b} minus {a}"] = {"auc": [round(float(np.percentile(d_auc, 2.5)), 4), round(float(np.percentile(d_auc, 97.5)), 4)],
                                  "recall@1%": [round(float(np.percentile(d_r1, 2.5)), 4), round(float(np.percentile(d_r1, 97.5)), 4)]}
    print("paired bootstrap 95% interval for the gain from graph features:", json.dumps(diffs), flush=True)
    out = {"dataset": name, "test_rows": int(len(te)), "gap_s": GAP, "results": results, "graph_gain_ci95": diffs}
    if ptype is not None:
        pt = ptype[te.index.to_numpy()]
        by = {}
        for vname in ("A model, no graph", "B model, + graph"):
            thr = np.quantile(scores[vname][yt == 0], 0.99)
            for t in sorted(set(pt) - {""}):
                m = pt == t
                by.setdefault(t, {"transactions": int(m.sum())})[vname] = round(float((scores[vname][m] > thr).mean()), 3)
        out["recall_by_pattern_at_1pct_fpr"] = by
        print("recall by laundering pattern at 1% false alarms:", json.dumps(by), flush=True)
    (ROOT / f"docs/results/mule_benchmark_{name}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
