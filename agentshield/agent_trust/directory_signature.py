"""Check that a key directory response is signed by keys in the directory itself (Web Bot Auth, tag
http-message-signatures-directory). The signature must cover the request authority and the Content-Digest, the digest must
match the body, the tag and algorithm must be right, and the time window must hold. Only keys with a valid signature count.
Anything malformed simply yields no valid signature.
"""
import base64
import hashlib
import hmac
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

TAG = "http-message-signatures-directory"
MAX_LIFETIME, SKEW = 7 * 86400, 60
_INPUT = re.compile(r'^([a-z][a-z0-9_-]{0,15})=\(((?:"[@a-z-]+"(?:;req)?(?: "[@a-z-]+"(?:;req)?)*)?)\)((?:; *[A-Za-z]+=(?:\d{1,12}|"[A-Za-z0-9._~-]{1,64}"))*)\Z')
_SIG = re.compile(r"^([a-z][a-z0-9_-]{0,15})=:([A-Za-z0-9+/]+={0,2}):\Z")
_DIGEST = re.compile(r"^sha-256=:([A-Za-z0-9+/]{43}=):\Z")
REQUIRED = {("@authority", ";req"), ("content-digest", "")}


def verify_directory_signatures(headers: dict, body: bytes, authority: str, keys: dict, now: float) -> set:
    """Return the key ids (thumbprints) in `keys` that signed this response validly."""
    h = {k.lower(): v for k, v in headers.items()}
    inp, sig, digest = h.get("signature-input"), h.get("signature"), h.get("content-digest", "")
    if not inp or not sig or len(inp) > 8192 or len(sig) > 16384:
        return set()
    md = _DIGEST.match(digest)
    if not md or not hmac.compare_digest(base64.b64decode(md.group(1)), hashlib.sha256(body).digest()):
        return set()
    sigs = {m.group(1): m.group(2) for part in sig.split(",") if (m := _SIG.match(part.strip()))}
    valid = set()
    for part in inp.split(","):
        part = part.strip()
        m = _INPUT.match(part)
        if not m or m.group(1) not in sigs:
            continue
        if set(re.findall(r'"([@a-z-]+)"(;req)?', m.group(2))) != REQUIRED:
            continue
        params, dup = {}, False
        for item in filter(None, m.group(3).split(";")):
            k, v = item.strip().split("=", 1)
            dup |= k in params
            params[k] = int(v) if v.isdigit() else v.strip('"')
        entry = keys.get(params.get("keyid"))
        created, expires = params.get("created"), params.get("expires")
        if dup or entry is None or params.get("tag") != TAG or params.get("alg") != entry[0]:
            continue
        if not isinstance(created, int) or not isinstance(expires, int):
            continue
        if created > now + SKEW or expires <= now or expires <= created or expires - created > MAX_LIFETIME:
            continue
        base = f'"@authority";req: {authority}\n"content-digest": {digest}\n"@signature-params": {part.split("=", 1)[1]}'
        try:
            raw = base64.b64decode(sigs[m.group(1)])
            if entry[0] == "ed25519":
                entry[1].verify(raw, base.encode())
            else:
                entry[1].verify(raw, base.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.AUTO), hashes.SHA256())
        except (InvalidSignature, ValueError):
            continue
        valid.add(params["keyid"])
    return valid
