"""A key directory signed by its own keys (Web Bot Auth http-message-signatures-directory profile)."""
import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentshield.agent_trust.directory import DirectoryError, KeyDirectoryFetcher, jwk_thumbprint
from agentshield.agent_trust.signer import public_bytes, sign_directory_response

NOW = 1_700_000_000
HOST = "agent.example.com"
URL = f"https://{HOST}/.well-known/http-message-signatures-directory"
CT = "application/http-message-signatures-directory+json"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def jwk(priv):
    if isinstance(priv, rsa.RSAPrivateKey):
        n = priv.public_key().public_numbers()
        return {"kty": "RSA", "n": b64u(n.n.to_bytes(256, "big")), "e": b64u(n.e.to_bytes(3, "big"))}
    return {"kty": "OKP", "crv": "Ed25519", "x": b64u(public_bytes(priv))}


class Http:
    def __init__(self, body, headers):
        self.body, self.headers = body, headers

    def __call__(self, ip, host, path, timeout, max_bytes):
        return 200, CT, self.body, self.headers


def fetch(privs, signers, require=True, now=NOW, authority=HOST, mutate=None, extra=None, bind_body=True, **sign_kw):
    body = json.dumps({"keys": [{**jwk(p), **(extra or {})} for p in privs]}).encode()
    headers = {}
    for i, p in enumerate(signers):
        h = sign_directory_response(p, jwk_thumbprint(jwk(p)), authority=authority, body=body, now=now, **{"label": f"sig{i + 1}", **sign_kw})
        for k, v in h.items():
            headers[k] = f"{headers[k]}, {v}" if k in headers and k != "content-digest" else v
    if mutate:
        body, headers = mutate(body, headers)
    f = KeyDirectoryFetcher(resolve=lambda h: ["93.184.216.34"], http_get=Http(body, headers), clock=lambda: NOW, require_signed=require, bind_body=bind_body)
    return f.keys(URL)


@pytest.fixture(scope="module")
def ed():
    return [Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()]


def test_a_directory_signed_by_its_own_key_is_accepted(ed):
    keys = fetch([ed[0]], [ed[0]])
    assert list(keys) == [jwk_thumbprint(jwk(ed[0]))]


def test_an_rsa_signed_directory_is_accepted():
    k = rsa.generate_private_key(65537, 2048)
    assert jwk_thumbprint(jwk(k)) in fetch([k], [k])


def test_an_unsigned_directory_is_refused_when_signatures_are_required(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [], require=True)


def test_an_unsigned_directory_is_still_accepted_when_signatures_are_not_required(ed):
    assert len(fetch([ed[0]], [], require=False)) == 1


def test_only_keys_with_a_valid_signature_are_kept(ed):
    keys = fetch([ed[0], ed[1]], [ed[0]])
    assert list(keys) == [jwk_thumbprint(jwk(ed[0]))]
    both = fetch([ed[0], ed[1]], [ed[0], ed[1]])
    assert len(both) == 2


def test_a_signature_by_a_key_that_is_not_in_the_directory_counts_for_nothing(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[1]])


def test_a_signature_made_for_another_host_is_rejected(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], authority="other.example.com")


def test_a_changed_body_fails_the_digest(ed):
    # the thumbprint does not cover exp, so an attacker could stretch a key's validity without touching its identity
    def stretch_validity(body, headers):
        return json.dumps({"keys": [{**jwk(ed[0]), "exp": NOW + 10**9}]}).encode(), headers

    assert len(fetch([ed[0]], [ed[0]], extra={"exp": NOW + 100})) == 1  # control: the untouched response is accepted
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], extra={"exp": NOW + 100}, mutate=stretch_validity)


def test_swapping_in_other_keys_is_refused(ed):
    def swap(body, headers):
        return json.dumps({"keys": [jwk(ed[1])]}).encode(), headers

    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], mutate=swap)


def test_expired_and_not_yet_valid_signatures_are_rejected(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], now=NOW - 7200, lifetime=3600)
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], now=NOW + 3600)


def test_a_signature_with_the_wrong_tag_is_rejected(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], tag="agent-browser-auth")


def test_a_signature_that_does_not_cover_both_required_parts_is_rejected(ed):
    for comps in (("content-digest",), ("@authority;req",)):
        with pytest.raises(DirectoryError):
            fetch([ed[0]], [ed[0]], components=comps)


def test_the_algorithm_must_match_the_key(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], alg="rsa-pss-sha256")


def test_malformed_signature_headers_never_crash(ed):
    for junk in ("", "garbage", "sig1=(", "sig1=:@@@:", "x" * 20000):
        def wreck(body, headers, junk=junk):
            return body, {**headers, "signature": junk, "signature-input": junk}

        with pytest.raises(DirectoryError):
            fetch([ed[0]], [ed[0]], mutate=wreck)


def test_a_lifetime_over_the_cap_is_rejected(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], lifetime=30 * 86400)


# ---- the profile real directories use today: only ("@authority";req) is signed, with no content-digest ----
def drop_digest(body, headers):
    return body, {k: v for k, v in headers.items() if k != "content-digest"}


def test_an_authority_only_signature_is_refused_by_default_because_it_does_not_bind_the_body(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], components=("@authority;req",), mutate=drop_digest)


def test_an_authority_only_signature_is_accepted_when_the_choice_is_made_explicitly(ed):
    keys = fetch([ed[0]], [ed[0]], components=("@authority;req",), mutate=drop_digest, bind_body=False)
    assert list(keys) == [jwk_thumbprint(jwk(ed[0]))]


def test_unbound_signatures_still_keep_only_the_keys_that_signed(ed):
    keys = fetch([ed[0], ed[1]], [ed[0]], components=("@authority;req",), mutate=drop_digest, bind_body=False)
    assert list(keys) == [jwk_thumbprint(jwk(ed[0]))]


def test_an_authority_only_signature_for_another_host_is_still_refused(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], components=("@authority;req",), mutate=drop_digest, bind_body=False, authority="other.example.com")


def test_a_digest_only_signature_is_never_accepted_even_when_unbound_bodies_are_allowed(ed):
    with pytest.raises(DirectoryError):
        fetch([ed[0]], [ed[0]], components=("content-digest",), bind_body=False)


def test_a_signature_label_with_digits_is_fine(ed):
    assert len(fetch([ed[0]], [ed[0]], components=("@authority;req",), mutate=drop_digest, bind_body=False, label="binding0")) == 1
