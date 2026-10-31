"""Fail-closed verifier for signed AI-agent requests (RFC 9421 style, Ed25519).

Three outcomes, never allow on failure:
  verified    -> allow (agent tier 'verified_agent')
  invalid     -> block  (crypto, time window, replay, policy or revocation failure)
  unverified  -> challenge (we could not check: key directory or nonce store unavailable, unknown key)
Pure deterministic code: no ML, no network. Closes the gaps found in docs/research/visa-tap-and-web-bot-auth-review.md.
"""
import base64
import hashlib
import hmac
import re
import threading
import time
from dataclasses import dataclass, field

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .directory import DirectoryError
from .signer import component_line

ALLOWED_COMPONENTS = {"@method", "@authority", "@path", "@query", "content-digest", "signature-agent"}
SUPPORTED_ALGS = {"ed25519", "rsa-pss-sha256"}
MIN_RSA_BITS = 2048
MANDATORY_PARAMS = ("created", "expires", "nonce", "keyid", "alg", "tag")
_INPUT = re.compile(r'^([a-z][a-z0-9_-]{0,15})=\(((?:"[@a-z0-9_-]+"(?: "[@a-z0-9_-]+")*)?)\)((?:;[a-z]+=(?:\d{1,12}|"[A-Za-z0-9._~-]{1,64}"))*)\Z')
_SIG = re.compile(r"^([a-z][a-z0-9_-]{0,15})=:([A-Za-z0-9+/]+={0,2}):\Z")
_SIGAGENT = re.compile(r'^(?:[a-z][a-z0-9_-]{0,15}=)?"(https://[^"\s]{1,2000})"\Z')
_DIGEST = re.compile(r"^sha-256=:([A-Za-z0-9+/]{43}=):\Z")
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
    body: bytes | None = None  # the exact bytes received; a non-empty body must be covered by a content-digest


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
    loaded: object = None  # the parsed public key, checked once at registration


class KeyRegistry:
    """Key ids are globally unique (the Visa sample's per-agent uniqueness allowed key-id squatting)."""

    def __init__(self) -> None:
        self._keys: dict[str, KeyRecord] = {}
        self._agents_active: dict[str, bool] = {}
        self.available = True

    def register(self, agent_id: str, keyid: str, alg: str, public_key: bytes) -> None:
        if keyid in self._keys:
            raise ValueError("key id already registered")
        if alg == "ed25519":
            if len(public_key) != 32:
                raise ValueError("ed25519 public key must be 32 raw bytes")
            loaded = Ed25519PublicKey.from_public_bytes(public_key)
        elif alg == "rsa-pss-sha256":
            try:
                loaded = serialization.load_der_public_key(public_key)
            except Exception as e:  # noqa: BLE001
                raise ValueError("invalid RSA public key (expected DER SubjectPublicKeyInfo)") from e
            if not isinstance(loaded, rsa.RSAPublicKey):
                raise ValueError("not an RSA public key")
            if loaded.key_size < MIN_RSA_BITS:
                raise ValueError(f"RSA keys must be at least {MIN_RSA_BITS} bits")
        else:
            raise ValueError(f"unsupported algorithm {alg}")
        self._keys[keyid] = KeyRecord(agent_id, keyid, alg, public_key, loaded=loaded)
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
    def __init__(self, registry: KeyRegistry, nonces: InMemoryNonceStore, policy: Policy | None = None, clock=time.time,
                 directory=None):
        self.registry, self.nonces, self.policy, self.clock = registry, nonces, policy or Policy(), clock
        self.directory = directory  # a KeyDirectoryFetcher; when set, requests carrying Signature-Agent use it

    def verify(self, req: Request) -> Result:
        try:
            return self._verify(req)
        except (KeyDirectoryUnavailable, DirectoryError):
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
        if params["alg"] not in SUPPORTED_ALGS:
            return Result("invalid", "unsupported_alg")
        if not isinstance(params["created"], int) or not isinstance(params["expires"], int) or not _NONCE.match(str(params["nonce"])):
            return Result("invalid", "malformed_signature")
        if any(c not in ALLOWED_COMPONENTS for c in covered):
            return Result("invalid", "unknown_component")
        if not set(pol.required_components) <= set(covered):
            return Result("invalid", "components_missing")
        if req.body and "content-digest" not in covered:
            return Result("invalid", "body_not_covered")  # a body the signature does not bind could be swapped
        agent_url = None
        if self.directory is not None and "signature-agent" in h:
            m_sa = _SIGAGENT.match(h["signature-agent"])
            if not m_sa:
                return Result("invalid", "bad_signature_agent")
            if "signature-agent" not in covered:
                return Result("invalid", "signature_agent_not_covered")  # otherwise a swapped directory would go unnoticed
            agent_url = m_sa.group(1)
        digest_header = h.get("content-digest", "")
        if "content-digest" in covered:
            m_d = _DIGEST.match(digest_header)
            if not m_d:
                return Result("invalid", "bad_content_digest")
            actual = hashlib.sha256(req.body or b"").digest()
            if not hmac.compare_digest(base64.b64decode(m_d.group(1)), actual):
                return Result("invalid", "digest_mismatch")
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
        if agent_url is not None:  # keys are looked up by (directory, thumbprint), never by a bare key id
            entry = self.directory.keys(agent_url).get(params["keyid"])
            rec = KeyRecord(agent_url, params["keyid"], entry[0], entry[2], loaded=entry[1]) if entry else None
        else:
            rec = self.registry.get(params["keyid"])
        if rec is None:
            return Result("unverified", "unknown_key", tier="unknown")
        if not rec.active:
            return Result("invalid", "key_revoked", rec.agent_id)
        if rec.alg != params["alg"]:
            return Result("invalid", "alg_mismatch", rec.agent_id)
        base = "\n".join([component_line(c, req.method, req.authority, req.path, req.query.lstrip("?"), digest_header, h.get("signature-agent", "")) for c in covered]
                         + [f'"@signature-params": {sig_in.split("=", 1)[1]}'])
        try:
            sig_bytes = base64.b64decode(m_sig.group(2))
            if rec.alg == "ed25519":
                rec.loaded.verify(sig_bytes, base.encode())
            else:  # rsa-pss-sha256; the salt length is not pinned because signers differ (hash length or maximum)
                rec.loaded.verify(sig_bytes, base.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO),
                                  hashes.SHA256())
        except (InvalidSignature, ValueError):
            return Result("invalid", "bad_signature", rec.agent_id)
        # Record the nonce only after the signature is valid, so a forger cannot burn a victim's nonce.
        if not self.nonces.check_and_record(f"{rec.keyid}:{params['nonce']}", params["expires"] - int(now) + pol.skew):
            return Result("invalid", "replayed_nonce", rec.agent_id)
        return Result("verified", "ok", rec.agent_id, "verified_agent")
