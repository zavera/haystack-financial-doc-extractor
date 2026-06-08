# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Haystack integration tests.

Verifies that components wire correctly inside a real Haystack Pipeline —
type checking, connection validation, serialization, and end-to-end run
without Azure credentials (extractor is mocked).

Run: pytest tests/test_haystack_integration.py -m unit -v
"""

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from haystack import Pipeline
from haystack.core.errors import PipelineConnectError

from haystack_integrations.components.azure_di_financial import (
    AzureDiExtractor,
    BytesIngestionComponent,
    DeltaCalculator,
    KvNormalizer,
    build_pipeline,
)
from haystack_integrations.components.azure_di_financial.models.extracted_field import Severity
from haystack_integrations.components.azure_di_financial.models.kv_entry import KvEntry

FIELD_MAP = {
    "amount from line 11a adjusted gross income": "agi",
    "standard deduction or itemized deductions from schedule a": "standard_deduction",
    "subtract line 14 from line 11b if zero or less enter 0 this is your taxable income": "taxable_income",
    "other taxes including selfemployment tax from schedule 2 line 21": "other_taxes",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_extraction(kv_entries: list[KvEntry]) -> list[dict]:
    """Simulate what AzureDiExtractor.run() returns — bypasses real Azure call."""
    return [{"document_id": "test-doc", "source_name": "test.pdf", "metadata": {},
             "kv_entries": kv_entries, "stage_used": "STAGE-0", "error": None}]


# ---------------------------------------------------------------------------
# Pipeline wiring
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestPipelineWiring:

    def test_correct_connections_do_not_raise(self):
        # Build a partial pipeline without the extractor — just the components
        # we can instantiate without Azure credentials.
        p = Pipeline()
        p.add_component("normalizer", KvNormalizer(FIELD_MAP, "INCOME", "IRS Form 1040"))
        p.add_component("delta", DeltaCalculator())
        # Should not raise — types match
        p.connect("normalizer.fields", "delta.fields")

    def test_mismatched_types_raise_pipeline_connect_error(self):
        p = Pipeline()
        p.add_component("ingest", BytesIngestionComponent())
        p.add_component("normalizer", KvNormalizer(FIELD_MAP, "INCOME", "IRS Form 1040"))
        with pytest.raises(PipelineConnectError):
            # ingest outputs DocumentPayload list, normalizer expects dict list — must fail
            p.connect("ingest.documents", "normalizer.extractions")

    def test_build_pipeline_returns_haystack_pipeline(self):
        # Patch only the Azure DI client construction inside __init__,
        # leaving the Haystack @component decorator machinery intact.
        with patch("haystack_integrations.components.azure_di_financial.azure_di_extractor.DocumentAnalysisClient"):
            pipeline = build_pipeline(
                azure_endpoint="https://fake.cognitiveservices.azure.com/",
                azure_api_key="fake-key",
                field_map=FIELD_MAP,
                section="INCOME",
                source_doc_type="IRS Form 1040",
            )
        assert isinstance(pipeline, Pipeline)


# ---------------------------------------------------------------------------
# Component output types declared correctly for Haystack
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestComponentOutputTypes:

    def test_bytes_ingestion_output_type_declared(self):
        comp = BytesIngestionComponent()
        assert hasattr(comp, "__haystack_output__")

    def test_kv_normalizer_output_type_declared(self):
        comp = KvNormalizer(FIELD_MAP, "INCOME", "IRS Form 1040")
        assert hasattr(comp, "__haystack_output__")

    def test_delta_calculator_output_type_declared(self):
        comp = DeltaCalculator()
        assert hasattr(comp, "__haystack_output__")


# ---------------------------------------------------------------------------
# End-to-end pipeline run with mocked extractor
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestEndToEndPipelineRun:

    def _build_pipeline_with_mock_extractor(self, kv_entries: list[KvEntry]) -> Pipeline:
        """Build a real Haystack pipeline with AzureDiExtractor mocked out."""
        mock_extractor = MagicMock()
        mock_extractor.__haystack_input__ = {"documents": MagicMock()}
        mock_extractor.__haystack_output__ = {"extractions": MagicMock()}
        mock_extractor.run = MagicMock(return_value={"extractions": _fake_extraction(kv_entries)})

        p = Pipeline()
        p.add_component("normalizer", KvNormalizer(FIELD_MAP, "INCOME", "IRS Form 1040"))
        p.add_component("delta", DeltaCalculator())
        p.connect("normalizer.fields", "delta.fields")
        return p, mock_extractor

    def test_full_run_produces_scored_fields(self):
        entries = [
            KvEntry("Amount from line 11a adjusted gross income", "83200", Decimal("0.68")),
            KvEntry("Standard deduction or itemized deductions (from Schedule A)", "13850", Decimal("0.68")),
            KvEntry("Subtract line 14 from line 11b. If zero or less, enter -0-. This is your taxable income", "11500", Decimal("0.60")),
        ]
        pipeline, mock_extractor = self._build_pipeline_with_mock_extractor(entries)
        extractions = mock_extractor.run(documents=[])["extractions"]

        result = pipeline.run({
            "normalizer": {"extractions": extractions},
            "delta":      {"reference_values": {"agi": 83200, "taxable_income": 12000}},
        })

        fields = result["delta"]["fields"]
        by_name = {f.field_name: f for f in fields}

        assert "agi" in by_name
        assert by_name["agi"].extracted_value == Decimal("83200")
        assert by_name["agi"].delta == Decimal("0")
        assert by_name["agi"].severity == Severity.LOW

        assert "taxable_income" in by_name
        assert by_name["taxable_income"].delta == Decimal("500")
        assert by_name["taxable_income"].severity == Severity.HIGH

        assert "standard_deduction" in by_name
        assert by_name["standard_deduction"].extracted_value == Decimal("13850")
        assert by_name["standard_deduction"].delta is None  # no reference supplied

    def test_pipeline_run_with_zero_reference_values(self):
        entries = [KvEntry("Amount from line 11a adjusted gross income", "83200", Decimal("0.68"))]
        pipeline, mock_extractor = self._build_pipeline_with_mock_extractor(entries)
        extractions = mock_extractor.run(documents=[])["extractions"]

        result = pipeline.run({
            "normalizer": {"extractions": extractions},
            "delta":      {"reference_values": {}},
        })
        fields = result["delta"]["fields"]
        assert all(f.delta is None for f in fields)
        assert all(f.severity is None for f in fields)

    def test_pipeline_run_with_high_delta_flags_correctly(self):
        entries = [KvEntry("Amount from line 11a adjusted gross income", "68000", Decimal("0.90"))]
        pipeline, mock_extractor = self._build_pipeline_with_mock_extractor(entries)
        extractions = mock_extractor.run(documents=[])["extractions"]

        result = pipeline.run({
            "normalizer": {"extractions": extractions},
            "delta":      {"reference_values": {"agi": 75000}},
        })
        agi = next(f for f in result["delta"]["fields"] if f.field_name == "agi")
        assert agi.severity == Severity.HIGH
        assert agi.delta == Decimal("7000")


# ---------------------------------------------------------------------------
# Serialisation round-trip via Haystack to_dict / from_dict
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestHaystackSerialisation:

    def test_kv_normalizer_to_dict_contains_type(self):
        comp = KvNormalizer(FIELD_MAP, "INCOME", "IRS Form 1040", confidence_threshold=0.6)
        d = comp.to_dict()
        assert d["type"] == "haystack_integrations.components.azure_di_financial.kv_normalizer.KvNormalizer"
        assert d["init_parameters"]["section"] == "INCOME"
        assert d["init_parameters"]["confidence_threshold"] == pytest.approx(0.6)

    def test_kv_normalizer_from_dict_restores_state(self):
        original = KvNormalizer(FIELD_MAP, "INCOME", "IRS Form 1040",
                                confidence_threshold=0.7, non_negative_fields=["wages"])
        restored = KvNormalizer.from_dict(original.to_dict())
        assert restored.section == "INCOME"
        assert float(restored.confidence_threshold) == pytest.approx(0.7)
        assert "wages" in restored.non_negative_fields

    def test_delta_calculator_to_dict_contains_type(self):
        comp = DeltaCalculator(high_threshold=1000.0, medium_threshold=200.0)
        d = comp.to_dict()
        assert d["type"] == "haystack_integrations.components.azure_di_financial.delta_calculator.DeltaCalculator"
        assert d["init_parameters"]["high_threshold"] == pytest.approx(1000.0)

    def test_delta_calculator_from_dict_restores_thresholds(self):
        original = DeltaCalculator(high_threshold=750.0, medium_threshold=150.0)
        restored = DeltaCalculator.from_dict(original.to_dict())
        assert float(restored.high_threshold) == pytest.approx(750.0)
        assert float(restored.medium_threshold) == pytest.approx(150.0)
