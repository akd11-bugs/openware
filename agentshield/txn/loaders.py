"""Map benchmark datasets to the standard transaction format."""
import numpy as np
import pandas as pd

from .schema import STANDARD_COLUMNS


def _to_minor(amount: pd.Series) -> pd.Series:
    return np.rint(amount.astype("float64") * 100).astype("int64")


def _with_extras(core: pd.DataFrame, src: pd.DataFrame, used: set[str]) -> pd.DataFrame:
    extras = src.drop(columns=[c for c in src.columns if c in used])
    extras = extras.add_prefix("x_")
    out = pd.concat([core.reset_index(drop=True), extras.reset_index(drop=True)], axis=1)
    return out[STANDARD_COLUMNS + [c for c in out.columns if c not in STANDARD_COLUMNS]]


def ieee_cis_to_standard(tx: pd.DataFrame, identity: pd.DataFrame | None = None) -> pd.DataFrame:
    """IEEE-CIS: no receiver; card1 is used as the sender proxy; identity fields joined as extras."""
    src = tx.merge(identity, on="TransactionID", how="left") if identity is not None else tx.copy()
    core = pd.DataFrame(
        {
            "txn_id": src["TransactionID"].astype("int64").astype(str),
            "ts": src["TransactionDT"].astype("int64"),
            "amount_minor": _to_minor(src["TransactionAmt"]),
            "sender_id": src["card1"].astype(str),
            "receiver_id": pd.Series([None] * len(src), dtype="object"),
            "label": src["isFraud"].astype("int64"),
            "source": "ieee_cis",
        }
    )
    used = {"TransactionID", "TransactionDT", "TransactionAmt", "card1", "isFraud"}
    out = _with_extras(core, src, used)
    out["txn_id"] = out["txn_id"].astype("int64")
    return out


def _epoch(s: pd.Series) -> np.ndarray:
    return pd.to_datetime(s).astype("datetime64[s]").astype("int64").to_numpy()


def indian_fin_to_standard(tx: pd.DataFrame, cards: pd.DataFrame | None = None,
                           customers: pd.DataFrame | None = None) -> pd.DataFrame:
    """Indian Financial Fraud Dataset (Transaction_Data_250k). Sender = customer, receiver = merchant.

    Only fields a fintech knows at the moment of the decision are used.
    Excluded on purpose:
      Fraud_Reason        the rule that generated the label (direct leak)
      Transaction_Status  an outcome known only after the decision
      Card_ID             a high-cardinality id
      Card_Status         a single snapshot, probably written after fraud was found; in production this is a
                          live hard rule (decline lost/blocked cards), not something to learn from history
      Gender, Age, Marital_Status, Occupation, Customer_Name   protected or personal attributes
    Card validity is derived at transaction time (expiry date vs the transaction date), never from the snapshot.
    """
    ts = pd.to_datetime(tx["Transaction_Date"] + " " + tx["Transaction_Time"]).astype("datetime64[s]").astype("int64")
    core = pd.DataFrame(
        {
            "txn_id": tx["Transaction_ID"].astype(str),
            "ts": ts,
            "amount_minor": _to_minor(tx["Transaction_Amount"]),
            "sender_id": tx["Customer_ID"].astype(str),
            "receiver_id": tx["Merchant_ID"].astype(str),
            "label": tx["Fraud_Flag"].astype("int64"),
            "source": "indian_fin",
        }
    )
    used = {"Transaction_ID", "Transaction_Date", "Transaction_Time", "Transaction_Amount", "Customer_ID",
            "Merchant_ID", "Fraud_Flag", "Fraud_Reason", "Transaction_Status", "Card_ID"}
    out = _with_extras(core, tx, used)
    tday = ts.to_numpy() // 86400 * 86400  # transaction date at midnight
    if cards is not None:
        c = tx[["Card_ID"]].merge(cards, on="Card_ID", how="left")
        issue, expiry = _epoch(c["Issue_Date"]), _epoch(c["Expiry_Date"])
        out["x_card_age_days"] = (tday - issue) // 86400
        out["x_card_days_to_expiry"] = (expiry - tday) // 86400
        out["x_card_expired_at_txn"] = (expiry < tday).astype("int64")
        out["x_credit_limit"] = c["Credit_Limit"].to_numpy(dtype="float64")
        out["x_amt_to_limit"] = tx["Transaction_Amount"].to_numpy(dtype="float64") / out["x_credit_limit"]
        for col in ["Card_Type", "Card_Network", "Contactless", "Card_Mode"]:
            out[f"x_{col}"] = c[col].to_numpy()
    if customers is not None:
        k = tx[["Customer_ID"]].merge(customers, on="Customer_ID", how="left")
        out["x_tenure_days"] = (tday - _epoch(k["Customer_Since"])) // 86400
        for col in ["Account_Type", "Customer_Segment", "Annual_Income"]:
            out[f"x_{col}"] = k[col].to_numpy()
    return out.sort_values("ts", kind="stable").reset_index(drop=True)


def ibm_aml_to_standard(df: pd.DataFrame) -> pd.DataFrame:
    """IBM AML transactions (synthetic, with labelled laundering rings). An account is bank id plus account id.

    Amounts are in the payment currency, and currencies differ across rows, so they are not comparable; the
    currency and a cross-currency flag are kept as features. Amount Received is dropped (it is the other side's view).
    """
    snd = df["From Bank"].astype(str) + "|" + df["Account"].astype(str)
    rcv = df["To Bank"].astype(str) + "|" + df["Account.1"].astype(str)
    ts = pd.to_datetime(df["Timestamp"], format="%Y/%m/%d %H:%M").astype("datetime64[s]").astype("int64")
    core = pd.DataFrame({
        "txn_id": np.arange(len(df), dtype="int64"), "ts": ts.to_numpy(), "amount_minor": _to_minor(df["Amount Paid"]),
        "sender_id": snd.to_numpy(), "receiver_id": rcv.to_numpy(), "label": df["Is Laundering"].astype("int64").to_numpy(),
        "source": "ibm_aml",
    })
    used = {"Timestamp", "From Bank", "Account", "To Bank", "Account.1", "Amount Received", "Amount Paid", "Is Laundering"}
    out = _with_extras(core, df, used)
    out["x_self_transfer"] = (snd.to_numpy() == rcv.to_numpy()).astype("int64")
    out["x_cross_currency"] = (df["Receiving Currency"].to_numpy() != df["Payment Currency"].to_numpy()).astype("int64")
    return out.sort_values("ts", kind="stable").reset_index(drop=True)


def paysim_to_standard(df: pd.DataFrame) -> pd.DataFrame:
    """PaySim: `step` is hours; sender nameOrig, receiver nameDest. Row order gives a unique id."""
    core = pd.DataFrame(
        {
            "txn_id": np.arange(len(df), dtype="int64"),
            "ts": df["step"].astype("int64") * 3600,
            "amount_minor": _to_minor(df["amount"]),
            "sender_id": df["nameOrig"].astype(str),
            "receiver_id": df["nameDest"].astype(str),
            "label": df["isFraud"].astype("int64"),
            "source": "paysim",
        }
    )
    used = {"step", "amount", "nameOrig", "nameDest", "isFraud"}
    return _with_extras(core, df, used)
