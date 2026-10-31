"""Transaction-level anomaly detectors. Unsupervised, causal (a row sees only earlier rows), label-free.

Input: a DataFrame sorted by `ts` with customer_id, ts (seconds), amount_minor (int paise/cents),
optional receiver_id. Output: one boolean column per detector, `anomaly_score` (flags raised) and `reasons`.

  AMT_OUTLIER     amount far above this customer's own recent norm (robust z-score), or above a peer threshold
                  when the customer has too little history
  STRUCTURING     several payments just under a configured limit within a short window
  RAPID_REPEAT    the same customer+amount (+receiver) again within minutes, or a burst of payments
  NEW_PAYEE_HIGH  a payee this customer never paid before, for a much larger amount than usual

Limits and thresholds are per-tenant configuration. The limits in AnomalyConfig defaults are empty on purpose.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from agentshield.txn.baseline import _prior_window_counts

FLAGS = ["AMT_OUTLIER", "STRUCTURING", "RAPID_REPEAT", "NEW_PAYEE_HIGH"]


@dataclass
class AnomalyConfig:
    min_hist: int = 5  # prior transactions needed for the customer's own baseline
    hist_window: int = 20  # how many recent transactions form the baseline
    z_thresh: float = 6.0
    ratio_thresh: float = 3.0  # amount must also be this many times the baseline median
    limits_minor: list = field(default_factory=list)  # per-tenant payment limits, in minor units
    limit_band: float = 0.10  # "just under" = within this fraction below a limit
    structuring_k: int = 3
    structuring_window: int = 86400
    repeat_window: int = 300
    burst_count: int = 5  # this many transactions inside burst_window
    burst_window: int = 600
    new_payee_min_hist: int = 3
    new_payee_ratio: float = 3.0


def fit_peer_thresholds(df_fit: pd.DataFrame, group_col: str, q: float = 0.999) -> dict:
    """Peer amount threshold per group (e.g. transaction type), fit on an EARLIER period only."""
    return df_fit.groupby(group_col)["amount_minor"].quantile(q).to_dict()


def _amount_outlier(df, cfg, peer_col, peer_thresholds):
    cust = df["customer_id"].to_numpy()
    amt = df["amount_minor"].to_numpy(dtype="float64")
    flag = np.zeros(len(df), dtype=bool)
    order = np.argsort(cust, kind="stable")  # stable keeps time order inside each customer
    cs = cust[order]
    bounds = np.flatnonzero(np.r_[True, cs[1:] != cs[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        idx = order[a:b]
        x = amt[idx]
        for i in range(cfg.min_hist, len(x)):
            h = x[max(0, i - cfg.hist_window):i]
            med_amt = np.median(h)
            if x[i] < cfg.ratio_thresh * med_amt:
                continue
            lh = np.log1p(h)  # log scale: amounts span orders of magnitude, so a linear z-score over-flags
            med = np.median(lh)
            scale = max(1.4826 * np.median(np.abs(lh - med)), 0.05)
            if (np.log1p(x[i]) - med) / scale >= cfg.z_thresh:
                flag[idx[i]] = True
    if peer_col is not None and peer_thresholds:
        prior = df.groupby("customer_id").cumcount().to_numpy()
        thr = df[peer_col].map(peer_thresholds).to_numpy(dtype="float64")
        flag |= (prior < cfg.min_hist) & (amt > thr)
    return flag


def _structuring(df, cfg):
    flag = np.zeros(len(df), dtype=bool)
    amt = df["amount_minor"].to_numpy()
    for limit in cfg.limits_minor:
        near = (amt < limit) & (amt >= limit * (1 - cfg.limit_band))
        if near.sum() < cfg.structuring_k:
            continue
        sub = df.loc[near]
        prior = _prior_window_counts(sub["ts"].to_numpy(), sub["customer_id"].to_numpy(), cfg.structuring_window)
        hit = (prior + 1) >= cfg.structuring_k
        flag[np.flatnonzero(near)[hit]] = True
    return flag


def _rapid_repeat(df, cfg):
    ts, cust = df["ts"].to_numpy(), df["customer_id"].to_numpy()
    key = cust.astype(str) + "|" + df["amount_minor"].astype(str).to_numpy()
    if df["receiver_id"].notna().any():
        key = key + "|" + df["receiver_id"].astype(str).to_numpy()
    repeat = _prior_window_counts(ts, key.astype(object), cfg.repeat_window) >= 1
    burst = (_prior_window_counts(ts, cust, cfg.burst_window) + 1) >= cfg.burst_count
    return repeat | burst


def _new_payee_high(df, cfg):
    if not df["receiver_id"].notna().any():
        return np.zeros(len(df), dtype=bool)
    g = df.groupby("customer_id", sort=False)
    prior_cnt = g.cumcount()
    prior_sum = g["amount_minor"].cumsum() - df["amount_minor"]
    prior_mean = prior_sum / prior_cnt.where(prior_cnt > 0)
    first_time = df.groupby(["customer_id", "receiver_id"], sort=False).cumcount() == 0
    big = df["amount_minor"] >= cfg.new_payee_ratio * prior_mean
    return (first_time & (prior_cnt >= cfg.new_payee_min_hist) & big & df["receiver_id"].notna()).to_numpy()


def detect(df: pd.DataFrame, cfg: AnomalyConfig | None = None, peer_col: str | None = None,
           peer_thresholds: dict | None = None) -> pd.DataFrame:
    cfg = cfg or AnomalyConfig()
    if not df["ts"].is_monotonic_increasing:
        raise ValueError("df must be sorted by ts ascending")
    df = df.reset_index(drop=True)
    if "receiver_id" not in df:
        df = df.assign(receiver_id=None)
    out = pd.DataFrame(
        {
            "AMT_OUTLIER": _amount_outlier(df, cfg, peer_col, peer_thresholds),
            "STRUCTURING": _structuring(df, cfg),
            "RAPID_REPEAT": _rapid_repeat(df, cfg),
            "NEW_PAYEE_HIGH": _new_payee_high(df, cfg),
        }
    )
    out["anomaly_score"] = out[FLAGS].sum(axis=1)
    names = np.array(FLAGS)
    out["reasons"] = [",".join(names[row]) for row in out[FLAGS].to_numpy()]
    return out
