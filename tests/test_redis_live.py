"""Runs the Redis nonce store against a real server. Skipped unless AGENTSHIELD_TEST_REDIS_URL is set."""
import os
import time
import uuid

import pytest

from agentshield.agent_trust.redis_store import RedisNonceStore
from agentshield.agent_trust.verifier import StoreUnavailable

URL = os.environ.get("AGENTSHIELD_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set AGENTSHIELD_TEST_REDIS_URL to a throwaway Redis")


def store(prefix=None, **kw):
    import redis
    return RedisNonceStore(redis.Redis.from_url(URL, socket_timeout=0.5), prefix=prefix or f"t{uuid.uuid4().hex}:", **kw)


def test_two_workers_share_replay_protection():
    a = store("shared:" + uuid.uuid4().hex + ":")
    b = RedisNonceStore(a.client.__class__.from_url(URL), prefix=a.prefix)
    assert a.check_and_record("k1:n1", 60) is True
    assert b.check_and_record("k1:n1", 60) is False


def test_a_nonce_expires_after_its_ttl():
    s = store()
    assert s.check_and_record("k:n", 1) and not s.check_and_record("k:n", 1)
    time.sleep(1.2)
    assert s.check_and_record("k:n", 1)


def test_the_per_key_quota_counter_has_an_expiry_and_the_cap_holds():
    s = store(max_per_key=2)
    assert s.check_and_record("k:a", 60) and s.check_and_record("k:b", 60)
    assert 0 < s.client.pttl(f"{s.prefix}count:k") <= s.window_ms
    with pytest.raises(StoreUnavailable):
        s.check_and_record("k:c", 60)


def test_an_unreachable_redis_fails_closed():
    import redis
    dead = RedisNonceStore(redis.Redis(port=1, socket_timeout=0.2, socket_connect_timeout=0.2))
    with pytest.raises(StoreUnavailable):
        dead.check_and_record("k:n", 60)
