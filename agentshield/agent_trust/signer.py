"""Reference signer: builds RFC 9421-style Signature-Input / Signature headers. Used by tests and the demo."""
import base64
import secrets

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_COVERED = ("@method", "@authority", "@path")


def public_bytes(private_key) -> bytes:
    """Raw 32 bytes for Ed25519, DER SubjectPublicKeyInfo for RSA."""
    if isinstance(private_key, rsa.RSAPrivateKey):
        return private_key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def component_line(name: str, method: str, authority: str, path: str, query: str) -> str:
    value = {"@method": method, "@authority": authority, "@path": path, "@query": query}.get(name, "")
    return f'"{name}": {value}'


def sign_request(private_key, keyid, *, method, authority, path, tag, now, lifetime=300, nonce=None,
                 covered=DEFAULT_COVERED, alg=None, query="", omit=(), rsa_salt="hash"):
    """Return {'signature-input', 'signature'}. `omit` drops parameters (for attack vectors).
    `alg` is only the label sent; it defaults to the key type. `rsa_salt` is "hash" (32 bytes) or "max"."""
    is_rsa = isinstance(private_key, rsa.RSAPrivateKey)
    alg = alg or ("rsa-pss-sha256" if is_rsa else "ed25519")
    params = {"created": now, "expires": now + lifetime, "nonce": nonce or secrets.token_urlsafe(12),
              "keyid": keyid, "alg": alg, "tag": tag}
    for p in omit:
        params.pop(p, None)
    inner = " ".join(f'"{c}"' for c in covered)
    parts = [f"{k}={v}" if isinstance(v, int) else f'{k}="{v}"' for k, v in params.items()]
    sig_params = f"({inner});" + ";".join(parts)
    base = "\n".join([component_line(c, method, authority, path, query) for c in covered]
                     + [f'"@signature-params": {sig_params}'])
    if is_rsa:
        salt = padding.PSS.MAX_LENGTH if rsa_salt == "max" else hashes.SHA256.digest_size
        raw = private_key.sign(base.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=salt), hashes.SHA256())
    else:
        raw = private_key.sign(base.encode())
    sig = base64.b64encode(raw).decode()
    return {"signature-input": f"sig1={sig_params}", "signature": f"sig1=:{sig}:"}
