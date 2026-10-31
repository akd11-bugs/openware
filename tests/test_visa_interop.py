"""Interoperability with the shape of Visa's public Trusted Agent Protocol sample (read from its cdn-proxy and tap-agent).

The sample signs ("@authority" "@path") only, writes "; " between parameters, calls the key id `keyId`, uses the label
sig2, builds @path from the URL, signs RSA-PSS with the maximum salt, and keeps keys in a registry (RSA as PEM, Ed25519
as 44 character base64). These tests build headers by hand in that shape, never with our own signer.
"""
import base64

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentshield.agent_trust.signer import public_bytes
from agentshield.agent_trust.verifier import InMemoryNonceStore, KeyRegistry, Policy, Request, Verifier

NOW, HOST = 1_700_000_000, "merchant.example.com"
ROUTES = {"agent-browser-auth": ("/product",), "agent-payer-auth": ("/checkout",)}
VISA = Policy(required_components=("@authority", "@path"), visa_compat=True, tag_routes=ROUTES)


def visa_headers(priv, key_id, alg, path="/product/1", tag="agent-browser-auth", nonce="visa-nonce-0001", created=NOW,
                 expires=NOW + 300, key_param="keyId"):
    params = f'("@authority" "@path"); created={created}; expires={expires}; {key_param}="{key_id}"; alg="{alg}"; nonce="{nonce}"; tag="{tag}"'
    base = f'"@authority": {HOST}\n"@path": {path}\n"@signature-params": {params}'
    if alg == "rsa-pss-sha256":
        raw = priv.sign(base.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH), hashes.SHA256())
    else:
        raw = priv.sign(base.encode())
    return {"signature-input": f"sig2={params}", "signature": f"sig2=:{base64.b64encode(raw).decode()}:"}


@pytest.fixture(scope="module")
def rsa_priv():
    return rsa.generate_private_key(65537, 2048)


@pytest.fixture
def registry(rsa_priv):
    r = KeyRegistry()
    pem = rsa_priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    r.register("visa-rsa-agent", "rsa-key-1", "rsa-pss-sha256", pem)  # the registry stores RSA keys as PEM
    ed = Ed25519PrivateKey.generate()
    r.ed = ed
    b64 = base64.b64encode(public_bytes(ed)).decode()
    assert len(b64) == 44  # the registry stores Ed25519 as 44 character base64
    r.register("visa-ed-agent", "ed-key-1", "ed25519", base64.b64decode(b64))
    return r


def verify(registry, headers, policy=VISA, path="/product/1", now=NOW):
    v = Verifier(registry, InMemoryNonceStore(), Policy(), clock=lambda: now)
    return v.verify(Request("GET", HOST, path, headers), policy)


def test_a_visa_shaped_rsa_pss_request_is_verified(registry, rsa_priv):
    r = verify(registry, visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256"))
    assert (r.outcome, r.agent_id) == ("verified", "visa-rsa-agent")


def test_a_visa_shaped_ed25519_request_is_verified(registry):
    r = verify(registry, visa_headers(registry.ed, "ed-key-1", "ed25519"))
    assert (r.outcome, r.agent_id) == ("verified", "visa-ed-agent")


def test_the_payer_tag_reaches_checkout_and_the_browser_tag_does_not(registry, rsa_priv):
    ok = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", path="/checkout/pay", tag="agent-payer-auth")
    assert verify(registry, ok, path="/checkout/pay").outcome == "verified"
    bad = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", path="/checkout/pay", tag="agent-browser-auth", nonce="visa-nonce-0002")
    assert verify(registry, bad, path="/checkout/pay").reason == "tag_route_mismatch"


def test_a_different_path_than_the_one_signed_is_rejected(registry, rsa_priv):
    h = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", path="/product/1")
    assert verify(registry, h, path="/product/999").reason == "bad_signature"


def test_replays_are_caught_even_though_the_sample_is_a_demo(registry, rsa_priv):
    v = Verifier(registry, InMemoryNonceStore(), Policy(), clock=lambda: NOW)
    h = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256")
    req = Request("GET", HOST, "/product/1", h)
    assert v.verify(req, VISA).outcome == "verified" and v.verify(req, VISA).reason == "replayed_nonce"


def test_the_sample_without_expiry_discipline_is_still_bounded(registry, rsa_priv):
    old = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", created=NOW - 1000, expires=NOW - 800)  # short lived, but over
    assert verify(registry, old).reason == "expired"
    long = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", expires=NOW + 86400, nonce="visa-nonce-0003")
    assert verify(registry, long).reason == "lifetime_too_long"


# ---- where we deliberately differ, written down so it is a choice and not an accident ----
def test_default_policy_rejects_the_camel_case_key_id(registry, rsa_priv):
    r = verify(registry, visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256"), policy=Policy(required_components=("@authority", "@path")))
    assert (r.outcome, r.reason) == ("invalid", "missing_param:keyid")  # RFC 9421 spells it keyid; the alias needs visa_compat


def test_default_policy_requires_the_method_to_be_signed(registry, rsa_priv):
    h = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", key_param="keyid")
    assert verify(registry, h, policy=Policy()).reason == "components_missing"  # the sample signs authority and path only
    assert verify(registry, visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", key_param="keyid", nonce="visa-nonce-0004"),
                  policy=Policy(required_components=("@authority", "@path"))).outcome == "verified"


def test_spaces_after_semicolons_are_accepted_because_the_standard_allows_them(registry, rsa_priv):
    h = visa_headers(rsa_priv, "rsa-key-1", "rsa-pss-sha256", key_param="keyid")
    assert "; created" in h["signature-input"]
    assert verify(registry, h, policy=Policy(required_components=("@authority", "@path"))).outcome == "verified"


def test_unsigned_requests_are_still_blocked(registry):
    assert verify(registry, {}).reason == "signature_missing"


# ---- the registry formats the sample uses ----
def test_rsa_keys_can_be_registered_as_pem(rsa_priv):
    r = KeyRegistry()
    pem = rsa_priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    r.register("a", "k", "rsa-pss-sha256", pem)
    assert r.get("k").public_key.startswith(b"0")  # stored as DER


def test_garbage_pem_is_rejected():
    with pytest.raises(ValueError):
        KeyRegistry().register("a", "k", "rsa-pss-sha256", b"-----BEGIN PUBLIC KEY-----\nnot a key\n-----END PUBLIC KEY-----")
