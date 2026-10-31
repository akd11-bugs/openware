"""Safety helpers for the LLM-proposer search loop (scripts/auto_search.py).

This is a screen plus a scrubbed environment, NOT a sandbox: a denylist cannot be complete. For untrusted proposers run the
child in a container or with no network and a read-only filesystem.


The model proposes either LightGBM params or a feature function. Proposed code is parsed with `ast`,
restricted to pandas/numpy-style expressions, written to a file and run only in subprocesses by the harness.
"""
import ast
import json
import re

ALLOWED_PARAMS = {
    "learning_rate", "num_leaves", "min_child_samples", "feature_fraction", "bagging_fraction",
    "lambda_l1", "lambda_l2", "max_bin", "min_gain_to_split", "scale_pos_weight", "cat_smooth",
    "cat_l2", "max_depth", "min_sum_hessian_in_leaf", "extra_trees", "path_smooth",
}
BANNED_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "input", "globals", "locals", "vars",
    "getattr", "setattr", "delattr", "breakpoint", "exit", "quit", "os", "sys", "subprocess", "socket",
}
BANNED_ATTRS = {
    "to_csv", "to_pickle", "to_parquet", "to_json", "to_excel", "to_sql", "to_hdf", "to_feather",
    "read_csv", "read_pickle", "read_parquet", "read_json", "read_excel", "read_sql", "read_hdf",
    "system", "popen", "remove", "unlink", "rmdir",
    # string-evaluating, pickle, file, and network routes found by the security audit
    "query", "eval", "io", "pickle", "load", "loads", "dump", "dumps", "save", "savez", "savez_compressed", "savetxt",
    "tofile", "fromfile", "loadtxt", "genfromtxt", "memmap", "open_memmap", "read_table", "read_fwf", "read_xml",
    "read_html", "read_clipboard", "read_orc", "read_feather", "read_sas", "read_spss", "read_stata", "to_xml",
    "to_string", "to_latex", "to_markdown", "to_clipboard", "to_orc", "to_stata",
}
SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "OMP_NUM_THREADS")
_IDENT = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def validate_source(src: str) -> None:
    """Raise ValueError unless src defines `feat(df)` using only safe constructs (no imports, I/O or dunders)."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e}") from e
    feat = None
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
        body = body[1:]  # a docstring is fine
    if len(body) != 1 or not isinstance(body[0], ast.FunctionDef):
        raise ValueError("the module may contain only one function, feat (no top-level code)")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("imports are not allowed (pd and np are provided)")
        if isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            raise ValueError(f"banned name: {node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in BANNED_ATTRS or node.attr in BANNED_NAMES:
                raise ValueError(f"banned attribute: {node.attr}")
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "feat":
            feat = node
    if feat is None:
        raise ValueError("must define feat(df)")
    if len(feat.args.args) != 1 or feat.args.vararg or feat.args.kwarg or feat.args.kwonlyargs:
        raise ValueError("feat must take exactly one argument (df)")


def safe_child_env(env: dict, root) -> dict:
    """Environment for the subprocess that runs proposed code: no API keys, tokens or other secrets are inherited."""
    out = {k: v for k, v in env.items() if k in SAFE_ENV_KEYS}
    out["PYTHONPATH"] = str(root)
    return out


def ensure_new(path) -> None:
    """Never overwrite a kept group file: a reused name would silently change an earlier, accepted experiment."""
    from pathlib import Path

    if Path(path).exists():
        raise FileExistsError(f"{path} already exists")


def wrap_module(name: str, src: str) -> str:
    if not _IDENT.match(name):
        raise ValueError("group name must match [a-z][a-z0-9_]{1,40}")
    validate_source(src)
    return (
        "import numpy as np\nimport pandas as pd\n"
        "from agentshield.txn.experiment import register\n\n"
        f"{src.strip()}\n\n"
        f'register("{name}", causal=True)(feat)\n'
    )


def parse_proposal(text: str) -> dict:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{.*\})", text, re.S)
    if not m:
        raise ValueError("no JSON object in reply")
    try:
        p = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise ValueError(f"bad JSON: {e}") from e
    action = p.get("action")
    if action == "params":
        params = p.get("params")
        if not isinstance(params, dict) or not params:
            raise ValueError("params must be a non-empty object")
        bad = set(params) - ALLOWED_PARAMS
        if bad:
            raise ValueError(f"param not allowed: {sorted(bad)}")
        return {"action": "params", "params": params}
    if action == "group":
        if not isinstance(p.get("name"), str) or not isinstance(p.get("code"), str):
            raise ValueError("group needs name and code strings")
        return {"action": "group", "name": p["name"], "code": p["code"]}
    raise ValueError("action must be 'params' or 'group'")
