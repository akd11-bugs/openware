"""Evaluate the transaction-anomaly detectors on Banking Transactions 2019-2024 (synthetic, Indian rails).

Reports (1) alert volume on organic traffic, (2) fraud lift among flagged rows, (3) recall on planted anomalies.
Peer thresholds are fit on the earlier half of the timeline; all numbers below are for the later half.
Limits are illustrative per-tenant config, not RBI/NPCI values.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentshield.anomaly.txn import FLAGS, AnomalyConfig, detect, fit_peer_thresholds  # noqa: E402

CSV = ROOT / "data/.kagglehub/datasets/belbino/indian-banking-transactions-20192024/versions/2/indian_banking_transactions.csv"
LIMITS = [50_000_00, 100_000_00, 200_000_00]  # illustrative: Rs 50k, 1 lakh, 2 lakh in paise
CFG = AnomalyConfig(limits_minor=LIMITS)
rng = np.random.default_rng(11)


def load() -> pd.DataFrame:
    d = pd.read_csv(CSV)
    ts = pd.to_datetime(d["transaction_date"] + " " + d["transaction_time"]).astype("datetime64[s]").astype("int64")
    df = pd.DataFrame({
        "customer_id": d["customer_id"], "ts": ts, "amount_minor": (d["transaction_amount"] * 100).round().astype("int64"),
        "receiver_id": None, "label": d["is_fraud"], "ttype": d["transaction_type"],
    })
    return df.sort_values("ts", kind="stable").reset_index(drop=True)


def organic_report(df, out, half):
    ev = np.arange(len(df)) >= half
    n = ev.sum()
    print(f"\n== Organic traffic, later half: {n:,} transactions, fraud rate {df.loc[ev, 'label'].mean():.3%}")
    print(f"{'detector':16s} {'flags/1000':>10s} {'fraud rate flagged':>20s} {'lift':>6s}")
    base = df.loc[ev, "label"].mean()
    for f in FLAGS + ["ANY"]:
        m = ev & (out[FLAGS].any(axis=1).to_numpy() if f == "ANY" else out[f].to_numpy())
        if m.sum() == 0:
            print(f"{f:16s} {0:10.2f} {'n/a (no flags)':>20s}")
            continue
        fr = df.loc[m, "label"].mean()
        print(f"{f:16s} {1000 * m.sum() / n:10.2f} {fr:20.3%} {fr / base:6.2f}x   ({int(m.sum()):,} flags)")
    prior = df.groupby("customer_id").cumcount().to_numpy()
    print(f"customer-baseline coverage (>= {CFG.min_hist} prior txns): {(prior[ev] >= CFG.min_hist).mean():.1%} of later-half rows")


def injection_report(df, half):
    """Plant anomalies for customers with history, rerun detectors, measure recall on the planted rows."""
    counts = df.groupby("customer_id").size()
    custs = counts[counts >= 10].index.to_numpy()
    last = df.groupby("customer_id")["ts"].max()
    med = df.groupby("customer_id")["amount_minor"].median()
    rows, tag = [], []
    pick = rng.choice(custs, 900, replace=False)
    for i, c in enumerate(pick):
        t0 = int(last[c]) + 3600 * (1 + i % 5)
        if i < 300:  # amount outlier: 25x this customer's median
            rows.append((c, t0, int(max(med[c], 100) * 25))); tag.append("AMT_OUTLIER")
        elif i < 600:  # structuring: four payments just under a limit within 6 hours
            for k in range(4):
                rows.append((c, t0 + k * 5400, int(LIMITS[1] * 0.96) - k * 100)); tag.append("STRUCTURING" if k == 3 else "setup")
        else:  # rapid repeat: same amount three times, 60 s apart
            for k in range(3):
                rows.append((c, t0 + k * 60, 77_700 + i)); tag.append("RAPID_REPEAT" if k > 0 else "setup")
    inj = pd.DataFrame(rows, columns=["customer_id", "ts", "amount_minor"])
    inj["receiver_id"], inj["label"], inj["ttype"], inj["inj"] = None, 0, "UPI", tag
    full = pd.concat([df.assign(inj=""), inj], ignore_index=True).sort_values("ts", kind="stable").reset_index(drop=True)
    pt = fit_peer_thresholds(df.iloc[:half], "ttype")
    out = detect(full, CFG, "ttype", pt)
    print("\n== Injection test (planted anomalies; recall = share of planted target rows flagged)")
    for kind, flag in [("AMT_OUTLIER", "AMT_OUTLIER"), ("STRUCTURING", "STRUCTURING"), ("RAPID_REPEAT", "RAPID_REPEAT")]:
        m = (full["inj"] == kind).to_numpy()
        print(f"{kind:14s} planted {int(m.sum()):4d}   caught by its detector {out.loc[m, flag].mean():6.1%}   caught by any {out.loc[m, FLAGS].any(axis=1).mean():6.1%}")
    org = (full["inj"] == "").to_numpy() & (np.arange(len(full)) >= half)
    print(f"organic alerts/1000 after injection: {1000 * out.loc[org, FLAGS].any(axis=1).mean():.2f}")


def main() -> None:
    df = load()
    half = len(df) // 2
    pt = fit_peer_thresholds(df.iloc[:half], "ttype")
    for z in (3.5, 5.0, 8.0):  # alert-volume vs recall trade-off for the amount detector
        CFG.z_thresh = z
        print(f"\n######## amount z-threshold {z}")
        out = detect(df, CFG, "ttype", pt)
        organic_report(df, out, half)
        injection_report(df, half)


if __name__ == "__main__":
    main()
