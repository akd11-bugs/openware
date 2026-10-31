"""XGBoost, CatBoost and a probability-average blend, registered next to the LightGBM model.

All early-stop on the VALIDATION slice only. Categorical columns are passed as integer codes (-1 = missing)
so every library sees the same matrix.
"""
import numpy as np
import pandas as pd

from .experiment import MODELS, register_model

LGBM_KEYS = {
    "learning_rate", "num_leaves", "min_child_samples", "feature_fraction", "bagging_fraction", "lambda_l1",
    "lambda_l2", "max_bin", "min_gain_to_split", "min_sum_hessian_in_leaf", "scale_pos_weight",
}


def _codes(X: pd.DataFrame) -> pd.DataFrame:
    out = X.copy()
    for c in out.columns:
        if isinstance(out[c].dtype, pd.CategoricalDtype):
            out[c] = out[c].cat.codes.astype("int32")
    return out


@register_model("xgb")
def xgb_model(Xf, yf, Xv, yv, params, n_jobs, seed):
    import xgboost as xgb

    Xf, Xv = _codes(Xf), _codes(Xv)
    p = dict(objective="binary:logistic", eval_metric="auc", learning_rate=0.05, max_depth=9, min_child_weight=5,
             subsample=0.8, colsample_bytree=0.4, reg_lambda=2.0, tree_method="hist", nthread=n_jobs, seed=seed)
    m = xgb.train(p, xgb.DMatrix(Xf, yf), num_boost_round=2000, evals=[(xgb.DMatrix(Xv, yv), "val")],
                  early_stopping_rounds=100, verbose_eval=False)
    best = m.best_iteration + 1
    return (lambda X: m.predict(xgb.DMatrix(_codes(X)), iteration_range=(0, best))), best


@register_model("cat")
def cat_model(Xf, yf, Xv, yv, params, n_jobs, seed):
    from catboost import CatBoostClassifier

    Xf, Xv = _codes(Xf), _codes(Xv)
    m = CatBoostClassifier(iterations=1500, learning_rate=0.08, depth=8, l2_leaf_reg=5, eval_metric="AUC",
                           thread_count=n_jobs, random_seed=seed, verbose=False, early_stopping_rounds=100)
    m.fit(Xf, yf, eval_set=(Xv, yv))
    return (lambda X: m.predict_proba(_codes(X))[:, 1]), int(m.get_best_iteration() or 0)


@register_model("blend")
def blend_model(Xf, yf, Xv, yv, params, n_jobs, seed):
    """Equal-weight mean of LightGBM, XGBoost and CatBoost probabilities. `params` go to LightGBM."""
    lgb_p = {k: v for k, v in (params or {}).items() if k in LGBM_KEYS}
    parts = [MODELS["lgbm"](Xf, yf, Xv, yv, lgb_p, n_jobs, seed),
             MODELS["xgb"](Xf, yf, Xv, yv, {}, n_jobs, seed),
             MODELS["cat"](Xf, yf, Xv, yv, {}, n_jobs, seed)]
    fns = [p[0] for p in parts]
    return (lambda X: np.mean([f(X) for f in fns], axis=0)), sum(p[1] for p in parts)
