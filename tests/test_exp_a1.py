import importlib.util
from pathlib import Path

import pandas as pd
import pytest
from agentshield.txn.experiment import FEATURE_GROUPS

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data/cache/ieee_std.pkl"
# These checks need the real IEEE-CIS cache, which is not in the repo (data/ is ignored), so they skip on a fresh clone.
pytestmark = pytest.mark.skipif(not CACHE.exists(), reason="needs data/cache/ieee_std.pkl (run scripts/experiment.py --prepare)")

spec = importlib.util.spec_from_file_location("a1g", ROOT / "experiments/a1/groups.py")
spec.loader.exec_module(importlib.util.module_from_spec(spec))


@pytest.mark.parametrize("g", ["amt_stats", "newness", "gaps", "hour_dev", "d_delta", "m_share"])
def test_past_only(g):
    df = pd.read_pickle(CACHE).iloc[:3000].copy()
    f = FEATURE_GROUPS[g][0]
    a = f(df).iloc[:1500]
    d2 = df.copy()
    d2.iloc[1500:, d2.columns.get_loc("amount_minor")] *= 7
    d2.iloc[1500:, d2.columns.get_loc("label")] = 1 - d2.iloc[1500:]["label"]
    b = f(d2).iloc[:1500]
    pd.testing.assert_frame_equal(a, b)
