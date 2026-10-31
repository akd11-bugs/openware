"""Rail-agnostic fintech features, batch form. Every window is [t - W, t): events with a strictly earlier
timestamp only, so a row never sees itself, rows sharing its timestamp, or the future.

  velocity_amt   amt_sum_1h, amt_sum_24h        money moved by the sender in the last hour / day
  amt_ratio_30d  amt_ratio_30d                  amount vs the sender's mean over the previous 30 days
  payee_risk     payee_uniq_senders_24h,        how many distinct senders paid this payee recently,
                 payee_cnt_24h,                 how many payments it received, and whether this
                 payee_new_for_sender           sender has never paid it before

The online store (agentshield/features_online/store.py) must produce identical values; see its parity test.
Location is deliberately not used: a payer's location says nothing reliable about UPI/QR payments.
"""
from collections import Counter

import numpy as np
import pandas as pd

from .experiment import register

HOUR, DAY = 3600, 86400


def _groups(keys: np.ndarray, ts: np.ndarray):
    order = np.lexsort((ts, keys))
    ks = keys[order]
    bounds = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        yield order[a:b]


def window_stats(keys: np.ndarray, ts: np.ndarray, amt: np.ndarray, window: int):
    """Per row: count and amount sum of the same key's events with ts in [t - window, t)."""
    cnt = np.zeros(len(ts), dtype="int64")
    tot = np.zeros(len(ts), dtype="float64")
    for idx in _groups(keys, ts):
        t, a = ts[idx], amt[idx].astype("float64")
        cs = np.concatenate([[0.0], np.cumsum(a)])
        pos = np.searchsorted(t, t, "left")
        left = np.searchsorted(t, t - window, "left")
        cnt[idx], tot[idx] = pos - left, cs[pos] - cs[left]
    return cnt, tot


@register("velocity_amt")
def velocity_amt(df: pd.DataFrame) -> pd.DataFrame:
    keys, ts, amt = df["sender_id"].to_numpy(), df["ts"].to_numpy(), df["amount_minor"].to_numpy()
    return pd.DataFrame(
        {"amt_sum_1h": window_stats(keys, ts, amt, HOUR)[1], "amt_sum_24h": window_stats(keys, ts, amt, DAY)[1]},
        index=df.index,
    )


@register("amt_ratio_30d")
def amt_ratio_30d(df: pd.DataFrame) -> pd.DataFrame:
    keys, ts, amt = df["sender_id"].to_numpy(), df["ts"].to_numpy(), df["amount_minor"].to_numpy()
    cnt, tot = window_stats(keys, ts, amt, 30 * DAY)
    mean = np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(mean > 0, amt / mean, np.nan)
    return pd.DataFrame({"amt_ratio_30d": ratio}, index=df.index)


@register("sender_hist")
def sender_hist(df: pd.DataFrame) -> pd.DataFrame:
    """All-time sender history from strictly earlier events (unlike card_hist, same-timestamp rows are not 'prior')."""
    keys, ts, amt = df["sender_id"].to_numpy(), df["ts"].to_numpy(), df["amount_minor"].to_numpy().astype("float64")
    n = len(df)
    cnt, mean, since = np.zeros(n), np.full(n, np.nan), np.full(n, np.nan)
    for idx in _groups(keys, ts):
        t, a = ts[idx], amt[idx]
        cs = np.concatenate([[0.0], np.cumsum(a)])
        pos = np.searchsorted(t, t, "left")
        cnt[idx] = pos
        mean[idx] = np.where(pos > 0, cs[pos] / np.maximum(pos, 1), np.nan)
        since[idx] = np.where(pos > 0, t - t[np.maximum(pos - 1, 0)], np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(mean > 0, amt / mean, np.nan)
    return pd.DataFrame({"sender_prior_cnt": cnt, "sender_prior_mean": mean, "sender_amt_ratio": ratio,
                         "sender_since_last": since}, index=df.index)


# Codes for fields that are categorical in the feed. These maps match IEEE-CIS vocabulary; a real tenant's feed
# has its own values (e.g. RuPay, UPI), so the model is retrained per tenant. Unknown or missing -> -1.
CHANNEL_CODES = {"w": 0, "c": 1, "h": 2, "r": 3, "s": 4}
INSTRUMENT_CODES = {"debit": 0, "credit": 1, "debit or credit": 2, "charge card": 3}
NETWORK_CODES = {"visa": 0, "mastercard": 1, "american express": 2, "discover": 3}


def code_of(value, mapping: dict) -> float:
    return float(mapping.get(str(value).lower(), -1)) if value is not None and value == value else -1.0


@register("instrument")
def instrument(df: pd.DataFrame) -> pd.DataFrame:
    def col(name, mapping):
        return df[name].astype(object).map(lambda v: code_of(v, mapping)).astype("float64")

    return pd.DataFrame({"channel_code": col("x_ProductCD", CHANNEL_CODES), "instrument_code": col("x_card6", INSTRUMENT_CODES),
                         "network_code": col("x_card4", NETWORK_CODES)}, index=df.index)


@register("device_share")
def device_share(df: pd.DataFrame) -> pd.DataFrame:
    """Device history from strictly earlier events: is this device new for this sender, how many devices has the
    sender used, and how many OTHER senders have used this device (shared devices are a mule signal)."""
    n = len(df)
    ts, snd = df["ts"].to_numpy(), df["sender_id"].to_numpy()
    dev = df["x_DeviceInfo"].astype(object).to_numpy()
    has = df["x_DeviceInfo"].notna().to_numpy()
    new, n_dev, n_snd = np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan)
    rows = np.flatnonzero(has)
    if len(rows) == 0:
        return pd.DataFrame({"device_new_for_sender": new, "sender_distinct_devices": n_dev, "device_uniq_senders": n_snd}, index=df.index)
    pairs = pd.DataFrame({"s": snd[rows], "d": dev[rows], "t": ts[rows]})
    first = pairs.groupby(["s", "d"])["t"].min().reset_index()
    by_sender = {k: np.sort(g["t"].to_numpy()) for k, g in first.groupby("s")}
    by_device = {k: np.sort(g["t"].to_numpy()) for k, g in first.groupby("d")}
    pair_first = pairs.groupby(["s", "d"])["t"].transform("min").to_numpy()
    is_new = ts[rows] == pair_first
    for j, i in enumerate(rows):
        n_dev[i] = np.searchsorted(by_sender[snd[i]], ts[i], "left")
        others = np.searchsorted(by_device[dev[i]], ts[i], "left") - (0 if is_new[j] else 1)
        n_snd[i] = others
        new[i] = 1.0 if is_new[j] else 0.0
    return pd.DataFrame({"device_new_for_sender": new, "sender_distinct_devices": n_dev, "device_uniq_senders": n_snd}, index=df.index)


@register("payee_risk")
def payee_risk(df: pd.DataFrame) -> pd.DataFrame:
    """Payee features. Rows without a receiver get NaN and are not events for any payee."""
    cols = ["payee_uniq_senders_24h", "payee_cnt_24h", "payee_new_for_sender"]
    n = len(df)
    has = df["receiver_id"].notna().to_numpy()
    if not has.any():
        return pd.DataFrame(np.nan, index=df.index, columns=cols)
    pos = np.flatnonzero(has)
    ts, snd = df["ts"].to_numpy()[pos], df["sender_id"].to_numpy()[pos]
    rcv = df["receiver_id"].astype(str).to_numpy()[pos]
    uniq, cnt = np.zeros(len(pos)), np.zeros(len(pos))
    for idx in _groups(rcv, ts):
        t, s = ts[idx], snd[idx]
        live: Counter = Counter()
        add = left = 0
        for i in range(len(idx)):
            while add < len(idx) and t[add] < t[i]:
                live[s[add]] += 1
                add += 1
            while left < add and t[left] < t[i] - DAY:
                live[s[left]] -= 1
                if live[s[left]] == 0:
                    del live[s[left]]
                left += 1
            uniq[idx[i]], cnt[idx[i]] = len(live), add - left
    pair_first = pd.Series(ts).groupby([snd, rcv]).transform("min").to_numpy()
    out = {c: np.full(n, np.nan) for c in cols}
    out["payee_uniq_senders_24h"][pos], out["payee_cnt_24h"][pos] = uniq, cnt
    out["payee_new_for_sender"][pos] = (ts == pair_first).astype(float)
    return pd.DataFrame(out, index=df.index)
