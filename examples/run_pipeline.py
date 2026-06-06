"""
End-to-end example: extract KV fields from a tax PDF and compare against
reference values from an authoritative system.

Run:
    pip install -e ".[dev]"
    python examples/run_pipeline.py --pdf path/to/form1040.pdf
"""

import argparse
import json
import os
from decimal import Decimal
from pathlib import Path

from haystack_financial_doc_extractor import build_pipeline, SqliteExtractionStore

# ---------------------------------------------------------------------------
# IRS Form 1040 field map — Azure DI raw label → canonical field name
# Extend this for W-2, Schedule C/E/K-1, etc.
# ---------------------------------------------------------------------------
FIELD_MAP_1040 = {
    "adjusted gross income": "agi",
    "total income": "total_income",
    "wages salaries tips": "wages",
    "taxable interest": "taxable_interest",
    "ordinary dividends": "dividends",
    "qualified dividends": "qualified_dividends",
    "ira distributions": "ira_distributions",
    "pensions and annuities": "pensions",
    "social security benefits": "social_security",
    "capital gain or loss": "capital_gain",
    "total tax": "total_tax",
    "federal income tax withheld": "tax_withheld",
}

# Simulated reference values from an authoritative system (e.g. PowerFAIDS).
# In production replace this with a real fetch.
REFERENCE_VALUES = {
    "agi": 75_000,
    "wages": 68_000,
    "total_income": 78_500,
    "total_tax": 12_400,
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run financial doc extraction pipeline")
    parser.add_argument("--pdf", required=True, help="Path to PDF file")
    parser.add_argument("--db", default="extractions.db", help="SQLite database path")
    parser.add_argument("--section", default="HHA_INCOME", help="Section key")
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    pdf_bytes = pdf_path.read_bytes()

    store = SqliteExtractionStore(db_path=args.db)

    # Cache check — skip Azure DI if we've already processed this exact file
    if store.is_cached(document_id=pdf_path.name, pdf_bytes=pdf_bytes):
        print(f"Cache hit for {pdf_path.name} — loading from SQLite")
        fields = store.load_cached(document_id=pdf_path.name, pdf_bytes=pdf_bytes)
    else:
        pipeline = build_pipeline(
            azure_endpoint=os.environ["AZURE_DI_ENDPOINT"],
            azure_api_key=os.environ["AZURE_DI_KEY"],
            field_map=FIELD_MAP_1040,
            section=args.section,
            source_doc_type="IRS Form 1040",
        )

        result = pipeline.run({
            "ingest": {
                "bytes_list": [pdf_bytes],
                "document_ids": [pdf_path.name],
                "source_names": [pdf_path.name],
            },
            "delta": {
                "reference_values": REFERENCE_VALUES,
            },
        })

        fields = result["delta"]["fields"]
        extractions = result["extractor"]["extractions"]
        stage_used = extractions[0]["stage_used"] if extractions else "UNKNOWN"

        store.save(
            document_id=pdf_path.name,
            source_name=pdf_path.name,
            pdf_bytes=pdf_bytes,
            stage_used=stage_used,
            fields=fields,
        )
        print(f"Extraction complete via {stage_used}. Persisted {len(fields)} fields.")

    # Print results
    print(f"\n{'FIELD':<30} {'EXTRACTED':>12} {'REFERENCE':>12} {'DELTA':>10} SEVERITY")
    print("-" * 80)
    for f in fields:
        ext = str(f.extracted_value) if f.extracted_value is not None else "—"
        ref = str(f.reference_value) if f.reference_value is not None else "—"
        delta = str(f.delta) if f.delta is not None else "—"
        sev = f.severity.value if f.severity else "—"
        print(f"{f.field_name:<30} {ext:>12} {ref:>12} {delta:>10} {sev}")


if __name__ == "__main__":
    main()
