# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Example: Extract and reconcile IRS Form 1040 fields using the pre-wired pipeline.

Requirements:
    export AZURE_DI_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com/
    export AZURE_DI_KEY=<your-api-key>

Usage:
    python examples/irs_1040_extraction.py path/to/1040.pdf
"""

import os
import sys
from pathlib import Path

from haystack_integrations.components.azure_di_financial import build_pipeline
from haystack_integrations.components.azure_di_financial.models.extracted_field import Severity

# ---------------------------------------------------------------------------
# Field map: Azure DI raw label -> canonical name
# Extend as needed for your form variant.
# ---------------------------------------------------------------------------
FIELD_MAP_1040 = {
    "adjusted gross income": "agi",
    "wages, salaries, tips, etc.": "wages",
    "taxable income": "taxable_income",
    "total tax": "total_tax",
    "total income": "total_income",
    "business income or (loss)": "business_income",
    "capital gain or (loss)": "capital_gain",
    "other income": "other_income",
}

# Reference values from an authoritative system (e.g. PowerFAIDS, SIS).
# Leave empty to skip delta scoring.
REFERENCE_VALUES = {
    "agi":    75000,
    "wages":  68000,
}


def main(pdf_path: str) -> None:
    endpoint = os.environ["AZURE_DI_ENDPOINT"]
    api_key = os.environ["AZURE_DI_KEY"]

    pdf_bytes = Path(pdf_path).read_bytes()

    pipeline = build_pipeline(
        azure_endpoint=endpoint,
        azure_api_key=api_key,
        field_map=FIELD_MAP_1040,
        section="INCOME",
        source_doc_type="IRS Form 1040",
    )

    result = pipeline.run(
        {
            "ingest": {
                "bytes_list": [pdf_bytes],
                "document_ids": ["1040-example"],
                "source_names": [Path(pdf_path).name],
            },
            "delta": {
                "reference_values": REFERENCE_VALUES,
            },
        }
    )

    fields = result["delta"]["fields"]
    print(f"\nExtracted {len(fields)} fields\n{'─' * 60}")

    for f in sorted(fields, key=lambda x: (x.severity or Severity.LOW).value):
        delta_str = f"  delta={f.delta:+,.2f}  [{f.severity}]" if f.delta is not None else ""
        print(f"  {f.field_name:<25} {str(f.extracted_value):>12}   conf={float(f.confidence):.2f}{delta_str}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python examples/irs_1040_extraction.py <path/to/1040.pdf>")
        sys.exit(1)
    main(sys.argv[1])
