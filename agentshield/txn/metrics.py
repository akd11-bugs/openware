"""Evaluation metrics. All take label array y (0/1) and score array s (higher = riskier)."""
import numpy as np
import pandas as pd


def _check_scores(s: np.ndarray) -> None:
    if np.isnan(np.asarray(s, dtype="float64")).any():
        raise ValueError("scores contain NaN")


def auc(y: np.ndarray, s: np.ndarray) -> float:
    """ROC AUC via ranks (Mann-Whitney U); ties get average rank."""
    _check_scores(s)
    y = np.asarray(y)
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("need both classes")
    ranks = pd.Series(np.asarray(s)).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def recall_at_fpr(y: np.ndarray, s: np.ndarray, fpr: float) -> float:
    """Share of fraud flagged when the alert threshold lets at most `fpr` of legit through.

    Flags are strictly above the threshold, so ties never inflate recall.
    """
    if not 0.0 < fpr < 1.0:
        raise ValueError("fpr must be in (0, 1)")
    _check_scores(s)
    y = np.asarray(y)
    s = np.asarray(s, dtype="float64")
    neg, pos = s[y == 0], s[y == 1]
    if len(neg) == 0 or len(pos) == 0:
        raise ValueError("need both classes")
    thr = np.quantile(neg, 1.0 - fpr)
    return float((pos > thr).mean())


def precision_at_k(y: np.ndarray, s: np.ndarray, k: int) -> float:
    order = np.argsort(-np.asarray(s, dtype="float64"), kind="stable")[:k]
    return float(np.asarray(y)[order].mean())
