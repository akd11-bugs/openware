"""Per-customer (tenant) settings and API-key authentication.

Each tenant picks its own false-alarm budgets. The model's score thresholds for those budgets are calibrated on
the validation slice and stored in the model card, so a tenant says "challenge the riskiest 5%, block the
riskiest 0.5% of good traffic" instead of tuning raw scores. API keys are stored only as SHA-256 hashes.
"""
import hashlib
import hmac
from dataclasses import dataclass


def hash_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    key_hash: str
    challenge_fpr: float = 0.05  # score above the threshold calibrated to this false-positive rate -> challenge
    block_fpr: float = 0.005  # ... -> block
    spike_ratio: float = 10.0  # online amount-spike flag: amount >= this many times the sender's average
    burst_per_hour: int = 8  # online burst flag: this many earlier transactions in the last hour
    # Agent trust is opt-in per tenant. Without agent_authorities, signed agent requests are not trusted at all.
    agent_authorities: tuple = ()  # lower-case hosts the tenant's agents may act on, compared exactly
    agent_path_prefixes: tuple = ()  # paths agents may act on; empty means any path on those hosts
    agent_tag_routes: tuple = ()  # ((signature tag, (route prefix, ...)), ...): which tag may reach which route

    def __post_init__(self) -> None:
        if not 0 < self.block_fpr <= self.challenge_fpr < 1:
            raise ValueError("need 0 < block_fpr <= challenge_fpr < 1")
        if any((not isinstance(a, str)) or not a or a != a.lower() for a in self.agent_authorities):
            raise ValueError("agent_authorities must be non-empty lower-case hosts")
        if any((not isinstance(p, str)) or not p.startswith("/") for p in self.agent_path_prefixes):
            raise ValueError("agent_path_prefixes must start with a slash")
        for tag, prefixes in self.agent_tag_routes:
            if not tag or any(not p.startswith("/") for p in prefixes):
                raise ValueError("agent_tag_routes needs a tag and route prefixes that start with a slash")

    def agent_policy(self):
        from agentshield.agent_trust.verifier import Policy

        return Policy(tag_routes={tag: tuple(prefixes) for tag, prefixes in self.agent_tag_routes})


class TenantRegistry:
    def __init__(self, tenants: list[Tenant]) -> None:
        self._tenants = list(tenants)

    @property
    def tenants(self) -> list:
        return list(self._tenants)

    def authenticate(self, api_key: str | None) -> Tenant | None:
        if not api_key:
            return None
        h = hash_key(api_key)
        found = None
        for t in self._tenants:  # compare against every tenant so timing does not reveal which one matched
            if hmac.compare_digest(t.key_hash, h):
                found = t
        return found
