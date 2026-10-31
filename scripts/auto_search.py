"""LLM-proposer search loop: Claude Sonnet proposes, our harness checks and scores (validation only).

  scripts/auto_search.py --track features --max-rounds 6 --token-cap 80000 --n-jobs 4

Each round: Sonnet proposes a LightGBM param change or a new feature function. Feature code is AST-validated,
must pass scripts/check_causal.py, and is only ever executed in subprocesses. A proposal is kept only if
validation AUC improves by >= MIN_GAIN. Stops after 3 non-improving rounds, max rounds, or the token cap.
Needs ANTHROPIC_API_KEY (loaded from .env). Never touches the test period.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd  # noqa: E402

from agentshield.txn.autosearch import ensure_new, parse_proposal, safe_child_env, sandbox_command, wrap_module  # noqa: E402
from check_key import load_env  # noqa: E402

MODEL = "claude-sonnet-5-5"
# Proposed code runs with no network, no secrets, and writes only here (results log and the verified data cache)
WRITE_DIRS = [ROOT / "docs/results", ROOT / "data/cache"]
MIN_GAIN = 0.001
BASE_GROUPS = ["card_hist", "roll_counts", "amt_cents"]
BASE_PARAMS = {"learning_rate": 0.03, "feature_fraction": 0.3, "min_child_samples": 120, "num_leaves": 192,
               "min_sum_hessian_in_leaf": 1, "bagging_fraction": 0.8, "lambda_l1": 1, "max_bin": 511}
SEED_HISTORY = [
    "group amt_stats: -0.0019 (rejected)", "group newness: -0.0028 (rejected)",
    "group gaps: -0.0009 (rejected)", "group hour_dev: -0.0029 (rejected)",
    "params num_leaves=64: -0.0039; num_leaves=256: +0.0001; lambda_l2=5: -0.0015; lambda_l2=20: -0.0046 (all rejected)",
    "already kept: num_leaves 192, lambda_l1 1, max_bin 511, min_sum_hessian 1 (+0.0014); feature_fraction 0.2: -0.0024, 0.4: -0.0028; "
    "min_gain_to_split 0.05 + path_smooth 10: -0.0012 (rejected)",
]


def columns_summary() -> str:
    df = pd.read_pickle(ROOT / "data/cache/ieee_std.pkl").iloc[:5]
    x = [c for c in df.columns if c.startswith("x_")]
    fam = {}
    for c in x:
        fam.setdefault("".join(ch for ch in c[2:] if not ch.isdigit()), []).append(c)
    parts = [f"{k}* ({len(v)} cols, e.g. {v[0]}..{v[-1]})" if len(v) > 3 else ", ".join(v) for k, v in fam.items()]
    return "ts (int seconds), amount_minor (int), sender_id (str, card1), label (NEVER use), " + "; ".join(parts)


SYSTEM = """You are improving a LightGBM fraud model on IEEE-CIS (3.5% fraud) by raising VALIDATION AUC. Reply with ONE JSON object only, in a ```json block, nothing else.

Two possible actions:
1. {"action":"params","params":{...}} changing LightGBM params. Allowed: learning_rate, num_leaves, min_child_samples, feature_fraction, bagging_fraction, lambda_l1, lambda_l2, max_bin, min_gain_to_split, scale_pos_weight, cat_smooth, cat_l2, max_depth, min_sum_hessian_in_leaf, extra_trees, path_smooth.
2. {"action":"group","name":"snake_case_name","code":"def feat(df): ..."} adding a feature function. Rules for code: no imports (pd and np are provided); define exactly `def feat(df):` returning a pd.DataFrame with the SAME index as df, with NEW column names that do not already exist; df rows are sorted by ts ascending. It MUST be causal: a row's features may use only EARLIER rows (and the row itself), never later rows, never the label column, no whole-column statistics (no df[col].mean(), no value_counts over all rows, no global quantiles); use groupby(...).shift/cumcount/expanding/rolling in time order. No file or network access. Features that fail the strict causality check are rejected automatically.

Columns in df: {cols}. C* are counts, D* are time deltas in days, M* are match flags (T/F), V* are engineered features, id_* identity/device info.

Keep the change small and distinct; do not repeat anything in the history. Prefer ideas with a real mechanism for fraud (for example the card's behaviour changing, an unusual combination of fields, bursts, first-time seen values) over generic statistics."""


def call_model(client, system: str, user: str):
    r = client.messages.create(model=MODEL, max_tokens=2200, system=system, messages=[{"role": "user", "content": user}])
    text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")  # skip thinking blocks
    return text, r.usage.input_tokens + r.usage.output_tokens


def run_experiment(groups, params, extras, track, n_jobs):
    cmd = [sys.executable, str(ROOT / "scripts/experiment.py"), "--agent", f"auto_{track}", "--n-jobs", str(n_jobs),
           "--groups", ",".join(groups), "--params", json.dumps(params)]
    for e in extras:
        cmd += ["--extra", e]
    p = subprocess.run(sandbox_command(cmd, ROOT, WRITE_DIRS), capture_output=True, text=True, timeout=1500, cwd=ROOT, env=safe_child_env(os.environ, ROOT))
    if p.returncode != 0:
        raise RuntimeError(p.stderr[-300:])
    return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--track", choices=["features", "params", "mixed"], default="mixed")
    ap.add_argument("--max-rounds", type=int, default=6)
    ap.add_argument("--token-cap", type=int, default=80000)
    ap.add_argument("--n-jobs", type=int, default=4)
    a = ap.parse_args()
    load_env()
    import anthropic

    client = anthropic.Anthropic()
    system = SYSTEM.replace("{cols}", columns_summary())
    if a.track != "mixed":
        system += f"\nThis run is restricted to action '{'group' if a.track == 'features' else 'params'}' only."
    (ROOT / "experiments/auto").mkdir(parents=True, exist_ok=True)
    log = open(ROOT / f"docs/results/auto_{a.track}_log.jsonl", "a")
    groups, params, extras = list(BASE_GROUPS), dict(BASE_PARAMS), []
    best = run_experiment(groups, params, extras, a.track, a.n_jobs)
    print(f"start val_auc {best['val_auc']} ll {best['val_logloss']} r1 {best['val_recall@1%']}", flush=True)
    history, tokens, stall = list(SEED_HISTORY), 0, 0
    for rnd in range(1, a.max_rounds + 1):
        if tokens >= a.token_cap or stall >= 3:
            break
        user = (f"Current best: groups={groups}, params={params}, val_auc={best['val_auc']}, val_logloss={best['val_logloss']}, "
                f"val_recall@1%={best['val_recall@1%']}.\nHistory (do not repeat):\n- " + "\n- ".join(history[-14:]) +
                "\nPropose the next change.")
        entry = {"round": rnd}
        try:
            text, used = call_model(client, system, user)
            tokens += used
            entry["tokens"] = used
            prop = parse_proposal(text)
            if a.track == "features" and prop["action"] != "group" or a.track == "params" and prop["action"] != "params":
                raise ValueError("action not allowed in this track")
            if prop["action"] == "group":
                name = prop["name"]
                path = ROOT / f"experiments/auto/{a.track}_{name}.py"
                ensure_new(path)
                path.write_text(wrap_module(name, prop["code"]))
                chk = subprocess.run(sandbox_command([sys.executable, str(ROOT / "scripts/check_causal.py"), "--extra", str(path), "--group", name], ROOT, WRITE_DIRS),
                                     capture_output=True, text=True, timeout=300, cwd=ROOT, env=safe_child_env(os.environ, ROOT))
                if chk.returncode != 0:
                    raise ValueError(chk.stdout.strip()[:200] or chk.stderr[-200:])
                res = run_experiment(groups + [name], params, extras + [str(path)], a.track, a.n_jobs)
                label, new_state = f"group {name}", (groups + [name], params, extras + [str(path)])
            else:
                merged = {**params, **prop["params"]}
                res = run_experiment(groups, merged, extras, a.track, a.n_jobs)
                label, new_state = f"params {prop['params']}", (groups, merged, extras)
            gain = res["val_auc"] - best["val_auc"]
            kept = gain >= MIN_GAIN
            entry.update(label=label, val_auc=res["val_auc"], gain=round(gain, 5), kept=kept)
            history.append(f"{label}: {gain:+.4f} ({'kept' if kept else 'rejected'})")
            if kept:
                best, (groups, params, extras), stall = res, new_state, 0
            else:
                stall += 1
        except Exception as e:  # noqa: BLE001 - any failure is a rejected round, never a crash
            entry.update(error=f"{type(e).__name__}: {str(e)[:200]}")
            history.append(f"proposal rejected: {str(e)[:120]}")
            stall += 1
        log.write(json.dumps(entry) + "\n")
        log.flush()
        print(json.dumps(entry), flush=True)
    print(json.dumps({"BEST_groups": groups, "BEST_params": params, "val_auc": best["val_auc"],
                      "val_logloss": best["val_logloss"], "val_recall@1%": best["val_recall@1%"], "tokens": tokens}), flush=True)


if __name__ == "__main__":
    main()
