"""Reference signer: builds RFC 9421-style Signature-Input / Signature headers. Used by tests and the demo."""
import base64
import secrets

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

DEFAULT_COVERED = ("@method", "@authority", "@path")


def public_bytes(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def component_line(name: str, method: str, authority: str, path: str, query: str) -> str:
    value = {"@method": method, "@authority": authority, "@path": path, "@query": query}.get(name, "")
    return f'"{name}": {value}'


def sign_request(private_key, keyid, *, method, authority, path, tag, now, lifetime=300, nonce=None,
                 covered=DEFAULT_COVERED, alg="ed25519", query="", omit=()):
    """Return {'signature-input', 'signature'}. `omit` drops parameters (for attack vectors)."""
    params = {"created": now, "expires": now + lifetime, "nonce": nonce or secrets.token_urlsafe(12),
              "keyid": keyid, "alg": alg, "tag": tag}
    for p in omit:
        params.pop(p, None)
    inner = " ".join(f'"{c}"' for c in covered)
    parts = [f"{k}={v}" if isinstance(v, int) else f'{k}="{v}"' for k, v in params.items()]
    sig_params = f"({inner});" + ";".join(parts)
    base = "\n".join([component_line(c, method, authority, path, query) for c in covered]
                     + [f'"@signature-params": {sig_params}'])
    sig = base64.b64encode(private_key.sign(base.encode())).decode()
    return {"signature-input": f"sig1={sig_params}", "signature": f"sig1=:{sig}:"}
