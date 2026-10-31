import numpy as np
import pandas as pd
from agentshield.txn.experiment import register


def _s(df):
    return df.sort_values("ts", kind="mergesort")


def _k(d, *cols):
    r = d[cols[0]].astype(str)
    for c in cols[1:]:
        r = r + "|" + d[c].astype(str)
    return r


@register("amt_stats")
def amt_stats(df):
    d = _s(df); g = d.groupby("sender_id")["amount_minor"]
    a = d["amount_minor"].astype(float)
    sh = g.shift(1)
    cnt = g.cumcount()
    cs = g.cumsum() - a
    cs2 = (a ** 2).groupby(d["sender_id"]).cumsum() - a ** 2
    mean = cs / cnt.replace(0, np.nan)
    var = cs2 / cnt.replace(0, np.nan) - mean ** 2
    std = np.sqrt(var.clip(lower=0))
    o = pd.DataFrame({"as_mean": mean, "as_std": std,
                      "as_max": g.cummax().groupby(d["sender_id"]).shift(1),
                      "as_z": (a - mean) / (std + 1.0), "as_prev_ratio": a / (sh + 1.0),
                      "as_prev": sh}, index=d.index)
    return o.reindex(df.index)


@register("newness")
def newness(df):
    d = _s(df); o = pd.DataFrame(index=d.index)
    for n, c in [("addr", "x_addr1"), ("pem", "x_P_emaildomain"), ("dev", "x_DeviceInfo")]:
        pair = d["sender_id"].astype(str) + "|" + d[c].astype(str)
        first = (~pair.duplicated()).astype(int)
        o[f"nw_{n}_first"] = first
        o[f"nw_{n}_nuniq"] = first.groupby(d["sender_id"]).cumsum()
    return o.reindex(df.index)


@register("gaps")
def gaps(df):
    d = _s(df); o = pd.DataFrame(index=d.index)
    for n, cols in [("ca", ("sender_id", "x_addr1")), ("ce", ("sender_id", "x_P_emaildomain")),
                    ("cd", ("sender_id", "x_DeviceInfo"))]:
        o[f"gp_{n}"] = d.groupby(_k(d, *cols))["ts"].diff()
    return o.reindex(df.index)


@register("hour_dev")
def hour_dev(df):
    d = _s(df); h = (d["ts"] // 3600) % 24
    ang = 2 * np.pi * h / 24
    s, c = np.sin(ang), np.cos(ang)
    g = d["sender_id"]
    cnt = s.groupby(g).cumcount()
    ms = (s.groupby(g).cumsum() - s) / cnt.replace(0, np.nan)
    mc = (c.groupby(g).cumsum() - c) / cnt.replace(0, np.nan)
    o = pd.DataFrame({"hd_dev": 1 - (s * ms + c * mc), "hd_conc": np.hypot(ms, mc), "hd_hour": h}, index=d.index)
    return o.reindex(df.index)


@register("d_delta")
def d_delta(df):
    d = _s(df); g = d["sender_id"]; o = pd.DataFrame(index=d.index)
    for i in (1, 2, 3, 4, 5, 10, 15):
        x = pd.to_numeric(d[f"x_D{i}"], errors="coerce")
        o[f"dd_D{i}"] = x - x.groupby(g).shift(1)
    return o.reindex(df.index)


@register("m_share")
def m_share(df):
    d = _s(df); g = d["sender_id"]
    f = pd.concat([(d[f"x_M{i}"].astype(str) == "F") for i in range(1, 10)], axis=1)
    known = pd.concat([d[f"x_M{i}"].notna() for i in range(1, 10)], axis=1)
    fn = f.sum(axis=1).astype(float); kn = known.sum(axis=1).astype(float)
    pf = fn.groupby(g).cumsum() - fn; pk = kn.groupby(g).cumsum() - kn
    return pd.DataFrame({"ms_share": pf / pk.replace(0, np.nan), "ms_cur": fn}, index=d.index).reindex(df.index)
