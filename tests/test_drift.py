import numpy as np
import pandas as pd
import pytest

from agentshield.monitoring.drift import alert_rate_check, feature_drift, psi


def test_identical_distributions_have_zero_psi():
    x = np.random.default_rng(0).normal(size=5000)
    assert psi(x, x) == pytest.approx(0.0, abs=1e-9)


def test_same_distribution_resampled_is_stable():
    r = np.random.default_rng(1)
    assert psi(r.normal(size=20000), r.normal(size=20000)) < 0.02


def test_shifted_distribution_is_major_drift():
    r = np.random.default_rng(2)
    assert psi(r.normal(size=5000), r.normal(loc=2.0, size=5000)) > 0.25


def test_nan_values_are_ignored_and_empty_gives_nan():
    r = np.random.default_rng(3)
    x = np.concatenate([r.normal(size=1000), [np.nan] * 50])
    assert psi(x, x) == pytest.approx(0.0, abs=1e-9)
    assert np.isnan(psi(np.array([np.nan]), x))


def test_binary_feature_with_same_share_is_stable_and_changed_share_is_flagged():
    r = np.random.default_rng(6)
    ref = (r.random(10000) < 0.08).astype(float)
    same = (r.random(10000) < 0.08).astype(float)
    moved = (r.random(10000) < 0.30).astype(float)
    assert psi(ref, same) < 0.02
    assert psi(ref, moved) > 0.25


def test_constant_reference_column():
    assert psi(np.ones(100), np.ones(50)) == 0.0
    assert psi(np.ones(100), np.full(50, 7.0)) >= 0.25


def test_feature_drift_table_ranks_and_labels():
    r = np.random.default_rng(4)
    ref = pd.DataFrame({"a": r.normal(size=3000), "b": r.normal(size=3000)})
    cur = pd.DataFrame({"a": r.normal(size=3000), "b": r.normal(loc=3, size=3000)})
    t = feature_drift(ref, cur)
    assert t.index[0] == "b" and t.loc["b", "status"] == "major" and t.loc["a", "status"] == "stable"


def test_missing_share_change_is_reported():
    ref = pd.DataFrame({"a": [1.0, 2.0, 3.0, 4.0] * 100})
    cur = pd.DataFrame({"a": [1.0, np.nan, np.nan, 4.0] * 100})
    assert feature_drift(ref, cur).loc["a", "missing_change"] == pytest.approx(0.5)


def test_alert_rate_check_flags_a_budget_breach():
    ok = alert_rate_check(observed=0.052, expected=0.05)
    bad = alert_rate_check(observed=0.15, expected=0.05)
    assert ok["status"] == "ok" and bad["status"] == "breach" and bad["ratio"] == pytest.approx(3.0)
