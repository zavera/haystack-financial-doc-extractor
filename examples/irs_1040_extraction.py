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
#
# Keys are the exact snake_case labels returned by prebuilt-document on a
# real IRS Form 1040 (2025). Azure DI concatenates the full line description
# as the key — these mappings normalise them to short canonical names.
#
# Derived fields (not directly extractable):
#   total_tax  = line 22 + line 23  (both present, sum them downstream)
#   total_payments = line 25d + line 26 + line 32
# ---------------------------------------------------------------------------
FIELD_MAP_1040 = {
    # Keys are the simplified Azure DI raw labels (lowercase, spaces preserved).
    # KvNormalizer strips special chars then matches — these align with that path.

    # Line 11a / 11b — adjusted gross income
    "amount from line 11a adjusted gross income":                                                               "agi",
    "subtract line 10 from line 9 this is your adjusted gross income":                                         "agi",

    # Line 12e — standard deduction
    "standard deduction or itemized deductions from schedule a":                                                "standard_deduction",

    # Line 14 — total deductions
    "add lines 12e 13a and 13b":                                                                                "total_deductions",

    # Line 15 — taxable income
    "subtract line 14 from line 11b if zero or less enter 0 this is your taxable income":                      "taxable_income",

    # Line 23 — other taxes (self-employment etc.)
    "other taxes including selfemployment tax from schedule 2 line 21":                                         "other_taxes",

    # Line 24 — total tax
    "add lines 22 and 23 this is your total tax":                                                               "total_tax",

    # Line 25d — total federal tax withheld
    "add lines 25a through 25c":                                                                                "federal_tax_withheld",

    # Line 7a — capital gain/loss
    "capital gain or loss attach schedule d if required":                                                       "capital_gain",

    # Line 8 — additional income
    "additional income from schedule 1 line 10":                                                                "additional_income",

    # Line 9 — total income
    "add lines 1z 2b 3b 4b 5b 6b 7a and 8 this is your total income":                                         "total_income",

    # Line 2b / 3b / 6b
    "taxable interest":                                                                                         "taxable_interest",
    "ordinary dividends":                                                                                       "ordinary_dividends",

    # Line 1a — W-2 wages
    "total amount from forms w2 box 1 see instructions":                                                        "wages_w2",
}

# ---------------------------------------------------------------------------
# Reference values — what the student/applicant self-reported.
# These come from your authoritative system (PowerFAIDS, SIS, application).
# Delta = reference - extracted. HIGH >= $500, MEDIUM >= $100, LOW < $100.
# ---------------------------------------------------------------------------
REFERENCE_VALUES = {
    "agi":              83200,   # matches form — expect zero delta
    "taxable_income":   11500,   # matches form — expect zero delta
    "wages_w2":         68000,   # hypothetical self-reported — will show delta
    "total_tax":        13200,   # hypothetical self-reported
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
