"""Drift monitoring: has the live data moved away from what the model was calibrated on?

PSI (population stability index) per feature, using the usual rule of thumb: below 0.1 stable, 0.1 to 0.25
moderate, above 0.25 major. Also checks the alert rate against the tenant's false-positive budget, because a
jump in alerts is often the first visible symptom (cold start, a new merchant, a broken feed).
"""
import numpy as np
import pandas as pd


def psi(expected, actual, bins: int = 10, eps: float = 1e-4) -> float:
    e = np.asarray(expected, dtype="float64")
    a = np.asarray(actual, dtype="float64")
    e, a = e[~np.isnan(e)], a[~np.isnan(a)]
    if len(e) == 0 or len(a) == 0:
        return float("nan")
    if len(np.unique(e)) <= bins:  # yes/no or few-valued feature: compare the share of each value
        vals = np.unique(np.concatenate([e, a]))
        pe = np.clip(np.array([(e == v).mean() for v in vals]), eps, None)
        pa = np.clip(np.array([(a == v).mean() for v in vals]), eps, None)
        return float(np.sum((pa - pe) * np.log(pa / pe)))
    edges = np.unique(np.quantile(e, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:  # many distinct values but quantiles collapse (a huge spike at one value)
        return 0.0 if np.all(a == e[0]) else 1.0
    edges[0], edges[-1] = -np.inf, np.inf
    pe = np.clip(np.histogram(e, edges)[0] / len(e), eps, None)
    pa = np.clip(np.histogram(a, edges)[0] / len(a), eps, None)
    return float(np.sum((pa - pe) * np.log(pa / pe)))


def _status(v: float) -> str:
    return "unknown" if np.isnan(v) else "stable" if v < 0.1 else "moderate" if v < 0.25 else "major"


def feature_drift(reference: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    rows = {}
    for c in reference.columns:
        v = psi(reference[c], current[c])
        rows[c] = {"psi": v, "status": _status(v),
                   "missing_change": float(current[c].isna().mean() - reference[c].isna().mean())}
    return pd.DataFrame(rows).T.astype({"psi": float, "missing_change": float}).sort_values("psi", ascending=False)


def alert_rate_check(observed: float, expected: float, tolerance: float = 1.5) -> dict:
    """Compare the share of traffic being challenged/blocked with the budget it was calibrated for."""
    ratio = observed / expected if expected > 0 else float("inf")
    return {"observed": observed, "expected": expected, "ratio": ratio,
            "status": "ok" if ratio <= tolerance else "breach"}
