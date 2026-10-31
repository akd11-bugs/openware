"""A tiny model trained on synthetic data, so config tests need no local model files."""
import json

import lightgbm as lgb
import numpy as np

NAMES = ["amount_minor", "hour", "dow", "x_DeviceType", "amt_cents", "amt_whole", "amt_round", "amt_sum_1h", "amt_sum_24h",
         "amt_ratio_30d", "sender_prior_cnt", "sender_prior_mean", "sender_amt_ratio", "sender_since_last",
         "card_cnt_3600s", "card_cnt_86400s", "card_cnt_604800s"]


def make_tiny_model(directory):
    rng = np.random.default_rng(0)
    X = rng.random((2000, len(NAMES))) * 100
    X[:, 0] = rng.integers(100, 1_000_000, 2000)
    y = (X[:, 0] > 700_000).astype(int)
    b = lgb.train({"objective": "binary", "verbose": -1, "num_leaves": 8, "seed": 1}, lgb.Dataset(X, y, feature_name=NAMES), 20)
    s = b.predict(X)
    thr = {f"{f:g}": float(np.quantile(s[y == 0], 1 - f)) for f in (0.001, 0.005, 0.01, 0.05)}
    b.save_model(str(directory / "model.txt"))
    (directory / "model_card.json").write_text(json.dumps({"name": "tiny", "feature_names": NAMES, "score_thresholds_by_val_fpr": thr,
                                                           "device_codes": {"mobile": 0}}))
    return directory
