"""POST /v1/assess and /v1/feedback: one call that combines agent verification, online features, the model,
per-tenant thresholds and plain-language reasons. Failures never allow: unknown state means challenge or block.

Design points (from the security review):
- Online state is per tenant (no cross-tenant leakage or poisoning).
- Feature state is recorded at SERVER time unless trust_client_ts is set (replay/benchmark mode only), so a caller
  cannot wipe or corrupt history with a forged timestamp.
- One lock makes score-then-record atomic; a repeated transaction_id returns the same answer, and 409 if the
  content differs.
- Reasons are computed only for challenge/block. Customer ids are salted-hashed per tenant in the audit log.
- SINGLE PROCESS ONLY: state (online store, nonce store, results) lives in memory. Several workers would let a
  signed request be replayed once per worker. A shared store (Redis) removes this limit.
"""
import base64
import binascii
import hashlib
import hmac
import json
import logging
import threading
import time
from collections import OrderedDict, deque
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from agentshield.agent_trust.verifier import Request as AgentReq
from agentshield.agent_trust.verifier import Verifier
from agentshield.features_online.store import ALL_FEATURE_NAMES, OnlineFeatureStore

from .model import ModelBundle
from .tenants import Tenant, TenantRegistry

log = logging.getLogger("agentshield.serving")
ID = r"^[A-Za-z0-9._:@-]+$"
SEVERITY = {"allow": 0, "challenge": 1, "block": 2}
STATELESS = {"amount_minor", "hour", "dow", "x_DeviceType", "amt_cents", "amt_whole", "amt_round"}


class AgentRequestIn(BaseModel):
    method: str = Field(max_length=16)
    authority: str = Field(max_length=255)
    path: str = Field(max_length=1024)
    query: str = Field(default="", max_length=2048)
    headers: dict[str, str]
    body_b64: str | None = Field(default=None, max_length=1_400_000)  # the agent's original request body, base64

    @field_validator("body_b64")
    @classmethod
    def _valid_b64(cls, v):
        if v is not None:
            try:
                base64.b64decode(v, validate=True)
            except (binascii.Error, ValueError) as e:
                raise ValueError("body_b64 is not valid base64") from e
        return v

    @field_validator("headers")
    @classmethod
    def _bounded(cls, v: dict) -> dict:
        if len(v) > 20 or any(len(k) > 64 or len(x) > 4096 for k, x in v.items()):
            raise ValueError("too many or too large headers")
        return v


class AssessIn(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64, pattern=ID)
    customer_id: str = Field(min_length=1, max_length=64, pattern=ID)
    payee_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=ID)
    amount_minor: int = Field(ge=0, le=10**13)
    timestamp: int | None = Field(default=None, ge=0, le=10**11)  # honoured only with trust_client_ts
    device_type: str | None = Field(default=None, max_length=32)
    device_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:@ /()+-]+$")
    channel: str | None = Field(default=None, max_length=32)
    instrument_type: str | None = Field(default=None, max_length=32)
    network: str | None = Field(default=None, max_length=32)
    agent_request: AgentRequestIn | None = None


class FeedbackIn(BaseModel):
    transaction_id: str = Field(min_length=1, max_length=64, pattern=ID)
    label: int = Field(ge=0, le=1)


def create_app(model_dir: Path, tenants: TenantRegistry, verifier: Verifier | None = None,
               audit_path: Path | None = None, audit_salt: str | None = None, clock=time.time,
               cache_size: int = 100_000, trust_client_ts: bool = False) -> FastAPI:
    bundle = ModelBundle.load(model_dir)
    available = set(ALL_FEATURE_NAMES) | STATELESS
    missing = set(bundle.names) - available
    if missing:  # fail at startup, not at request time
        raise RuntimeError(f"model needs features the serving layer cannot provide: {sorted(missing)}")
    for t in tenants.tenants:  # a budget with no calibrated threshold would silently challenge everything
        for fpr in (t.challenge_fpr, t.block_fpr):
            try:
                bundle.threshold(fpr)
            except ValueError as e:
                raise RuntimeError(f"tenant {t.tenant_id}: {e}") from e
    if audit_path is not None and not audit_salt:
        raise RuntimeError("an audit salt is required when audit logging is on")
    stores: dict[str, OnlineFeatureStore] = {}
    last_ts: dict[str, int] = {}  # per tenant: never record an event earlier than the last one
    results: OrderedDict = OrderedDict()  # (tenant, transaction_id) -> (body hash, response)
    feedback: deque = deque(maxlen=100_000)
    lock = threading.Lock()
    app = FastAPI(title="AgentShield", version="0.1")
    app.state.bundle, app.state.feedback = bundle, feedback

    def store_for(tenant_id: str) -> OnlineFeatureStore:
        s = stores.get(tenant_id)
        if s is None:
            s = stores[tenant_id] = OnlineFeatureStore()
        return s

    app.state.store_for = store_for

    def tenant_dep(x_api_key: str | None = Header(default=None)) -> Tenant:
        t = tenants.authenticate(x_api_key)
        if t is None:
            raise HTTPException(status_code=401, detail="invalid or missing API key")
        return t

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/v1/assess")
    def assess(body: AssessIn, tenant: Tenant = Depends(tenant_dep)) -> dict:
        t0 = time.perf_counter()
        body_hash = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        cache_key = (tenant.tenant_id, body.transaction_id)
        with lock:
            if cache_key in results:
                prev_hash, prev = results[cache_key]
                if not hmac.compare_digest(prev_hash, body_hash):
                    raise HTTPException(status_code=409, detail="transaction_id was already used with different content")
                return prev
            ts = body.timestamp if (trust_client_ts and body.timestamp is not None) else int(clock())
            ts = max(ts, last_ts.get(tenant.tenant_id, 0))
            last_ts[tenant.tenant_id] = ts
            store = store_for(tenant.tenant_id)
            agent, agent_action = None, "allow"
            if body.agent_request is not None:
                if verifier is None:
                    agent, agent_action = {"outcome": "unverified", "reason": "verifier_not_configured", "tier": "unknown"}, "challenge"
                else:
                    d = body.agent_request.model_dump()
                    b64 = d.pop("body_b64")
                    r = verifier.verify(AgentReq(**d, body=base64.b64decode(b64) if b64 is not None else None))
                    agent = {"outcome": r.outcome, "reason": r.reason, "tier": r.tier, "agent_id": r.agent_id}
                    agent_action = r.action
            score, model_action, flags, feats = None, "challenge", [], None
            try:
                feats = store.features(body.customer_id, body.payee_id, ts, body.amount_minor, body.device_id, body.channel,
                                       body.instrument_type, body.network)
                feats.update(amount_minor=float(body.amount_minor), hour=float((ts // 3600) % 24), dow=float((ts // 86400) % 7),
                             amt_cents=float(body.amount_minor % 100), amt_whole=float(body.amount_minor // 100),
                             amt_round=float(body.amount_minor % 100 == 0),
                             x_DeviceType=float(bundle.device_codes.get((body.device_type or "").lower(), -1)))
                s = bundle.score(feats)
                if s == s:  # a NaN score is treated as a failure below, never as "allow"
                    # strictly above, as in the calibration metric (recall_at_fpr), so tied scores never inflate alerts
                    score = s
                    thr_c, thr_b = bundle.threshold(tenant.challenge_fpr), bundle.threshold(tenant.block_fpr)
                    model_action = "block" if s > thr_b else "challenge" if s > thr_c else "allow"
                ratio, prior = feats["sender_amt_ratio"], feats["sender_prior_cnt"]
                if prior >= 5 and ratio == ratio and ratio >= tenant.spike_ratio:  # ratio == ratio filters NaN
                    flags.append("ONLINE_AMOUNT_SPIKE")
                if feats["card_cnt_3600s"] + store.same_ts_count(body.customer_id, ts) >= tenant.burst_per_hour:
                    flags.append("ONLINE_BURST_1H")
            except Exception as e:  # noqa: BLE001 - never allow because scoring or features failed
                log.warning("scoring failed (%s); challenging", type(e).__name__)
                score, model_action = None, "challenge"
            flag_action = "challenge" if flags else "allow"
            action = max((model_action, agent_action, flag_action), key=SEVERITY.__getitem__)
            reasons = []
            if score is not None and model_action != "allow":
                try:
                    reasons = bundle.reasons(feats)
                except Exception as e:  # noqa: BLE001
                    log.warning("reason codes failed (%s)", type(e).__name__)
                    reasons = []
            if agent_action != "allow" and agent is not None:
                reasons.insert(0, {"code": f"AGENT_{agent['reason'].upper()}", "text": f"Agent check: {agent['reason']}"})
            if score is None:
                reasons.append({"code": "SCORING_UNAVAILABLE", "text": "Risk score unavailable; challenged for safety"})
            reasons += [{"code": f, "text": f.replace("ONLINE_", "").replace("_", " ").title()} for f in flags]
            store.update(body.customer_id, body.payee_id, ts, body.amount_minor, body.device_id)
            resp = {"transaction_id": body.transaction_id, "action": action, "score": None if score is None else round(score, 5),
                    "reasons": reasons, "anomaly_flags": flags, "agent": agent, "model": bundle.card["name"],
                    "tenant": tenant.tenant_id, "latency_ms": round((time.perf_counter() - t0) * 1000, 3)}
            results[cache_key] = (body_hash, resp)
            if len(results) > cache_size:
                results.popitem(last=False)
            if audit_path is not None:
                who = hashlib.sha256(f"{audit_salt}|{tenant.tenant_id}|{body.customer_id}".encode()).hexdigest()[:16]
                try:
                    with open(audit_path, "a") as f:
                        f.write(json.dumps({"ts": ts, "tenant": tenant.tenant_id, "txn": body.transaction_id, "cust": who,
                                            "action": action, "score": resp["score"], "reasons": [r["code"] for r in reasons]}) + "\n")
                except OSError as e:  # the decision is already made and recorded: do not turn it into a 500
                    log.error("audit write failed (%s)", type(e).__name__)
            return resp

    @app.post("/v1/feedback", status_code=202)
    def post_feedback(body: FeedbackIn, tenant: Tenant = Depends(tenant_dep)) -> dict:
        with lock:
            if (tenant.tenant_id, body.transaction_id) not in results:
                raise HTTPException(status_code=404, detail="unknown transaction_id for this tenant")
            feedback.append({"tenant": tenant.tenant_id, "transaction_id": body.transaction_id, "label": body.label})
        return {"status": "accepted"}

    return app
