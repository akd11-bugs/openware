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
