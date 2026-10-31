"""ASGI entry point: `uvicorn agentshield.serving.main:app` (ONE worker process only; state is in memory).

Environment:
  AGENTSHIELD_API_KEY        required, at least 32 characters (stored only as a hash). No default key exists.
  AGENTSHIELD_MODEL_DIR      model folder (default models/portable_v1)
  AGENTSHIELD_AUDIT          optional path for the JSONL audit log; then AGENTSHIELD_AUDIT_SALT is required too
  AGENTSHIELD_TRUST_CLIENT_TS=1   benchmark/replay only: use the client's timestamp for feature state
"""
import os
from pathlib import Path

from .app import create_app
from .tenants import Tenant, TenantRegistry, hash_key


def build():
    key = os.environ.get("AGENTSHIELD_API_KEY", "")
    if len(key) < 32:
        raise RuntimeError("set AGENTSHIELD_API_KEY to a random string of at least 32 characters")
    if os.environ.get("WEB_CONCURRENCY", "1") not in ("", "1"):
        raise RuntimeError("run a single worker process: nonce, feature and result state is per process")
    audit = os.environ.get("AGENTSHIELD_AUDIT")
    return create_app(Path(os.environ.get("AGENTSHIELD_MODEL_DIR", "models/portable_v1")),
                      TenantRegistry([Tenant("demo", hash_key(key))]),
                      audit_path=Path(audit) if audit else None, audit_salt=os.environ.get("AGENTSHIELD_AUDIT_SALT"),
                      trust_client_ts=os.environ.get("AGENTSHIELD_TRUST_CLIENT_TS") == "1")


app = build()
