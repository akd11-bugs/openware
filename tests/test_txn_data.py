import pandas as pd
import pytest

from agentshield.txn.schema import STANDARD_COLUMNS, validate_standard
from agentshield.txn.loaders import ieee_cis_to_standard, paysim_to_standard
from agentshield.txn.split import time_split


def _ieee_frames():
    tx = pd.DataFrame(
        {
            "TransactionID": [1, 2, 3],
            "isFraud": [0, 1, 0],
            "TransactionDT": [86400, 90000, 100000],
            "TransactionAmt": [10.5, 99.99, 5.0],
            "card1": [1111, 2222, 1111],
            "ProductCD": ["W", "C", "W"],
        }
    )
    ident = pd.DataFrame({"TransactionID": [2], "DeviceType": ["mobile"]})
    return tx, ident


def _paysim_frame():
    return pd.DataFrame(
        {
            "step": [1, 1, 2],
            "type": ["TRANSFER", "CASH_OUT", "PAYMENT"],
            "amount": [100.0, 100.0, 12.34],
            "nameOrig": ["C1", "C2", "C3"],
            "oldbalanceOrg": [100.0, 100.0, 50.0],
            "newbalanceOrig": [0.0, 0.0, 37.66],
            "nameDest": ["C2", "C9", "M1"],
            "oldbalanceDest": [0.0, 0.0, 0.0],
            "newbalanceDest": [100.0, 100.0, 12.34],
            "isFraud": [1, 1, 0],
            "isFlaggedFraud": [0, 0, 0],
        }
    )


def test_ieee_maps_to_standard_columns():
    tx, ident = _ieee_frames()
    df = ieee_cis_to_standard(tx, ident)
    validate_standard(df)
    assert list(df.columns[: len(STANDARD_COLUMNS)]) == STANDARD_COLUMNS
    assert df["label"].tolist() == [0, 1, 0]
    assert df["source"].unique().tolist() == ["ieee_cis"]


def test_ieee_amount_is_integer_minor_units():
    tx, ident = _ieee_frames()
    df = ieee_cis_to_standard(tx, ident)
    assert df["amount_minor"].tolist() == [1050, 9999, 500]
    assert str(df["amount_minor"].dtype).startswith("int")


def test_ieee_keeps_unmapped_fields_as_extras_and_joins_identity():
    tx, ident = _ieee_frames()
    df = ieee_cis_to_standard(tx, ident)
    assert "x_ProductCD" in df.columns
    assert df.loc[df["txn_id"] == 2, "x_DeviceType"].item() == "mobile"
    assert pd.isna(df.loc[df["txn_id"] == 1, "x_DeviceType"].item())


def test_ieee_has_no_receiver():
    tx, ident = _ieee_frames()
    df = ieee_cis_to_standard(tx, ident)
    assert df["receiver_id"].isna().all()
    assert df["sender_id"].tolist() == ["1111", "2222", "1111"]


def test_paysim_step_is_hours_converted_to_seconds():
    df = paysim_to_standard(_paysim_frame())
    validate_standard(df)
    assert df["ts"].tolist() == [3600, 3600, 7200]
    assert df["receiver_id"].tolist() == ["C2", "C9", "M1"]
    assert df["amount_minor"].tolist() == [10000, 10000, 1234]
    assert df["source"].unique().tolist() == ["paysim"]


def test_paysim_ids_are_unique():
    df = paysim_to_standard(_paysim_frame())
    assert df["txn_id"].is_unique


def test_validate_rejects_missing_column():
    tx, ident = _ieee_frames()
    df = ieee_cis_to_standard(tx, ident).drop(columns=["label"])
    with pytest.raises(ValueError, match="label"):
        validate_standard(df)


def test_validate_rejects_non_binary_label():
    tx, ident = _ieee_frames()
    df = ieee_cis_to_standard(tx, ident)
    df.loc[0, "label"] = 2
    with pytest.raises(ValueError, match="label"):
        validate_standard(df)


def test_time_split_has_no_overlap_and_respects_order():
    df = paysim_to_standard(_paysim_frame())
    df = pd.concat([df] * 10, ignore_index=True)
    df["ts"] = range(len(df))
    train, test = time_split(df, train_frac=0.7)
    assert train["ts"].max() < test["ts"].min()
    assert len(train) + len(test) == len(df)
    assert len(train) == 21


def test_time_split_never_splits_identical_timestamps():
    df = paysim_to_standard(_paysim_frame())
    df = pd.concat([df] * 10, ignore_index=True)
    df["ts"] = [i // 5 for i in range(len(df))]  # blocks of 5 equal timestamps
    train, test = time_split(df, train_frac=0.7)
    assert set(train["ts"]).isdisjoint(set(test["ts"]))


def test_time_split_gap_drops_rows_between_train_and_test():
    df = paysim_to_standard(_paysim_frame())
    df = pd.concat([df] * 10, ignore_index=True)
    df["ts"] = range(len(df))
    train, test = time_split(df, train_frac=0.7, gap=5)
    assert test["ts"].min() - train["ts"].max() > 5
    assert len(train) + len(test) < len(df)


def test_time_split_keeps_original_index_labels():
    df = paysim_to_standard(_paysim_frame())
    df = pd.concat([df] * 10, ignore_index=True)
    df["ts"] = list(range(len(df)))[::-1]  # reverse-sorted input
    train, test = time_split(df, train_frac=0.7)
    # index labels must point back at the caller's rows, not positions in a re-sorted copy
    assert (df.loc[train.index, "ts"] == train["ts"]).all()
    assert (df.loc[test.index, "ts"] == test["ts"]).all()


def test_time_split_rejects_bad_fraction():
    df = paysim_to_standard(_paysim_frame())
    with pytest.raises(ValueError):
        time_split(df, train_frac=1.0)


def _ibm_frame():
    return pd.DataFrame({
        "Timestamp": ["2022/09/01 00:20", "2022/09/01 00:10", "2022/09/01 00:10"],
        "From Bank": [10, 3208, 5], "Account": ["8000EBD30", "8000F4580", "A1"],
        "To Bank": [10, 1, 5], "Account.1": ["8000EBD30", "8000F5340", "B1"],
        "Amount Received": [100.0, 5.5, 20.0], "Receiving Currency": ["US Dollar", "US Dollar", "Euro"],
        "Amount Paid": [100.0, 5.5, 22.0], "Payment Currency": ["US Dollar", "US Dollar", "US Dollar"],
        "Payment Format": ["Reinvestment", "Cheque", "Wire"], "Is Laundering": [0, 0, 1],
    })


def test_ibm_aml_maps_accounts_time_and_label():
    from agentshield.txn.loaders import ibm_aml_to_standard
    df = ibm_aml_to_standard(_ibm_frame())
    validate_standard(df)
    assert df["ts"].is_monotonic_increasing and df["source"].unique().tolist() == ["ibm_aml"]
    assert df["sender_id"].tolist()[2] == "10|8000EBD30" and set(df["receiver_id"]) == {"10|8000EBD30", "1|8000F5340", "5|B1"}
    assert df["label"].sum() == 1 and sorted(df["amount_minor"].tolist()) == [550, 2200, 10000]
    assert df["txn_id"].is_unique


def test_ibm_aml_flags_self_transfer_and_cross_currency():
    from agentshield.txn.loaders import ibm_aml_to_standard
    df = ibm_aml_to_standard(_ibm_frame()).set_index("sender_id")
    assert df.loc["10|8000EBD30", "x_self_transfer"] == 1 and df.loc["5|A1", "x_self_transfer"] == 0
    assert df.loc["5|A1", "x_cross_currency"] == 1 and df.loc["10|8000EBD30", "x_cross_currency"] == 0
    assert "x_Amount Received" not in df.columns
