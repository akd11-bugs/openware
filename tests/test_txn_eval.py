import numpy as np
import pandas as pd
import pytest

from agentshield.txn.metrics import auc, precision_at_k, recall_at_fpr
from agentshield.txn.baseline import IeeeCisBaseline, PaySimBaseline


def test_auc_perfect_and_random_and_inverted():
    y = np.array([0, 0, 1, 1])
    assert auc(y, np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert auc(y, np.array([0.9, 0.8, 0.2, 0.1])) == 0.0
    assert auc(y, np.array([0.5, 0.5, 0.5, 0.5])) == 0.5


def test_recall_at_fpr_counts_only_strictly_above_threshold():
    y = np.array([0] * 100 + [1] * 10)
    s = np.concatenate([np.arange(100) / 100.0, np.linspace(1.0, 1.5, 10)])
    # at 1% FPR exactly one negative may be flagged: threshold = 98th-index negative score
    r = recall_at_fpr(y, s, 0.01)
    assert r == 1.0


def test_recall_at_fpr_ties_do_not_inflate():
    y = np.array([0] * 90 + [1] * 10)
    s = np.zeros(100)  # all tied: nothing may be flagged without exceeding the FPR cap
    assert recall_at_fpr(y, s, 0.01) == 0.0


def test_recall_at_fpr_rejects_bad_inputs():
    with pytest.raises(ValueError):
        recall_at_fpr(np.array([0, 1]), np.array([0.1, 0.2]), 0.0)
    with pytest.raises(ValueError):
        recall_at_fpr(np.array([0, 0]), np.array([0.1, 0.2]), 0.01)


def test_precision_at_k():
    y = np.array([1, 0, 1, 0, 0])
    s = np.array([0.9, 0.8, 0.7, 0.1, 0.0])
    assert precision_at_k(y, s, 2) == 0.5
    assert precision_at_k(y, s, 3) == pytest.approx(2 / 3)


def _paysim_train():
    return pd.DataFrame(
        {
            "amount_minor": [1000, 2000, 3000, 4000, 1_000_000] * 20,
            "label": [0, 0, 0, 0, 1] * 20,
            "x_type": ["PAYMENT", "PAYMENT", "TRANSFER", "CASH_OUT", "TRANSFER"] * 20,
            "x_oldbalanceOrg": [5000, 5000, 5000, 9000, 1_000_000] * 20,
            "x_newbalanceOrig": [4000, 3000, 2000, 5000, 0] * 20,
        }
    )


def test_paysim_baseline_scores_fraud_shape_higher():
    train = _paysim_train()
    m = PaySimBaseline(use_balance_fields=True).fit(train)
    s = m.score(train)
    assert s[train["label"] == 1].min() > s[train["label"] == 0].max()


def test_paysim_baseline_without_balance_ignores_balance_columns():
    train = _paysim_train()
    m = PaySimBaseline(use_balance_fields=False).fit(train)
    a = m.score(train)
    b = m.score(train.assign(x_oldbalanceOrg=0, x_newbalanceOrig=0))
    assert np.array_equal(a, b)


def test_ieee_baseline_uses_only_train_stats_and_past_rows_for_velocity():
    n = 400
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            "ts": np.sort(rng.integers(86400, 86400 * 30, n)),
            "amount_minor": rng.integers(100, 10_000, n),
            "sender_id": rng.choice(["a", "b", "c", "d"], n),
            "label": rng.integers(0, 2, n),
            "x_ProductCD": rng.choice(["W", "C"], n),
            "x_P_emaildomain": rng.choice(["x.com", "y.com"], n),
        }
    )
    train, test = df.iloc[:300], df.iloc[300:]
    m = IeeeCisBaseline(categorical_cols=["x_ProductCD", "x_P_emaildomain"]).fit(train)
    s1 = m.score(test)
    # changing test labels must not change scores (no label peeking at scoring time)
    s2 = m.score(test.assign(label=1 - test["label"]))
    assert np.array_equal(s1, s2)
    assert len(s1) == len(test)
