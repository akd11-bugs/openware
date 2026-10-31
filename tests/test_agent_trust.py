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


def req(headers, method="GET", path="/product/42", query="", authority=HOST, body=None):
    return Request(method=method, authority=authority, path=path, query=query, headers=headers, body=body)


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


# ---- content-digest (RFC 9530) ----
import base64 as _b64  # noqa: E402
import hashlib as _hl  # noqa: E402

BODY = b'{"amount": 500, "payee": "shop-1"}'


def digest(body: bytes, algo="sha-256") -> str:
    h = _hl.sha256(body) if algo == "sha-256" else _hl.sha512(body)
    return f"{algo}=:{_b64.b64encode(h.digest()).decode()}:"


def post_signed(keys, body=BODY, **kw):
    kw.setdefault("method", "POST")
    kw.setdefault("path", "/checkout/pay")
    return signed(keys, body=body, **kw)


def test_signed_body_with_matching_digest_is_verified(registry, keys):
    h = post_signed(keys)
    assert h["content-digest"] == digest(BODY)
    r = check(make(registry), h, method="POST", path="/checkout/pay", body=BODY)
    assert r.outcome == "verified"


def test_body_changed_after_signing_is_invalid(registry, keys):
    h = post_signed(keys)
    r = check(make(registry), h, method="POST", path="/checkout/pay", body=b'{"amount": 50000, "payee": "evil"}')
    assert (r.outcome, r.reason) == ("invalid", "digest_mismatch")


def test_body_and_digest_header_both_swapped_still_fails_the_signature(registry, keys):
    h = post_signed(keys)
    evil = b'{"amount": 50000}'
    h["content-digest"] = digest(evil)  # the signature covered the ORIGINAL header value
    r = check(make(registry), h, method="POST", path="/checkout/pay", body=evil)
    assert r.reason == "bad_signature"


def test_a_body_the_signature_does_not_cover_is_invalid(registry, keys):
    h = post_signed(keys, cover_body=False)
    r = check(make(registry), h, method="POST", path="/checkout/pay", body=BODY)
    assert r.reason == "body_not_covered"


def test_covered_digest_but_missing_or_malformed_header_is_invalid(registry, keys):
    h = post_signed(keys)
    gone = {k: v for k, v in h.items() if k != "content-digest"}
    assert check(make(registry), gone, method="POST", path="/checkout/pay", body=BODY).reason == "bad_content_digest"
    for junk in ["sha-256=:short:", "md5=:AAAA:", "sha-256=AAAA", digest(BODY, "sha-512"), digest(BODY) + "\n"]:
        h2 = {**h, "content-digest": junk}
        r = check(make(registry), h2, method="POST", path="/checkout/pay", body=BODY)
        assert r.outcome == "invalid" and r.reason in ("bad_content_digest", "bad_signature"), junk


def test_requests_without_a_body_do_not_need_a_digest(registry, keys):
    assert check(make(registry), signed(keys), body=None).outcome == "verified"
    assert check(make(registry), signed(keys), body=b"").outcome == "verified"


def test_empty_body_with_a_covered_correct_digest_is_verified(registry, keys):
    h = post_signed(keys, body=b"")
    assert check(make(registry), h, method="POST", path="/checkout/pay", body=b"").outcome == "verified"


# ---- Signature-Agent and the key directory ----
import json as _json  # noqa: E402

from agentshield.agent_trust.directory import KeyDirectoryFetcher, jwk_thumbprint  # noqa: E402

DIR_A = "https://agent.example.com/.well-known/http-message-signatures-directory"
DIR_B = "https://other.example.com/.well-known/http-message-signatures-directory"
DIR_CT = "application/http-message-signatures-directory+json"


def _jwk(priv):
    return {"kty": "OKP", "crv": "Ed25519", "x": _b64.urlsafe_b64encode(public_bytes(priv)).decode().rstrip("=")}


class DirHttp:
    """Serves a directory per host and counts the calls."""

    def __init__(self, by_host, status=200):
        self.by_host, self.status, self.calls = by_host, status, []

    def __call__(self, ip, host, path, timeout, max_bytes):
        self.calls.append(host)
        return self.status, DIR_CT, _json.dumps({"keys": [_jwk(p) for p in self.by_host.get(host, [])]}).encode()


def verifier_with_directory(by_host, status=200, resolve=None):
    http = DirHttp(by_host, status)
    fetcher = KeyDirectoryFetcher(resolve=resolve or (lambda h: ["93.184.216.34"]), http_get=http, clock=lambda: NOW)
    return Verifier(KeyRegistry(), InMemoryNonceStore(), Policy(), clock=lambda: NOW, directory=fetcher), http


def agent_signed(priv, url=DIR_A, **kw):
    kw.setdefault("method", "GET")
    kw.setdefault("authority", HOST)
    kw.setdefault("path", "/product/42")
    kw.setdefault("tag", "agent-browser-auth")
    kw.setdefault("now", NOW)
    return sign_request(priv, jwk_thumbprint(_jwk(priv)), signature_agent=url, **kw)


def test_a_key_found_in_the_directory_is_verified(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k1"]]})
    r = check(v, agent_signed(keys["k1"]))
    assert (r.outcome, r.agent_id) == ("verified", DIR_A)


def test_directory_failure_is_unverified_never_allow(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k1"]]}, status=500)
    r = check(v, agent_signed(keys["k1"]))
    assert (r.outcome, r.reason, r.action) == ("unverified", "key_directory_unavailable", "challenge")


def test_a_key_missing_from_the_directory_is_unverified(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k2"]]})
    assert check(v, agent_signed(keys["k1"])).reason == "unknown_key"


def test_signature_agent_must_be_covered_by_the_signature(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k1"]]})
    assert check(v, agent_signed(keys["k1"], cover_agent=False)).reason == "signature_agent_not_covered"


def test_signature_agent_must_be_https(keys):
    v, http = verifier_with_directory({"agent.example.com": [keys["k1"]]})
    r = check(v, agent_signed(keys["k1"], url="http://agent.example.com/dir"))
    assert r.reason == "bad_signature_agent" and http.calls == []


def test_a_claimed_thumbprint_cannot_be_used_with_someone_elses_signature(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k1"]]})
    victim_id = jwk_thumbprint(_jwk(keys["k1"]))
    forged = sign_request(keys["k2"], victim_id, method="GET", authority=HOST, path="/product/42", tag="agent-browser-auth",
                          now=NOW, signature_agent=DIR_A)
    assert check(v, forged).reason == "bad_signature"


def test_directory_urls_pointing_inside_the_network_are_never_fetched(keys):
    v, http = verifier_with_directory({}, resolve=lambda h: ["10.0.0.5"])
    for url in ("https://internal.example.com/dir", "https://10.0.0.5/dir", "https://169.254.169.254/latest"):
        r = check(v, agent_signed(keys["k1"], url=url))
        assert (r.outcome, r.reason) == ("unverified", "key_directory_unavailable")
    assert http.calls == []


def test_keys_are_looked_up_by_directory_and_key_together(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k1"]], "other.example.com": [keys["k2"]]})
    assert check(v, agent_signed(keys["k1"], url=DIR_B)).reason == "unknown_key"  # the key exists, but not in THAT directory
    assert check(v, agent_signed(keys["k1"], url=DIR_A)).outcome == "verified"


def test_directory_keys_are_replay_protected_too(keys):
    v, _ = verifier_with_directory({"agent.example.com": [keys["k1"]]})
    h = agent_signed(keys["k1"], nonce="dir-nonce-0001")
    assert check(v, h).outcome == "verified" and check(v, h).reason == "replayed_nonce"


def test_without_a_directory_the_local_registry_is_used(registry, keys):
    h = signed(keys, signature_agent=DIR_A)
    assert check(make(registry), h).outcome == "verified"


def test_a_policy_given_per_call_overrides_the_default_for_that_call_only(registry, keys):
    v = make(registry)
    strict = Policy(tag_routes={"agent-payer-auth": ("/checkout",)})
    h = signed(keys)  # a browsing tag on /product/42
    assert v.verify(req(h), strict).reason == "unknown_tag"
    assert v.verify(req(signed(keys))).outcome == "verified"  # the default policy still applies without an override
