"""Reference signer: builds RFC 9421-style Signature-Input / Signature headers. Used by tests and the demo."""
import base64
import hashlib
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


def content_digest(body: bytes) -> str:
    """RFC 9530 Content-Digest header value (sha-256)."""
    return f"sha-256=:{base64.b64encode(hashlib.sha256(body).digest()).decode()}:"


def component_line(name: str, method: str, authority: str, path: str, query: str, digest: str = "", sigagent: str = "") -> str:
    value = {"@method": method, "@authority": authority, "@path": path, "@query": query, "content-digest": digest,
             "signature-agent": sigagent}.get(name, "")
    return f'"{name}": {value}'


DIRECTORY_TAG = "http-message-signatures-directory"


def sign_directory_response(private_key, keyid, *, authority, body, now, lifetime=3600, tag=DIRECTORY_TAG, label="sig1", alg=None,
                            components=("@authority;req", "content-digest")):
    """Headers a key directory sends so clients can check the response was signed by one of its own keys."""
    digest = content_digest(body)
    is_rsa = isinstance(private_key, rsa.RSAPrivateKey)
    alg = alg or ("rsa-pss-sha256" if is_rsa else "ed25519")
    inner = " ".join('"@authority";req' if c == "@authority;req" else f'"{c}"' for c in components)
    params = f'({inner});created={now};expires={now + lifetime};keyid="{keyid}";alg="{alg}";tag="{tag}"'
    lines = [f'"@authority";req: {authority}' if c == "@authority;req" else f'"content-digest": {digest}' for c in components]
    base = "\n".join(lines + [f'"@signature-params": {params}'])
    if is_rsa:
        raw = private_key.sign(base.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256.digest_size), hashes.SHA256())
    else:
        raw = private_key.sign(base.encode())
    return {"content-digest": digest, "signature-input": f"{label}={params}", "signature": f"{label}=:{base64.b64encode(raw).decode()}:"}


def sign_request(private_key, keyid, *, method, authority, path, tag, now, lifetime=300, nonce=None,
                 covered=DEFAULT_COVERED, alg=None, query="", omit=(), rsa_salt="hash", body=None, cover_body=True,
                 signature_agent=None, cover_agent=True):
    """Return {'signature-input', 'signature'}. `omit` drops parameters (for attack vectors).
    `alg` is only the label sent; it defaults to the key type. `rsa_salt` is "hash" (32 bytes) or "max"."""
    digest = content_digest(body) if body is not None else ""
    if body is not None and cover_body and "content-digest" not in covered:
        covered = (*covered, "content-digest")
    agent_header = f'sig1="{signature_agent}"' if signature_agent is not None else ""
    if signature_agent is not None and cover_agent and "signature-agent" not in covered:
        covered = (*covered, "signature-agent")
    is_rsa = isinstance(private_key, rsa.RSAPrivateKey)
    alg = alg or ("rsa-pss-sha256" if is_rsa else "ed25519")
    params = {"created": now, "expires": now + lifetime, "nonce": nonce or secrets.token_urlsafe(12),
              "keyid": keyid, "alg": alg, "tag": tag}
    for p in omit:
        params.pop(p, None)
    inner = " ".join(f'"{c}"' for c in covered)
    parts = [f"{k}={v}" if isinstance(v, int) else f'{k}="{v}"' for k, v in params.items()]
    sig_params = f"({inner});" + ";".join(parts)
    base = "\n".join([component_line(c, method, authority, path, query, digest, agent_header) for c in covered]
                     + [f'"@signature-params": {sig_params}'])
    if is_rsa:
        salt = padding.PSS.MAX_LENGTH if rsa_salt == "max" else hashes.SHA256.digest_size
        raw = private_key.sign(base.encode(), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=salt), hashes.SHA256())
    else:
        raw = private_key.sign(base.encode())
    sig = base64.b64encode(raw).decode()
    headers = {"signature-input": f"sig1={sig_params}", "signature": f"sig1=:{sig}:"}
    if body is not None:
        headers["content-digest"] = digest
    if signature_agent is not None:
        headers["signature-agent"] = agent_header
    return headers
