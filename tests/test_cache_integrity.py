import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from experiment import _load_verified, _save_verified  # noqa: E402


def test_a_saved_cache_loads_and_a_tampered_one_is_refused(tmp_path):
    p = tmp_path / "c.pkl"
    df = pd.DataFrame({"a": [1, 2, 3]})
    _save_verified(df, p)
    assert _load_verified(p).equals(df)
    p.write_bytes(p.read_bytes() + b"tampered")  # any change to the file must be caught before unpickling
    assert _load_verified(p) is None


def test_a_cache_without_a_recorded_hash_is_refused(tmp_path):
    p = tmp_path / "c.pkl"
    pd.DataFrame({"a": [1]}).to_pickle(p)  # no sidecar hash
    assert _load_verified(p) is None


def test_missing_cache_returns_none(tmp_path):
    assert _load_verified(tmp_path / "nope.pkl") is None
