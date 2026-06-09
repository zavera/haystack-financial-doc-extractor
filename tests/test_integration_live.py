# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Live integration tests — require real Azure DI credentials and network access.

Run with:
    export AZURE_DI_ENDPOINT=https://<resource>.cognitiveservices.azure.com/
    export AZURE_DI_KEY=<your-api-key>
    pytest -m integration -v

These tests are intentionally excluded from CI (no Azure credentials in CI).
They exist to verify end-to-end behaviour against real Azure Document Intelligence.
"""

import os
from decimal import Decimal
from pathlib import Path

import pytest

from haystack_integrations.components.azure_di_financial import build_pipeline
from haystack_integrations.components.azure_di_financial.models.extracted_field import ExtractedField, Severity

SAMPLES = Path(__file__).parent.parent / "samples"

FIELD_MAP_W2 = {
    "wages, tips, other compensation": "wages_w2",
    "federal income tax withheld":     "federal_tax_withheld",
    "social security wages":           "ss_wages",
    "social security tax withheld":    "ss_tax_withheld",
    "medicare wages and tips":         "medicare_wages",
    "medicare tax withheld":           "medicare_tax_withheld",
    "dependent care benefits":         "dependent_care",
    "state wages, tips, etc.":         "state_wages",
    "state income tax":                "state_income_tax",
}

FIELD_MAP_1040 = {
    "amount from line 11a adjusted gross income":    "agi",
    "wages salaries tips etc attach forms w-2":      "wages",
    "total tax":                                     "total_tax",
    "federal income tax withheld":                   "federal_tax_withheld",
    "amount you owe":                                "amount_owed",
    "overpaid":                                      "overpaid",
}


@pytest.fixture(scope="module")
def azure_endpoint():
    val = os.environ.get("AZURE_DI_ENDPOINT", "")
    if not val:
        pytest.skip("AZURE_DI_ENDPOINT not set — skipping live integration tests")
    return val


@pytest.fixture(scope="module")
def azure_api_key():
    val = os.environ.get("AZURE_DI_KEY", "")
    if not val:
        pytest.skip("AZURE_DI_KEY not set — skipping live integration tests")
    return val


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_pipeline(pdf_path: Path, field_map: dict, endpoint: str, api_key: str,
                 reference_values: dict | None = None) -> list[ExtractedField]:
    pipeline = build_pipeline(
        azure_endpoint=endpoint,
        azure_api_key=api_key,
        field_map=field_map,
        section="INCOME",
        source_doc_type=pdf_path.stem,
        confidence_threshold=0.40,
    )
    result = pipeline.run({
        "ingest": {
            "bytes_list":   [pdf_path.read_bytes()],
            "document_ids": [pdf_path.stem],
            "source_names": [pdf_path.name],
        },
        "delta": {
            "reference_values": reference_values or {},
        },
    })
    return result["delta"]["fields"]


# ---------------------------------------------------------------------------
# W-2 tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestW2Live:
    def test_pipeline_returns_fields(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "fw2_filled.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key)
        assert len(fields) > 0, "Expected at least one extracted field from W-2"

    def test_all_extracted_values_are_decimal_or_none(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "fw2_filled.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key)
        for f in fields:
            assert f.extracted_value is None or isinstance(f.extracted_value, Decimal), (
                f"Expected Decimal or None for {f.field_name}, got {type(f.extracted_value)}"
            )

    def test_confidence_scores_are_between_0_and_1(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "fw2_filled.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key)
        for f in fields:
            assert Decimal("0") <= f.confidence <= Decimal("1"), (
                f"Confidence out of range for {f.field_name}: {f.confidence}"
            )

    def test_at_least_one_mapped_field_resolved(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "fw2_filled.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key)
        canonical_names = {f.field_name for f in fields}
        mapped = canonical_names & set(FIELD_MAP_W2.values())
        assert len(mapped) > 0, (
            f"Expected at least one canonical field from FIELD_MAP_W2 to resolve. Got: {canonical_names}"
        )

    def test_delta_scoring_assigns_severity(self, azure_endpoint, azure_api_key):
        reference = {"wages_w2": 999999}  # deliberately large mismatch -> HIGH
        fields = run_pipeline(
            SAMPLES / "fw2_filled.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key,
            reference_values=reference,
        )
        wages_fields = [f for f in fields if f.field_name == "wages_w2" and f.delta is not None]
        if wages_fields:
            assert wages_fields[0].severity == Severity.HIGH, (
                f"Expected HIGH severity for large delta, got {wages_fields[0].severity}"
            )

    def test_section_label_attached(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "fw2_filled.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key)
        for f in fields:
            assert f.section == "INCOME"

    def test_fake_w2_also_extracts(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "fw2_fake.pdf", FIELD_MAP_W2, azure_endpoint, azure_api_key)
        assert len(fields) > 0, "Expected fields from fw2_fake.pdf"


# ---------------------------------------------------------------------------
# 1040 tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestForm1040Live:
    def test_pipeline_returns_fields(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "f1040_filled.pdf", FIELD_MAP_1040, azure_endpoint, azure_api_key)
        assert len(fields) > 0, "Expected at least one extracted field from 1040"

    def test_all_extracted_values_are_decimal_or_none(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "f1040_filled.pdf", FIELD_MAP_1040, azure_endpoint, azure_api_key)
        for f in fields:
            assert f.extracted_value is None or isinstance(f.extracted_value, Decimal)

    def test_fake_1040_extracts(self, azure_endpoint, azure_api_key):
        fields = run_pipeline(SAMPLES / "f1040_fake.pdf", FIELD_MAP_1040, azure_endpoint, azure_api_key)
        assert len(fields) > 0, "Expected fields from f1040_fake.pdf"


# ---------------------------------------------------------------------------
# Multi-document batch test
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestBatchLive:
    def test_two_documents_in_one_run(self, azure_endpoint, azure_api_key):
        pipeline = build_pipeline(
            azure_endpoint=azure_endpoint,
            azure_api_key=azure_api_key,
            field_map=FIELD_MAP_W2,
            section="INCOME",
            source_doc_type="IRS Form W-2",
            confidence_threshold=0.40,
        )
        w2_bytes      = (SAMPLES / "fw2_filled.pdf").read_bytes()
        w2_fake_bytes = (SAMPLES / "fw2_fake.pdf").read_bytes()

        result = pipeline.run({
            "ingest": {
                "bytes_list":   [w2_bytes, w2_fake_bytes],
                "document_ids": ["w2-real", "w2-fake"],
                "source_names": ["fw2_filled.pdf", "fw2_fake.pdf"],
            },
            "delta": {"reference_values": {}},
        })
        fields = result["delta"]["fields"]
        assert len(fields) > 0, "Expected fields from batch of two documents"

    def test_serialisation_round_trip_after_live_run(self, azure_endpoint, azure_api_key):
        """Pipeline can be serialised to dict and reconstructed without error."""
        from haystack_integrations.components.azure_di_financial import (
            AzureDiExtractor,
            KvNormalizer,
            DeltaCalculator,
        )
        extractor = AzureDiExtractor(endpoint=azure_endpoint, api_key=azure_api_key)
        restored  = AzureDiExtractor.from_dict(extractor.to_dict())
        assert restored.endpoint == extractor.endpoint
        assert restored.max_workers == extractor.max_workers

        normalizer = KvNormalizer(field_map=FIELD_MAP_W2, section="INCOME", source_doc_type="W-2")
        restored_n = KvNormalizer.from_dict(normalizer.to_dict())
        assert restored_n.section == "INCOME"

        calc      = DeltaCalculator(high_threshold=500.0, medium_threshold=100.0)
        restored_c = DeltaCalculator.from_dict(calc.to_dict())
        assert restored_c.high_threshold == Decimal("500")
