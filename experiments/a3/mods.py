"""a3 NON-PRODUCTION: full-period label-free aggregates + diverse models."""
import numpy as np, pandas as pd
from agentshield.txn.experiment import register, register_model


def _uid(df):
    from agentshield.txn.experiment import add_uid
    return add_uid(df)["uid"]


@register("uid_aggs", causal=False)
def uid_aggs(df):
    u = _uid(df)
    out = pd.DataFrame(index=df.index)
    out["uid_cnt"] = u.map(u.value_counts()).astype("float32")
    cols = [c for c in ["x_D4", "x_D9", "x_D10", "x_D15", "amount_minor"] if c in df]
    cols += [c for c in df.columns if c in [f"x_C{i}" for i in range(1, 15)]]
    for c in cols:
        x = pd.to_numeric(df[c], errors="coerce")
        g = x.groupby(u)
        out[f"uid_{c}_mean"] = g.transform("mean").astype("float32")
        if c in ("amount_minor", "x_D15", "x_D4"):
            out[f"uid_{c}_std"] = g.transform("std").astype("float32")
    for c in [f"x_M{i}" for i in range(1, 10)]:
        if c in df:
            m = df[c].astype(object).map({"T": 1.0, "F": 0.0, 1: 1.0, 0: 0.0, True: 1.0, False: 0.0})
            out[f"uid_{c}_mean"] = m.astype(float).groupby(u).transform("mean").astype("float32")
    return out


@register("fp_counts", causal=False)
def fp_counts(df):
    out = pd.DataFrame(index=df.index)
    for c in ["sender_id","x_card2","x_addr1","x_P_emaildomain","x_R_emaildomain","x_DeviceInfo"]:
        if c in df:
            out[f"fp_{c}"] = df[c].map(df[c].value_counts()).astype("float32")
    return out


@register("amt_dollars", causal=False)
def amt_dollars(df):
    a = df["amount_minor"] / 100
    return pd.DataFrame({"amt_floor": np.floor(a), "amt_dec": (a - np.floor(a)).round(3)}, index=df.index)


def _codes(X):
    X = X.copy()
    for c in X.columns:
        if str(X[c].dtype) == "category":
            X[c] = X[c].cat.codes
    return X


@register_model("xgb")
def xgb_m(Xf, yf, Xv, yv, params, n_jobs, seed):
    import xgboost as xgb
    p = dict(n_estimators=3000, learning_rate=0.03, max_depth=8, subsample=0.8, colsample_bytree=0.5,
             min_child_weight=3, tree_method="hist", enable_categorical=True, n_jobs=n_jobs,
             random_state=seed, early_stopping_rounds=100, eval_metric="auc")
    p.update(params or {})
    m = xgb.XGBClassifier(**p).fit(Xf, yf, eval_set=[(Xv, yv)], verbose=False)
    return (lambda X: m.predict_proba(X)[:, 1]), int(m.best_iteration + 1)


@register_model("cat")
def cat_m(Xf, yf, Xv, yv, params, n_jobs, seed):
    from catboost import CatBoostClassifier
    p = dict(iterations=3000, learning_rate=0.06, depth=8, thread_count=n_jobs, random_seed=seed,
             eval_metric="AUC", early_stopping_rounds=100, verbose=0)
    p.update(params or {})
    m = CatBoostClassifier(**p).fit(_codes(Xf), yf, eval_set=(_codes(Xv), yv))
    return (lambda X: m.predict_proba(_codes(X))[:, 1]), int(m.get_best_iteration() + 1)


@register_model("blend")
def blend(Xf, yf, Xv, yv, params, n_jobs, seed):
    import lightgbm as lgb
    from scipy.stats import rankdata
    p = dict(n_estimators=3000, learning_rate=0.03, num_leaves=128, subsample=0.8, subsample_freq=1,
             colsample_bytree=0.5, n_jobs=n_jobs, random_state=seed, verbose=-1)
    ml = lgb.LGBMClassifier(**p).fit(Xf, yf, eval_set=[(Xv, yv)], eval_metric="auc",
                                     callbacks=[lgb.early_stopping(100, verbose=False)])
    fx, it_x = xgb_m(Xf, yf, Xv, yv, {}, n_jobs, seed)
    fc, it_c = cat_m(Xf, yf, Xv, yv, {}, n_jobs, seed)
    fl = lambda X: ml.predict_proba(X)[:, 1]
    fs = [fl, fx, fc]

    def pred(X):
        return np.mean([f(X) for f in fs], axis=0)
    return pred, int(ml.best_iteration_ or 0) + it_x + it_c
