# haystack-financial-doc-extractor

Haystack components for structured KV extraction from financial documents (tax returns, W-2s, schedules) via Azure Document Intelligence.

## Components

| Component | Description |
|---|---|
| `AzureDiExtractor` | Submits PDFs to Azure DI with a 4-stage recovery chain (full doc → page splitter → DPI reduction → rotation) |
| `KvNormalizer` | Converts raw string KV pairs to typed `Decimal` values with field-name canonicalisation |
| `DeltaCalculator` | Compares extracted values against reference values, assigns HIGH/MEDIUM/LOW severity |
| `BytesIngestionComponent` | Accepts raw PDF bytes directly for testing or upstream-managed fetches |
| `DocumentIngestionComponent` | Placeholder — replace with your DMS fetch logic (OnBase, S3, SharePoint) |

## Install

```bash
pip install haystack-financial-doc-extractor
```

## Quick start

```python
from haystack_financial_doc_extractor import build_pipeline

pipeline = build_pipeline(
    azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
    azure_api_key="...",
    field_map={"adjusted gross income": "agi", "wages salaries tips": "wages"},
    section="HHA_INCOME",
    source_doc_type="IRS Form 1040",
)

result = pipeline.run({
    "ingest": {
        "bytes_list": [open("form1040.pdf", "rb").read()],
        "document_ids": ["doc-001"],
        "source_names": ["1040-2023.pdf"],
    },
    "delta": {
        "reference_values": {"agi": 75000, "wages": 68000},
    },
})

for field in result["delta"]["fields"]:
    print(field.field_name, field.extracted_value, field.severity)
```

## Sections

Six financial aid sections are supported out of the box via `SectionKey`:

- `HHA_INCOME` — Household A income
- `HHB_INCOME` — Household B income
- `STUDENT` — Student income/assets
- `ASSETS` — Asset documentation
- `HOUSEHOLD` — Household composition
- `EXPENSES` — Expense documentation

## Persistence

Optional SQLite store with MD5-based cache invalidation — skips Azure DI on re-runs if the document hasn't changed:

```python
from haystack_financial_doc_extractor import SqliteExtractionStore

store = SqliteExtractionStore("extractions.db")
if not store.is_cached("doc-001", pdf_bytes):
    # run pipeline, then:
    store.save("doc-001", "1040.pdf", pdf_bytes, stage_used, fields)
```

## License

Apache 2.0
