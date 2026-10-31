"""Parity and latency of the online feature store on real data. Exits non-zero on ANY mismatch.

Replays the first N events of IEEE-CIS (devices, instrument fields) and of the Indian dataset (payees) through the
online store and compares every feature the data can support with the batch code, including the missing-value pattern.
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentshield.features_online.store import ALL_FEATURE_NAMES, OnlineFeatureStore  # noqa: E402
from agentshield.txn import experiment as E  # noqa: E402
import agentshield.txn.fintech_features  # noqa: E402,F401
from experiment import load_df  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000


def groups_for(df: pd.DataFrame) -> list[str]:
    g = ["velocity_amt", "amt_ratio_30d", "sender_hist", "roll_counts"]
    if df["receiver_id"].notna().any():
        g.append("payee_risk")
    if "x_DeviceInfo" in df:
        g += ["device_share", "instrument"]
    return g


def check(name: str) -> bool:
    df = load_df(name).iloc[:N].reset_index(drop=True)
    batch = pd.concat([E.FEATURE_GROUPS[g][0](df) for g in groups_for(df)], axis=1)
    col = lambda c: df[c].astype(object).to_numpy() if c in df else [None] * len(df)  # noqa: E731
    dev, chan, inst, net = col("x_DeviceInfo"), col("x_ProductCD"), col("x_card6"), col("x_card4")
    has_r = df["receiver_id"].notna().to_numpy()
    store, rows, lat = OnlineFeatureStore(), [], []
    for i, (s, r, t, a) in enumerate(zip(df["sender_id"], df["receiver_id"], df["ts"].to_numpy(), df["amount_minor"].to_numpy())):
        r = r if has_r[i] else None
        t0 = time.perf_counter()
        rows.append(store.features(s, r, int(t), int(a), dev[i], chan[i], inst[i], net[i]))
        store.update(s, r, int(t), int(a), dev[i])
        lat.append((time.perf_counter() - t0) * 1e6)
    online = pd.DataFrame(rows, columns=ALL_FEATURE_NAMES)
    ok = True
    print(f"\n{name}: {len(df):,} events, {int(df['ts'].duplicated().sum()):,} timestamp ties, groups {groups_for(df)}")
    for c in batch.columns:
        a, b = online[c].to_numpy(float), batch[c].to_numpy(float)
        same_nan = np.array_equal(np.isnan(a), np.isnan(b))
        both = ~np.isnan(a) & ~np.isnan(b)
        diff = float(np.max(np.abs(a[both] - b[both]))) if both.any() else 0.0
        good = same_nan and diff < 1e-6
        ok &= good
        print(f"  {c:26s} missing-pattern equal: {same_nan!s:5s} max abs diff: {diff:.3g}  {'ok' if good else 'MISMATCH'}")
    lat.sort()
    print(f"  online update + lookup per event: p50 {lat[len(lat) // 2]:.0f} us, p99 {lat[int(len(lat) * .99)]:.0f} us")
    return ok


def main() -> None:
    results = [check(n) for n in ("ieee", "indian")]
    print("\nPARITY OK" if all(results) else "\nPARITY MISMATCH")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
