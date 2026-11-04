import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from tiny_model import make_tiny_model  # noqa: E402

from agentshield.agent_trust.redis_store import RedisNonceStore  # noqa: E402

KEY = "k" * 40


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    return make_tiny_model(tmp_path_factory.mktemp("m"))


def build(model, **extra):
    import importlib

    # importing main builds a module level app from os.environ, so give it a harmless one first
    import os
    os.environ["AGENTSHIELD_API_KEY"] = KEY
    os.environ["AGENTSHIELD_MODEL_DIR"] = str(model)
    main = importlib.import_module("agentshield.serving.main")
    env = {"AGENTSHIELD_API_KEY": KEY, "AGENTSHIELD_MODEL_DIR": str(model), **extra}
    return main.build(env, redis_from_url=extra.pop("redis_from_url", None)) if "redis_from_url" in extra else main.build(env)


def test_a_short_api_key_is_refused(model):
    import importlib, os
    os.environ["AGENTSHIELD_API_KEY"] = KEY
    os.environ["AGENTSHIELD_MODEL_DIR"] = str(model)
    main = importlib.import_module("agentshield.serving.main")
    with pytest.raises(RuntimeError, match="32"):
        main.build({"AGENTSHIELD_API_KEY": "short", "AGENTSHIELD_MODEL_DIR": str(model)})


def test_several_workers_are_refused(model):
    with pytest.raises(RuntimeError, match="single worker"):
        build(model, WEB_CONCURRENCY="4")


def test_without_agent_settings_no_verifier_is_configured(model):
    app = build(model)
    assert app.state.verifier is None


def test_the_directory_fetcher_can_be_switched_on(model):
    app = build(model, AGENTSHIELD_AGENT_DIRECTORY="1")
    assert app.state.verifier.directory is not None


def test_a_visa_style_registry_file_can_be_loaded(model, tmp_path):
    from test_visa_registry import keys as _k  # noqa: F401  (fixtures are not importable; build the db inline instead)
    db = tmp_path / "reg.db"
    c = sqlite3.connect(db)
    c.executescript("""create table agents (id integer primary key, name text, domain text, description text, contact_email text,
                       is_active text, created_at text, updated_at text);
                       create table agent_keys (id integer primary key, agent_id integer, key_id text, public_key text, algorithm text,
                       description text, is_active text, created_at text, updated_at text);""")
    c.execute("insert into agents(id,name,domain,is_active) values (1,'A','a.example','true')")
    import base64
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from agentshield.agent_trust.signer import public_bytes
    c.execute("insert into agent_keys(agent_id,key_id,public_key,algorithm,is_active) values (1,'k-ed',?, 'ed25519','true')",
              (base64.b64encode(public_bytes(Ed25519PrivateKey.generate())).decode(),))
    c.commit()
    c.close()
    app = build(model, AGENTSHIELD_VISA_REGISTRY_DB=str(db))
    assert app.state.verifier.registry.get("k-ed").alg == "ed25519"


def test_agent_policy_is_read_from_the_environment(model):
    policy = {"authorities": ["shop.example.com"], "paths": ["/product", "/checkout"], "tag_routes": {"agent-payer-auth": ["/checkout"]}}
    app = build(model, AGENTSHIELD_AGENT_DIRECTORY="1", AGENTSHIELD_AGENT_POLICY=json.dumps(policy))
    t = app.state.tenants.tenants[0]
    assert t.agent_authorities == ("shop.example.com",) and t.agent_path_prefixes == ("/product", "/checkout")
    assert dict(t.agent_tag_routes) == {"agent-payer-auth": ("/checkout",)}


def test_a_bad_agent_policy_stops_startup(model):
    for bad in ("not json", json.dumps({"authorities": ["Shop.Example.COM"]}), json.dumps({"paths": ["nope"]})):
        with pytest.raises((RuntimeError, ValueError)):
            build(model, AGENTSHIELD_AGENT_DIRECTORY="1", AGENTSHIELD_AGENT_POLICY=bad)


def test_redis_is_used_for_nonces_when_configured(model):
    class Fake:
        def set(self, *a, **k):
            return True

        def incr(self, *a, **k):
            return 1

    app = build(model, AGENTSHIELD_AGENT_DIRECTORY="1", AGENTSHIELD_REDIS_URL="redis://example:6379/0", redis_from_url=lambda url: Fake())
    assert isinstance(app.state.verifier.nonces, RedisNonceStore)


def test_a_missing_redis_library_is_a_clear_error(model, monkeypatch):
    monkeypatch.setitem(sys.modules, "redis", None)  # makes `import redis` fail
    with pytest.raises(RuntimeError, match="redis"):
        build(model, AGENTSHIELD_AGENT_DIRECTORY="1", AGENTSHIELD_REDIS_URL="redis://example:6379/0")


def test_signed_directories_can_be_required(model):
    app = build(model, AGENTSHIELD_AGENT_DIRECTORY="1", AGENTSHIELD_REQUIRE_SIGNED_DIRECTORY="1")
    assert app.state.verifier.directory.require_signed is True
    assert build(model, AGENTSHIELD_AGENT_DIRECTORY="1").state.verifier.directory.require_signed is False  # off unless asked


def write_warm(path, n=5, bad=None):
    lines = [json.dumps({"customer_id": "c1", "amount_minor": 1000, "timestamp": 1_700_000_000 + i * 60}) for i in range(n)]
    if bad:
        lines.insert(2, bad)
    path.write_text("\n".join(lines) + "\n")
    return path


def test_a_warm_start_file_fills_the_store_at_startup(model, tmp_path):
    app = build(model, AGENTSHIELD_WARM_START=str(write_warm(tmp_path / "warm.jsonl")))
    f = app.state.store_for("demo").features("c1", None, 1_700_000_000 + 10_000, 1000)
    assert f["sender_prior_cnt"] == 5


def test_a_bad_warm_line_stops_startup_without_echoing_its_content(model, tmp_path):
    p = write_warm(tmp_path / "warm.jsonl", bad='{"customer_id": "c1", "amount_minor": -5, "timestamp": 1, "note": "SECRETVALUE"}')
    with pytest.raises(RuntimeError, match="line 3") as e:
        build(model, AGENTSHIELD_WARM_START=str(p))
    assert "SECRETVALUE" not in str(e.value)


def test_a_missing_warm_file_stops_startup(model, tmp_path):
    with pytest.raises(RuntimeError, match="warm"):
        build(model, AGENTSHIELD_WARM_START=str(tmp_path / "nope.jsonl"))


def test_the_number_of_warm_events_is_capped(model, tmp_path):
    p = write_warm(tmp_path / "warm.jsonl", n=20)
    with pytest.raises(RuntimeError, match="at most"):
        build(model, AGENTSHIELD_WARM_START=str(p), AGENTSHIELD_WARM_START_MAX="10")


def _tenants_file(tmp_path, entries):
    p = tmp_path / "tenants.json"
    p.write_text(json.dumps(entries))
    return str(p)


def _build_multi(model, path):
    import importlib, os
    os.environ["AGENTSHIELD_API_KEY"] = KEY
    os.environ["AGENTSHIELD_MODEL_DIR"] = str(model)
    main = importlib.import_module("agentshield.serving.main")
    return main.build({"AGENTSHIELD_TENANTS": path, "AGENTSHIELD_MODEL_DIR": str(model)})


def test_each_tenant_authenticates_with_its_own_key_only(model, tmp_path):
    from agentshield.serving.tenants import hash_key
    a, b = "a" * 40, "b" * 40
    app = _build_multi(model, _tenants_file(tmp_path, [{"tenant_id": "a", "key_hashes": [hash_key(a)]},
                                                       {"tenant_id": "b", "key_hashes": [hash_key(b)]}]))
    reg = app.state.tenants
    assert reg.authenticate(a).tenant_id == "a" and reg.authenticate(b).tenant_id == "b"
    assert reg.authenticate("c" * 40) is None


def test_a_rotating_tenant_accepts_old_and_new_keys_until_the_old_is_removed(model, tmp_path):
    from agentshield.serving.tenants import hash_key
    old, new = "o" * 40, "n" * 40
    both = _build_multi(model, _tenants_file(tmp_path, [{"tenant_id": "a", "key_hashes": [hash_key(new), hash_key(old)]}]))
    assert both.state.tenants.authenticate(old).tenant_id == both.state.tenants.authenticate(new).tenant_id == "a"
    only_new = _build_multi(model, _tenants_file(tmp_path, [{"tenant_id": "a", "key_hashes": [hash_key(new)]}]))
    assert only_new.state.tenants.authenticate(old) is None


def test_a_key_shared_by_two_tenants_is_refused(model, tmp_path):
    from agentshield.serving.tenants import hash_key
    h = hash_key("s" * 40)
    with pytest.raises(RuntimeError, match="more than one"):
        _build_multi(model, _tenants_file(tmp_path, [{"tenant_id": "a", "key_hashes": [h]}, {"tenant_id": "b", "key_hashes": [h]}]))


@pytest.mark.parametrize("entry", [{"tenant_id": "a", "key_hashes": ["notahash"]}, {"tenant_id": "a", "key_hashes": []},
                                   {"key_hashes": ["0" * 64]}, {"tenant_id": "", "key_hashes": ["0" * 64]},
                                   {"tenant_id": 5, "key_hashes": ["0" * 64]},
                                   {"tenant_id": "a", "key_hashes": ["0" * 64], "agnet": {}},
                                   {"tenant_id": "a", "key_hashes": ["0" * 64], "block_fp": 0.01}])
def test_a_bad_tenant_entry_is_refused(model, tmp_path, entry):
    with pytest.raises(RuntimeError, match="tenant"):
        _build_multi(model, _tenants_file(tmp_path, [entry]))


class _Redis:
    def __init__(self, up):
        self.up = up

    def ping(self):
        if not self.up:
            raise ConnectionError("down")
        return True


def _ready(model, up=None):
    from fastapi.testclient import TestClient
    extra = {} if up is None else {"AGENTSHIELD_AGENT_DIRECTORY": "1", "AGENTSHIELD_REDIS_URL": "redis://x"}
    import importlib, os
    os.environ["AGENTSHIELD_API_KEY"] = KEY
    os.environ["AGENTSHIELD_MODEL_DIR"] = str(model)
    main = importlib.import_module("agentshield.serving.main")
    env = {"AGENTSHIELD_API_KEY": KEY, "AGENTSHIELD_MODEL_DIR": str(model), **extra}
    app = main.build(env, redis_from_url=lambda u: _Redis(up)) if up is not None else main.build(env)
    return TestClient(app).get("/readyz")


def test_ready_without_redis(model):
    assert _ready(model).status_code == 200


def test_ready_when_redis_answers(model):
    assert _ready(model, up=True).status_code == 200


def test_not_ready_when_redis_is_down(model):
    assert _ready(model, up=False).status_code == 503


def test_an_empty_tenants_file_is_refused_with_a_clear_error(model, tmp_path):
    with pytest.raises(RuntimeError, match="tenant"):
        _build_multi(model, _tenants_file(tmp_path, []))


def test_two_tenants_with_the_same_id_are_refused(model, tmp_path):
    from agentshield.serving.tenants import hash_key
    with pytest.raises(RuntimeError, match="tenant_id"):
        _build_multi(model, _tenants_file(tmp_path, [{"tenant_id": "a", "key_hashes": [hash_key("1" * 40)]},
                                                      {"tenant_id": "a", "key_hashes": [hash_key("2" * 40)]}]))
