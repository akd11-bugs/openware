"""Run Visa's OWN signing code (tap-agent/agent_app.py) and their sample registry through our verifier.

Skipped unless VISA_TAP_DIR points at a checkout of github.com/visa/trusted-agent-protocol (we do not vendor their code).
Only the signing function bodies are executed, with a stub for the Streamlit error display. Keys are generated fresh,
as the sample's README instructs, because the sample registry holds public keys only.
"""
import ast
import base64
import hashlib
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentshield.agent_trust.signer import public_bytes
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Request, Verifier
from agentshield.agent_trust.visa_registry import load_visa_registry

VISA_DIR = os.environ.get("VISA_TAP_DIR")
pytestmark = pytest.mark.skipif(not VISA_DIR or not (Path(VISA_DIR or "") / "tap-agent/agent_app.py").exists(),
                                reason="set VISA_TAP_DIR to a checkout of visa/trusted-agent-protocol")
NOW, HOST = 1_700_000_000, "merchant.example.com"
ROUTES = {"agent-browser-auth": ("/product",), "agent-payer-auth": ("/checkout",)}
VISA = Policy(required_components=("@authority", "@path"), visa_compat=True, tag_routes=ROUTES)
WANTED = {"create_http_message_signature", "create_ed25519_signature", "get_ed25519_keys_from_env"}


@pytest.fixture(scope="module")
def visa_fns():
    tree = ast.parse((Path(VISA_DIR) / "tap-agent/agent_app.py").read_text())
    defs = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in WANTED:
            defs[node.name] = node  # the file defines some twice; the last definition is the one that runs
    assert set(defs) == WANTED
    ns = {"base64": base64, "os": os, "padding": padding, "hashes": hashes, "serialization": serialization,
          "default_backend": default_backend, "st": SimpleNamespace(error=lambda *a, **k: None)}
    exec(compile(ast.Module(list(defs.values()), []), "visa_agent_app_functions", "exec"), ns)  # noqa: S102 - bodies reviewed
    return ns


@pytest.fixture(scope="module")
def keys():
    return {"rsa": rsa.generate_private_key(65537, 2048), "ed": Ed25519PrivateKey.generate()}


@pytest.fixture
def registry(keys):
    r = KeyRegistry()
    pem = keys["rsa"].public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    r.register("visa-sample-agent", "primary-rsa", "rsa-pss-sha256", pem)
    r.register("visa-sample-agent", "primary-ed25519", "ed25519", base64.b64decode(base64.b64encode(public_bytes(keys["ed"]))))
    return r


def rsa_headers(fns, keys, path="/product/1", tag="agent-browser-auth", nonce="live-rsa-nonce-1", created=NOW, expires=NOW + 300):
    pem = keys["rsa"].private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    sig_input, sig = fns["create_http_message_signature"](pem, HOST, path, "primary-rsa", nonce, created, expires, tag)
    assert sig_input and sig
    return {"signature-input": sig_input, "signature": sig}


def ed_headers(fns, keys, monkeypatch, path="/product/1", tag="agent-browser-auth", nonce="live-ed-nonce-1"):
    raw = keys["ed"].private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    monkeypatch.setenv("ED25519_PRIVATE_KEY", base64.b64encode(raw).decode())
    monkeypatch.setenv("ED25519_PUBLIC_KEY", base64.b64encode(public_bytes(keys["ed"])).decode())
    sig_input, sig = fns["create_ed25519_signature"]("", HOST, path, "primary-ed25519", nonce, NOW, NOW + 300, tag)
    assert sig_input and sig
    return {"signature-input": sig_input, "signature": sig}


def verify(registry, headers, path="/product/1", policy=VISA, verifier=None):
    v = verifier or Verifier(registry, InMemoryNonceStore(), Policy(), clock=lambda: NOW)
    return v.verify(Request("GET", HOST, path, headers), policy)


def test_visas_own_rsa_pss_signatures_verify(visa_fns, keys, registry):
    r = verify(registry, rsa_headers(visa_fns, keys))
    assert (r.outcome, r.agent_id) == ("verified", "visa-sample-agent")


def test_visas_own_ed25519_signatures_verify(visa_fns, keys, registry, monkeypatch):
    r = verify(registry, ed_headers(visa_fns, keys, monkeypatch))
    assert (r.outcome, r.agent_id) == ("verified", "visa-sample-agent")


def test_visas_payer_tag_reaches_checkout_and_the_browser_tag_does_not(visa_fns, keys, registry):
    ok = rsa_headers(visa_fns, keys, path="/checkout/pay", tag="agent-payer-auth", nonce="live-pay-nonce-1")
    assert verify(registry, ok, path="/checkout/pay").outcome == "verified"
    bad = rsa_headers(visa_fns, keys, path="/checkout/pay", tag="agent-browser-auth", nonce="live-pay-nonce-2")
    assert verify(registry, bad, path="/checkout/pay").reason == "tag_route_mismatch"


def test_visas_signatures_fail_when_the_request_is_changed(visa_fns, keys, registry):
    h = rsa_headers(visa_fns, keys, path="/product/1")
    assert verify(registry, h, path="/product/2").reason == "bad_signature"


def test_visas_signatures_cannot_be_replayed(visa_fns, keys, registry):
    v = Verifier(registry, InMemoryNonceStore(), Policy(), clock=lambda: NOW)
    h = rsa_headers(visa_fns, keys, nonce="live-replay-nonce")
    assert verify(registry, h, verifier=v).outcome == "verified"
    assert verify(registry, h, verifier=v).reason == "replayed_nonce"


def test_visas_expired_signatures_are_rejected(visa_fns, keys, registry):
    old = rsa_headers(visa_fns, keys, created=NOW - 1000, expires=NOW - 800, nonce="live-old-nonce")
    assert verify(registry, old).reason == "expired"


def test_a_signature_from_a_different_key_is_rejected(visa_fns, keys, registry):
    other = {"rsa": rsa.generate_private_key(65537, 2048)}
    assert verify(registry, rsa_headers(visa_fns, other, nonce="live-forged-nonce")).reason == "bad_signature"


def test_our_strict_defaults_reject_visas_headers_as_documented(visa_fns, keys, registry):
    h = rsa_headers(visa_fns, keys, nonce="live-strict-nonce")
    assert verify(registry, h, policy=Policy()).reason == "missing_param:keyid"
    assert verify(registry, h, policy=Policy(visa_compat=True)).reason == "components_missing"


def test_the_real_sample_registry_can_be_read():
    db = Path(VISA_DIR) / "agent-registry/agent_registry.db"
    if not db.exists():
        pytest.skip("sample database not present")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ids = [r[0] for r in con.execute("select key_id from agent_keys")]
    con.close()
    reg, report = load_visa_registry(db)
    dup = {k for k in ids if ids.count(k) > 1}
    assert all(report["skipped"].get(k, "").startswith("ambiguous") for k in dup)  # shared key ids resolve for nobody
    assert set(report["loaded"]) | set(report["skipped"]) == set(ids)
    assert report["loaded"], "at least one sample key should load"
    for k in report["loaded"]:
        assert reg.get(k).alg in ("ed25519", "rsa-pss-sha256")
