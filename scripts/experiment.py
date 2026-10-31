"""Run one validation-only experiment and append the result to docs/results/experiments_<agent>.jsonl.

  scripts/experiment.py --prepare
  scripts/experiment.py --agent a1 --groups card_hist,roll_counts --params '{"learning_rate":0.03}' [--model lgbm] [--extra experiments/a1/groups.py]

Prints one JSON line. Only validation metrics exist here; the test period is never scored by this script.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentshield.txn import experiment as E  # noqa: E402
from agentshield.txn.loaders import ieee_cis_to_standard  # noqa: E402

IEEE = ROOT / "data/.kagglehub/competitions/ieee-fraud-detection"
CACHE = ROOT / "data/cache/ieee_std.pkl"
OUT = ROOT / "docs/results"


INDIAN = ROOT / "data/.kagglehub/datasets/jatinkhandelwal112/indian-financial-fraud-dataset/versions/1/Transaction_Data_250k.csv"
INDIAN_CACHE = ROOT / "data/cache/indian_ctx.pkl"
INDIAN_DIR = INDIAN.parent


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_verified(path: Path):
    """Unpickling runs code, so only load a cache whose hash matches the one recorded when we wrote it."""
    side = path.with_name(path.name + ".sha256")
    if path.exists() and side.exists() and side.read_text().strip() == _sha256(path):
        return pd.read_pickle(path)
    return None


def _save_verified(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    os.chmod(path, 0o600)
    path.with_name(path.name + ".sha256").write_text(_sha256(path))


def load_df(dataset: str = "ieee") -> pd.DataFrame:
    if dataset == "indian":
        cached = _load_verified(INDIAN_CACHE)
        if cached is not None:
            return cached
        from agentshield.txn.loaders import indian_fin_to_standard

        df = indian_fin_to_standard(pd.read_csv(INDIAN), pd.read_csv(INDIAN_DIR / "Cards_Data.csv"),
                                    pd.read_csv(INDIAN_DIR / "Cusmtomer_data.csv"))
        _save_verified(df, INDIAN_CACHE)
        return df
    cached = _load_verified(CACHE)
    if cached is not None:
        return cached
    tx = pd.read_csv(IEEE / "train_transaction.csv")
    ident = pd.read_csv(IEEE / "train_identity.csv")
    df = ieee_cis_to_standard(tx, ident)
    assert df["ts"].is_monotonic_increasing and df.index.is_unique
    _save_verified(df, CACHE)
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prepare", action="store_true")
    ap.add_argument("--agent", default="main")
    ap.add_argument("--groups", default="")
    ap.add_argument("--params", default="{}")
    ap.add_argument("--model", default="lgbm")
    ap.add_argument("--extra", action="append", default=[])
    ap.add_argument("--n-jobs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--portable", action="store_true", help="only fields an Indian UPI feed would have")
    ap.add_argument("--dataset", choices=["ieee", "indian"], default="ieee")
    a = ap.parse_args()
    import agentshield.txn.fintech_features  # noqa: F401  (registers velocity_amt, amt_ratio_30d, payee_risk)
    import agentshield.txn.models  # noqa: F401  (registers xgb, cat, blend)
    df = load_df(a.dataset)
    if a.prepare:
        print(f"cached {len(df):,} rows at {CACHE}")
        return
    for path in a.extra:
        spec = importlib.util.spec_from_file_location("extra_" + Path(path).stem, path)
        spec.loader.exec_module(importlib.util.module_from_spec(spec))
    groups = [g for g in a.groups.split(",") if g]
    res = E.evaluate(df, groups, json.loads(a.params), a.model, a.n_jobs, a.seed, a.portable)
    res["agent"] = a.agent
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / f"experiments_{a.agent}.jsonl", "a") as f:
        f.write(json.dumps(res) + "\n")
    print(json.dumps(res))


if __name__ == "__main__":
    main()
