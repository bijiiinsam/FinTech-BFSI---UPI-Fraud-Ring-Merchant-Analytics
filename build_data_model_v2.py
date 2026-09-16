
import pandas as pd
import numpy as np
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent
OUTPUT_PATH = BASE_PATH / "model_output"
OUTPUT_PATH.mkdir(exist_ok=True)

# -----------------------------
# LOAD
# -----------------------------
def load_csv(name):
    path = BASE_PATH / name
    if not path.exists():
        raise FileNotFoundError(f"Missing input file: {path}")
    return pd.read_csv(path)

transactions = load_csv("track1_upi_transactions_clean.csv")
kyc = load_csv("track1_kyc_records_cleaned.csv")
merchants = load_csv("track1_merchants_master_cleaned.csv")
chargebacks = load_csv("json_cleaned.csv")

def clean_text(value):
    if pd.isna(value):
        return pd.NA
    value = str(value).strip().upper()
    return pd.NA if value in {"", "NAN", "NONE", "NULL", "NA", "N/A"} else value

def standardize_basic_id(series):
    return (
        series.astype("string").str.strip().str.upper()
        .replace({"": pd.NA, "NAN": pd.NA, "NONE": pd.NA,
                  "NULL": pd.NA, "NA": pd.NA, "N/A": pd.NA})
    )

def standardize_prefixed_id(series, prefix):
    s = standardize_basic_id(series)
    s = s.str.replace(r"\.0$", "", regex=True)
    s = s.str.replace(r"[\s_\-]", "", regex=True)
    s = s.str.replace(
        r"^(USR|USER|MER|MCH|MERCHANT|TXN|TRANSACTION|CB|CHARGEBACK|CMP|COMPLAINT)",
        "", regex=True
    )
    numeric_part = s.str.extract(r"(\d+)", expand=False)
    return numeric_part.where(numeric_part.isna(), prefix + numeric_part).astype("string")

def standardize_date(df, column):
    if column in df.columns:
        df[column] = pd.to_datetime(df[column], errors="coerce")
    return df

def ensure_numeric(df, columns):
    for col in columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def ensure_bool(series):
    if pd.api.types.is_bool_dtype(series):
        return series.fillna(False)
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(0).astype(bool)
    return (
        series.astype("string").str.strip().str.upper()
        .map({"TRUE": True, "1": True, "YES": True, "Y": True,
              "FALSE": False, "0": False, "NO": False, "N": False})
        .fillna(False)
    )

def pct(n, d):
    return round((100 * n / d), 2) if d else 0.0

# -----------------------------
# STANDARDIZE IDS AND TYPES
# -----------------------------
for df, cols in [
    (kyc, ["user_id"]),
    (transactions, ["user_id", "merchant_id", "txn_id"]),
    (chargebacks, ["user_id", "merchant_id", "txn_id", "complaint_id"]),
    (merchants, ["merchant_id"]),
]:
    for col in cols:
        if col in df.columns:
            prefix = {"user_id": "USR", "merchant_id": "MER",
                      "txn_id": "TXN", "complaint_id": "CB"}[col]
            df[col] = standardize_prefixed_id(df[col], prefix)

for col in ["timestamp", "signup_timestamp", "onboarding_date"]:
    transactions = standardize_date(transactions, col)
    kyc = standardize_date(kyc, col)
    merchants = standardize_date(merchants, col)

for col in ["transaction_timestamp", "reported_timestamp", "bank_response_timestamp"]:
    chargebacks = standardize_date(chargebacks, col)

transactions = ensure_numeric(transactions, ["amount", "mcc"])
kyc = ensure_numeric(kyc, ["monthly_income", "age", "signup_year",
                           "signup_month", "account_age", "account_age_days",
                           "conflict_count"])
merchants = ensure_numeric(merchants, ["mcc", "declared_avg_ticket_size",
                                       "category_median_ticket_size"])
chargebacks = ensure_numeric(chargebacks, ["reporting_delay_days",
                                           "bank_response_delay_days",
                                           "disputed_amount"])

# -----------------------------
# DIM_USERS + KYC QUALITY
# -----------------------------
kyc_model = kyc[kyc["user_id"].notna()].copy()
kyc_model["_non_missing_count"] = kyc_model.notna().sum(axis=1)

if "has_conflict" not in kyc_model:
    kyc_model["has_conflict"] = False
if "conflict_count" not in kyc_model:
    kyc_model["conflict_count"] = 0

kyc_model["has_conflict"] = ensure_bool(kyc_model["has_conflict"])
kyc_model["conflict_count"] = pd.to_numeric(
    kyc_model["conflict_count"], errors="coerce"
).fillna(0)

kyc_model = kyc_model.sort_values(
    ["user_id", "has_conflict", "conflict_count", "_non_missing_count"],
    ascending=[True, True, True, False]
)

dim_users = (
    kyc_model.drop_duplicates("user_id", keep="first")
    .drop(columns="_non_missing_count")
    .reset_index(drop=True)
)

kyc_valid = kyc[kyc["user_id"].notna()].copy()
kyc_quality = kyc_valid.groupby("user_id").agg(
    original_kyc_records=("user_id", "size"),
    kyc_conflict_records=("has_conflict", "sum"),
    max_conflict_count=("conflict_count", "max"),
    any_identity_conflict=("has_conflict", "max"),
    missing_pan=("pan", lambda x: x.isna().sum()),
    missing_aadhaar=("aadhaar", lambda x: x.isna().sum()),
    missing_city=("city", lambda x: x.isna().sum()),
    missing_state=("state", lambda x: x.isna().sum()),
    missing_income=("monthly_income", lambda x: x.isna().sum()),
    missing_occupation=("occupation", lambda x: x.isna().sum()),
    missing_dob=("date_of_birth", lambda x: x.isna().sum()),
).reset_index()
kyc_quality["has_duplicate_kyc_records"] = kyc_quality["original_kyc_records"] > 1
kyc_quality["extra_kyc_records"] = kyc_quality["original_kyc_records"] - 1

# -----------------------------
# DIM_MERCHANTS
# Preserve merchant blanks; do not hide them.
# -----------------------------
dim_merchants = (
    merchants[merchants["merchant_id"].notna()]
    .drop_duplicates("merchant_id", keep="first")
    .reset_index(drop=True)
)

# Explicit merchant-category quality fields
dim_merchants["merchant_category_missing"] = (
    dim_merchants["merchant_category"].isna()
    if "merchant_category" in dim_merchants else True
)
dim_merchants["mcc_missing"] = (
    dim_merchants["mcc"].isna() if "mcc" in dim_merchants else True
)
dim_merchants["settlement_account_missing"] = (
    dim_merchants["settlement_account"].isna()
    if "settlement_account" in dim_merchants else True
)

# -----------------------------
# FACT_TRANSACTIONS
# -----------------------------
fact_transactions = transactions.copy()
fact_transactions = standardize_date(fact_transactions, "timestamp")

fact_transactions["transaction_date"] = fact_transactions["timestamp"].dt.normalize()
fact_transactions["transaction_year"] = fact_transactions["timestamp"].dt.year
fact_transactions["transaction_quarter"] = (
    "Q" + fact_transactions["timestamp"].dt.quarter.fillna(0).astype(int).astype(str)
)
fact_transactions["transaction_month_number"] = fact_transactions["timestamp"].dt.month
fact_transactions["transaction_month"] = fact_transactions["timestamp"].dt.month_name()
fact_transactions["transaction_year_month"] = fact_transactions["timestamp"].dt.strftime("%Y-%m")
fact_transactions["transaction_day"] = fact_transactions["timestamp"].dt.day
fact_transactions["transaction_day_name"] = fact_transactions["timestamp"].dt.day_name()
fact_transactions["transaction_hour"] = fact_transactions["timestamp"].dt.hour
fact_transactions["is_weekend"] = fact_transactions["timestamp"].dt.dayofweek >= 5

fact_transactions["amount_missing"] = fact_transactions["amount"].isna()
fact_transactions["user_id_missing"] = fact_transactions["user_id"].isna()
fact_transactions["merchant_id_missing"] = fact_transactions["merchant_id"].isna()
fact_transactions["txn_id_missing"] = fact_transactions["txn_id"].isna()
fact_transactions["utr_missing"] = (
    fact_transactions["utr"].isna() if "utr" in fact_transactions else True
)
fact_transactions["mcc_missing"] = (
    fact_transactions["mcc"].isna() if "mcc" in fact_transactions else True
)

status = (
    fact_transactions["status"].astype("string").str.strip().str.upper()
    if "status" in fact_transactions else pd.Series(pd.NA, index=fact_transactions.index, dtype="string")
)
fact_transactions["normalized_status"] = status
fact_transactions["status_group"] = np.select(
    [
        status.isin(["SUCCESS", "COMPLETED"]),
        status.isin(["FAILED", "FAILURE", "DECLINED"]),
        status.isin(["PENDING", "PROCESSING"]),
        status.isin(["INITIATED"]),
    ],
    ["Success", "Failed", "Pending", "Initiated"],
    default="Unknown"
)
fact_transactions["is_success"] = fact_transactions["status_group"].eq("Success")
fact_transactions["is_failed"] = fact_transactions["status_group"].eq("Failed")
fact_transactions["is_pending"] = fact_transactions["status_group"].eq("Pending")
fact_transactions["is_initiated"] = fact_transactions["status_group"].eq("Initiated")
fact_transactions["is_unknown_status"] = fact_transactions["status_group"].eq("Unknown")
fact_transactions["is_reversal_flag"] = (
    ensure_bool(fact_transactions["is_reversal"])
    if "is_reversal" in fact_transactions else False
)

# -----------------------------
# FACT_CHARGEBACKS
# -----------------------------
fact_chargebacks = chargebacks.copy()
for col in ["transaction_timestamp", "reported_timestamp", "bank_response_timestamp"]:
    fact_chargebacks = standardize_date(fact_chargebacks, col)

fact_chargebacks["transaction_date"] = fact_chargebacks["transaction_timestamp"].dt.normalize()
fact_chargebacks["reported_date"] = fact_chargebacks["reported_timestamp"].dt.normalize()
fact_chargebacks["bank_response_date"] = fact_chargebacks["bank_response_timestamp"].dt.normalize()

for col, flag in [
    ("txn_id", "txn_id_missing_flag"),
    ("user_id", "user_id_missing_flag"),
    ("merchant_id", "merchant_id_missing_flag"),
    ("disputed_amount", "disputed_amount_missing_flag"),
]:
    fact_chargebacks[flag] = fact_chargebacks[col].isna()

fact_chargebacks["has_long_reporting_delay"] = fact_chargebacks["reporting_delay_days"] > 7
fact_chargebacks["has_long_bank_response_delay"] = fact_chargebacks["bank_response_delay_days"] > 7
fact_chargebacks["invalid_reporting_delay"] = fact_chargebacks["reporting_delay_days"] < 0
fact_chargebacks["invalid_bank_response_delay"] = fact_chargebacks["bank_response_delay_days"] < 0

# -----------------------------
# DATE DIMENSION
# -----------------------------
date_series = [
    fact_transactions["transaction_date"].dropna(),
    fact_chargebacks["transaction_date"].dropna(),
    fact_chargebacks["reported_date"].dropna(),
    fact_chargebacks["bank_response_date"].dropna(),
]
all_dates = pd.concat(date_series) if date_series else pd.Series(dtype="datetime64[ns]")
if all_dates.empty:
    raise ValueError("No valid dates found.")
min_date, max_date = all_dates.min(), all_dates.max()
dim_date = pd.DataFrame({"date": pd.date_range(min_date, max_date, freq="D")})
dim_date["year"] = dim_date["date"].dt.year
dim_date["quarter"] = "Q" + dim_date["date"].dt.quarter.astype(str)
dim_date["month_number"] = dim_date["date"].dt.month
dim_date["month_name"] = dim_date["date"].dt.month_name()
dim_date["year_month"] = dim_date["date"].dt.strftime("%Y-%m")
dim_date["day"] = dim_date["date"].dt.day
dim_date["day_name"] = dim_date["date"].dt.day_name()
dim_date["week_number"] = dim_date["date"].dt.isocalendar().week.astype(int)
dim_date["is_weekend"] = dim_date["date"].dt.dayofweek >= 5

# -----------------------------
# REFERENTIAL INTEGRITY
# -----------------------------
user_ids = set(dim_users["user_id"].dropna())
merchant_ids = set(dim_merchants["merchant_id"].dropna())
transaction_ids = set(fact_transactions["txn_id"].dropna())

fact_transactions["user_found_in_kyc"] = fact_transactions["user_id"].isin(user_ids)
fact_transactions["merchant_found_in_master"] = fact_transactions["merchant_id"].isin(merchant_ids)
fact_chargebacks["user_found_in_kyc"] = fact_chargebacks["user_id"].isin(user_ids)
fact_chargebacks["merchant_found_in_master"] = fact_chargebacks["merchant_id"].isin(merchant_ids)
fact_chargebacks["transaction_found"] = fact_chargebacks["txn_id"].isin(transaction_ids)

fact_transactions["user_id_unmatched"] = fact_transactions["user_id"].notna() & ~fact_transactions["user_found_in_kyc"]
fact_transactions["merchant_id_unmatched"] = fact_transactions["merchant_id"].notna() & ~fact_transactions["merchant_found_in_master"]
fact_chargebacks["user_id_unmatched"] = fact_chargebacks["user_id"].notna() & ~fact_chargebacks["user_found_in_kyc"]
fact_chargebacks["merchant_id_unmatched"] = fact_chargebacks["merchant_id"].notna() & ~fact_chargebacks["merchant_found_in_master"]
fact_chargebacks["txn_id_unmatched"] = fact_chargebacks["txn_id"].notna() & ~fact_chargebacks["transaction_found"]

# Transaction-side merchant category quality impact.
# A transaction is unavailable for category analysis when its merchant is
# missing/unmatched or the matched merchant has a blank category.
category_map = dim_merchants.set_index("merchant_id")["merchant_category"].to_dict()
fact_transactions["merchant_category_missing_or_unmatched"] = (
    fact_transactions["merchant_id"].isna()
    | ~fact_transactions["merchant_id"].isin(merchant_ids)
    | fact_transactions["merchant_id"].map(category_map).isna()
)

# -----------------------------
# VALIDATION REPORT
# -----------------------------
def add_validation(rows, category, metric, count, denominator, severity, description):
    rows.append({
        "category": category,
        "metric": metric,
        "count": int(count) if pd.notna(count) else 0,
        "percentage": pct(count, denominator),
        "denominator": int(denominator),
        "severity": severity,
        "description": description,
    })

validation_rows = []

# Referential integrity
add_validation(validation_rows, "Referential Integrity", "Transactions without merchant match",
               (~fact_transactions["merchant_found_in_master"]).sum(), len(fact_transactions), "Critical",
               "Transaction merchant_id is missing or absent from merchant master.")
add_validation(validation_rows, "Referential Integrity", "Transactions without KYC match",
               (~fact_transactions["user_found_in_kyc"]).sum(), len(fact_transactions), "High",
               "Transaction user_id is missing or absent from canonical KYC users.")
add_validation(validation_rows, "Referential Integrity", "Chargebacks without merchant match",
               (~fact_chargebacks["merchant_found_in_master"]).sum(), len(fact_chargebacks), "High",
               "Chargeback merchant_id is missing or absent from merchant master.")
add_validation(validation_rows, "Referential Integrity", "Chargebacks without transaction match",
               (~fact_chargebacks["transaction_found"]).sum(), len(fact_chargebacks), "High",
               "Chargeback transaction reference is missing or absent from transaction fact.")

# Merchant quality
merchant_n = len(dim_merchants)
cat_missing = dim_merchants["merchant_category_missing"].sum()
mcc_missing = dim_merchants["mcc_missing"].sum()
settlement_missing = dim_merchants["settlement_account_missing"].sum()
add_validation(validation_rows, "Merchant Data Quality", "Missing merchant category",
               cat_missing, merchant_n, "High",
               "Merchant master records without a merchant category; retain as Blank in the dashboard.")
add_validation(validation_rows, "Merchant Data Quality", "Missing MCC",
               mcc_missing, merchant_n, "Medium",
               "Merchant master records without MCC.")
add_validation(validation_rows, "Merchant Data Quality", "Missing settlement account",
               settlement_missing, merchant_n, "High",
               "Merchant master records without settlement account.")
add_validation(validation_rows, "Merchant Data Quality", "Transactions with unavailable merchant category",
               fact_transactions["merchant_category_missing_or_unmatched"].sum(),
               len(fact_transactions), "Critical",
               "Transaction cannot be assigned a merchant category because the merchant is unmatched or its category is blank.")

# Transaction quality
tx_n = len(fact_transactions)
for metric, mask, severity, desc in [
    ("Missing transaction amount", fact_transactions["amount_missing"], "High", "Transactions without amount."),
    ("Missing UTR", fact_transactions["utr_missing"], "High", "Transactions without UTR."),
    ("Missing MCC", fact_transactions["mcc_missing"], "Medium", "Transactions without MCC."),
    ("Unknown transaction status", fact_transactions["is_unknown_status"], "Medium", "Statuses not mapped to Success, Failed, Pending, or Initiated."),
    ("Negative transaction amount", fact_transactions["amount"].lt(0), "High", "Transactions with negative amount."),
]:
    add_validation(validation_rows, "Transaction Data Quality", metric, mask.sum(), tx_n, severity, desc)

add_validation(validation_rows, "Transaction Status", "Initiated transactions",
               fact_transactions["is_initiated"].sum(), tx_n, "Info",
               "Transactions explicitly marked INITIATED and kept separate from Pending.")

# KYC quality
kyc_n = len(kyc_quality)
add_validation(validation_rows, "KYC Quality", "Users with duplicate KYC records",
               kyc_quality["has_duplicate_kyc_records"].sum(), kyc_n, "High",
               "Users with more than one KYC record.")
add_validation(validation_rows, "KYC Quality", "Users with identity conflict",
               kyc_quality["any_identity_conflict"].astype(bool).sum(), kyc_n, "High",
               "Users with at least one conflict flag in their KYC history.")

# Chargebacks
cb_n = len(fact_chargebacks)
add_validation(validation_rows, "Chargeback Quality", "Missing chargeback transaction ID",
               fact_chargebacks["txn_id_missing_flag"].sum(), cb_n, "High",
               "Chargeback records without transaction ID.")
add_validation(validation_rows, "Chargeback Quality", "Non-missing but unmatched chargeback transaction ID",
               (fact_chargebacks["txn_id_unmatched"] & ~fact_chargebacks["txn_id_missing_flag"]).sum(),
               cb_n, "High", "Chargeback transaction ID exists but does not match transaction fact.")
add_validation(validation_rows, "Chargeback Quality", "Invalid reporting delay",
               fact_chargebacks["invalid_reporting_delay"].sum(), cb_n, "High",
               "Negative reporting delay.")
add_validation(validation_rows, "Chargeback Quality", "Invalid bank response delay",
               fact_chargebacks["invalid_bank_response_delay"].sum(), cb_n, "High",
               "Negative bank response delay.")

validation_report = pd.DataFrame(validation_rows)

# -----------------------------
# SAVE
# -----------------------------
tables = {
    "dim_users.csv": dim_users,
    "user_kyc_quality.csv": kyc_quality,
    "dim_merchants.csv": dim_merchants,
    "dim_date.csv": dim_date,
    "fact_transactions.csv": fact_transactions,
    "fact_chargebacks.csv": fact_chargebacks,
    "validation_report.csv": validation_report,
}
for filename, df in tables.items():
    df.to_csv(OUTPUT_PATH / filename, index=False)

# Console summary
print("=" * 70)
print("DATA MODEL BUILD V2 COMPLETE")
print("=" * 70)
for filename, df in tables.items():
    print(f"Saved: {filename:<32} Shape: {df.shape}")

print("\nKEY FINDINGS")
print(f"Transactions: {len(fact_transactions):,}")
print(f"Canonical users: {len(dim_users):,}")
print(f"Merchants: {len(dim_merchants):,}")
print(f"Chargebacks: {len(fact_chargebacks):,}")
print(f"Transactions without merchant match: {(~fact_transactions['merchant_found_in_master']).sum():,}")
print(f"Missing merchant categories: {cat_missing:,} ({pct(cat_missing, merchant_n)}%)")
print(f"Transactions with unavailable merchant category: {fact_transactions['merchant_category_missing_or_unmatched'].sum():,} ({pct(fact_transactions['merchant_category_missing_or_unmatched'].sum(), tx_n)}%)")
print(f"Initiated transactions: {fact_transactions['is_initiated'].sum():,}")
print(f"Unknown statuses: {fact_transactions['is_unknown_status'].sum():,}")
print(f"Validation rows: {len(validation_report):,}")
print(f"\nOutput folder: {OUTPUT_PATH}")
