# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Example: Extract and reconcile IRS Form W-2 fields using the pre-wired pipeline.

Field map keys are written as human-readable strings — the KvNormalizer handles
whitespace normalisation (including \\n in Azure DI keys like "Statutory\\nemployee")
and punctuation tolerance (commas, apostrophes) transparently.

Requirements:
    export AZURE_DI_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com/
    export AZURE_DI_KEY=<your-api-key>

Usage:
    python examples/irs_w2_extraction.py path/to/w2.pdf
"""

import os
import sys
from pathlib import Path

from haystack_integrations.components.azure_di_financial import build_pipeline
from haystack_integrations.components.azure_di_financial.models.extracted_field import Severity

# ---------------------------------------------------------------------------
# Field map: Azure DI raw label -> canonical name
#
# Keys match the simplified form of what Azure DI's prebuilt-document model
# returns for a W-2. Newlines and punctuation in Azure DI keys are normalised
# automatically — write keys with spaces, the component handles the rest.
#
# W-2 boxes covered:
#   Box 1  — wages_w2                (Wages, tips, other compensation)
#   Box 2  — federal_tax_withheld    (Federal income tax withheld)
#   Box 3  — ss_wages                (Social security wages)
#   Box 4  — ss_tax_withheld         (Social security tax withheld)
#   Box 5  — medicare_wages          (Medicare wages and tips)
#   Box 6  — medicare_tax_withheld   (Medicare tax withheld)
#   Box 10 — dependent_care          (Dependent care benefits)
#   Box 16 — state_wages             (State wages, tips, etc.)
#   Box 17 — state_income_tax        (State income tax)
#
# Intentionally excluded (non-financial / non-numeric):
#   Box a  — SSN  (PII — never in field map or logs)
#   Box b  — EIN
#   Box c  — employer name/address
#   Box d  — control number
#   Box e/f — employee name/address
#   Box 7  — social security tips  (usually null)
#   Box 8  — allocated tips        (usually null)
#   Box 11 — nonqualified plans    (usually null)
#   Box 12 — various codes         (handled separately if needed)
#   Box 13 — checkboxes            (:selected:/:unselected: — non-numeric)
#   Box 18/19/20 — local wages/tax/locality (usually null)
# ---------------------------------------------------------------------------
FIELD_MAP_W2 = {
    # Box 1
    "wages, tips, other compensation":  "wages_w2",
    # Box 2
    "federal income tax withheld":      "federal_tax_withheld",
    # Box 3
    "social security wages":            "ss_wages",
    # Box 4
    "social security tax withheld":     "ss_tax_withheld",
    # Box 5
    "medicare wages and tips":          "medicare_wages",
    # Box 6
    "medicare tax withheld":            "medicare_tax_withheld",
    # Box 10
    "dependent care benefits":          "dependent_care",
    # Box 16
    "state wages, tips, etc.":          "state_wages",
    # Box 17
    "state income tax":                 "state_income_tax",
}

# ---------------------------------------------------------------------------
# Reference values — what the student/applicant self-reported.
# These come from PowerFAIDS, SIS, or application form.
# Delta = |reference - extracted|. HIGH >= $500, MEDIUM >= $100, LOW < $100.
# ---------------------------------------------------------------------------
REFERENCE_VALUES = {
    "wages_w2":            88450,   # self-reported — expect zero delta if accurate
    "federal_tax_withheld": 6912,   # approximate — small delta expected
    "ss_wages":            88450,
    "medicare_wages":      88450,
    "dependent_care":      12000,
}


def main(pdf_path: str) -> None:
    endpoint = os.environ["AZURE_DI_ENDPOINT"]
    api_key = os.environ["AZURE_DI_KEY"]

    pdf_bytes = Path(pdf_path).read_bytes()

    pipeline = build_pipeline(
        azure_endpoint=endpoint,
        azure_api_key=api_key,
        field_map=FIELD_MAP_W2,
        section="INCOME",
        source_doc_type="IRS Form W-2",
        confidence_threshold=0.50,
    )

    result = pipeline.run(
        {
            "ingest": {
                "bytes_list":   [pdf_bytes],
                "document_ids": ["w2-example"],
                "source_names": [Path(pdf_path).name],
            },
            "delta": {
                "reference_values": REFERENCE_VALUES,
            },
        }
    )

    fields = result["delta"]["fields"]
    # Show only fields that are in our field map (have a short canonical name)
    mapped = [f for f in fields if "_" in f.field_name and len(f.field_name) < 30]
    print(f"\nExtracted {len(mapped)} mapped fields from W-2\n{'─' * 65}")

    for f in sorted(mapped, key=lambda x: (x.severity or Severity.LOW).value, reverse=True):
        delta_str = f"  delta={f.delta:+,.2f}  [{f.severity.name}]" if f.delta is not None else ""
        print(f"  {f.field_name:<25} {str(f.extracted_value or ''):>12}   conf={float(f.confidence):.2f}{delta_str}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python examples/irs_w2_extraction.py <path/to/w2.pdf>")
        sys.exit(1)
    main(sys.argv[1])
