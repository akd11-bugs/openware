"""ASGI entry point: `uvicorn agentshield.serving.main:app` (ONE worker process only; feature and result state is in memory).

Environment:
  AGENTSHIELD_API_KEY            required, at least 32 characters (stored only as a hash). No default key exists.
  AGENTSHIELD_TENANTS            optional JSON file listing many tenants, each with sha256 key hashes (first is current, the
                                 rest are old keys still accepted while rotating); replaces AGENTSHIELD_API_KEY
  AGENTSHIELD_MODEL_DIR          model folder (default models/portable_v1)
  AGENTSHIELD_AUDIT              optional path for the JSONL audit log; then AGENTSHIELD_AUDIT_SALT is required too
  AGENTSHIELD_TRUST_CLIENT_TS=1  benchmark/replay only: use the client's timestamp for feature state
  AGENTSHIELD_WARM_START         JSONL of recent past transactions (customer_id, amount_minor, timestamp, optional payee_id and
                                 device_id) loaded into the demo tenant's feature store before serving; capped by
                                 AGENTSHIELD_WARM_START_MAX (default 2,000,000 lines)
Agent trust (all optional; with none of them set, signed agent requests are challenged, never trusted):
  AGENTSHIELD_AGENT_DIRECTORY=1      fetch keys from the key directory named in Signature-Agent (https, public hosts only)
  AGENTSHIELD_REQUIRE_SIGNED_DIRECTORY=1  only trust directory keys that signed the directory response themselves
  AGENTSHIELD_DIRECTORY_ALLOW_UNBOUND_BODY=1  with the above, also accept directories that sign only the request authority (the
                                     profile real directories use today); the body is then not bound by the signature
  AGENTSHIELD_VISA_REGISTRY_DB       path to a registry file shaped like Visa's sample (read only)
  AGENTSHIELD_AGENT_POLICY           JSON: {"authorities": [...], "paths": [...], "tag_routes": {"tag": ["/route"]}}
  AGENTSHIELD_REDIS_URL              keep replay protection (nonces) in Redis; needs the redis package
"""
import json
import os
import re
from pathlib import Path

from agentshield.agent_trust.directory import KeyDirectoryFetcher
from agentshield.agent_trust.redis_store import RedisNonceStore
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Verifier
from agentshield.agent_trust.visa_registry import load_visa_registry

from pydantic import ValidationError

from .app import WarmEvent, create_app
from .tenants import Tenant, TenantRegistry, hash_key


def _agent_policy(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        p = raw if isinstance(raw, dict) else json.loads(raw)
        if not isinstance(p, dict):
            raise ValueError
        return dict(agent_authorities=tuple(p.get("authorities", ())), agent_path_prefixes=tuple(p.get("paths", ())),
                    agent_tag_routes=tuple((t, tuple(r)) for t, r in p.get("tag_routes", {}).items()))
    except (ValueError, AttributeError, TypeError) as e:
        raise RuntimeError("AGENTSHIELD_AGENT_POLICY must be JSON like "
                           '{"authorities": ["shop.example.com"], "paths": ["/product"], "tag_routes": {"tag": ["/route"]}}') from e


TENANT_FIELDS = {"tenant_id", "key_hashes", "challenge_fpr", "block_fpr", "agent"}  # a typo in any other name is an error


def _load_tenants(path) -> list:
    try:
        tenants = []
        for e in json.loads(Path(path).read_text()):
            hs = e["key_hashes"]
            if not set(e) <= TENANT_FIELDS or not isinstance(e["tenant_id"], str) or not e["tenant_id"] or not hs or not all(isinstance(h, str) and re.fullmatch("[0-9a-f]{64}", h) for h in hs):
                raise ValueError
            tenants.append(Tenant(e["tenant_id"], hs[0], tuple(hs[1:]), **{k: e[k] for k in ("challenge_fpr", "block_fpr") if k in e},
                                  **_agent_policy(e.get("agent"))))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise RuntimeError('AGENTSHIELD_TENANTS must be a JSON list like [{"tenant_id": "a", "key_hashes": ["<sha256 hex of the '
                           'current key>", "<old key, while rotating>"]}]; each tenant needs an id and valid hashes') from e
    ids = [t.tenant_id for t in tenants]
    if not tenants or len(set(ids)) != len(ids):
        raise RuntimeError("AGENTSHIELD_TENANTS needs at least one tenant, and each tenant_id must be unique")
    hashes = [h for t in tenants for h in (t.key_hash, *t.old_key_hashes)]
    if len(set(hashes)) != len(hashes):
        raise RuntimeError("an API key hash is used by more than one tenant or listed twice")
    return tenants


def _nonce_store(env, redis_from_url):
    url = env.get("AGENTSHIELD_REDIS_URL")
    if not url:
        return InMemoryNonceStore()
    if redis_from_url is None:
        try:
            import redis
        except ImportError as e:
            raise RuntimeError("AGENTSHIELD_REDIS_URL is set but the redis package is not installed (pip install redis)") from e

        def redis_from_url(u):
            return redis.Redis.from_url(u, socket_timeout=0.5, socket_connect_timeout=0.5)
    return RedisNonceStore(redis_from_url(url))


def load_warm_events(path, limit: int) -> list:
    p = Path(path)
    if not p.is_file():
        raise RuntimeError("AGENTSHIELD_WARM_START file not found, cannot warm the feature store")
    events = []
    with open(p) as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            if len(events) >= limit:
                raise RuntimeError(f"warm start file has more than the allowed at most {limit} events")
            try:
                events.append(WarmEvent(**json.loads(line)))
            except (ValueError, ValidationError, TypeError):  # never echo the line: it may hold customer data
                raise RuntimeError(f"warm start file: invalid event on line {n}") from None
    return events


def build(env=None, redis_from_url=None):
    env = os.environ if env is None else env
    if env.get("WEB_CONCURRENCY", "1") not in ("", "1"):
        raise RuntimeError("run a single worker process: feature and result state is per process (Redis only shares nonces)")
    if env.get("AGENTSHIELD_TENANTS"):
        tenants = _load_tenants(env["AGENTSHIELD_TENANTS"])
    else:
        key = env.get("AGENTSHIELD_API_KEY", "")
        if len(key) < 32:
            raise RuntimeError("set AGENTSHIELD_API_KEY to a random string of at least 32 characters")
        try:
            tenants = [Tenant("demo", hash_key(key), **_agent_policy(env.get("AGENTSHIELD_AGENT_POLICY")))]
        except ValueError as e:
            raise RuntimeError(f"invalid agent policy: {e}") from e
    tenant = tenants[0]
    registry, directory = KeyRegistry(), None
    if env.get("AGENTSHIELD_VISA_REGISTRY_DB"):
        registry, _report = load_visa_registry(env["AGENTSHIELD_VISA_REGISTRY_DB"])
    if env.get("AGENTSHIELD_AGENT_DIRECTORY") == "1":
        directory = KeyDirectoryFetcher(require_signed=env.get("AGENTSHIELD_REQUIRE_SIGNED_DIRECTORY") == "1",
                                        bind_body=env.get("AGENTSHIELD_DIRECTORY_ALLOW_UNBOUND_BODY") != "1")
    verifier = None
    if env.get("AGENTSHIELD_VISA_REGISTRY_DB") or directory is not None:
        verifier = Verifier(registry, _nonce_store(env, redis_from_url), Policy(), directory=directory)
    audit = env.get("AGENTSHIELD_AUDIT")
    app = create_app(Path(env.get("AGENTSHIELD_MODEL_DIR", "models/portable_v1")), TenantRegistry(tenants), verifier,
                     audit_path=Path(audit) if audit else None, audit_salt=env.get("AGENTSHIELD_AUDIT_SALT"),
                     trust_client_ts=env.get("AGENTSHIELD_TRUST_CLIENT_TS") == "1")
    if env.get("AGENTSHIELD_WARM_START"):
        app.state.warm(tenant.tenant_id, load_warm_events(env["AGENTSHIELD_WARM_START"], int(env.get("AGENTSHIELD_WARM_START_MAX", 2_000_000))))
    return app


app = build()
