import numpy as np
import pandas as pd
import pytest

from agentshield.graph.account_features import GRAPH_FEATURES, account_graph_features


def frame(rows):
    """rows: (sender, receiver, ts, amount, label)"""
    df = pd.DataFrame(rows, columns=["sender_id", "receiver_id", "ts", "amount_minor", "label"])
    return df.sort_values("ts", kind="stable").reset_index(drop=True)


BASE = [("A", "B", 10, 100, 0), ("B", "C", 20, 90, 0), ("D", "B", 30, 50, 0), ("B", "E", 40, 200, 0)]


def test_receiver_side_counts_sums_age_and_pass_through():
    f = account_graph_features(frame(BASE))
    r = f.iloc[2]  # D -> B at t=30: B received 100 at t=10 and sent 90 at t=20
    assert (r["rcv_in_cnt"], r["rcv_in_sum"], r["rcv_out_cnt"], r["rcv_out_sum"]) == (1, 100, 1, 90)
    assert r["rcv_age_s"] == 20 and r["rcv_pass_through"] == pytest.approx(0.9)


def test_sender_side_history():
    f = account_graph_features(frame(BASE))
    r = f.iloc[3]  # B -> E at t=40
    assert (r["snd_in_cnt"], r["snd_in_sum"], r["snd_out_cnt"], r["snd_out_sum"]) == (2, 150, 1, 90)


def test_new_accounts_have_zero_history_and_no_age():
    f = account_graph_features(frame(BASE))
    assert f.iloc[0][["rcv_in_cnt", "rcv_out_cnt", "snd_in_cnt", "snd_out_cnt"]].tolist() == [0, 0, 0, 0]
    assert np.isnan(f.iloc[0]["rcv_age_s"]) and np.isnan(f.iloc[0]["rcv_pass_through"])


def test_events_at_the_same_timestamp_are_not_prior():
    f = account_graph_features(frame([("A", "B", 10, 100, 0), ("C", "B", 10, 70, 0), ("D", "B", 20, 5, 0)]))
    assert f["rcv_in_cnt"].tolist() == [0, 0, 2] and f["rcv_in_sum"].tolist() == [0, 0, 170]


def test_confirmed_fraud_is_only_known_after_the_label_delay():
    rows = [("X", "B", 100, 500, 1), ("Y", "B", 150, 10, 0), ("Z", "B", 250, 10, 0)]
    f = account_graph_features(frame(rows), fraud_delay=100)
    assert f["rcv_known_fraud_in"].tolist() == [0, 0, 1]  # known from t=200; the t=150 query is too early


def test_features_are_causal():
    rng = np.random.default_rng(1)
    rows = [(f"a{rng.integers(0, 15)}", f"a{rng.integers(0, 15)}", int(i * 30), int(rng.integers(10, 900)), 0) for i in range(300)]
    df = frame(rows)
    a = account_graph_features(df)
    ch = df.copy()
    ch.loc[200:, "amount_minor"] = 999_999
    ch.loc[200:, "receiver_id"] = ch.loc[200:, "receiver_id"].to_numpy()[::-1]
    b = account_graph_features(ch)
    pd.testing.assert_frame_equal(a.iloc[:200], b.iloc[:200])


def test_requires_time_sorted_input_and_exposes_feature_names():
    with pytest.raises(ValueError):
        account_graph_features(frame(BASE).iloc[::-1])
    assert set(GRAPH_FEATURES) == set(account_graph_features(frame(BASE)).columns)
