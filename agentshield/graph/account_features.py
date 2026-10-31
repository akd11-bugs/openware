"""Account-level graph features for mule detection, vectorised for millions of rows.

For every transaction we describe the RECEIVING account and the SENDING account as they were just before it:
money in and out so far, how long the account has existed, and how much of what came in has already gone out
(a mule passes money straight through). Events with an earlier timestamp only; same-timestamp rows do not count.
Confirmed fraud counts from `fraud_delay` seconds after the fraud transaction onward (inclusive), because real
fraud labels arrive late. A missing sender or receiver id is an unknown account: it gets NaN features on that
side and is not pooled with other unknowns.
"""
import numpy as np
import pandas as pd

GRAPH_FEATURES = ["rcv_in_cnt", "rcv_in_sum", "rcv_out_cnt", "rcv_out_sum", "rcv_age_s", "rcv_pass_through",
                  "rcv_known_fraud_in", "snd_in_cnt", "snd_in_sum", "snd_out_cnt", "snd_out_sum", "snd_age_s",
                  "snd_known_fraud_out"]
SPAN = 1 << 31  # timestamps are shifted to start at 0 and must stay below this


def _log(codes: np.ndarray, t: np.ndarray, vals: np.ndarray):
    """Events sorted by (account, time) with running sums, so any 'before time t' query is two binary searches."""
    key = codes.astype("int64") * SPAN + t
    order = np.argsort(key, kind="stable")
    return key[order], np.concatenate([[0.0], np.cumsum(vals[order])])


def _before(log, q_codes: np.ndarray, q_t: np.ndarray, side: str = "left"):
    keys, cs = log
    if len(keys) == 0:
        z = np.zeros(len(q_codes))
        return z, z, np.full(len(q_codes), -1)
    start = np.searchsorted(keys, q_codes.astype("int64") * SPAN, "left")
    pos = np.searchsorted(keys, q_codes.astype("int64") * SPAN + q_t, side)
    first_t = np.where(pos > start, keys[np.minimum(start, len(keys) - 1)] - q_codes.astype("int64") * SPAN, -1)
    return pos - start, cs[pos] - cs[start], first_t


def account_graph_features(df: pd.DataFrame, fraud_delay: int = 86400) -> pd.DataFrame:
    if not df["ts"].is_monotonic_increasing:
        raise ValueError("df must be sorted by ts ascending")
    n = len(df)
    t = (df["ts"].to_numpy() - df["ts"].iloc[0]).astype("int64")
    if t.max() + fraud_delay >= SPAN:
        raise ValueError("time span too long for the key encoding")
    codes, _ = pd.factorize(pd.concat([df["sender_id"], df["receiver_id"]], ignore_index=True))
    snd, rcv = codes[:n], codes[n:]
    amt = df["amount_minor"].to_numpy(dtype="float64")
    fraud = df["label"].to_numpy() == 1
    ones = np.ones(n)
    vs, vr = snd >= 0, rcv >= 0  # factorize gives missing ids code -1: leave them out of every log
    inflow, outflow = _log(rcv[vr], t[vr], amt[vr]), _log(snd[vs], t[vs], amt[vs])  # money in (receiver) and out (sender)
    fr, fs = fraud & vr, fraud & vs
    known_in = _log(rcv[fr], t[fr] + fraud_delay, ones[fr])  # fraud becomes "known" fraud_delay later
    known_out = _log(snd[fs], t[fs] + fraud_delay, ones[fs])

    def side(codes_):
        c_in, s_in, f_in = _before(inflow, codes_, t)
        c_out, s_out, f_out = _before(outflow, codes_, t)
        firsts = np.where(f_in < 0, np.inf, f_in)
        firsts = np.minimum(firsts, np.where(f_out < 0, np.inf, f_out))
        age = np.where(np.isfinite(firsts), t - firsts, np.nan)
        return c_in, s_in, c_out, s_out, age

    r_in_c, r_in_s, r_out_c, r_out_s, r_age = side(rcv)
    s_in_c, s_in_s, s_out_c, s_out_s, s_age = side(snd)
    with np.errstate(divide="ignore", invalid="ignore"):
        pass_through = np.where(r_in_s > 0, r_out_s / r_in_s, np.nan)
    rk = _before(known_in, rcv, t, "right")[0]
    sk = _before(known_out, snd, t, "right")[0]
    out = pd.DataFrame({
        "rcv_in_cnt": r_in_c, "rcv_in_sum": r_in_s, "rcv_out_cnt": r_out_c, "rcv_out_sum": r_out_s, "rcv_age_s": r_age,
        "rcv_pass_through": pass_through, "rcv_known_fraud_in": rk, "snd_in_cnt": s_in_c, "snd_in_sum": s_in_s,
        "snd_out_cnt": s_out_c, "snd_out_sum": s_out_s, "snd_age_s": s_age, "snd_known_fraud_out": sk,
    }, index=df.index)[GRAPH_FEATURES]
    out.loc[~vr, [c for c in GRAPH_FEATURES if c.startswith("rcv_")]] = np.nan
    out.loc[~vs, [c for c in GRAPH_FEATURES if c.startswith("snd_")]] = np.nan
    return out
