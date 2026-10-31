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
