"""Online/offline parity: the real-time store must give the same feature values as the batch code, row for row."""
import numpy as np
import pandas as pd
import pytest

from agentshield.features_online.store import ALL_FEATURE_NAMES as FEATURE_NAMES
from agentshield.features_online.store import OnlineFeatureStore, replay
from agentshield.txn import experiment as E
from agentshield.txn import fintech_features  # noqa: F401


def make(n=1500, seed=2, senders=40, receivers=12, step=700):
    rng = np.random.default_rng(seed)
    ts = np.sort(86400 + rng.integers(0, n * step, n))  # many ties on purpose
    df = pd.DataFrame({
        "sender_id": rng.choice([f"s{i}" for i in range(senders)], n),
        "receiver_id": rng.choice([f"m{i}" for i in range(receivers)], n),
        "ts": ts, "amount_minor": rng.integers(100, 500_000, n), "label": 0,
        "x_DeviceInfo": rng.choice([f"dev{i}" for i in range(25)] + [None] * 8, n),
        "x_ProductCD": rng.choice(["W", "C", "H", "R", "S", "zzz"], n),
        "x_card6": rng.choice(["debit", "credit", "debit or credit", None], n),
        "x_card4": rng.choice(["visa", "mastercard", "discover", "american express", None], n),
    })
    return df.reset_index(drop=True)


def batch(df):
    out = []
    for g in ("velocity_amt", "amt_ratio_30d", "payee_risk", "sender_hist", "roll_counts", "device_share", "instrument"):
        out.append(E.FEATURE_GROUPS[g][0](df))
    return pd.concat(out, axis=1)


def test_online_features_equal_batch_features_row_for_row():
    df = make()
    online = replay(df)
    want = batch(df)
    for c in FEATURE_NAMES:
        np.testing.assert_allclose(online[c].to_numpy(dtype=float), want[c].to_numpy(dtype=float),
                                   rtol=1e-9, atol=1e-9, equal_nan=True, err_msg=c)


def test_features_do_not_include_the_event_being_scored():
    s = OnlineFeatureStore()
    first = s.features("a", "m", 1000, 5000)
    assert first["amt_sum_1h"] == 0 and first["payee_cnt_24h"] == 0 and first["payee_new_for_sender"] == 1
    s.update("a", "m", 1000, 5000)
    again = s.features("a", "m", 1000, 5000)  # same timestamp: still excluded
    assert again["amt_sum_1h"] == 0
    later = s.features("a", "m", 1001, 5000)
    assert later["amt_sum_1h"] == 5000 and later["payee_new_for_sender"] == 0


def test_old_events_are_evicted_so_memory_is_bounded():
    s = OnlineFeatureStore(retention=30 * 86400)
    for i in range(2000):
        s.update("a", "m", i * 86400, 100)
    assert s.size() < 40  # about 31 days of events kept for one sender, plus its payee log


def test_breaking_the_window_is_caught_by_the_parity_test(monkeypatch):
    df = make(400)
    import agentshield.features_online.store as st

    monkeypatch.setattr(st, "HOUR", 3000)  # corrupt the 1h window
    online = replay(df)
    want = batch(df)
    with pytest.raises(AssertionError):
        np.testing.assert_allclose(online["amt_sum_1h"].to_numpy(dtype=float), want["amt_sum_1h"].to_numpy(dtype=float))


def test_state_is_capped_by_lru_and_empty_keys_are_dropped():
    s = OnlineFeatureStore(max_senders=100, max_pairs=150)
    for i in range(1000):
        s.update(f"s{i}", f"m{i}", i * 10, 100)
    assert len(s._hist) <= 100 and len(s._pair_first) <= 150
    s2 = OnlineFeatureStore(retention=50)
    s2.update("a", None, 0, 1)
    s2.update("a", None, 100_000, 1)  # the first event is evicted; the key must not linger when emptied
    s2._by_sender.evict_before("a", 10**9)
    assert "a" not in s2._by_sender._ts


def test_store_exposes_the_device_and_instrument_features():
    new = ["device_new_for_sender", "sender_distinct_devices", "device_uniq_senders", "channel_code", "instrument_code", "network_code"]
    assert set(new) <= set(FEATURE_NAMES)
