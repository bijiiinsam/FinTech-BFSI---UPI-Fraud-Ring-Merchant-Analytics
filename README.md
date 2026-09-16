# UPI Fraud Ring & Merchant Analytics
**TransOrg AgentIQ Datathon 2026 — Track 1: FinTech & BFSI**

An analytics pipeline that turns four messy, synthetic UPI payments datasets into a
governed data model and an interactive Power BI dashboard — with documented
fraud-risk, churn, and data-quality metrics for merchant and user risk scoring.

## Problem statement

A National Payments Authority needs to analyze micro-transaction data to spot circular
money-laundering rings, synthetic identity fraud, and compromised merchant accounts,
starting from raw logs with missing UTR numbers, mismatched PAN/Aadhaar formats,
OCR errors, and currency symbols embedded in numeric columns.

## What's in this repo

| File | Rows | What it is |
|---|---|---|
| `track1_upi_transactions_clean.csv` | 20,000 | Cleaned UPI transaction ledger |
| `track1_kyc_records_cleaned.csv` | 35,878 | Cleaned customer KYC records |
| `track1_merchants_master_cleaned.csv` | 6,000 | Cleaned merchant master |
| `json_cleaned.csv` | 2,800 | Cleaned chargeback/dispute records (originally JSON) |
| `track1_analytics_layer.ipynb` | — | Builds a star-schema SQLite DB + fraud/churn metrics |
| `track1_analytics.db` | — | Output SQLite database (6 indexed tables) |
| `build_data_model_v2.py` | — | Builds the dimensional model that powers the dashboard |
| `dim_users.csv` | 28,920 | One row per canonical user, deduplicated from KYC |
| `dim_merchants.csv` | 6,000 | One row per merchant, with data-quality flags |
| `dim_date.csv` | 162 | Date dimension spanning the dataset's date range |
| `fact_transactions.csv` | 20,000 | Transactions enriched with date parts, status groups, match flags |
| `fact_chargebacks.csv` | 2,800 | Chargebacks enriched with delay/validity flags, match flags |
| `user_kyc_quality.csv` | 28,920 | Per-user KYC quality summary (duplicates, conflicts, missing fields) |
| `validation_report.csv` | 20 checks | Data-quality/referential-integrity audit, with severity ratings |
| `Datathon_Dashboard.pbix` | — | Power BI dashboard (5 pages) built on the model above |

See `DATA_DICTIONARY.md` for the column-by-column reference on the four cleaned
source files.

## Pipeline

```
Raw data (messy)
      ↓ cleaning
4 cleaned CSVs (this repo)
      ↓                              ↓
track1_analytics_layer.ipynb   build_data_model_v2.py
      ↓                              ↓
track1_analytics.db          dim_*/fact_*/validation_report.csv
(star schema, fraud/churn          ↓
 metrics)                   Datathon_Dashboard.pbix
```

Two parallel modeling layers sit on top of the same four cleaned files:

- **`track1_analytics_layer.ipynb`** — builds a star-schema SQLite database and
  computes the fraud-risk and churn *formulas* (see below).
- **`build_data_model_v2.py`** — builds the flattened dimension/fact tables the Power
  BI dashboard reads directly, and runs 20 referential-integrity and data-quality
  checks, written out to `validation_report.csv`.

### Cleaning

Each cleaned file carries columns that flag what the cleaning step found and fixed —
for example `ticket_size_was_negative`, `missing_mcc_or_category`, and
`id_collision_flag` in the merchant file; `utr_missing` and `mcc_missing` in
transactions; `impossible_reporting_delay` and `disputed_amount_missing` in
chargebacks. These flags are the audit trail of what was wrong in the raw data and how
it was handled (rather than silently dropped).

> **Note:** the original notebook/script that turned the *raw* files into these
> cleaned CSVs isn't in this repo — only its output. `build_data_model_v2.py` picks up
> from the cleaned files and does further standardization (ID formats, dates, status
> grouping) plus the referential-integrity checks in `validation_report.csv`.

### Analytics layer (`track1_analytics_layer.ipynb`)

Run this notebook top to bottom (it expects the four cleaned CSVs in the same folder)
to rebuild `track1_analytics.db`. It:

1. **Resolves entity IDs.** `user_id` and `merchant_id` aren't reliable primary keys as-is
   — the same logical entity can appear under multiple ID formats. The notebook
   documents this and resolves it into clean `dim_users` / `dim_merchants` tables,
   flagging ambiguous cases rather than silently merging them.
2. **Builds a star schema** (`dim_users`, `dim_merchants`, `fact_transactions`,
   `fact_chargebacks`, `merchant_metrics`, `user_metrics`).
3. **Computes metrics** (full formulas in the notebook, section 4):
   - **Merchant Fraud Risk Score (0–100):** a weighted blend of normalized
     chargeback rate (30%), dispute-severity-weighted rate (20%), failure rate (20%),
     reversal rate (15%), and share of counterpart users flagged high-risk in KYC
     (15%) — with a hard floor of 90 for merchants already marked suspended/blocked.
   - **User Fraud Risk Score (0–100):** chargeback rate (30%), failure rate (15%),
     reversal rate (15%), KYC risk segment (25%), and KYC conflict count (15%) — floor
     of 80 for rejected/failed KYC.
   - **Merchant Churn:** a merchant is churned if it was active in the 30–60 day
     baseline window before the latest transaction but had zero transactions in the
     most recent 30 days.
4. Persists everything to `track1_analytics.db` as six indexed SQLite tables.

### Data model for the dashboard (`build_data_model_v2.py`)

Run with `python build_data_model_v2.py` (needs the four cleaned CSVs in the same
folder; writes to a `model_output/` folder). It:

- Standardizes ID formats (`user_id`/`merchant_id`/`txn_id`/`complaint_id`) and parses
  all date columns.
- Deduplicates KYC into one `dim_users` row per user (keeping the most complete,
  least-conflicted record), and separately reports duplicate/conflict counts per user
  in `user_kyc_quality.csv`.
- Enriches transactions with date parts (year/quarter/month/day/hour/weekend) and a
  standardized `status_group` (Success / Failed / Pending / Initiated / Unknown).
- Enriches chargebacks with delay flags and validity checks.
- Builds a `dim_date` calendar table.
- Cross-checks referential integrity (transactions/chargebacks against the merchant
  and user dimensions) and writes a 20-row `validation_report.csv` — each row is a
  named data-quality metric with a count, a percentage, and a severity rating
  (Critical/High/Medium/Info).

## Dashboard (`Datathon_Dashboard.pbix`)

A 5-page interactive Power BI dashboard built on the tables above:

| Page | What it covers |
|---|---|
| **Executive Overview** | Total transactions, total value, chargeback ratio, failure rate; transaction volume/value trend over time; status distribution; value by merchant category. Filterable by date, status, and merchant category. |
| **Merchant Performance & Risk** | Active/suspended/high-risk merchant counts, merchant chargeback ratio; value contribution and top chargeback volume by merchant; a merchant risk & performance leaderboard. Filterable by merchant status. |
| **Customer & KYC Risk Analytics** | KYC completion rate, verified/rejected counts, identity-conflict counts; KYC status and risk-segment distribution; KYC data-quality issue breakdown; a searchable user table. |
| **Fraud & Chargeback Intelligence** | Merchants with repeated/high-risk chargebacks; top merchants by disputed amount; chargeback rate by category; transaction value vs. disputed amount by category; a merchant chargeback-exposure table. |
| **Data Quality & Analytics Reliability** | Merchant match rate, unmatched-merchant transactions, missing-UTR count; merchant master coverage; transaction reconciliation and completeness by status — this page surfaces the same issues as `validation_report.csv`, interactively. |

### Opening it

1. Install [Power BI Desktop](https://powerbi.microsoft.com/desktop/) (Windows only).
2. Open `Datathon_Dashboard.pbix` — it embeds its own data, so it opens standalone
   with no separate connection needed.
3. To refresh against regenerated data, run `build_data_model_v2.py` first, then use
   **Home → Refresh** in Power BI Desktop.

> ⚠️ **Submission requirement:** the datathon rules require a *live deployed
> dashboard link*, not just the `.pbix` file. Publish this report to the Power BI
> Service (**Home → Publish**, or **File → Publish → Publish to web** for a public
> embed link) and include that link in your submission — the file in this repo alone
> won't satisfy that requirement.

## How to run

```bash
pip install pandas numpy
jupyter notebook track1_analytics_layer.ipynb   # rebuilds track1_analytics.db
python build_data_model_v2.py                   # rebuilds the dashboard's data model
```

Query the analytics DB directly, e.g.:

```python
import sqlite3, pandas as pd
conn = sqlite3.connect("track1_analytics.db")
pd.read_sql("""
    SELECT merchant_id, fraud_risk_score FROM merchant_metrics
    ORDER BY fraud_risk_score DESC LIMIT 10
""", conn)
```

## Status

- [x] Data cleaning (4 raw files → 4 cleaned files)
- [x] Analytics layer — star schema + fraud risk + churn metrics, in SQLite
- [x] Executive dashboard — 5-page Power BI report with filters, KPIs, and a
      dedicated data-quality page
- [ ] Bonus: natural-language-to-chart AI agent

## Known limitations

- The step that produced the four `*_cleaned.csv` files from the *raw* datathon files
  was done inline and that notebook/script isn't checked into this repo — only its
  output, plus the further standardization in `build_data_model_v2.py`.
- The `.pbix` file needs to be published to the Power BI Service to get the live link
  the submission rules require (see above) — it isn't published from this repo alone.
- No AI agent yet — this repo covers Data Rescue, Analytics Layer, and Executive
  Dashboard; the bonus Agentic Graph AI layer isn't built.
