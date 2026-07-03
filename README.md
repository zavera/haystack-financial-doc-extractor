# haystack-financial-doc-extractor

> **Copyright 2026 Ambreen Zaver, Callisto Tech. Licensed under Apache 2.0.**

Haystack components for structured key-value extraction from financial documents —
IRS Form 1040, W-2, Schedule C/E, K-1 (1065) — via Azure Document Intelligence.

Designed for use cases where extracted values must be compared deterministically
against an authoritative reference system (e.g. your practice management software,
a tax reconciliation engine, or an audit workflow). All parsing, normalization, and
delta computation is done in Python with no LLM involvement.

Built for accountants and tax professionals who need to reconcile scanned client
documents against system-of-record values — without hand-keying every field.

---

## Why this package

Standard Haystack document loaders treat a PDF as a blob of text. Financial forms
are structured: every field has a known label, a line reference, and a numeric
value that must round-trip to `Decimal` without loss. This package handles:

- **4-stage Azure DI recovery chain** — full doc → page splitter → DPI reduction → rotation block
- **Financial string normalization** — `$75,000`, `(12,500)`, `75000 USD`, `N/A`, `12.5%`
- **Non-negative field protection** — W-2 box values printed in parens are positive, not negative
- **Delta + severity scoring** — HIGH / MEDIUM / LOW against a reference value dict
- **Privacy-conscious by design** — no PII in logs, opaque document IDs, stateless per-document processing

---

## Install

```bash
pip install azure-di-financial-haystack
```

Requires Python 3.10+.

---

## Components

| Component | Input | Output |
|---|---|---|
| `BytesIngestionComponent` | `bytes_list`, `document_ids`, `source_names` | `list[DocumentPayload]` |
| `DocumentIngestionComponent` | `document_ids` (stub — implement for your DMS) | `list[DocumentPayload]` |
| `AzureDiExtractor` | `list[DocumentPayload]` | `list[dict]` with `kv_entries` |
| `KvNormalizer` | `list[dict]` from extractor | `list[ExtractedField]` |
| `DeltaCalculator` | `list[ExtractedField]` + `reference_values` | `list[ExtractedField]` with delta + severity |

---

## Quick start

```python
from haystack_integrations.components.azure_di_financial import build_pipeline

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map={"adjusted gross income": "agi", "wages salaries tips": "wages"},
    section="INCOME",
    source_doc_type="IRS Form 1040",
)

with open("samples/f1040_filled.pdf", "rb") as f:
    pdf_bytes = f.read()

result = pipeline.run({
    "ingest": {
        "bytes_list": [pdf_bytes],
        "document_ids": ["doc-001"],
        "source_names": ["f1040_filled.pdf"],
    },
    "delta": {
        "reference_values": {"agi": 75000, "wages": 68000},
    },
})

for field in result["delta"]["fields"]:
    print(f"{field.field_name:<30} extracted={field.extracted_value}  delta={field.delta}  severity={field.severity}")
```

---

## Sample usage by form type

All examples below use the synthetic sample forms in `samples/` — all names,
SSNs, EINs, and dollar amounts are entirely fictional (see [Data handling & privacy](#data-handling--privacy)).

### Form 1040

```python
from haystack_integrations.components.azure_di_financial import build_pipeline

FIELD_MAP_1040 = {
    "adjusted gross income":    "agi",
    "wages salaries tips":      "wages",
    "total income":             "total_income",
    "taxable interest":         "taxable_interest",
    "ordinary dividends":       "dividends",
    "capital gain or loss":     "capital_gain",
    "total tax":                "total_tax",
    "federal income tax withheld": "tax_withheld",
}

# Reference values from your authoritative system (e.g. practice management software, prior-year return)
REFERENCE = {"agi": 83200, "wages": 82000, "total_tax": 11500}

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map=FIELD_MAP_1040,
    section="INCOME",
    source_doc_type="IRS Form 1040",
    # capital gains and losses can legitimately be negative — no non_negative_fields here
)

with open("samples/f1040_filled.pdf", "rb") as f:
    pdf_bytes = f.read()

result = pipeline.run({
    "ingest": {"bytes_list": [pdf_bytes], "document_ids": ["1040-2023"], "source_names": ["f1040_filled.pdf"]},
    "delta": {"reference_values": REFERENCE},
})
```

### W-2

```python
from haystack_integrations.components.azure_di_financial import build_pipeline

FIELD_MAP_W2 = {
    "wages tips other compensation": "wages",
    "federal income tax withheld":   "federal_withheld",
    "social security wages":         "ss_wages",
    "social security tax withheld":  "ss_tax_withheld",
    "medicare wages and tips":       "medicare_wages",
    "medicare tax withheld":         "medicare_tax_withheld",
}

REFERENCE = {"wages": 82000, "federal_withheld": 13200}

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map=FIELD_MAP_W2,
    section="INCOME",
    source_doc_type="W-2",
    # W-2 box values are never negative — parenthetical notation means something else
    non_negative_fields=["wages", "federal_withheld", "ss_wages", "ss_tax_withheld",
                         "medicare_wages", "medicare_tax_withheld"],
)

with open("samples/fw2_filled.pdf", "rb") as f:
    pdf_bytes = f.read()

result = pipeline.run({
    "ingest": {"bytes_list": [pdf_bytes], "document_ids": ["w2-2023"], "source_names": ["fw2_filled.pdf"]},
    "delta": {"reference_values": REFERENCE},
})
```

### Schedule C (self-employment)

```python
from haystack_integrations.components.azure_di_financial import build_pipeline

FIELD_MAP_SCHEDULE_C = {
    "gross receipts or sales":   "gross_receipts",
    "gross profit":              "gross_profit",
    "gross income":              "gross_income",
    "total expenses":            "total_expenses",
    "tentative profit or loss":  "net_profit",
    "net profit or loss":        "net_profit",
}

REFERENCE = {"gross_receipts": 45000, "net_profit": 37400}

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map=FIELD_MAP_SCHEDULE_C,
    section="INCOME",
    source_doc_type="Schedule C",
    # net profit CAN be negative (a loss) — do not add to non_negative_fields
)

with open("samples/f1040sc_filled.pdf", "rb") as f:
    pdf_bytes = f.read()

result = pipeline.run({
    "ingest": {"bytes_list": [pdf_bytes], "document_ids": ["schc-2023"], "source_names": ["f1040sc_filled.pdf"]},
    "delta": {"reference_values": REFERENCE},
})
```

### Schedule E (rental income)

```python
from haystack_integrations.components.azure_di_financial import build_pipeline

FIELD_MAP_SCHEDULE_E = {
    "rents received":            "rental_income",
    "royalties received":        "royalties",
    "total rental real estate":  "net_rental",
    "advertising":               "expense_advertising",
    "insurance":                 "expense_insurance",
    "mortgage interest paid":    "expense_mortgage_interest",
}

REFERENCE = {"rental_income": 18000, "net_rental": 16350}

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map=FIELD_MAP_SCHEDULE_E,
    section="INCOME",
    source_doc_type="Schedule E",
)

with open("samples/f1040se_filled.pdf", "rb") as f:
    pdf_bytes = f.read()

result = pipeline.run({
    "ingest": {"bytes_list": [pdf_bytes], "document_ids": ["sche-2023"], "source_names": ["f1040se_filled.pdf"]},
    "delta": {"reference_values": REFERENCE},
})
```

### Schedule K-1 (Form 1065 — partnership)

```python
from haystack_integrations.components.azure_di_financial import build_pipeline

FIELD_MAP_K1 = {
    "ordinary business income loss":  "ordinary_income",
    "net rental real estate income":  "rental_income",
    "interest income":                "interest_income",
    "ordinary dividends":             "dividends",
    "net short term capital gain":    "st_capital_gain",
    "net long term capital gain":     "lt_capital_gain",
}

REFERENCE = {"ordinary_income": 18400, "interest_income": 320}

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map=FIELD_MAP_K1,
    section="INCOME",
    source_doc_type="Schedule K-1 (1065)",
    # ordinary income can be a loss — allow negatives
)

with open("samples/f1065sk1_filled.pdf", "rb") as f:
    pdf_bytes = f.read()

result = pipeline.run({
    "ingest": {"bytes_list": [pdf_bytes], "document_ids": ["k1-2023"], "source_names": ["f1065sk1_filled.pdf"]},
    "delta": {"reference_values": REFERENCE},
})
```

---

## Persistence

There is no built-in caching or storage layer. `build_pipeline()` calls Azure DI
directly on every `pipeline.run()` call; if you need to persist results or skip
re-processing unchanged documents, handle that in your own application code.

---

## Sections

`section` is a plain string label passed to `build_pipeline()` / `KvNormalizer` —
it's stamped onto every `ExtractedField` so you can group and filter results
downstream (e.g. by engagement, client, or income category). There's no fixed
enum; use whatever labels fit your workflow:

```python
build_pipeline(..., section="INCOME", ...)         # income-statement fields
build_pipeline(..., section="EXPENSES", ...)        # deductible expense fields
build_pipeline(..., section="ASSETS", ...)          # balance-sheet / asset fields
build_pipeline(..., section="CLIENT_A_2023", ...)   # per-client, per-year batches
```

---

## Running integration tests

Integration tests hit a live Azure DI endpoint and verify end-to-end extraction
against the synthetic sample forms in `samples/`.

```bash
export AZURE_DI_ENDPOINT="https://<resource>.cognitiveservices.azure.com/"
export AZURE_DI_KEY="<your-key>"

# Unit tests only (no Azure credentials required)
pytest -m unit -v

# Integration tests (live Azure DI calls, ~2 min)
pytest -m integration -v -s
```

Each integration test prints a one-line extraction summary:

```
✅ fw2_filled.pdf  form=W-2  stage=STAGE-0  kv=9
✅ f1040_filled.pdf  form=1040  stage=STAGE-0  kv=14
```

| Field | Meaning |
|---|---|
| `form` | Inferred IRS form type (1040, W-2, Schedule C/E/K-1, 1065, 1120, 1120-S, unknown) |
| `stage` | Recovery stage used (STAGE-0 = full doc, STAGE-1 = page split, STAGE-2 = DPI reduction, STAGE-3 = rotation) |
| `kv` | Number of key-value pairs returned by Azure DI |

Tests are split into three classes:

| Class | Forms tested |
|---|---|
| `TestW2Live` | `fw2_filled.pdf`, `fw2_fake.pdf` — W-2 field extraction, confidence, delta scoring, section labels |
| `TestForm1040Live` | `f1040_filled.pdf`, `f1040_fake.pdf` — 1040 field extraction |
| `TestBatchLive` | Two documents in one `pipeline.run()` call, serialisation round-trip |

---

## Running the example script

```bash
# Install
pip install -e ".[dev]"

# Set Azure credentials
export AZURE_DI_ENDPOINT="https://<resource>.cognitiveservices.azure.com/"
export AZURE_DI_KEY="<your-key>"

# Run against a sample form
python examples/run_pipeline.py --pdf samples/f1040_filled.pdf --section INCOME

# Output:
# FIELD                          EXTRACTED    REFERENCE      DELTA SEVERITY
# --------------------------------------------------------------------------------
# agi                             83200.00      83200.00       0.00 LOW
# wages                           82000.00      82000.00       0.00 LOW
# total_tax                       11500.00      11500.00       0.00 LOW
```

---

## Data handling & privacy

This package is designed for deployment in environments that process sensitive
taxpayer and client financial data.

### What this package does

- **No PII in logs.** The logger emits field names and numeric values only.
  Raw document content (which may contain names and SSNs) is never logged.
- **Opaque document IDs.** The `document_id` passed to components is an
  opaque caller-supplied string. The package does not inspect, store, or log
  it in a way that exposes client identity.
- **No cross-document state.** Each `pipeline.run()` call is stateless.
  No data from one document is accessible during processing of another.
- **Nothing persisted by the package.** The package itself does not write
  extracted values, PDF bytes, or any other data to disk.

### Sample data

All files in `samples/` were generated by `samples/generate_samples.py` using
entirely fictional data:

- Names: `James Harrington` (fictional)
- SSNs: `XXX-XX-1234` (masked — not a real SSN format)
- EINs: `12-3456789`, `98-7654321` (fictional)
- Addresses: `742 Evergreen Terrace, Springfield IL` (fictional)
- Dollar amounts: representative but invented

No real taxpayer data was used. Do not commit real tax documents to this repository.

### Deployer responsibilities

Overall data protection depends on how you deploy this package:

| Concern | Your responsibility |
|---|---|
| Azure DI data retention | Disable Azure DI input/output logging in your Azure resource |
| Network boundary | Deploy behind VPN or private endpoint — never expose extraction endpoints publicly |
| Auth | Protect the endpoints that accept PDF bytes with Bearer JWT or equivalent |
| Blob storage | If storing PDFs in Azure Blob, enable encryption at rest and restrict access |

---

## License

Copyright 2026 Ambreen Zaver, Callisto Tech.
Licensed under the [Apache License, Version 2.0](LICENSE).
