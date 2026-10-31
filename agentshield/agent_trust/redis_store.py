"""Nonce store backed by Redis, so replay protection holds across worker processes and restarts.

Uses only SET key value NX PX ttl (atomic check and record) and INCR, through any redis-py compatible client:
    RedisNonceStore(redis.Redis.from_url(url))
Any client error raises StoreUnavailable, which the verifier turns into "unverified": a Redis outage can never
become a free pass. A per key id counter, created with a TTL, caps how many nonces one key can mint per window.
Tested here against a fake client that mimics those commands; it has not been run against a live Redis server.
"""
from .verifier import StoreUnavailable


class RedisNonceStore:
    def __init__(self, client, prefix: str = "agentshield:nonce:", max_per_key: int = 10_000, window_ms: int = 600_000) -> None:
        self.client, self.prefix, self.max_per_key, self.window_ms = client, prefix, max_per_key, window_ms

    def check_and_record(self, key: str, ttl: int) -> bool:
        counter = f"{self.prefix}count:{key.split(':', 1)[0]}"
        try:
            if not self.client.set(self.prefix + key, "1", nx=True, px=max(int(ttl * 1000), 1)):
                return False  # already seen: a replay
            self.client.set(counter, 0, nx=True, px=self.window_ms)  # creates the counter WITH its expiry
            used = self.client.incr(counter)
        except Exception as e:  # noqa: BLE001 - connection, timeout, anything: fail closed
            raise StoreUnavailable("nonce store error") from e
        if used > self.max_per_key:
            raise StoreUnavailable("per-key nonce quota reached")
        return True
