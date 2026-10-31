import numpy as np
import pandas as pd

from agentshield.txn import experiment as E


def _df(n=60):
    r = np.random.default_rng(0)
    return pd.DataFrame(
        {
            "txn_id": range(n), "ts": np.sort(r.integers(86400, 86400 * 20, n)),
            "amount_minor": r.integers(100, 9000, n), "sender_id": r.choice(["1", "2"], n),
            "receiver_id": [None] * n, "label": r.integers(0, 2, n), "source": "ieee_cis",
            "x_C1": r.integers(0, 5, n), "x_D1": r.integers(0, 9, n).astype(float),
            "x_M1": r.choice(["T", "F"], n), "x_V12": r.random(n), "x_card4": r.choice(["visa", "mc"], n),
            "x_P_emaildomain": r.choice(["gmail.com", "yahoo.com"], n), "x_id_30": r.choice(["ios", "android"], n),
            "x_DeviceType": r.choice(["mobile", "desktop"], n), "x_ProductCD": r.choice(["W", "C"], n),
        }
    )


def test_portable_matrix_keeps_only_fields_an_upi_feed_would_have():
    X = E.base_matrix(_df(), portable=True)
    assert set(X.columns) <= {"amount_minor", "hour", "dow", "x_DeviceType"}
    assert {"amount_minor", "hour", "dow"} <= set(X.columns)
    for banned in ["x_C1", "x_D1", "x_M1", "x_V12", "x_card4", "x_P_emaildomain", "x_id_30", "x_ProductCD", "card1_num"]:
        assert banned not in X.columns


def test_full_matrix_still_has_everything():
    X = E.base_matrix(_df())
    assert {"x_C1", "x_card4", "card1_num", "hour", "dow"} <= set(X.columns)
    assert "ts" not in X.columns


def test_build_matrix_portable_still_adds_history_groups():
    X, prod = E.build_matrix(_df(), ["card_hist"], portable=True)
    assert prod is True
    assert "card_prior_cnt" in X.columns and "x_C1" not in X.columns


def test_blend_and_single_models_are_registered():
    from agentshield.txn import models  # noqa: F401

    assert {"lgbm", "xgb", "cat", "blend"} <= set(E.MODELS)
