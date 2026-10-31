import pytest

from agentshield.agent_trust.redis_store import RedisNonceStore
from agentshield.agent_trust.verifier import StoreUnavailable


class FakeRedis:
    """Just enough of redis-py: SET with NX and PX, INCR that keeps the key's expiry, and a controllable clock."""

    def __init__(self):
        self.t = 1000.0
        self.d = {}
        self.fail = None

    def _alive(self, k):
        return k in self.d and self.d[k][1] > self.t

    def set(self, name, value, nx=False, px=None):
        if self.fail:
            raise self.fail
        if nx and self._alive(name):
            return None
        self.d[name] = (value, self.t + (px / 1000 if px else 10**9))
        return True

    def incr(self, name):
        if self.fail:
            raise self.fail
        v, exp = self.d[name] if self._alive(name) else (0, self.t + 10**9)
        self.d[name] = (int(v) + 1, exp)
        return int(v) + 1


def test_a_nonce_is_accepted_once_and_a_replay_is_refused():
    s = RedisNonceStore(FakeRedis())
    assert s.check_and_record("key1:nonce-aaaa", 300) is True
    assert s.check_and_record("key1:nonce-aaaa", 300) is False
    assert s.check_and_record("key1:nonce-bbbb", 300) is True


def test_the_same_nonce_under_another_key_id_is_a_different_nonce():
    s = RedisNonceStore(FakeRedis())
    assert s.check_and_record("key1:shared", 300) and s.check_and_record("key2:shared", 300)


def test_a_nonce_expires_after_its_ttl():
    r = FakeRedis()
    s = RedisNonceStore(r)
    s.check_and_record("key1:n", 10)
    r.t += 11
    assert s.check_and_record("key1:n", 10) is True


def test_keys_carry_a_prefix_so_they_do_not_collide_with_other_data():
    r = FakeRedis()
    RedisNonceStore(r, prefix="app1:").check_and_record("key1:n", 10)
    assert any(k.startswith("app1:") for k in r.d) and not any(k == "key1:n" for k in r.d)


def test_a_redis_failure_is_unavailable_never_a_free_pass():
    r = FakeRedis()
    s = RedisNonceStore(r)
    for exc in (ConnectionError("down"), TimeoutError("slow"), RuntimeError("boom")):
        r.fail = exc
        with pytest.raises(StoreUnavailable):
            s.check_and_record("key1:n", 10)


def test_one_key_id_cannot_exhaust_the_quota_for_others():
    s = RedisNonceStore(FakeRedis(), max_per_key=3)
    got = []
    for i in range(5):
        try:
            got.append(s.check_and_record(f"key1:nonce-{i:04d}", 300))
        except StoreUnavailable:
            got.append("quota")
    assert got == [True, True, True, "quota", "quota"]
    assert s.check_and_record("key2:nonce-0001", 300) is True


def test_the_quota_window_resets():
    r = FakeRedis()
    s = RedisNonceStore(r, max_per_key=1, window_ms=1000)
    assert s.check_and_record("key1:a", 300)
    with pytest.raises(StoreUnavailable):
        s.check_and_record("key1:b", 300)
    r.t += 2
    assert s.check_and_record("key1:c", 300)


def test_a_zero_or_negative_ttl_still_expires_quickly_not_never():
    r = FakeRedis()
    s = RedisNonceStore(r)
    s.check_and_record("key1:n", 0)
    r.t += 1
    assert s.check_and_record("key1:n", 0) is True
