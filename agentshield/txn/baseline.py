"""Transparent rules-only baselines: what a fintech runs today without ML.

Rule thresholds are learned from the training period only; scoring never reads labels.
Score = number of rules fired + a small amount-percentile term to break ties.
"""
import numpy as np
import pandas as pd

DAY = 86400


def _prior_window_counts(ts: np.ndarray, sender: np.ndarray, window: int = DAY) -> np.ndarray:
    """For each row: how many earlier rows (strictly earlier ts) by the same sender in the last `window` s."""
    out = np.zeros(len(ts), dtype="int64")
    order = np.lexsort((ts, sender))
    s_sorted, t_sorted = sender[order], ts[order]
    bounds = np.flatnonzero(np.r_[True, s_sorted[1:] != s_sorted[:-1], True])
    for a, b in zip(bounds[:-1], bounds[1:]):
        t = t_sorted[a:b]
        out[order[a:b]] = np.searchsorted(t, t, "left") - np.searchsorted(t, t - window, "left")
    return out


class IeeeCisBaseline:
    def __init__(
        self,
        categorical_cols: list[str],
        amount_q: float = 0.99,
        velocity_q: float = 0.99,
        risk_ratio: float = 2.0,
        min_support: int = 500,
    ):
        self.categorical_cols = categorical_cols
        self.amount_q, self.velocity_q = amount_q, velocity_q
        self.risk_ratio, self.min_support = risk_ratio, min_support

    def _velocity(self, df: pd.DataFrame) -> np.ndarray:
        h = self._hist
        past = h[h["ts"] < df["ts"].min()]
        ts = np.concatenate([past["ts"].to_numpy(), df["ts"].to_numpy()])
        sd = np.concatenate([past["sender_id"].to_numpy(), df["sender_id"].to_numpy()])
        return _prior_window_counts(ts, sd)[len(past):]

    def fit(self, train: pd.DataFrame) -> "IeeeCisBaseline":
        self._hist = train[["ts", "sender_id"]].copy()
        self._amounts = np.sort(train["amount_minor"].to_numpy())
        self.amount_thr = float(np.quantile(self._amounts, self.amount_q))
        v = _prior_window_counts(train["ts"].to_numpy(), train["sender_id"].to_numpy())
        self.velocity_thr = max(2.0, float(np.quantile(v, self.velocity_q)))
        base = train["label"].mean()
        self.risky: dict[str, set] = {}
        for c in self.categorical_cols:
            g = train.groupby(c, dropna=False)["label"].agg(["mean", "size"])
            hit = g[(g["size"] >= self.min_support) & (g["mean"] >= self.risk_ratio * base)]
            self.risky[c] = set(hit.index)
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        amt = df["amount_minor"].to_numpy()
        score = (amt >= self.amount_thr).astype(float)
        score += (self._velocity(df) >= self.velocity_thr).astype(float)
        for c in self.categorical_cols:
            score += df[c].isin(self.risky[c]).to_numpy().astype(float)
        pct = np.searchsorted(self._amounts, amt, "right") / len(self._amounts)
        return score + 0.5 * pct


class PaySimBaseline:
    """Fraud in PaySim lives in TRANSFER and CASH_OUT; balance-drain is the classic rule.

    `use_balance_fields=False` drops the balance columns, which nearly give fraud away.
    """

    RISKY_TYPES = {"TRANSFER", "CASH_OUT"}

    def __init__(self, amount_q: float = 0.99, use_balance_fields: bool = True):
        self.amount_q, self.use_balance_fields = amount_q, use_balance_fields

    def fit(self, train: pd.DataFrame) -> "PaySimBaseline":
        self._amounts = np.sort(train["amount_minor"].to_numpy())
        self.amount_thr = float(np.quantile(self._amounts, self.amount_q))
        return self

    def score(self, df: pd.DataFrame) -> np.ndarray:
        amt = df["amount_minor"].to_numpy()
        score = df["x_type"].isin(self.RISKY_TYPES).to_numpy().astype(float)
        score += (amt >= self.amount_thr).astype(float)
        if self.use_balance_fields:
            drained = (df["x_oldbalanceOrg"].to_numpy() > 0) & (df["x_newbalanceOrig"].to_numpy() == 0)
            score += drained.astype(float)
        pct = np.searchsorted(self._amounts, amt, "right") / len(self._amounts)
        return score + 0.5 * pct
