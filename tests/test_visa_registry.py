"""Importing a registry shaped like Visa's sample (agents and agent_keys tables, RSA labelled RSA-SHA256, string flags)."""
import base64
import sqlite3

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agentshield.agent_trust.signer import public_bytes
from agentshield.agent_trust.visa_registry import load_visa_registry


def pem(priv) -> str:
    return priv.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.fixture(scope="module")
def keys():
    return {"rsa": rsa.generate_private_key(65537, 2048), "rsa2": rsa.generate_private_key(65537, 2048),
            "weak": rsa.generate_private_key(65537, 1024), "ed": Ed25519PrivateKey.generate()}


def make_db(path, keys):
    c = sqlite3.connect(path)
    c.executescript("""create table agents (id integer primary key, name text, domain text, description text, contact_email text,
                       is_active text, created_at text, updated_at text);
                       create table agent_keys (id integer primary key, agent_id integer, key_id text, public_key text, algorithm text,
                       description text, is_active text, created_at text, updated_at text);""")
    c.executemany("insert into agents(id,name,domain,is_active) values (?,?,?,?)",
                  [(1, "Agent One", "one.example", "true"), (2, "Agent Two", "two.example", "true"), (3, "Retired", "old.example", "false")])
    ed_b64 = base64.b64encode(public_bytes(keys["ed"])).decode()
    rows = [(1, "k-rsa", pem(keys["rsa"]), "RSA-SHA256", "true"), (1, "k-ed", ed_b64, "ed25519", "true"),
            (1, "k-off", pem(keys["rsa2"]), "RSA-SHA256", "false"), (1, "k-weak", pem(keys["weak"]), "RSA-SHA256", "true"),
            (1, "k-junk", "not a key", "RSA-SHA256", "true"), (1, "k-alg", ed_b64, "hmac-sha256", "true"),
            (1, "primary", pem(keys["rsa"]), "RSA-SHA256", "true"), (2, "primary", pem(keys["rsa2"]), "RSA-SHA256", "true"),
            (3, "k-old", pem(keys["rsa2"]), "RSA-SHA256", "true")]
    c.executemany("insert into agent_keys(agent_id,key_id,public_key,algorithm,is_active) values (?,?,?,?,?)", rows)
    c.commit()
    c.close()


@pytest.fixture
def loaded(tmp_path, keys):
    db = tmp_path / "reg.db"
    make_db(db, keys)
    return load_visa_registry(db)


def test_valid_active_keys_are_loaded_with_the_algorithm_they_really_use(loaded):
    reg, report = loaded
    assert reg.get("k-rsa").alg == "rsa-pss-sha256" and reg.get("k-rsa").agent_id == "Agent One"  # RSA-SHA256 label means RSA-PSS
    assert reg.get("k-ed").alg == "ed25519"
    assert sorted(report["loaded"]) == ["k-ed", "k-rsa"]


def test_inactive_weak_malformed_and_unsupported_keys_are_skipped_with_reasons(loaded):
    reg, report = loaded
    skipped = report["skipped"]
    for kid in ("k-off", "k-weak", "k-junk", "k-alg"):
        assert reg.get(kid) is None and kid in skipped
    assert skipped["k-off"] == "inactive" and "2048" in skipped["k-weak"] and skipped["k-alg"] == "unsupported algorithm"


def test_a_key_id_shared_by_two_agents_resolves_for_neither(loaded):
    reg, report = loaded
    assert reg.get("primary") is None and report["skipped"]["primary"] == "ambiguous key id (used by several agents)"


def test_keys_of_a_deactivated_agent_are_not_trusted(loaded):
    reg, report = loaded
    rec = reg.get("k-old")
    assert rec is None or rec.active is False


def test_the_database_is_opened_read_only(tmp_path, keys):
    db = tmp_path / "reg.db"
    make_db(db, keys)
    before = db.read_bytes()
    load_visa_registry(db)
    assert db.read_bytes() == before


def test_a_missing_database_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_visa_registry(tmp_path / "nope.db")
