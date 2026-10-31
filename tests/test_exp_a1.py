import importlib.util
import pandas as pd
import pytest
from agentshield.txn.experiment import FEATURE_GROUPS

spec = importlib.util.spec_from_file_location("a1g", "experiments/a1/groups.py")
spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.mark.parametrize("g", ["amt_stats", "newness", "gaps", "hour_dev", "d_delta", "m_share"])
def test_past_only(g):
    df = pd.read_pickle("data/cache/ieee_std.pkl").iloc[:3000].copy()
    f = FEATURE_GROUPS[g][0]
    a = f(df).iloc[:1500]
    d2 = df.copy()
    d2.iloc[1500:, d2.columns.get_loc("amount_minor")] *= 7
    d2.iloc[1500:, d2.columns.get_loc("label")] = 1 - d2.iloc[1500:]["label"]
    b = f(d2).iloc[:1500]
    pd.testing.assert_frame_equal(a, b)
