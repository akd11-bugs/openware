import numpy as np
import pandas as pd
import pytest

from agentshield.anomaly.txn import AnomalyConfig, detect, fit_peer_thresholds

DAY, MIN = 86400, 60


def frame(rows):
    """rows: (customer, ts, amount_rupees[, receiver])"""
    df = pd.DataFrame(rows, columns=["customer_id", "ts", "amount", "receiver_id"][: len(rows[0])])
    df["amount_minor"] = (df["amount"] * 100).round().astype("int64")
    if "receiver_id" not in df:
        df["receiver_id"] = None
    df["label"] = 0
    return df.sort_values("ts", kind="stable").reset_index(drop=True)


CFG = AnomalyConfig(min_hist=5, limits_minor=[50_000_00], structuring_k=3)


def normal_history(cust="c1", n=8, amount=1000.0):
    return [(cust, DAY * (i + 1), amount + (i % 3) * 50) for i in range(n)]


def test_amount_far_above_customers_norm_is_flagged():
    df = frame(normal_history() + [("c1", DAY * 20, 60_000.0)])
    out = detect(df, CFG)
    assert out["AMT_OUTLIER"].iloc[-1]
    assert not out["AMT_OUTLIER"].iloc[:-1].any()


def test_normal_amount_is_not_flagged():
    df = frame(normal_history() + [("c1", DAY * 20, 1100.0)])
    assert not detect(df, CFG)["AMT_OUTLIER"].any()


def test_amount_outlier_needs_enough_history():
    df = frame(normal_history(n=2) + [("c1", DAY * 20, 60_000.0)])
    assert not detect(df, CFG)["AMT_OUTLIER"].any()


def test_peer_baseline_covers_customers_with_thin_history():
    fit = frame([(f"p{i}", DAY * (i % 5 + 1), 1000.0 + i) for i in range(200)])
    th = fit_peer_thresholds(fit.assign(grp="A"), "grp", q=0.99)
    df = frame([("new", DAY * 30, 90_000.0)]).assign(grp="A")
    out = detect(df, CFG, peer_col="grp", peer_thresholds=th)
    assert out["AMT_OUTLIER"].iloc[0]


def test_structuring_just_under_limit_repeated_is_flagged():
    base = normal_history()
    s = [("c1", DAY * 20 + i * 3600, 49_000.0 + i * 100) for i in range(4)]
    out = detect(frame(base + s), CFG)
    assert out["STRUCTURING"].iloc[-1]
    assert not out["STRUCTURING"].iloc[:8].any()


def test_single_payment_just_under_limit_is_not_structuring():
    out = detect(frame(normal_history() + [("c1", DAY * 20, 49_500.0)]), CFG)
    assert not out["STRUCTURING"].any()


def test_structuring_amounts_far_under_limit_ignored():
    s = [("c1", DAY * 20 + i * 3600, 20_000.0) for i in range(5)]
    assert not detect(frame(normal_history() + s), CFG)["STRUCTURING"].any()


def test_structuring_spread_over_many_days_not_flagged():
    s = [("c1", DAY * (20 + 3 * i), 49_500.0) for i in range(4)]
    assert not detect(frame(normal_history() + s), CFG)["STRUCTURING"].any()


def test_rapid_identical_repeat_is_flagged_second_time():
    r = [("c1", DAY * 20, 2500.0), ("c1", DAY * 20 + 90, 2500.0)]
    out = detect(frame(normal_history() + r), CFG)
    assert not out["RAPID_REPEAT"].iloc[-2]
    assert out["RAPID_REPEAT"].iloc[-1]


def test_same_amount_hours_apart_is_not_a_repeat():
    r = [("c1", DAY * 20, 2500.0), ("c1", DAY * 20 + 5 * 3600, 2500.0)]
    assert not detect(frame(normal_history() + r), CFG)["RAPID_REPEAT"].any()


def test_burst_of_many_transactions_is_flagged():
    b = [("c1", DAY * 20 + i * 20, 100.0 + i * 13) for i in range(6)]  # different amounts, 20 s apart
    assert detect(frame(normal_history() + b), CFG)["RAPID_REPEAT"].iloc[-1]


def test_new_payee_with_large_amount_is_flagged():
    h = [("c1", DAY * (i + 1), 1000.0, "r1") for i in range(6)]
    out = detect(frame(h + [("c1", DAY * 20, 9000.0, "r-new")]), CFG)
    assert out["NEW_PAYEE_HIGH"].iloc[-1]


def test_known_payee_or_small_amount_is_not_flagged():
    h = [("c1", DAY * (i + 1), 1000.0, "r1") for i in range(6)]
    out = detect(frame(h + [("c1", DAY * 20, 9000.0, "r1"), ("c1", DAY * 21, 1100.0, "r-new")]), CFG)
    assert not out["NEW_PAYEE_HIGH"].any()


def test_no_receiver_column_values_disables_new_payee_detector():
    out = detect(frame(normal_history() + [("c1", DAY * 20, 60_000.0)]), CFG)
    assert not out["NEW_PAYEE_HIGH"].any()


def test_flags_are_past_only_and_label_free():
    rng = np.random.default_rng(3)
    rows = [(f"c{i % 6}", int(DAY * (1 + t / 6)), float(rng.integers(100, 5000)), f"r{rng.integers(0, 4)}")
            for i, t in enumerate(range(240))]
    df = frame(rows)
    a = detect(df, CFG)
    changed = df.copy()
    changed.loc[150:, "amount_minor"] = changed.loc[150:, "amount_minor"].to_numpy()[::-1] * 7
    changed["label"] = 1 - changed["label"]
    b = detect(changed, CFG)
    cols = ["AMT_OUTLIER", "STRUCTURING", "RAPID_REPEAT", "NEW_PAYEE_HIGH"]
    pd.testing.assert_frame_equal(a.loc[:149, cols], b.loc[:149, cols])


def test_score_and_reasons_reflect_flags():
    df = frame(normal_history() + [("c1", DAY * 20, 60_000.0)])
    out = detect(df, CFG)
    assert out["anomaly_score"].iloc[-1] >= 1
    assert "AMT_OUTLIER" in out["reasons"].iloc[-1]
    assert out["reasons"].iloc[0] == ""


def test_input_must_be_time_sorted():
    df = frame(normal_history()).iloc[::-1]
    with pytest.raises(ValueError):
        detect(df, CFG)
