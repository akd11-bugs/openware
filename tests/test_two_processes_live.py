"""Two real API processes sharing one Redis: a signed request replayed against the OTHER process must be refused.
Skipped unless AGENTSHIELD_TEST_REDIS_URL is set."""
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).parent))
from test_visa_registry import make_db  # noqa: E402
from tiny_model import make_tiny_model  # noqa: E402

from agentshield.agent_trust.signer import sign_request  # noqa: E402
from agentshield.serving.tenants import hash_key  # noqa: E402

URL = os.environ.get("AGENTSHIELD_TEST_REDIS_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set AGENTSHIELD_TEST_REDIS_URL to a throwaway Redis")
KEY, HOST = "k" * 40, "shop.example.com"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def procs(tmp_path_factory):
    d = tmp_path_factory.mktemp("two")
    priv = Ed25519PrivateKey.generate()
    keys = {"ed": priv, "rsa": priv, "rsa2": priv, "weak": priv}
    make_db(d / "reg.db", keys)
    tenants = [{"tenant_id": "shop", "key_hashes": [hash_key(KEY)], "agent": {
        "authorities": [HOST], "paths": ["/checkout"], "tag_routes": {"agent-payer-auth": ["/checkout"]}}}]
    (d / "tenants.json").write_text(json.dumps(tenants))
    (d / "m").mkdir()
    model = make_tiny_model(d / "m")
    env = {**os.environ, "AGENTSHIELD_TENANTS": str(d / "tenants.json"), "AGENTSHIELD_MODEL_DIR": str(model),
           "AGENTSHIELD_VISA_REGISTRY_DB": str(d / "reg.db"), "AGENTSHIELD_REDIS_URL": URL}
    env.pop("AGENTSHIELD_API_KEY", None)
    ports, ps = [free_port(), free_port()], []
    for p in ports:
        ps.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "agentshield.serving.main:app", "--port", str(p), "--workers", "1"],
                                   env=env, cwd=Path(__file__).parent.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    for p in ports:
        for _ in range(100):
            try:
                if httpx.get(f"http://127.0.0.1:{p}/readyz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            pytest.fail("a worker did not start")
    yield priv, ports
    for p in ps:
        p.terminate()
        p.wait(10)


def call(port, priv, nonce, n):
    h = sign_request(priv, "k-ed", method="GET", authority=HOST, path="/checkout/pay", tag="agent-payer-auth", now=int(time.time()), nonce=nonce)
    body = {"transaction_id": f"tx-{n}-{uuid.uuid4().hex[:8]}", "customer_id": "c1", "amount_minor": 5000, "actor": "agent",
            "agent_request": {"method": "GET", "authority": HOST, "path": "/checkout/pay", "query": "", "headers": h}}
    return httpx.post(f"http://127.0.0.1:{port}/v1/assess", json=body, headers={"x-api-key": KEY}, timeout=5).json()


def test_a_request_replayed_on_the_other_process_is_refused(procs):
    priv, (a, b) = procs
    nonce = "replay-" + uuid.uuid4().hex
    first, second = call(a, priv, nonce, 1), call(b, priv, nonce, 2)
    assert first["agent"]["outcome"] == "verified"
    assert second["agent"]["outcome"] == "invalid" and second["action"] == "block"


def test_different_requests_each_verify_on_either_process(procs):
    priv, (a, b) = procs
    assert call(a, priv, "n-" + uuid.uuid4().hex, 3)["agent"]["outcome"] == "verified"
    assert call(b, priv, "n-" + uuid.uuid4().hex, 4)["agent"]["outcome"] == "verified"
