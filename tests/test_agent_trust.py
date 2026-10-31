"""Attack vectors for the agent-trust verifier. Each test is one vector from the Visa/Web Bot Auth review."""
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentshield.agent_trust.signer import public_bytes, sign_request
from agentshield.agent_trust.verifier import (
    InMemoryNonceStore, KeyRegistry, Policy, Request, StoreUnavailable, Verifier,
)

NOW = 1_700_000_000
HOST = "shop.example.in"


class DownStore(InMemoryNonceStore):
    def check_and_record(self, key, ttl):
        raise StoreUnavailable("redis down")


@pytest.fixture
def keys():
    return {"k1": Ed25519PrivateKey.generate(), "k2": Ed25519PrivateKey.generate()}


@pytest.fixture
def registry(keys):
    r = KeyRegistry()
    r.register("agent-1", "k1", "ed25519", public_bytes(keys["k1"]))
    r.register("agent-2", "k2", "ed25519", public_bytes(keys["k2"]))
    return r


def make(registry, store=None, policy=None, now=NOW):
    return Verifier(registry, store or InMemoryNonceStore(), policy or Policy(), clock=lambda: now)


def req(headers, method="GET", path="/product/42", query="", authority=HOST):
    return Request(method=method, authority=authority, path=path, query=query, headers=headers)


def signed(keys, who="k1", **kw):
    kw.setdefault("method", "GET")
    kw.setdefault("authority", HOST)
    kw.setdefault("path", "/product/42")
    kw.setdefault("tag", "agent-browser-auth")
    kw.setdefault("now", NOW)
    return sign_request(keys[who], who, **kw)


def check(v, headers, **rk):
    return v.verify(req(headers, **rk))


def test_valid_signed_request_is_verified(registry, keys):
    r = check(make(registry), signed(keys))
    assert (r.outcome, r.reason, r.agent_id, r.action) == ("verified", "ok", "agent-1", "allow")


def test_unsigned_request_on_protected_route_is_blocked(registry):
    r = check(make(registry), {})
    assert (r.outcome, r.reason, r.action) == ("invalid", "signature_missing", "block")


def test_public_route_needs_no_signature(registry):
    v = make(registry, policy=Policy(public_prefixes=("/health",)))
    r = check(v, {}, path="/health")
    assert r.outcome == "not_required" and r.action == "allow"


def test_wrong_key_forged_signature_is_invalid(registry, keys):
    forged = sign_request(keys["k2"], "k1", method="GET", authority=HOST, path="/product/42",
                          tag="agent-browser-auth", now=NOW)  # signed by k2, claims to be k1
    r = check(make(registry), forged)
    assert (r.outcome, r.reason) == ("invalid", "bad_signature")


def test_signature_for_other_path_is_invalid(registry, keys):
    h = signed(keys, path="/product/1")
    r = check(make(registry), h, path="/product/2")
    assert r.reason == "bad_signature"


def test_signature_replayed_at_other_merchant_is_invalid(registry, keys):
    h = signed(keys, authority="merchant-a.in")
    r = check(make(registry), h, authority="merchant-b.in")
    assert r.reason == "bad_signature"


def test_expired_signature_is_invalid(registry, keys):
    h = signed(keys, now=NOW - 1000, lifetime=300)
    assert check(make(registry), h).reason == "expired"


def test_created_in_future_beyond_skew_is_invalid(registry, keys):
    h = signed(keys, now=NOW + 600)
    assert check(make(registry), h).reason == "created_in_future"


def test_small_clock_skew_is_tolerated(registry, keys):
    h = signed(keys, now=NOW + 20)
    assert check(make(registry), h).outcome == "verified"


def test_lifetime_over_cap_is_invalid(registry, keys):
    h = signed(keys, lifetime=3600)
    assert check(make(registry), h).reason == "lifetime_too_long"


@pytest.mark.parametrize("param", ["created", "expires", "nonce", "keyid", "alg", "tag"])
def test_missing_mandatory_parameter_is_invalid(registry, keys, param):
    h = signed(keys, omit=(param,))
    r = check(make(registry), h)
    assert r.outcome == "invalid" and r.reason in (f"missing_param:{param}", "malformed_signature")


def test_replayed_nonce_is_invalid(registry, keys):
    v = make(registry)
    h = signed(keys, nonce="nonce-aaaa-1111")
    assert check(v, h).outcome == "verified"
    assert check(v, h).reason == "replayed_nonce"


def test_forged_request_does_not_burn_the_victims_nonce(registry, keys):
    v = make(registry)
    forged = sign_request(keys["k2"], "k1", method="GET", authority=HOST, path="/product/42",
                          tag="agent-browser-auth", now=NOW, nonce="victim-nonce-1")
    assert check(v, forged).reason == "bad_signature"
    genuine = signed(keys, nonce="victim-nonce-1")
    assert check(v, genuine).outcome == "verified"


def test_nonce_store_down_is_unverified_never_allow(registry, keys):
    r = check(make(registry, store=DownStore()), signed(keys))
    assert (r.outcome, r.reason, r.action) == ("unverified", "nonce_store_unavailable", "challenge")


def test_nonce_store_full_fails_closed(registry, keys):
    v = make(registry, store=InMemoryNonceStore(max_entries=2))
    assert check(v, signed(keys, nonce="nonce-0000-0001")).outcome == "verified"
    assert check(v, signed(keys, nonce="nonce-0000-0002")).outcome == "verified"
    assert check(v, signed(keys, nonce="nonce-0000-0003")).outcome == "unverified"


def test_key_directory_down_is_unverified(registry, keys):
    registry.available = False
    r = check(make(registry), signed(keys))
    assert (r.outcome, r.reason, r.action) == ("unverified", "key_directory_unavailable", "challenge")


def test_unknown_key_is_unverified(registry, keys):
    stranger = Ed25519PrivateKey.generate()
    h = sign_request(stranger, "k-unknown", method="GET", authority=HOST, path="/product/42",
                     tag="agent-browser-auth", now=NOW)
    r = check(make(registry), h)
    assert (r.outcome, r.reason) == ("unverified", "unknown_key")


def test_revoked_key_is_invalid(registry, keys):
    registry.revoke_key("k1")
    r = check(make(registry), signed(keys))
    assert (r.outcome, r.reason) == ("invalid", "key_revoked")


def test_revoked_agent_with_active_key_is_invalid(registry, keys):
    registry.revoke_agent("agent-1")
    assert check(make(registry), signed(keys)).reason == "key_revoked"


def test_algorithm_must_match_key_record(registry, keys):
    h = signed(keys, alg="rsa-pss-sha256")  # an Ed25519 key cannot be used under an RSA claim
    r = check(make(registry), h)
    assert (r.outcome, r.reason) == ("invalid", "alg_mismatch")


def test_unsupported_algorithm_is_rejected(registry, keys):
    assert check(make(registry), signed(keys, alg="hmac-sha256")).reason == "unsupported_alg"


def test_browsing_tag_cannot_authorise_payment_route(registry, keys):
    p = Policy(tag_routes={"agent-browser-auth": ("/product",), "agent-payer-auth": ("/checkout",)})
    h = signed(keys, path="/checkout/pay", tag="agent-browser-auth")
    r = check(make(registry, policy=p), h, path="/checkout/pay")
    assert r.reason == "tag_route_mismatch"
    ok = signed(keys, path="/checkout/pay", tag="agent-payer-auth")
    assert check(make(registry, policy=p), ok, path="/checkout/pay").outcome == "verified"


def test_unknown_tag_is_invalid(registry, keys):
    p = Policy(tag_routes={"agent-browser-auth": ("/product",)})
    h = signed(keys, tag="made-up-tag")
    assert check(make(registry, policy=p), h).reason == "unknown_tag"


def test_signature_must_cover_method_authority_and_path(registry, keys):
    h = signed(keys, covered=("@authority",))
    assert check(make(registry), h).reason == "components_missing"


def test_unknown_covered_component_is_invalid(registry, keys):
    h = signed(keys, covered=("@method", "@authority", "@path", "x-custom"))
    assert check(make(registry), h).reason == "unknown_component"


def test_path_excludes_query_string(registry, keys):
    h = signed(keys, path="/product/42")
    r = check(make(registry), h, path="/product/42", query="?utm=1")
    assert r.outcome == "verified"


def test_multiple_signatures_rejected(registry, keys):
    h = signed(keys)
    h["signature"] = h["signature"] + ", sig2=:AAAA:"
    assert check(make(registry), h).reason == "multiple_signatures"


@pytest.mark.parametrize("junk", ["", "garbage", "sig1=(", 'sig1=("@path");created=abc', "\x00\x01"])
def test_malformed_headers_are_invalid_never_crash(registry, keys, junk):
    h = signed(keys)
    h["signature-input"] = junk
    r = check(make(registry), h)
    assert r.outcome == "invalid" and r.action == "block"


def test_label_mismatch_between_headers_is_invalid(registry, keys):
    h = signed(keys)
    h["signature"] = h["signature"].replace("sig1=", "sigX=")
    assert check(make(registry), h).reason == "malformed_signature"


def test_duplicate_param_is_rejected(registry, keys):
    h = signed(keys)
    h["signature-input"] += ';created=1'
    assert check(make(registry), h).outcome == "invalid"


def test_duplicate_key_id_across_agents_cannot_be_registered(registry, keys):
    with pytest.raises(ValueError):
        registry.register("agent-evil", "k1", "ed25519", public_bytes(Ed25519PrivateKey.generate()))


def test_bad_public_key_length_rejected_at_registration():
    with pytest.raises(ValueError):
        KeyRegistry().register("a", "k", "ed25519", b"short")


def test_any_internal_error_fails_closed(registry, keys, monkeypatch):
    v = make(registry)
    monkeypatch.setattr(registry, "get", lambda k: (_ for _ in ()).throw(RuntimeError("boom")))
    r = check(v, signed(keys))
    assert r.outcome in ("invalid", "unverified") and r.action in ("block", "challenge")


def test_trailing_newline_in_header_is_rejected(registry, keys):
    h = signed(keys)
    h["signature-input"] += "\n"
    assert check(make(registry), h).outcome == "invalid"


def test_one_agent_key_cannot_exhaust_the_nonce_store_for_others(registry, keys):
    v = make(registry, store=InMemoryNonceStore(max_per_key=3))
    outcomes = [check(v, signed(keys, who="k1", nonce=f"nonce-aaaa-{i:04d}")).outcome for i in range(5)]
    assert outcomes == ["verified"] * 3 + ["unverified"] * 2
    assert check(v, signed(keys, who="k2", nonce="nonce-bbbb-0001")).outcome == "verified"


# ---- RSA-PSS-SHA256 ----
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402


@pytest.fixture(scope="module")
def rsa_keys():
    return {"r1": rsa.generate_private_key(65537, 2048), "r2": rsa.generate_private_key(65537, 2048)}


@pytest.fixture
def rsa_registry(registry, rsa_keys):
    registry.register("agent-rsa", "r1", "rsa-pss-sha256", public_bytes(rsa_keys["r1"]))
    registry.register("agent-rsa2", "r2", "rsa-pss-sha256", public_bytes(rsa_keys["r2"]))
    return registry


def rsa_signed(rsa_keys, who="r1", **kw):
    kw.setdefault("method", "GET")
    kw.setdefault("authority", HOST)
    kw.setdefault("path", "/product/42")
    kw.setdefault("tag", "agent-browser-auth")
    kw.setdefault("now", NOW)
    return sign_request(rsa_keys[who], who, **kw)


def test_rsa_pss_signed_request_is_verified(rsa_registry, rsa_keys):
    r = check(make(rsa_registry), rsa_signed(rsa_keys))
    assert (r.outcome, r.agent_id) == ("verified", "agent-rsa")


def test_rsa_signature_from_the_wrong_rsa_key_is_invalid(rsa_registry, rsa_keys):
    forged = sign_request(rsa_keys["r2"], "r1", method="GET", authority=HOST, path="/product/42",
                          tag="agent-browser-auth", now=NOW)
    assert check(make(rsa_registry), forged).reason == "bad_signature"


def test_rsa_key_cannot_be_used_under_an_ed25519_claim(rsa_registry, rsa_keys):
    h = rsa_signed(rsa_keys, alg="ed25519")
    assert check(make(rsa_registry), h).reason == "alg_mismatch"


def test_rsa_signature_with_a_maximum_length_salt_still_verifies(rsa_registry, rsa_keys):
    # Visa's sample signs with the maximum PSS salt length, so verification must not insist on one salt length
    h = rsa_signed(rsa_keys, rsa_salt="max")
    assert check(make(rsa_registry), h).outcome == "verified"


def test_rsa_replay_is_still_caught(rsa_registry, rsa_keys):
    v = make(rsa_registry)
    h = rsa_signed(rsa_keys, nonce="rsa-nonce-0001")
    assert check(v, h).outcome == "verified" and check(v, h).reason == "replayed_nonce"


def test_weak_rsa_keys_cannot_be_registered(registry):
    weak = rsa.generate_private_key(65537, 1024)
    with pytest.raises(ValueError, match="2048"):
        registry.register("a", "weak", "rsa-pss-sha256", public_bytes(weak))


def test_garbage_rsa_public_key_is_rejected_at_registration(registry):
    with pytest.raises(ValueError):
        registry.register("a", "bad", "rsa-pss-sha256", b"not a key")
