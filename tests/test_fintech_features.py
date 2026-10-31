import numpy as np
import pandas as pd
import pytest

from agentshield.txn import experiment as E
from agentshield.txn import fintech_features  # noqa: F401  (registers the groups)
from agentshield.txn.loaders import indian_fin_to_standard

H, D = 3600, 86400


def frame(rows):
    """rows: (sender, receiver, ts, amount_rupees)"""
    df = pd.DataFrame(rows, columns=["sender_id", "receiver_id", "ts", "amount"])
    df["amount_minor"] = (df["amount"] * 100).round().astype("int64")
    df["label"] = 0
    return df.sort_values("ts", kind="stable").reset_index(drop=True)


def feats(df, group):
    return E.FEATURE_GROUPS[group][0](df)


def test_amt_sum_windows_exclude_current_and_same_timestamp_rows():
    df = frame([("a", "m1", 100 * H, 10), ("a", "m1", 100 * H + 1800, 20), ("a", "m1", 100 * H + 3000, 5),
                ("a", "m1", 100 * H + 3000, 7), ("a", "m1", 100 * H + 2 * H, 1)])
    f = feats(df, "velocity_amt")
    assert f["amt_sum_1h"].tolist() == [0, 1000, 3000, 3000, 0]  # strictly earlier ts, within [t-1h, t)
    assert f["amt_sum_24h"].tolist() == [0, 1000, 3000, 3000, 4200]


def test_amt_ratio_30d_uses_prior_mean_only():
    df = frame([("a", "m1", D, 100), ("a", "m1", 2 * D, 300), ("a", "m1", 3 * D, 800), ("a", "m1", 40 * D, 500)])
    f = feats(df, "amt_ratio_30d")
    assert np.isnan(f["amt_ratio_30d"].iloc[0])
    assert f["amt_ratio_30d"].iloc[1] == 3.0
    assert f["amt_ratio_30d"].iloc[2] == pytest.approx(800 / 200)
    assert np.isnan(f["amt_ratio_30d"].iloc[3])  # everything earlier is older than 30 days


def test_payee_risk_counts_distinct_senders_in_prior_24h():
    df = frame([("s1", "m1", 10 * H, 50), ("s2", "m1", 11 * H, 50), ("s1", "m1", 12 * H, 50),
                ("s3", "m1", 13 * H, 50), ("s4", "m2", 13 * H, 50), ("s5", "m1", 60 * H, 50)])
    f = feats(df, "payee_risk")
    assert f["payee_uniq_senders_24h"].tolist() == [0, 1, 2, 2, 0, 0]
    assert f["payee_cnt_24h"].tolist() == [0, 1, 2, 3, 0, 0]
    assert f["payee_new_for_sender"].tolist() == [1, 1, 0, 1, 1, 1]


def test_payee_risk_is_nan_when_no_receiver():
    df = frame([("s1", "m1", 10 * H, 50), ("s2", "m1", 11 * H, 50)]).assign(receiver_id=None)
    assert feats(df, "payee_risk").isna().all().all()


@pytest.mark.parametrize("group", ["velocity_amt", "amt_ratio_30d", "payee_risk"])
def test_groups_are_causal_and_label_free(group):
    rng = np.random.default_rng(5)
    n = 400
    df = frame([(f"s{rng.integers(0, 8)}", f"m{rng.integers(0, 4)}", int(D + t * 900), float(rng.integers(10, 5000)))
                for t in range(n)])
    a = feats(df, group)
    changed = df.copy()
    perm = rng.permutation(np.arange(250, n))
    for c in ["sender_id", "receiver_id", "amount_minor"]:
        changed.loc[250:, c] = changed.loc[250:, c].to_numpy()[perm - 250]
    changed["label"] = 1 - changed["label"]
    b = feats(changed, group)
    pd.testing.assert_frame_equal(a.iloc[:250], b.iloc[:250])


def _joined_inputs():
    tx = pd.DataFrame({
        "Transaction_ID": ["T1", "T2"], "Customer_ID": ["C1", "C1"], "Card_ID": ["K1", "K1"], "Merchant_ID": ["M1", "M2"],
        "Transaction_Date": ["2024-06-01", "2023-01-01"], "Transaction_Time": ["10:00:00", "09:00:00"],
        "Transaction_Amount": [5000.0, 1000.0], "Payment_Method": ["Debit Card", "Debit Card"],
        "Transaction_Channel": ["POS", "POS"], "Device_Type": ["x", "x"], "Transaction_Status": ["Successful", "Successful"],
        "Is_International": [0, 0], "Fraud_Flag": [1, 0], "Fraud_Reason": ["x", None], "Merchant_Risk_Level": ["Low", "Low"],
        "Merchant_Category": ["Food", "Food"], "Customer_State": ["A", "A"], "Customer_City": ["X", "X"],
        "Merchant_State": ["A", "A"], "Merchant_City": ["X", "X"],
    })
    cards = pd.DataFrame({"Card_ID": ["K1"], "Customer_ID": ["C1"], "Card_Type": ["Gold"], "Card_Network": ["Visa"],
                          "Credit_Limit": [50000], "Card_Status": ["Blocked"], "Contactless": ["Yes"], "Card_Mode": ["Physical"],
                          "Issue_Date": ["2022-01-01"], "Expiry_Date": ["2024-01-01"]})
    cust = pd.DataFrame({"Customer_ID": ["C1"], "Customer_Name": ["Someone"], "Gender": ["F"], "Age": [40],
                         "Marital_Status": ["Single"], "Occupation": ["Nurse"], "Annual_Income": [600000],
                         "Customer_Segment": ["Gold"], "State": ["A"], "City": ["X"], "Account_Type": ["Savings"],
                         "Customer_Since": ["2020-01-01"]})
    return tx, cards, cust


def test_loader_joins_decision_time_card_and_customer_fields_only():
    tx, cards, cust = _joined_inputs()
    df = indian_fin_to_standard(tx, cards, cust)
    by = df.set_index("txn_id")
    # derived at the time of the transaction, not read from a later snapshot
    assert by.loc["T1", "x_card_expired_at_txn"] == 1 and by.loc["T2", "x_card_expired_at_txn"] == 0
    assert by.loc["T2", "x_card_age_days"] == 365 and by.loc["T2", "x_card_days_to_expiry"] == 365
    assert by.loc["T1", "x_amt_to_limit"] == pytest.approx(5000 / 50000)
    assert by.loc["T2", "x_tenure_days"] == 1096
    assert {"x_Card_Type", "x_Card_Network", "x_credit_limit", "x_Account_Type", "x_Customer_Segment"} <= set(df.columns)
    banned = {"x_Card_Status", "x_Gender", "x_Age", "x_Marital_Status", "x_Occupation", "x_Customer_Name", "x_Fraud_Reason"}
    assert banned.isdisjoint(df.columns)


def test_loader_without_joins_still_works():
    tx, _, _ = _joined_inputs()
    assert "x_credit_limit" not in indian_fin_to_standard(tx).columns


def test_loader_maps_columns_and_excludes_leaky_and_outcome_fields():
    tx = pd.DataFrame({
        "Transaction_ID": ["T1", "T2"], "Customer_ID": ["C1", "C1"], "Card_ID": ["K1", "K1"], "Merchant_ID": ["M1", "M2"],
        "Transaction_Date": ["2024-01-02", "2024-01-01"], "Transaction_Time": ["10:00:00", "09:30:00"],
        "Transaction_Amount": [100.5, 20.0], "Payment_Method": ["UPI", "Debit Card"], "Transaction_Channel": ["POS", "POS"],
        "Device_Type": ["x", "y"], "Transaction_Status": ["Declined", "Successful"], "Is_International": [0, 1],
        "Fraud_Flag": [1, 0], "Fraud_Reason": ["Transaction Attempt on Lost Card", None], "Merchant_Risk_Level": ["Low", "High"],
        "Merchant_Category": ["Food", "Travel"], "Customer_State": ["A", "A"], "Customer_City": ["X", "X"],
        "Merchant_State": ["B", "B"], "Merchant_City": ["Y", "Y"],
    })
    df = indian_fin_to_standard(tx)
    assert df["ts"].is_monotonic_increasing
    assert df["txn_id"].tolist() == ["T2", "T1"]  # sorted by time
    assert df["amount_minor"].tolist() == [2000, 10050]
    assert df["receiver_id"].tolist() == ["M2", "M1"] and df["label"].tolist() == [0, 1]
    assert "x_Fraud_Reason" not in df.columns and "x_Transaction_Status" not in df.columns
    assert "x_Payment_Method" in df.columns and df["source"].unique().tolist() == ["indian_fin"]


def dev_frame(rows):
    """rows: (sender, device, ts)"""
    df = pd.DataFrame(rows, columns=["sender_id", "x_DeviceInfo", "ts"])
    df["amount_minor"], df["label"], df["receiver_id"] = 100, 0, None
    return df.sort_values("ts", kind="stable").reset_index(drop=True)


def test_device_share_counts_earlier_devices_and_senders():
    df = dev_frame([("a", "d1", 10), ("b", "d1", 20), ("a", "d2", 30), ("c", "d1", 40), ("a", "d1", 50), ("a", None, 60)])
    f = feats(df, "device_share")
    assert f["device_new_for_sender"].tolist()[:5] == [1, 1, 1, 1, 0] and np.isnan(f["device_new_for_sender"].iloc[5])
    assert f["sender_distinct_devices"].tolist()[:5] == [0, 0, 1, 0, 2]
    assert f["device_uniq_senders"].tolist()[:5] == [0, 1, 0, 2, 2]  # d1 was earlier used by a, b, c (a again at 50)


def test_device_share_same_timestamp_events_are_not_prior():
    df = dev_frame([("a", "d1", 10), ("b", "d1", 10), ("c", "d1", 20)])
    f = feats(df, "device_share")
    assert f["device_uniq_senders"].tolist() == [0, 0, 2] and f["device_new_for_sender"].tolist() == [1, 1, 1]


def test_instrument_group_maps_known_values_and_marks_unknown():
    df = dev_frame([("a", "d1", 1), ("b", "d1", 2)]).assign(
        x_ProductCD=["W", "zzz"], x_card6=["debit", None], x_card4=["visa", "mastercard"])
    f = feats(df, "instrument")
    assert f["channel_code"].tolist() == [0, -1] and f["instrument_code"].tolist() == [0, -1]
    assert f["network_code"].tolist() == [0, 1]


@pytest.mark.parametrize("group", ["device_share"])
def test_device_group_is_causal(group):
    rng = np.random.default_rng(8)
    n = 300
    df = dev_frame([(f"s{rng.integers(0, 10)}", f"d{rng.integers(0, 6)}" if rng.random() > 0.2 else None, int(100 + i * 50)) for i in range(n)])
    a = feats(df, group)
    ch = df.copy()
    perm = rng.permutation(np.arange(200, n)) 
    for c in ["sender_id", "x_DeviceInfo"]:
        ch.loc[200:, c] = ch.loc[200:, c].to_numpy()[perm - 200]
    b = feats(ch, group)
    pd.testing.assert_frame_equal(a.iloc[:200], b.iloc[:200])
