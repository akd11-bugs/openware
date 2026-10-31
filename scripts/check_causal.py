"""Stricter causality check for a proposed feature group, run in its own process.

Takes the first 3000 rows, scrambles EVERY column except ts for rows >= 1500 (values shuffled among those
rows, label flipped), and requires the group's features for rows < 1500 to be unchanged. Also rejects
column names that collide with existing model inputs.

  scripts/check_causal.py --extra experiments/auto/foo.py --group foo
Exit 0 = pass, 1 = fail (reason on stdout).
"""
import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentshield.txn import experiment as E  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extra", required=True)
    ap.add_argument("--group", required=True)
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("extra_group", a.extra)
    spec.loader.exec_module(importlib.util.module_from_spec(spec))
    fn = E.FEATURE_GROUPS[a.group][0]
    df = pd.read_pickle(ROOT / "data/cache/ieee_std.pkl").iloc[:3000].copy()
    rng = np.random.default_rng(1)
    d2 = df.copy()
    fut = d2.index[1500:]
    for c in d2.columns:
        if c == "ts":
            continue
        d2.loc[fut, c] = d2.loc[fut, c].to_numpy()[rng.permutation(len(fut))]
    d2.loc[fut, "label"] = 1 - df.loc[fut, "label"].to_numpy()
    try:
        out1, out2 = fn(df), fn(d2)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: feature function raised {type(e).__name__}: {str(e)[:160]}")
        return 1
    if not isinstance(out1, pd.DataFrame) or not out1.index.equals(df.index) or out1.shape[1] == 0:
        print("FAIL: must return a non-empty DataFrame with the same index as df")
        return 1
    clash = set(out1.columns) & set(E.base_matrix(df.iloc[:50]).columns)
    if clash:
        print(f"FAIL: column names collide with existing inputs: {sorted(clash)[:5]}")
        return 1
    try:
        pd.testing.assert_frame_equal(out1.iloc[:1500], out2.iloc[:1500])
    except AssertionError as e:
        print(f"FAIL: uses future rows or labels ({str(e)[:100]})")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
