"""Parity and latency of the online feature store on real data (Indian Financial Fraud Dataset, first N rows)."""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from agentshield.features_online.store import FEATURE_NAMES, OnlineFeatureStore  # noqa: E402
from agentshield.txn import experiment as E  # noqa: E402
from agentshield.txn import fintech_features  # noqa: E402,F401
from experiment import load_df  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 60_000


def main() -> None:
    df = load_df("indian").iloc[:N].reset_index(drop=True)
    batch = pd.concat([E.FEATURE_GROUPS[g][0](df) for g in ("velocity_amt", "amt_ratio_30d", "payee_risk")], axis=1)
    store, rows, lat = OnlineFeatureStore(), [], []
    for s, r, t, a in zip(df["sender_id"], df["receiver_id"], df["ts"].to_numpy(), df["amount_minor"].to_numpy()):
        t0 = time.perf_counter()
        rows.append(store.features(s, r, int(t), int(a)))
        store.update(s, r, int(t), int(a))
        lat.append((time.perf_counter() - t0) * 1e6)
    online = pd.DataFrame(rows, columns=FEATURE_NAMES)
    print(f"events replayed: {len(df):,}  (ts ties: {int(df['ts'].duplicated().sum()):,})")
    worst = 0.0
    for c in FEATURE_NAMES:
        a, b = online[c].to_numpy(float), batch[c].to_numpy(float)
        same_nan = np.array_equal(np.isnan(a), np.isnan(b))
        diff = np.nanmax(np.abs(a - b)) if (~np.isnan(a)).any() else 0.0
        worst = max(worst, diff)
        print(f"  {c:24s} nan-pattern equal: {same_nan}   max abs diff: {diff:.3g}")
    lat.sort()
    print(f"online update+lookup per event: p50 {lat[len(lat) // 2]:.0f} us, p99 {lat[int(len(lat) * .99)]:.0f} us, max {lat[-1]:.0f} us")
    print(f"log entries held at the end: {store.size():,}")
    print("PARITY OK" if worst < 1e-6 else "PARITY MISMATCH")


if __name__ == "__main__":
    main()
