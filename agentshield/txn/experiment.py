"""Experiment harness: named feature groups + model registry + a validation-only evaluation.

Agents add feature groups / models in their own module (loaded with --extra) and call register().
`causal=True` groups must use only earlier rows and never labels (see tests/test_experiment_groups.py).
`causal=False` groups (full-period aggregates, winner-style) are reported as non-production.
The test period is never evaluated here; see scripts/final_eval.py.
"""
import time
from typing import Callable

import numpy as np
import pandas as pd

from .baseline import _prior_window_counts
from .features import add_history_features, add_uid
from .metrics import auc, recall_at_fpr
from .split import time_split

GAP = 7 * 86400
FEATURE_GROUPS: dict[str, tuple[Callable[[pd.DataFrame], pd.DataFrame], bool]] = {}
MODELS: dict[str, Callable] = {}


def register(name: str, causal: bool = True):
    def deco(fn):
        FEATURE_GROUPS[name] = (fn, causal)
        return fn
    return deco


def register_model(name: str):
    def deco(fn):
        MODELS[name] = fn
        return fn
    return deco


def _hist(df: pd.DataFrame, key: pd.Series, prefix: str) -> pd.DataFrame:
    tmp = df[["ts", "amount_minor"]].copy()
    tmp["_k"] = key.astype(str).to_numpy()
    out = add_history_features(tmp, "_k", prefix)
    return out[[c for c in out.columns if c.startswith(prefix + "_")]]


@register("card_hist")
def _card_hist(df):
    return _hist(df, df["sender_id"], "card")


@register("addr_hist")
def _addr_hist(df):
    return _hist(df, df["sender_id"].astype(str) + "_" + df["x_addr1"].astype(str), "addr")


@register("email_hist")
def _email_hist(df):
    return _hist(df, df["x_P_emaildomain"], "email")


@register("device_hist")
def _device_hist(df):
    return _hist(df, df["x_DeviceInfo"], "dev")


@register("roll_counts")
def _roll_counts(df):
    ts, sd = df["ts"].to_numpy(), df["sender_id"].to_numpy()
    return pd.DataFrame(
        {f"card_cnt_{w}s": _prior_window_counts(ts, sd, w) for w in (3600, 86400, 604800)},
        index=df.index,
    )


@register("amt_cents")
def _amt_cents(df):
    a = df["amount_minor"]
    return pd.DataFrame({"amt_cents": a % 100, "amt_whole": a // 100, "amt_round": (a % 100 == 0).astype(int)}, index=df.index)


@register("W_freq", causal=False)
def _w_freq(df):
    """Winner-style: full-period frequency encodings and uid aggregates (uses future rows; label-free)."""
    d = add_uid(df)
    out = {}
    for c in ["sender_id", "x_addr1", "x_P_emaildomain", "x_R_emaildomain", "x_DeviceInfo", "uid"]:
        out[f"wfreq_{c}"] = d[c].map(d[c].value_counts()).astype("float64")
    g = d.groupby("uid")["amount_minor"]
    out["w_uid_amt_mean"] = g.transform("mean")
    out["w_uid_amt_std"] = g.transform("std")
    out["w_uid_amt_ratio"] = d["amount_minor"] / out["w_uid_amt_mean"]
    return pd.DataFrame(out, index=df.index)


# Fields an Indian UPI/wallet feed realistically has: amount, time of day, a coarse device type.
# Everything else in IEEE-CIS (card type, US email domains, Vesta's C/D/M/V columns, id_*) is not portable.
PORTABLE_BASE = ["amount_minor", "hour", "dow", "x_DeviceType"]


def base_matrix(df: pd.DataFrame, portable: bool = False) -> pd.DataFrame:
    X = df.drop(columns=["txn_id", "label", "source", "sender_id", "receiver_id", "uid"], errors="ignore").copy()
    num = pd.to_numeric(df["sender_id"], errors="coerce")
    if num.notna().all():  # IEEE-CIS card1 is a numeric field; other feeds' ids (e.g. CUST123) are not features
        X["card1_num"] = num.astype("int64")
    X["hour"] = (df["ts"] // 3600) % 24
    X["dow"] = (df["ts"] // 86400) % 7
    X = X.drop(columns=["ts"])  # raw time cannot generalise to a later period
    if portable:
        X = X[[c for c in PORTABLE_BASE if c in X.columns]]
    for c in X.columns:
        if not (pd.api.types.is_numeric_dtype(X[c]) or pd.api.types.is_bool_dtype(X[c])):
            X[c] = X[c].astype("category")
    return X


def build_matrix(df: pd.DataFrame, groups: list[str], portable: bool = False) -> tuple[pd.DataFrame, bool]:
    X = base_matrix(df, portable)
    production_valid = True
    for g in groups:
        fn, causal = FEATURE_GROUPS[g]
        production_valid &= causal
        new = fn(df).replace([np.inf, -np.inf], np.nan)
        X = pd.concat([X, new], axis=1)
    return X.loc[:, ~X.columns.duplicated()], production_valid


@register_model("lgbm")
def _lgbm(Xf, yf, Xv, yv, params, n_jobs, seed):
    import lightgbm as lgb

    p = dict(objective="binary", learning_rate=0.05, num_leaves=128, min_child_samples=60,
             feature_fraction=0.5, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
             n_jobs=n_jobs, verbose=-1, seed=seed)
    p.update(params)
    dfit = lgb.Dataset(Xf, yf)
    dval = lgb.Dataset(Xv, yv, reference=dfit)
    m = lgb.train(p, dfit, num_boost_round=3000, valid_sets=[dval],
                  callbacks=[lgb.early_stopping(100, verbose=False)])
    return (lambda X: m.predict(X, num_iteration=m.best_iteration)), m.best_iteration


def logloss(y, p, eps=1e-7):
    p = np.clip(p, eps, 1 - eps)
    return float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean())


def evaluate(df: pd.DataFrame, groups: list[str], params: dict | None = None, model: str = "lgbm",
             n_jobs: int = 3, seed: int = 7, portable: bool = False) -> dict:
    """Fit on the early slice, score the VALIDATION slice only. The test slice is never touched."""
    t0 = time.perf_counter()
    X, prod = build_matrix(df, groups, portable)
    y = df["label"].to_numpy()
    tr_all, _test = time_split(df, 0.7, gap=GAP)  # test rows are discarded here on purpose
    fit, val = time_split(tr_all, 0.7857, gap=GAP)
    predict, iters = MODELS[model](X.loc[fit.index], y[fit.index], X.loc[val.index], y[val.index], params or {}, n_jobs, seed)
    s = predict(X.loc[val.index])
    yv = y[val.index]
    rows = X.loc[val.index].iloc[:150]
    lat = []
    for i in range(len(rows)):  # single-row scoring latency (pandas overhead included; an upper bound)
        t = time.perf_counter()
        predict(rows.iloc[[i]])
        lat.append((time.perf_counter() - t) * 1000)
    return {
        "model": model, "groups": groups, "params": params or {}, "features": X.shape[1],
        "portable": portable, "production_valid": prod, "iters": iters, "val_rows": len(yv),
        "val_auc": round(auc(yv, s), 5), "val_recall@1%": round(recall_at_fpr(yv, s, 0.01), 4),
        "val_recall@0.1%": round(recall_at_fpr(yv, s, 0.001), 4), "val_logloss": round(logloss(yv, s), 5),
        "latency_ms_p50": round(float(np.percentile(lat, 50)), 2), "latency_ms_p99": round(float(np.percentile(lat, 99)), 2),
        "seconds": round(time.perf_counter() - t0, 1),
    }
