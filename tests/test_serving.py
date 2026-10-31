import json

import lightgbm as lgb
import numpy as np
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from agentshield.agent_trust.signer import public_bytes, sign_request
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Verifier
from agentshield.serving.app import create_app
from agentshield.serving.model import ModelBundle
from agentshield.serving.tenants import Tenant, TenantRegistry, hash_key

NAMES = ["amount_minor", "hour", "dow", "x_DeviceType", "amt_cents", "amt_whole", "amt_round", "amt_sum_1h", "amt_sum_24h",
         "amt_ratio_30d", "sender_prior_cnt", "sender_prior_mean", "sender_amt_ratio", "sender_since_last",
         "card_cnt_3600s", "card_cnt_86400s", "card_cnt_604800s"]
NOW = 1_700_000_000


@pytest.fixture(scope="module")
def model_dir(tmp_path_factory):
    """A tiny model trained on synthetic data where fraud = large amount, so tests do not depend on local model files."""
    rng = np.random.default_rng(0)
    X = rng.random((4000, len(NAMES))) * 100
    X[:, 0] = rng.integers(100, 1_000_000, 4000)
    y = (X[:, 0] > 700_000).astype(int)
    b = lgb.train({"objective": "binary", "verbose": -1, "num_leaves": 8, "seed": 1}, lgb.Dataset(X, y, feature_name=NAMES), 30)
    s = b.predict(X)
    thr = {f"{f:g}": float(np.quantile(s[y == 0], 1 - f)) for f in (0.001, 0.005, 0.01, 0.05)}
    d = tmp_path_factory.mktemp("model")
    b.save_model(str(d / "model.txt"))
    (d / "model_card.json").write_text(json.dumps({"name": "test_model", "feature_names": NAMES, "score_thresholds_by_val_fpr": thr,
                                                   "device_codes": {"mobile": 0, "desktop": 1}}))
    return d


KEY = "test-key-123"


@pytest.fixture
def client(model_dir, tmp_path):
    keys = {"k1": Ed25519PrivateKey.generate()}
    reg = KeyRegistry()
    reg.register("agent-1", "k1", "ed25519", public_bytes(keys["k1"]))
    ver = Verifier(reg, InMemoryNonceStore(), Policy(), clock=lambda: NOW)
    tenants = TenantRegistry([Tenant("t1", hash_key(KEY)), Tenant("t2", hash_key("other-key"), challenge_fpr=0.01, block_fpr=0.001)])
    app = create_app(model_dir, tenants, ver, audit_path=tmp_path / "audit.jsonl", audit_salt="test-salt", clock=lambda: NOW,
                     trust_client_ts=True)
    c = TestClient(app)
    c.keys = keys
    c.audit = tmp_path / "audit.jsonl"
    return c


def post(c, body, key=KEY):
    return c.post("/v1/assess", json=body, headers={"x-api-key": key} if key else {})


def txn(i=1, amount=5000, **kw):
    return {"transaction_id": f"t{i}", "customer_id": "c1", "amount_minor": amount, "timestamp": NOW + i * 60, **kw}


def test_requires_a_valid_api_key(client):
    assert post(client, txn(), key=None).status_code == 401
    assert post(client, txn(), key="wrong").status_code == 401


def test_small_amount_is_allowed_with_no_reasons(client):
    r = post(client, txn(amount=1000)).json()
    assert r["action"] == "allow" and r["reasons"] == [] and r["tenant"] == "t1"
    assert 0 <= r["score"] <= 1 and r["latency_ms"] >= 0


def test_large_amount_is_blocked_with_reasons(client):
    r = post(client, txn(amount=999_000)).json()
    assert r["action"] == "block"
    assert r["reasons"] and r["reasons"][0]["code"] == "AMOUNT_LARGE"


def test_response_is_idempotent_and_does_not_double_count_history(client):
    a = post(client, txn(1)).json()
    b = post(client, txn(1)).json()
    assert a == b
    assert client.app.state.store_for("t1").features("c1", None, NOW + 10_000, 100)["sender_prior_cnt"] == 1


def test_tenants_have_independent_thresholds(client):
    body = txn(amount=620_000)  # mid-risk
    a = post(client, {**body, "transaction_id": "a"}, key=KEY).json()["action"]
    b = post(client, {**body, "transaction_id": "b"}, key="other-key").json()["action"]
    assert SEV[b] <= SEV[a]  # a smaller false-positive budget means a higher threshold, so never a stronger action


SEV = {"allow": 0, "challenge": 1, "block": 2}


def test_history_features_flow_from_the_online_store(client):
    for i in range(1, 7):
        post(client, txn(i, amount=1000))
    r = post(client, txn(7, amount=50_000)).json()
    assert "ONLINE_AMOUNT_SPIKE" in r["anomaly_flags"] and r["action"] in ("challenge", "block")


def test_burst_flag(client):
    out = [post(client, {**txn(i, amount=1000), "timestamp": NOW + i}).json() for i in range(1, 12)]
    assert any("ONLINE_BURST_1H" in o["anomaly_flags"] for o in out)


def signed_agent(client, **kw):
    h = sign_request(client.keys["k1"], "k1", method="GET", authority="shop.in", path="/product/1", tag="agent-browser-auth", now=NOW, **kw)
    return {"method": "GET", "authority": "shop.in", "path": "/product/1", "query": "", "headers": h}


def test_verified_agent_is_allowed_and_reported(client):
    r = post(client, txn(amount=1000, agent_request=signed_agent(client))).json()
    assert r["action"] == "allow" and r["agent"]["outcome"] == "verified" and r["agent"]["tier"] == "verified_agent"


def test_forged_agent_is_blocked_even_when_the_transaction_looks_fine(client):
    forged = signed_agent(client)
    forged["headers"]["signature"] = forged["headers"]["signature"][:-6] + "AAAA:"
    r = post(client, txn(amount=1000, agent_request=forged)).json()
    assert r["action"] == "block" and r["agent"]["outcome"] == "invalid"
    assert r["reasons"][0]["code"].startswith("AGENT_")


def test_agent_request_without_a_verifier_is_challenged_not_allowed(model_dir):
    tenants = TenantRegistry([Tenant("t1", hash_key(KEY))])
    c = TestClient(create_app(model_dir, tenants, verifier=None, clock=lambda: NOW, trust_client_ts=True))
    r = post(c, txn(amount=1000, agent_request={"method": "GET", "authority": "a", "path": "/p", "headers": {}})).json()
    assert r["action"] == "challenge" and r["agent"]["reason"] == "verifier_not_configured"


@pytest.mark.parametrize("bad", [
    {"amount_minor": -5}, {"amount_minor": 10**14}, {"customer_id": ""}, {"customer_id": "bad id; drop"},
    {"transaction_id": "x" * 100}, {"amount_minor": "abc"}, {"timestamp": -1},
])
def test_invalid_input_is_rejected(client, bad):
    assert post(client, {**txn(), **bad}).status_code == 422


def test_feedback_requires_a_known_transaction(client):
    assert client.post("/v1/feedback", json={"transaction_id": "nope", "label": 1}, headers={"x-api-key": KEY}).status_code == 404
    post(client, txn(1))
    r = client.post("/v1/feedback", json={"transaction_id": "t1", "label": 1}, headers={"x-api-key": KEY})
    assert r.status_code == 202 and client.app.state.feedback[0]["label"] == 1


def test_feedback_is_isolated_between_tenants(client):
    post(client, txn(1), key=KEY)
    r = client.post("/v1/feedback", json={"transaction_id": "t1", "label": 1}, headers={"x-api-key": "other-key"})
    assert r.status_code == 404


def test_audit_log_has_no_raw_customer_id_or_amount(client):
    post(client, txn(1, amount=123_457))
    line = client.audit.read_text().splitlines()[0]
    assert "c1" not in json.loads(line)["cust"] and "123457" not in line and json.loads(line)["tenant"] == "t1"


def test_scoring_failure_never_allows(client, monkeypatch):
    monkeypatch.setattr(ModelBundle, "score", lambda self, f: (_ for _ in ()).throw(RuntimeError("boom")))
    r = post(client, txn(amount=1000)).json()
    assert r["action"] == "challenge" and r["score"] is None


def test_startup_fails_if_model_needs_unknown_features(tmp_path, model_dir):
    import shutil
    d = tmp_path / "m"
    shutil.copytree(model_dir, d)
    card = json.loads((d / "model_card.json").read_text())
    card["feature_names"] = card["feature_names"] + ["mystery_feature"]
    (d / "model_card.json").write_text(json.dumps(card))
    with pytest.raises(RuntimeError, match="mystery_feature"):
        create_app(d, TenantRegistry([]))


def test_tenant_rejects_inverted_budgets():
    with pytest.raises(ValueError):
        Tenant("x", "h", challenge_fpr=0.001, block_fpr=0.05)


def test_tenants_do_not_share_online_state(client):
    post(client, txn(1, amount=1000), key=KEY)
    assert client.app.state.store_for("t2").features("c1", None, NOW + 10_000, 100)["sender_prior_cnt"] == 0
    assert client.app.state.store_for("t1").features("c1", None, NOW + 10_000, 100)["sender_prior_cnt"] == 1


def test_client_timestamp_cannot_poison_state_by_default(model_dir):
    tenants = TenantRegistry([Tenant("t1", hash_key(KEY))])
    c = TestClient(create_app(model_dir, tenants, clock=lambda: NOW))  # trust_client_ts defaults to False
    assert post(c, txn(1, amount=1000, timestamp=10**11)).status_code == 200
    f = c.app.state.store_for("t1").features("c1", None, NOW + 5, 100)
    assert f["sender_prior_cnt"] == 1 and f["sender_since_last"] == 5  # recorded at server time, not 1e11


def test_reusing_a_transaction_id_with_different_content_is_rejected(client):
    assert post(client, txn(1, amount=1000)).status_code == 200
    assert post(client, txn(1, amount=1000)).status_code == 200
    assert post(client, txn(1, amount=999_000)).status_code == 409


def test_nan_score_is_challenged_not_allowed(client, monkeypatch):
    monkeypatch.setattr(ModelBundle, "score", lambda self, f: float("nan"))
    r = post(client, txn(amount=1000)).json()
    assert r["action"] == "challenge"


def test_agent_request_with_too_many_headers_is_rejected(client):
    ar = {"method": "GET", "authority": "a", "path": "/p", "headers": {f"h{i}": "x" for i in range(100)}}
    assert post(client, txn(amount=1000, agent_request=ar)).status_code == 422


def test_healthz_does_not_disclose_the_model(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_audit_requires_a_salt(model_dir, tmp_path):
    with pytest.raises(RuntimeError, match="salt"):
        create_app(model_dir, TenantRegistry([]), audit_path=tmp_path / "a.jsonl")


def test_device_sharing_is_tracked_across_customers_within_a_tenant_only(client):
    for i, cust in enumerate(["c1", "c2"], start=1):
        assert post(client, {**txn(i, amount=1000), "customer_id": cust, "device_id": "dev-shared-1"}).status_code == 200
    t1 = client.app.state.store_for("t1").features("c3", None, NOW + 99_999, 1, "dev-shared-1")
    t2 = client.app.state.store_for("t2").features("c3", None, NOW + 99_999, 1, "dev-shared-1")
    assert t1["device_uniq_senders"] == 2 and t1["device_new_for_sender"] == 1
    assert t2["device_uniq_senders"] == 0


def test_instrument_fields_are_accepted_and_validated(client):
    ok = {**txn(1, amount=1000), "channel": "W", "instrument_type": "debit", "network": "visa", "device_id": "Pixel 7 Build/TQ3A"}
    assert post(client, ok).status_code == 200
    assert post(client, {**txn(2), "device_id": "bad;id<script>"}).status_code == 422


def test_startup_fails_if_a_tenant_budget_has_no_calibrated_threshold(model_dir):
    bad = TenantRegistry([Tenant("odd", hash_key(KEY), challenge_fpr=0.07, block_fpr=0.005)])
    with pytest.raises(RuntimeError, match="odd"):
        create_app(model_dir, bad)


def test_out_of_order_timestamps_cannot_corrupt_history(client):
    post(client, {**txn(1, amount=1000), "timestamp": NOW + 100})
    post(client, {**txn(2, amount=1000), "timestamp": NOW + 50})  # arrives late; recorded no earlier than the last event
    f = client.app.state.store_for("t1").features("c1", None, NOW + 200, 1000)
    assert f["sender_since_last"] == 100 and f["sender_prior_cnt"] == 2


def test_audit_write_failure_does_not_turn_a_decision_into_an_error(model_dir, tmp_path):
    tenants = TenantRegistry([Tenant("t1", hash_key(KEY))])
    c = TestClient(create_app(model_dir, tenants, audit_path=tmp_path / "missing_dir" / "audit.jsonl", audit_salt="s",
                              clock=lambda: NOW, trust_client_ts=True))
    r = post(c, txn(1, amount=1000))
    assert r.status_code == 200 and r.json()["action"] in ("allow", "challenge", "block")


def test_burst_inside_one_second_is_still_flagged(client):
    out = [post(client, {**txn(i, amount=1000), "timestamp": NOW}).json() for i in range(1, 13)]
    assert any("ONLINE_BURST_1H" in o["anomaly_flags"] for o in out)
