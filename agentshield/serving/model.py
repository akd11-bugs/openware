"""Loads a saved model + card and turns a feature dict into a score and plain-language reason codes."""
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np

# feature -> (reason code, text). Only features pushing the score UP (toward fraud) become reasons.
REASONS = {
    "amount_minor": ("AMOUNT_LARGE", "Unusually large amount"),
    "amt_sum_1h": ("VELOCITY_AMOUNT_1H", "A lot of money moved by this customer in the last hour"),
    "amt_sum_24h": ("VELOCITY_AMOUNT_24H", "A lot of money moved by this customer in the last day"),
    "amt_ratio_30d": ("AMOUNT_VS_30D_BASELINE", "Amount far above this customer's 30-day average"),
    "sender_prior_cnt": ("CUSTOMER_HISTORY_LENGTH", "Limited or unusual transaction history for this customer"),
    "sender_prior_mean": ("CUSTOMER_AVG_AMOUNT", "This customer's typical amount differs from the norm"),
    "sender_amt_ratio": ("AMOUNT_VS_HISTORY", "Amount far above this customer's usual amount"),
    "sender_since_last": ("SHORT_GAP_SINCE_LAST", "Unusual time since the customer's previous transaction"),
    "card_cnt_3600s": ("BURST_1H", "Many transactions in the last hour"),
    "card_cnt_86400s": ("BURST_24H", "Many transactions in the last day"),
    "card_cnt_604800s": ("BURST_7D", "Many transactions in the last week"),
    "hour": ("UNUSUAL_HOUR", "Unusual time of day"),
    "dow": ("UNUSUAL_WEEKDAY", "Unusual day of the week"),
    "x_DeviceType": ("DEVICE_TYPE", "Device type associated with higher risk"),
    "device_new_for_sender": ("DEVICE_NEW", "First time this customer uses this device"),
    "sender_distinct_devices": ("MANY_DEVICES", "This customer has used many different devices"),
    "device_uniq_senders": ("DEVICE_SHARED", "This device has also been used by other customers"),
    "channel_code": ("CHANNEL_RISK", "Transaction channel associated with higher risk"),
    "instrument_code": ("INSTRUMENT_RISK", "Payment instrument associated with higher risk"),
    "network_code": ("NETWORK_RISK", "Card network associated with higher risk"),
    "amt_cents": ("AMOUNT_PATTERN", "Amount pattern associated with higher risk"),
    "amt_whole": ("AMOUNT_PATTERN", "Amount pattern associated with higher risk"),
    "amt_round": ("AMOUNT_PATTERN", "Amount pattern associated with higher risk"),
}


class ModelBundle:
    def __init__(self, booster: lgb.Booster, card: dict) -> None:
        self.booster, self.card = booster, card
        self.names: list[str] = card["feature_names"]
        self.thresholds: dict[str, float] = card["score_thresholds_by_val_fpr"]
        self.device_codes: dict = card.get("device_codes") or {}

    @classmethod
    def load(cls, model_dir: Path) -> "ModelBundle":
        card = json.loads((Path(model_dir) / "model_card.json").read_text())
        return cls(lgb.Booster(model_file=str(Path(model_dir) / "model.txt")), card)

    def threshold(self, fpr: float) -> float:
        key = f"{fpr:g}"
        if key not in self.thresholds:
            raise ValueError(f"no calibrated threshold for false-positive rate {fpr}; available: {sorted(self.thresholds)}")
        return self.thresholds[key]

    def _row(self, feats: dict) -> np.ndarray:
        return np.array([[feats.get(n, np.nan) for n in self.names]], dtype="float64")

    def score(self, feats: dict) -> float:
        return float(self.booster.predict(self._row(feats))[0])

    def reasons(self, feats: dict, top: int = 3) -> list[dict]:
        contrib = self.booster.predict(self._row(feats), pred_contrib=True)[0][:-1]  # last column is the bias term
        seen, out = set(), []
        for i in np.argsort(-contrib):
            if contrib[i] <= 0 or len(out) >= top:
                break
            code, text = REASONS.get(self.names[i], (f"FEATURE_{self.names[i].upper()}", self.names[i]))
            if code not in seen:
                seen.add(code)
                out.append({"code": code, "text": text})
        return out
