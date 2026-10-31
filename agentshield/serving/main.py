"""ASGI entry point: `uvicorn agentshield.serving.main:app` (ONE worker process only; feature and result state is in memory).

Environment:
  AGENTSHIELD_API_KEY            required, at least 32 characters (stored only as a hash). No default key exists.
  AGENTSHIELD_MODEL_DIR          model folder (default models/portable_v1)
  AGENTSHIELD_AUDIT              optional path for the JSONL audit log; then AGENTSHIELD_AUDIT_SALT is required too
  AGENTSHIELD_TRUST_CLIENT_TS=1  benchmark/replay only: use the client's timestamp for feature state
Agent trust (all optional; with none of them set, signed agent requests are challenged, never trusted):
  AGENTSHIELD_AGENT_DIRECTORY=1      fetch keys from the key directory named in Signature-Agent (https, public hosts only)
  AGENTSHIELD_VISA_REGISTRY_DB       path to a registry file shaped like Visa's sample (read only)
  AGENTSHIELD_AGENT_POLICY           JSON: {"authorities": [...], "paths": [...], "tag_routes": {"tag": ["/route"]}}
  AGENTSHIELD_REDIS_URL              keep replay protection (nonces) in Redis; needs the redis package
"""
import json
import os
from pathlib import Path

from agentshield.agent_trust.directory import KeyDirectoryFetcher
from agentshield.agent_trust.redis_store import RedisNonceStore
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Verifier
from agentshield.agent_trust.visa_registry import load_visa_registry

from .app import create_app
from .tenants import Tenant, TenantRegistry, hash_key


def _agent_policy(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        p = json.loads(raw)
        if not isinstance(p, dict):
            raise ValueError
        return dict(agent_authorities=tuple(p.get("authorities", ())), agent_path_prefixes=tuple(p.get("paths", ())),
                    agent_tag_routes=tuple((t, tuple(r)) for t, r in p.get("tag_routes", {}).items()))
    except (ValueError, AttributeError, TypeError) as e:
        raise RuntimeError("AGENTSHIELD_AGENT_POLICY must be JSON like "
                           '{"authorities": ["shop.example.com"], "paths": ["/product"], "tag_routes": {"tag": ["/route"]}}') from e


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


def build(env=None, redis_from_url=None):
    env = os.environ if env is None else env
    key = env.get("AGENTSHIELD_API_KEY", "")
    if len(key) < 32:
        raise RuntimeError("set AGENTSHIELD_API_KEY to a random string of at least 32 characters")
    if env.get("WEB_CONCURRENCY", "1") not in ("", "1"):
        raise RuntimeError("run a single worker process: feature and result state is per process (Redis only shares nonces)")
    try:
        tenant = Tenant("demo", hash_key(key), **_agent_policy(env.get("AGENTSHIELD_AGENT_POLICY")))
    except ValueError as e:
        raise RuntimeError(f"invalid agent policy: {e}") from e
    registry, directory = KeyRegistry(), None
    if env.get("AGENTSHIELD_VISA_REGISTRY_DB"):
        registry, _report = load_visa_registry(env["AGENTSHIELD_VISA_REGISTRY_DB"])
    if env.get("AGENTSHIELD_AGENT_DIRECTORY") == "1":
        directory = KeyDirectoryFetcher()
    verifier = None
    if env.get("AGENTSHIELD_VISA_REGISTRY_DB") or directory is not None:
        verifier = Verifier(registry, _nonce_store(env, redis_from_url), Policy(), directory=directory)
    audit = env.get("AGENTSHIELD_AUDIT")
    return create_app(Path(env.get("AGENTSHIELD_MODEL_DIR", "models/portable_v1")), TenantRegistry([tenant]), verifier,
                      audit_path=Path(audit) if audit else None, audit_salt=env.get("AGENTSHIELD_AUDIT_SALT"),
                      trust_client_ts=env.get("AGENTSHIELD_TRUST_CLIENT_TS") == "1")


app = build()
