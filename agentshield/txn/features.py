"""Time-safe features: every value for a row uses only rows that came before it.

Never uses labels. Safe to compute over train+test together because it only looks backwards.
"""
import numpy as np
import pandas as pd

DAY = 86400


def add_history_features(df: pd.DataFrame, key: str, prefix: str) -> pd.DataFrame:
    """Per-`key` history: prior count, prior mean amount, amount vs that mean, seconds since last txn."""
    if not df.index.is_unique:
        raise ValueError("df index must be unique")
    order = np.argsort(df["ts"].to_numpy(), kind="stable")
    s = df.iloc[order]
    g = s.groupby(key, sort=False)
    amt = s["amount_minor"].astype("float64")
    cnt = g.cumcount()
    prior_sum = g["amount_minor"].cumsum().astype("float64") - amt
    prior_mean = prior_sum / cnt.where(cnt > 0)
    feats = pd.DataFrame(
        {
            f"{prefix}_prior_cnt": cnt.to_numpy(),
            f"{prefix}_prior_mean": prior_mean.to_numpy(),
            f"{prefix}_amt_ratio": (amt / prior_mean).to_numpy(),
            f"{prefix}_since_last": (s["ts"] - g["ts"].shift(1)).to_numpy(),
        },
        index=s.index,
    )
    out = df.copy()
    for c in feats.columns:
        out[c] = feats[c].reindex(df.index)
    return out


def add_uid(df: pd.DataFrame) -> pd.DataFrame:
    """Pseudo-client id: card + address + estimated first-seen day (ts_day - D1).

    This is the grouping trick top IEEE-CIS solutions used to treat a client, not a transaction,
    as the unit. It depends on the D1 field, which few real feeds provide.
    """
    out = df.copy()
    first_day = np.floor(out["ts"] / DAY - out["x_D1"]).astype("float64")
    out["uid"] = (
        out["sender_id"].astype(str) + "_" + out["x_addr1"].astype(str) + "_" + first_day.astype(str)
    )
    return out
