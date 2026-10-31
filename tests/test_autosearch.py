import pandas as pd
import pytest

from agentshield.txn.autosearch import validate_source, wrap_module, parse_proposal

GOOD = """
def feat(df):
    g = df.groupby("sender_id")["amount_minor"]
    return pd.DataFrame({"x_lag_amt": g.shift(1)}, index=df.index)
"""


def test_valid_source_passes():
    validate_source(GOOD)


@pytest.mark.parametrize(
    "bad",
    [
        "import os\ndef feat(df):\n    return df",
        "from subprocess import run\ndef feat(df):\n    return df",
        "def feat(df):\n    return open('x').read()",
        "def feat(df):\n    eval('1')\n    return df",
        "def feat(df):\n    exec('1')\n    return df",
        "def feat(df):\n    return __import__('os')",
        "def feat(df):\n    return df.__class__",
        "def feat(df):\n    return df.to_csv('x.csv')",
        "def feat(df):\n    return pd.read_csv('x.csv')",
        "x = 1",  # no feat function
        "def feat(df, other):\n    return df",  # wrong arity
    ],
)
def test_unsafe_or_malformed_source_rejected(bad):
    with pytest.raises(ValueError):
        validate_source(bad)


def test_wrap_module_registers_causal_group_by_name():
    text = wrap_module("my_grp", GOOD)
    assert "register" in text and '"my_grp"' in text and "causal=True" in text
    compile(text, "<wrapped>", "exec")


def test_group_name_must_be_a_safe_identifier():
    with pytest.raises(ValueError):
        wrap_module("bad name; import os", GOOD)


def test_parse_proposal_groups_and_params():
    p = parse_proposal('here you go\n```json\n{"action":"params","params":{"num_leaves":96}}\n```')
    assert p == {"action": "params", "params": {"num_leaves": 96}}
    g = parse_proposal('{"action":"group","name":"lag","code":"def feat(df):\\n    return df"}')
    assert g["action"] == "group" and g["name"] == "lag"


def test_parse_proposal_rejects_garbage_and_unknown_param():
    with pytest.raises(ValueError):
        parse_proposal("no json here")
    with pytest.raises(ValueError):
        parse_proposal('{"action":"params","params":{"os_system":1}}')
    with pytest.raises(ValueError):
        parse_proposal('{"action":"delete_everything"}')


@pytest.mark.parametrize("bad", [
    'def feat(df):\n    return df.query("@pd.io.common.os.getcwd()==\'\'")',
    'def feat(df):\n    return df.eval("a + b")',
    'def feat(df):\n    return pd.io.pickle.pickle.loads(b"x")',
    'def feat(df):\n    return np.load("f.npy", allow_pickle=True)',
    'def feat(df):\n    return pd.read_table("http://example.com/x")',
    'def feat(df):\n    return pd.read_fwf("f.txt")',
    'def feat(df):\n    np.save("out.npy", df.values)\n    return df',
    'def feat(df):\n    df.values.tofile("x.bin")\n    return df',
    'def feat(df):\n    return df.to_xml("x.xml")',
    "x = 1 + 1\ndef feat(df):\n    return df",
    "def helper():\n    pass\ndef feat(df):\n    return df",
    "print('hi')\ndef feat(df):\n    return df",
])
def test_known_escape_routes_and_top_level_code_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_source(bad)


def test_a_docstring_is_still_allowed():
    validate_source('"""notes"""\ndef feat(df):\n    return pd.DataFrame({"x": df["amount_minor"].shift(1)}, index=df.index)')


def test_child_environment_has_no_secrets():
    from agentshield.txn.autosearch import safe_child_env
    env = safe_child_env({"PATH": "/usr/bin", "HOME": "/h", "ANTHROPIC_API_KEY": "sk-ant-x", "AWS_SECRET_ACCESS_KEY": "s",
                          "GITHUB_TOKEN": "t", "KAGGLE_KEY": "k", "LANG": "en_US.UTF-8"}, "/repo")
    assert env["PATH"] == "/usr/bin" and env["PYTHONPATH"] == "/repo"
    assert not any(k for k in env if any(w in k.upper() for w in ("KEY", "TOKEN", "SECRET", "PASSWORD")))


def test_an_existing_group_file_is_never_overwritten(tmp_path):
    from agentshield.txn.autosearch import ensure_new
    p = tmp_path / "features_foo.py"
    ensure_new(p)
    p.write_text("x")
    with pytest.raises(FileExistsError):
        ensure_new(p)
