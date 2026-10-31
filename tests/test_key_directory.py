"""Key directory fetcher: SSRF limits, size caps, caching, and thumbprint-keyed results. No real network is used."""
import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentshield.agent_trust.directory import (
    DirectoryError, KeyDirectoryFetcher, check_url, is_public, jwk_thumbprint, parse_directory,
)
from agentshield.agent_trust.signer import public_bytes

NOW = 1_700_000_000
CT = "application/http-message-signatures-directory+json"
URL = "https://agent.example.com/.well-known/http-message-signatures-directory"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def ed_jwk(priv=None, **extra):
    priv = priv or Ed25519PrivateKey.generate()
    return priv, {"kty": "OKP", "crv": "Ed25519", "x": b64u(public_bytes(priv)), **extra}


def body_of(*jwks) -> bytes:
    return json.dumps({"keys": list(jwks)}).encode()


def resolver(mapping):
    return lambda host: mapping[host]


PUBLIC = resolver({"agent.example.com": ["93.184.216.34"]})


class FakeHttp:
    def __init__(self, status=200, ctype=CT, body=b"{}", exc=None):
        self.status, self.ctype, self.body, self.exc, self.calls = status, ctype, body, exc, []

    def __call__(self, ip, host, path, timeout, max_bytes):
        self.calls.append((ip, host, path, timeout, max_bytes))
        if self.exc:
            raise self.exc
        return self.status, self.ctype, self.body


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def fetcher(http, clock=None, **kw):
    return KeyDirectoryFetcher(resolve=PUBLIC, http_get=http, clock=clock or Clock(), **kw)


# ---- the address checks ----
@pytest.mark.parametrize("ip", ["10.0.0.1", "127.0.0.1", "169.254.169.254", "192.168.1.5", "172.16.0.9", "100.64.0.1",
                                "0.0.0.0", "::1", "fc00::1", "fe80::1", "::ffff:10.0.0.1", "224.0.0.1", "240.0.0.1"])
def test_non_public_addresses_are_refused(ip):
    assert not is_public(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_allowed(ip):
    assert is_public(ip)


@pytest.mark.parametrize("url", [
    "http://agent.example.com/dir", "ftp://agent.example.com/x", "file:///etc/passwd", "https://user:pw@agent.example.com/d",
    "https://agent.example.com:8443/d", "https:///nohost", "https://127.0.0.1/d", "https://10.1.2.3/d", "https://[::1]/d",
    "https://169.254.169.254/latest/meta-data", "https://agent.example.com/" + "a" * 3000, "agent.example.com/dir", "",
])
def test_unsafe_urls_are_refused(url):
    with pytest.raises(DirectoryError):
        check_url(url, PUBLIC)


def test_a_hostname_resolving_to_a_private_address_is_refused():
    for ips in (["10.0.0.7"], ["93.184.216.34", "10.0.0.7"], ["::ffff:192.168.0.1"], []):
        with pytest.raises(DirectoryError):
            check_url("https://evil.example/d", resolver({"evil.example": ips}))


def test_a_good_url_returns_the_checked_address_host_and_path():
    host, ip, path = check_url(URL + "?v=1", PUBLIC)
    assert (host, ip, path) == ("agent.example.com", "93.184.216.34", "/.well-known/http-message-signatures-directory?v=1")


def test_the_request_goes_to_the_checked_address_not_the_hostname():
    http = FakeHttp(body=body_of(ed_jwk()[1]))
    fetcher(http).keys(URL)
    ip, host, path, timeout, cap = http.calls[0]
    assert ip == "93.184.216.34" and host == "agent.example.com" and timeout > 0 and cap > 0  # DNS cannot change in between


# ---- responses ----
def test_a_good_directory_returns_keys_by_thumbprint():
    priv, jwk = ed_jwk()
    keys = fetcher(FakeHttp(body=body_of(jwk))).keys(URL)
    tp = b64u(hashlib.sha256(json.dumps({"crv": "Ed25519", "kty": "OKP", "x": jwk["x"]}, separators=(",", ":"), sort_keys=True).encode()).digest())
    assert list(keys) == [tp] == [jwk_thumbprint(jwk)]
    alg, loaded, raw = keys[tp]
    assert alg == "ed25519" and raw == public_bytes(priv)


def test_a_claimed_kid_cannot_rename_a_key():
    _, jwk = ed_jwk(kid="admin-key")
    keys = fetcher(FakeHttp(body=body_of(jwk))).keys(URL)
    assert "admin-key" not in keys and jwk_thumbprint(jwk) in keys


@pytest.mark.parametrize("kw", [
    {"status": 404}, {"status": 301}, {"status": 302}, {"status": 500}, {"status": 204},
    {"ctype": "text/html"}, {"ctype": ""}, {"body": b"not json"}, {"body": b"[]"}, {"body": b'{"keys": "x"}'},
])
def test_bad_responses_are_errors(kw):
    base = {"body": body_of(ed_jwk()[1])}
    with pytest.raises(DirectoryError):
        fetcher(FakeHttp(**{**base, **kw})).keys(URL)


def test_oversize_bodies_and_too_many_keys_are_refused():
    with pytest.raises(DirectoryError):
        fetcher(FakeHttp(body=b" " * 200_000 + body_of(ed_jwk()[1])), ).keys(URL)
    with pytest.raises(DirectoryError):
        fetcher(FakeHttp(body=body_of(*[ed_jwk()[1] for _ in range(25)]))).keys(URL)


def test_network_errors_become_directory_errors():
    for exc in (TimeoutError("slow"), ConnectionError("down"), OSError("x"), RuntimeError("boom")):
        with pytest.raises(DirectoryError):
            fetcher(FakeHttp(exc=exc)).keys(URL)


def test_only_currently_valid_supported_strong_keys_are_returned():
    good = ed_jwk()[1]
    expired, future = ed_jwk(exp=NOW - 10)[1], ed_jwk(nbf=NOW + 1000)[1]
    unsupported = {"kty": "EC", "crv": "P-256", "x": "AA", "y": "AA"}
    weak = rsa.generate_private_key(65537, 1024).public_key().public_numbers()
    weak_jwk = {"kty": "RSA", "n": b64u(weak.n.to_bytes(128, "big")), "e": b64u(weak.e.to_bytes(3, "big"))}
    keys = parse_directory(body_of(good, expired, future, unsupported, weak_jwk), NOW, max_keys=20)
    assert list(keys) == [jwk_thumbprint(good)]


def test_rsa_keys_in_a_directory_are_loaded():
    pub = rsa.generate_private_key(65537, 2048).public_key().public_numbers()
    jwk = {"kty": "RSA", "n": b64u(pub.n.to_bytes(256, "big")), "e": b64u(pub.e.to_bytes(3, "big"))}
    alg, loaded, raw = parse_directory(body_of(jwk), NOW, 20)[jwk_thumbprint(jwk)]
    assert alg == "rsa-pss-sha256" and loaded.key_size == 2048


# ---- caching ----
def test_failures_are_cached_for_five_minutes():
    http = FakeHttp(status=500)
    clock = Clock()
    f = fetcher(http, clock)
    for _ in range(3):
        with pytest.raises(DirectoryError):
            f.keys(URL)
    assert len(http.calls) == 1
    clock.t += 301
    with pytest.raises(DirectoryError):
        f.keys(URL)
    assert len(http.calls) == 2


def test_successes_are_cached_then_refreshed():
    http = FakeHttp(body=body_of(ed_jwk()[1]))
    clock = Clock()
    f = fetcher(http, clock, pos_ttl=300)
    f.keys(URL), f.keys(URL)
    assert len(http.calls) == 1
    clock.t += 301
    f.keys(URL)
    assert len(http.calls) == 2


def test_the_cache_is_bounded():
    f = KeyDirectoryFetcher(resolve=lambda h: ["93.184.216.34"], http_get=FakeHttp(status=500), clock=Clock(), max_cache=5)
    for i in range(40):
        with pytest.raises(DirectoryError):
            f.keys(f"https://h{i}.example.com/d")
    assert len(f._cache) <= 5


def test_millisecond_timestamps_are_read_as_milliseconds():
    # a real public directory publishes nbf as 1743465600000 (April 2025 in milliseconds); read as seconds it never starts
    past_ms = ed_jwk(nbf=(NOW - 1000) * 1000)[1]
    future_ms = ed_jwk(nbf=(NOW + 1000) * 1000)[1]
    expired_ms = ed_jwk(exp=(NOW - 1000) * 1000)[1]
    keys = parse_directory(body_of(past_ms, future_ms, expired_ms), NOW, 20)
    assert list(keys) == [jwk_thumbprint(past_ms)]


def test_second_timestamps_keep_working():
    assert len(parse_directory(body_of(ed_jwk(nbf=NOW - 5)[1], ed_jwk(exp=NOW + 5)[1]), NOW, 20)) == 2
