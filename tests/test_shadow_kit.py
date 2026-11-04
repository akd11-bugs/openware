import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "examples"))
from shadow_middleware import check  # noqa: E402

TXN = {"transaction_id": "t1", "customer_id": "c1", "amount_minor": 5000}
REQ = {"method": "GET", "authority": "shop.example.com", "path": "/p", "headers": {"signature": "SECRET-SIG"}}


class Resp:
    def __init__(self, code=200, body=None):
        self.status_code, self._b = code, body or {}

    def json(self):
        return self._b


def api(action="block", code=200):
    body = {"action": action, "agent": {"outcome": "invalid"}, "reasons": [{"code": "AGENT_BAD_SIGNATURE"}], "latency_ms": 0.1}
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append((url, json, headers, timeout))
        return Resp(code, body)
    post.calls = calls
    return post


def run(tmp_path, post, shadow):
    log = tmp_path / "log.jsonl"
    return check(TXN, REQ, url="https://x/v1/assess", api_key="KEY", shadow=shadow, log_path=log, post=post), log


def test_enforce_returns_the_api_action(tmp_path):
    post = api("block")
    assert run(tmp_path, post, shadow=False)[0] == "block"
    url, body, headers, timeout = post.calls[0]
    assert headers["x-api-key"] == "KEY" and body["actor"] == "agent" and body["agent_request"] == REQ and timeout


def test_shadow_never_blocks_but_logs_what_would_have_happened(tmp_path):
    action, log = run(tmp_path, api("block"), shadow=True)
    rec = json.loads(log.read_text())
    assert action == "allow" and rec["would_do"] == "block" and rec["reasons"] == ["AGENT_BAD_SIGNATURE"]


def test_an_unreachable_api_means_challenge_not_allow(tmp_path):
    def boom(*a, **k):
        raise ConnectionError
    assert run(tmp_path, boom, shadow=False)[0] == "challenge"
    assert run(tmp_path, boom, shadow=True)[0] == "allow"


def test_a_server_error_means_challenge(tmp_path):
    assert run(tmp_path, api("allow", code=500), shadow=False)[0] == "challenge"


def test_the_log_holds_no_signatures_or_keys(tmp_path):
    _, log = run(tmp_path, api("allow"), shadow=True)
    assert "SECRET-SIG" not in log.read_text() and "KEY" not in log.read_text()
