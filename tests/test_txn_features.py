import numpy as np
import pandas as pd

from agentshield.txn.features import add_history_features, add_uid


def _df():
    return pd.DataFrame(
        {
            "ts": [100, 200, 300, 400, 500],
            "amount_minor": [1000, 3000, 2000, 500, 9000],
            "sender_id": ["a", "a", "b", "a", "b"],
            "label": [0, 1, 0, 0, 1],
        }
    )


def test_history_features_known_values():
    out = add_history_features(_df(), "sender_id", "s")
    assert out["s_prior_cnt"].tolist() == [0, 1, 0, 2, 1]
    # prior mean amount for the 4th row (sender a): (1000 + 3000) / 2
    assert out.loc[3, "s_prior_mean"] == 2000
    assert np.isnan(out.loc[0, "s_prior_mean"])
    assert np.isnan(out.loc[0, "s_since_last"])
    assert out.loc[1, "s_since_last"] == 100
    assert out.loc[3, "s_amt_ratio"] == 500 / 2000


def test_history_features_are_past_only():
    base = _df()
    changed = base.copy()
    changed.loc[4, ["amount_minor", "label"]] = [123456, 0]  # change the last (future) row
    a = add_history_features(base, "sender_id", "s")
    b = add_history_features(changed, "sender_id", "s")
    cols = [c for c in a.columns if c.startswith("s_")]
    pd.testing.assert_frame_equal(a.loc[:3, cols], b.loc[:3, cols])


def test_history_features_never_use_labels():
    base = _df()
    flipped = base.assign(label=1 - base["label"])
    a = add_history_features(base, "sender_id", "s")
    b = add_history_features(flipped, "sender_id", "s")
    cols = [c for c in a.columns if c.startswith("s_")]
    pd.testing.assert_frame_equal(a[cols], b[cols])


def test_history_features_keep_original_row_order():
    df = _df().iloc[::-1].reset_index(drop=True)  # unsorted input
    out = add_history_features(df, "sender_id", "s")
    assert out["ts"].tolist() == [500, 400, 300, 200, 100]
    assert out["s_prior_cnt"].tolist() == [1, 2, 0, 1, 0]


def test_add_uid_groups_same_card_same_first_seen_day():
    df = pd.DataFrame(
        {
            "ts": [86400 * 10, 86400 * 12, 86400 * 12],
            "sender_id": ["1", "1", "1"],
            "x_addr1": [300.0, 300.0, 300.0],
            "x_D1": [5.0, 7.0, 3.0],  # first seen day: 5, 5, 9
        }
    )
    out = add_uid(df)
    assert out.loc[0, "uid"] == out.loc[1, "uid"]
    assert out.loc[0, "uid"] != out.loc[2, "uid"]
