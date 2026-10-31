"""Fail-closed verifier for signed AI-agent requests (RFC 9421 style, Ed25519).

Three outcomes, never allow on failure:
  verified    -> allow (agent tier 'verified_agent')
  invalid     -> block  (crypto, time window, replay, policy or revocation failure)
  unverified  -> challenge (we could not check: key directory or nonce store unavailable, unknown key)
Pure deterministic code: no ML, no network. Closes the gaps found in docs/research/visa-tap-and-web-bot-auth-review.md.
"""
import base64
import re
import threading
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .signer import component_line

ALLOWED_COMPONENTS = {"@method", "@authority", "@path", "@query"}
MANDATORY_PARAMS = ("created", "expires", "nonce", "keyid", "alg", "tag")
_INPUT = re.compile(r'^([a-z][a-z0-9_-]{0,15})=\(((?:"[@a-z0-9_-]+"(?: "[@a-z0-9_-]+")*)?)\)((?:;[a-z]+=(?:\d{1,12}|"[A-Za-z0-9._~-]{1,64}"))*)\Z')
_SIG = re.compile(r"^([a-z][a-z0-9_-]{0,15})=:([A-Za-z0-9+/]+={0,2}):\Z")
_NONCE = re.compile(r"^[A-Za-z0-9._~-]{8,64}\Z")


class StoreUnavailable(Exception):
    pass


class KeyDirectoryUnavailable(Exception):
    pass


@dataclass
class Request:
    method: str
    authority: str
    path: str  # without query string
    headers: dict
    query: str = ""


@dataclass
class Policy:
    required_components: tuple = ("@method", "@authority", "@path")
    max_lifetime: int = 480  # seconds; the Visa-style window, far below the 24 h ceiling in the draft
    skew: int = 30
    tag_routes: dict = field(default_factory=dict)  # tag -> allowed route prefixes; empty = tags not route-bound
    public_prefixes: tuple = ()  # everything else is default-deny


@dataclass
class Result:
    outcome: str
    reason: str
    agent_id: str | None = None
    tier: str = "none"

    @property
    def action(self) -> str:
        return {"verified": "allow", "not_required": "allow", "unverified": "challenge"}.get(self.outcome, "block")


@dataclass
class KeyRecord:
    agent_id: str
    keyid: str
    alg: str
    public_key: bytes
    active: bool = True


class KeyRegistry:
    """Key ids are globally unique (the Visa sample's per-agent uniqueness allowed key-id squatting)."""

    def __init__(self) -> None:
        self._keys: dict[str, KeyRecord] = {}
        self._agents_active: dict[str, bool] = {}
        self.available = True

    def register(self, agent_id: str, keyid: str, alg: str, public_key: bytes) -> None:
        if keyid in self._keys:
            raise ValueError("key id already registered")
        if alg != "ed25519" or len(public_key) != 32:
            raise ValueError("ed25519 public key must be 32 raw bytes")
        self._keys[keyid] = KeyRecord(agent_id, keyid, alg, public_key)
        self._agents_active.setdefault(agent_id, True)

    def revoke_key(self, keyid: str) -> None:
        self._keys[keyid].active = False

    def revoke_agent(self, agent_id: str) -> None:
        self._agents_active[agent_id] = False

    def get(self, keyid: str) -> KeyRecord | None:
        if not self.available:
            raise KeyDirectoryUnavailable()
        rec = self._keys.get(keyid)
        if rec is not None and not (rec.active and self._agents_active.get(rec.agent_id, False)):
            return KeyRecord(rec.agent_id, rec.keyid, rec.alg, rec.public_key, active=False)
        return rec


class InMemoryNonceStore:
    """Atomic check-and-record, bounded overall and per key id (one agent key cannot exhaust the store for others).
    Production swaps in Redis `SET key NX PX ttl` behind the same method."""

    def __init__(self, max_entries: int = 100_000, max_per_key: int = 10_000, clock=time.time) -> None:
        self._by: dict[str, dict[str, float]] = {}
        self._n = 0
        self._max, self._max_per_key, self._clock, self._lock = max_entries, max_per_key, clock, threading.Lock()

    def _purge(self, d: dict, now: float) -> None:
        for k in [k for k, v in d.items() if v <= now]:
            del d[k]
            self._n -= 1

    def check_and_record(self, key: str, ttl: int) -> bool:
        with self._lock:
            now = self._clock()
            d = self._by.setdefault(key.split(":", 1)[0], {})
            if d.get(key, 0) > now:
                return False
            if len(d) >= self._max_per_key:
                self._purge(d, now)
                if len(d) >= self._max_per_key:
                    raise StoreUnavailable("per-key nonce quota reached")
            if self._n >= self._max:
                for dd in self._by.values():
                    self._purge(dd, now)
                if self._n >= self._max:
                    raise StoreUnavailable("nonce store full")  # fail closed rather than evict and allow replay
            if key not in d:
                self._n += 1
            d[key] = now + max(ttl, 1)
            return True


class Verifier:
    def __init__(self, registry: KeyRegistry, nonces: InMemoryNonceStore, policy: Policy | None = None, clock=time.time):
        self.registry, self.nonces, self.policy, self.clock = registry, nonces, policy or Policy(), clock

    def verify(self, req: Request) -> Result:
        try:
            return self._verify(req)
        except KeyDirectoryUnavailable:
            return Result("unverified", "key_directory_unavailable", tier="unknown")
        except StoreUnavailable:
            return Result("unverified", "nonce_store_unavailable", tier="unknown")
        except Exception:  # noqa: BLE001 - any unexpected error blocks; never allow
            return Result("invalid", "internal_error")

    def _verify(self, req: Request) -> Result:
        pol = self.policy
        if any(req.path.startswith(p) for p in pol.public_prefixes):
            return Result("not_required", "public_route")
        h = {k.lower(): v for k, v in req.headers.items()}
        sig_in, sig = h.get("signature-input"), h.get("signature")
        if not sig_in or not sig:
            return Result("invalid", "signature_missing")
        if "," in sig_in or "," in sig:
            return Result("invalid", "multiple_signatures")
        m_in, m_sig = _INPUT.match(sig_in), _SIG.match(sig)
        if not m_in or not m_sig or m_in.group(1) != m_sig.group(1):
            return Result("invalid", "malformed_signature")
        covered = re.findall(r'"([^"]+)"', m_in.group(2))
        params: dict = {}
        for item in filter(None, m_in.group(3).split(";")):
            k, v = item.split("=", 1)
            if k in params:
                return Result("invalid", "malformed_signature")
            params[k] = int(v) if v.isdigit() else v.strip('"')
        for name in MANDATORY_PARAMS:
            if name not in params:
                return Result("invalid", f"missing_param:{name}")
        if params["alg"] != "ed25519":
            return Result("invalid", "unsupported_alg")
        if not isinstance(params["created"], int) or not isinstance(params["expires"], int) or not _NONCE.match(str(params["nonce"])):
            return Result("invalid", "malformed_signature")
        if any(c not in ALLOWED_COMPONENTS for c in covered):
            return Result("invalid", "unknown_component")
        if not set(pol.required_components) <= set(covered):
            return Result("invalid", "components_missing")
        if pol.tag_routes:
            prefixes = pol.tag_routes.get(params["tag"])
            if prefixes is None:
                return Result("invalid", "unknown_tag")
            if not any(req.path.startswith(p) for p in prefixes):
                return Result("invalid", "tag_route_mismatch")
        now = self.clock()
        if params["created"] > now + pol.skew:
            return Result("invalid", "created_in_future")
        if params["expires"] <= params["created"]:
            return Result("invalid", "expires_before_created")
        if params["expires"] - params["created"] > pol.max_lifetime:
            return Result("invalid", "lifetime_too_long")
        if now > params["expires"]:
            return Result("invalid", "expired")
        rec = self.registry.get(params["keyid"])
        if rec is None:
            return Result("unverified", "unknown_key", tier="unknown")
        if not rec.active:
            return Result("invalid", "key_revoked", rec.agent_id)
        if rec.alg != params["alg"]:
            return Result("invalid", "alg_mismatch", rec.agent_id)
        base = "\n".join([component_line(c, req.method, req.authority, req.path, req.query.lstrip("?")) for c in covered]
                         + [f'"@signature-params": {sig_in.split("=", 1)[1]}'])
        try:
            Ed25519PublicKey.from_public_bytes(rec.public_key).verify(base64.b64decode(m_sig.group(2)), base.encode())
        except (InvalidSignature, ValueError):
            return Result("invalid", "bad_signature", rec.agent_id)
        # Record the nonce only after the signature is valid, so a forger cannot burn a victim's nonce.
        if not self.nonces.check_and_record(f"{rec.keyid}:{params['nonce']}", params["expires"] - int(now) + pol.skew):
            return Result("invalid", "replayed_nonce", rec.agent_id)
        return Result("verified", "ok", rec.agent_id, "verified_agent")
