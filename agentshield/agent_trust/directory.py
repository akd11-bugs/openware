"""Fetch an agent's public keys from its key directory (a JWKS), without letting the agent aim us at our own network.

Rules, from the Web Bot Auth drafts and the Visa sample review:
- https only, port 443, no credentials in the URL, no redirects.
- the host is resolved first and every address must be public; the request then goes to that exact address, so a
  DNS answer cannot change between the check and the connection (SNI and the Host header keep the real name).
- a size cap, a key count cap and a short timeout.
- keys are identified by their JWK thumbprint (RFC 7638 / 8037), never by a name the directory chooses.
- failures are cached for five minutes so a broken or hostile directory cannot make us fetch on every request.
Any failure raises DirectoryError, which the verifier turns into "unverified" (never "allow").
"""
import base64
import hashlib
import ipaddress
import json
import socket
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .directory_signature import verify_directory_signatures  # noqa: E402

MAX_BYTES, MAX_KEYS, TIMEOUT, NEG_TTL, POS_TTL = 65_536, 20, 3.0, 300, 300
ALLOWED_TYPES = {"application/http-message-signatures-directory+json", "application/jwk-set+json"}


class DirectoryError(Exception):
    pass


def is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return addr.is_global and not addr.is_multicast


def default_resolve(host: str) -> list[str]:
    try:
        return [ai[4][0] for ai in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    except OSError as e:
        raise DirectoryError("cannot resolve host") from e


def check_url(url: str, resolve=default_resolve) -> tuple[str, str, str]:
    """Return (host, checked ip, path) or raise DirectoryError."""
    if not isinstance(url, str) or not url or len(url) > 2048:
        raise DirectoryError("bad url")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as e:
        raise DirectoryError("bad url") from e
    if parts.scheme != "https" or parts.username or parts.password or parts.fragment:
        raise DirectoryError("url must be plain https")
    host = parts.hostname
    if not host or port not in (None, 443):
        raise DirectoryError("host or port not allowed")
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        ips = list(resolve(host))
    if not ips or not all(is_public(i) for i in ips):
        raise DirectoryError("address not allowed")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, ips[0], path


def _b64u_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def jwk_thumbprint(jwk: dict) -> str:
    """RFC 7638 thumbprint (with RFC 8037 for Ed25519): SHA-256 over the required members, sorted, no spaces."""
    members = {"OKP": ("crv", "kty", "x"), "RSA": ("e", "kty", "n")}[jwk["kty"]]
    canon = json.dumps({m: jwk[m] for m in members}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(hashlib.sha256(canon.encode()).digest()).decode().rstrip("=")


def parse_directory(body: bytes, now: float, max_keys: int = MAX_KEYS) -> dict:
    """thumbprint -> (alg, loaded public key, raw bytes). Unsupported, weak, expired or not yet valid keys are skipped."""
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError) as e:
        raise DirectoryError("not json") from e
    keys = doc.get("keys") if isinstance(doc, dict) else None
    if not isinstance(keys, list):
        raise DirectoryError("no key list")
    if len(keys) > max_keys:
        raise DirectoryError("too many keys")
    out = {}
    for jwk in keys:
        try:
            if not isinstance(jwk, dict) or jwk.get("use", "sig") != "sig":
                continue
            nbf, exp = jwk.get("nbf"), jwk.get("exp")
            if (nbf is not None and nbf > now) or (exp is not None and exp <= now):
                continue
            if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519":
                raw = _b64u_decode(jwk["x"])
                if len(raw) != 32:
                    continue
                out[jwk_thumbprint(jwk)] = ("ed25519", Ed25519PublicKey.from_public_bytes(raw), raw)
            elif jwk.get("kty") == "RSA":
                pub = rsa.RSAPublicNumbers(int.from_bytes(_b64u_decode(jwk["e"]), "big"),
                                           int.from_bytes(_b64u_decode(jwk["n"]), "big")).public_key()
                if pub.key_size < 2048:
                    continue
                der = pub.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                out[jwk_thumbprint(jwk)] = ("rsa-pss-sha256", pub, der)
        except (KeyError, ValueError, TypeError):
            continue  # one malformed key does not spoil the others
    return out


def default_http_get(ip: str, host: str, path: str, timeout: float, max_bytes: int):
    import httpx

    netloc = f"[{ip}]" if ":" in ip else ip
    with httpx.Client(timeout=timeout, follow_redirects=False, verify=True) as client:
        with client.stream("GET", f"https://{netloc}{path}", headers={"Host": host, "Accept": ", ".join(sorted(ALLOWED_TYPES))},
                           extensions={"sni_hostname": host}) as r:
            chunks, size = [], 0
            for chunk in r.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise DirectoryError("response too large")
                chunks.append(chunk)
            wanted = ("content-digest", "signature", "signature-input")
            return r.status_code, r.headers.get("content-type", ""), b"".join(chunks), {k: r.headers[k] for k in wanted if k in r.headers}


class KeyDirectoryFetcher:
    def __init__(self, resolve=default_resolve, http_get=default_http_get, clock=time.time, timeout: float = TIMEOUT,
                 max_bytes: int = MAX_BYTES, max_keys: int = MAX_KEYS, pos_ttl: int = POS_TTL, neg_ttl: int = NEG_TTL,
                 max_cache: int = 1000, require_signed: bool = False) -> None:
        self._resolve, self._http_get, self._clock = resolve, http_get, clock
        self.timeout, self.max_bytes, self.max_keys = timeout, max_bytes, max_keys
        self.pos_ttl, self.neg_ttl, self.max_cache = pos_ttl, neg_ttl, max_cache
        self.require_signed = require_signed  # only keep keys that signed the directory response themselves
        self._cache: OrderedDict = OrderedDict()  # url -> (expires_at, keys or None)

    def keys(self, url: str) -> dict:
        now = self._clock()
        hit = self._cache.get(url)
        if hit is not None and hit[0] > now:
            if hit[1] is None:
                raise DirectoryError("directory unavailable (cached)")
            return hit[1]
        try:
            host, ip, path = check_url(url, self._resolve)
            result = self._http_get(ip, host, path, self.timeout, self.max_bytes)
            status, ctype, body = result[:3]
            resp_headers = result[3] if len(result) > 3 else {}
            if status != 200:  # redirects and everything else are failures; we never follow them
                raise DirectoryError(f"status {status}")
            if ctype.split(";")[0].strip().lower() not in ALLOWED_TYPES:
                raise DirectoryError("unexpected content type")
            if len(body) > self.max_bytes:
                raise DirectoryError("response too large")
            keys = parse_directory(body, now, self.max_keys)
            if self.require_signed:
                signed = verify_directory_signatures(resp_headers, body, host, keys, now)
                keys = {k: v for k, v in keys.items() if k in signed}
                if not keys:
                    raise DirectoryError("directory is not signed by its own keys")
        except DirectoryError:
            self._store(url, now + self.neg_ttl, None)
            raise
        except Exception as e:  # noqa: BLE001 - timeouts, TLS errors, anything else
            self._store(url, now + self.neg_ttl, None)
            raise DirectoryError("fetch failed") from e
        self._store(url, now + self.pos_ttl, keys)
        return keys

    def _store(self, url: str, expires: float, keys) -> None:
        self._cache[url] = (expires, keys)
        self._cache.move_to_end(url)
        while len(self._cache) > self.max_cache:
            self._cache.popitem(last=False)
