"""Unit tests for DeltaCalculator — pure function, no I/O."""

from decimal import Decimal
import pytest
from haystack_financial_doc_extractor.components.delta_calculator import DeltaCalculator
from haystack_financial_doc_extractor.models.extracted_field import ExtractedField, Severity


def make_field(field_name: str, extracted_value: Decimal | None) -> ExtractedField:
    return ExtractedField(
        field_name=field_name,
        extracted_value=extracted_value,
        raw_value=str(extracted_value),
        confidence=Decimal("0.99"),
        source_doc_type="IRS Form 1040",
        source_line_ref=None,
        section="HHA_INCOME",
    )


def make_calc(**kwargs) -> DeltaCalculator:
    return DeltaCalculator(high_threshold=500.0, medium_threshold=100.0, **kwargs)


class TestSeverityAssignment:
    def test_high_severity_when_delta_exceeds_high_threshold(self):
        calc = make_calc()
        field = make_field("agi", Decimal("74000"))
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        assert result["fields"][0].severity == Severity.HIGH

    def test_medium_severity_when_delta_between_thresholds(self):
        calc = make_calc()
        field = make_field("agi", Decimal("74800"))
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        assert result["fields"][0].severity == Severity.MEDIUM

    def test_low_severity_for_small_delta(self):
        calc = make_calc()
        field = make_field("agi", Decimal("74950"))
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        assert result["fields"][0].severity == Severity.LOW

    def test_severity_uses_absolute_delta(self):
        # Extracted > reference (negative delta) should still classify by magnitude
        calc = make_calc()
        field = make_field("agi", Decimal("76000"))
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        assert result["fields"][0].severity == Severity.HIGH


class TestDeltaComputation:
    def test_delta_is_reference_minus_extracted(self):
        calc = make_calc()
        field = make_field("agi", Decimal("74000"))
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        assert result["fields"][0].delta == Decimal("1000")

    def test_negative_delta_when_extracted_exceeds_reference(self):
        calc = make_calc()
        field = make_field("agi", Decimal("76000"))
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        assert result["fields"][0].delta == Decimal("-1000")


class TestEdgeCases:
    def test_no_reference_value_leaves_field_unchanged(self):
        calc = make_calc()
        field = make_field("unknown_field", Decimal("1000"))
        result = calc.run(fields=[field], reference_values={})
        f = result["fields"][0]
        assert f.delta is None
        assert f.severity is None

    def test_none_extracted_value_leaves_field_unchanged(self):
        calc = make_calc()
        field = make_field("agi", None)
        result = calc.run(fields=[field], reference_values={"agi": 75000})
        f = result["fields"][0]
        assert f.delta is None
        assert f.severity is None

    def test_reference_accepts_float_and_int(self):
        calc = make_calc()
        field = make_field("agi", Decimal("75000"))
        result = calc.run(fields=[field], reference_values={"agi": 75000.0})
        assert result["fields"][0].delta == Decimal("0")
