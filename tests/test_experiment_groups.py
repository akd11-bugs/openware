import numpy as np
import pandas as pd
import pytest

from agentshield.txn import experiment as E


def _df(n=300, seed=0):
    r = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "ts": np.sort(r.integers(86400, 86400 * 40, n)),
            "amount_minor": r.integers(100, 50_000, n),
            "sender_id": r.choice(["1", "2", "3", "4", "5"], n),
            "label": r.integers(0, 2, n),
            "x_addr1": r.choice([100.0, 200.0, np.nan], n),
            "x_D1": r.integers(0, 30, n).astype(float),
            "x_P_emaildomain": r.choice(["a.com", "b.com", None], n),
            "x_R_emaildomain": r.choice(["a.com", "b.com", None], n),
            "x_DeviceInfo": r.choice(["d1", "d2", None], n),
        }
    )


# Built-in groups only: agent-added groups register into the same registry when their own test files
# import them, and are tested there against real columns.
CAUSAL = ["card_hist", "addr_hist", "email_hist", "device_hist", "roll_counts", "amt_cents"]


@pytest.mark.parametrize("name", CAUSAL)
def test_causal_groups_do_not_use_future_rows_or_labels(name):
    fn = E.FEATURE_GROUPS[name][0]
    base = _df()
    changed = base.copy()
    changed.loc[200:, ["amount_minor", "label"]] = [999_999, 1 - base.loc[200:, "label"].iloc[0]]
    a = fn(base).iloc[:200]
    b = fn(changed).iloc[:200]
    pd.testing.assert_frame_equal(a, b)
    flipped = fn(base.assign(label=1 - base["label"]))
    pd.testing.assert_frame_equal(fn(base), flipped)


def test_noncausal_group_is_flagged_and_marks_result_non_production():
    assert E.FEATURE_GROUPS["W_freq"][1] is False
    _, prod = E.build_matrix(_df(), ["card_hist", "W_freq"])
    assert prod is False
    _, prod2 = E.build_matrix(_df(), ["card_hist"])
    assert prod2 is True


def test_evaluate_never_returns_test_metrics_keys():
    # evaluate() output must only carry validation metrics
    keys = {"val_auc", "val_recall@1%", "val_recall@0.1%", "val_logloss"}
    src = open(E.__file__).read()
    assert all(k in src for k in keys)
    assert '"test_' not in src
