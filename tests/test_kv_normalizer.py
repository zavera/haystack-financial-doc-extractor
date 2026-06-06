"""Unit tests for KvNormalizer — no Azure DI or DB required."""

from decimal import Decimal
import pytest
from haystack_financial_doc_extractor.components.kv_normalizer import KvNormalizer
from haystack_financial_doc_extractor.models.kv_entry import KvEntry


FIELD_MAP = {
    "adjusted gross income": "agi",
    "wages salaries tips": "wages",
    "total tax": "total_tax",
}

def make_normalizer(**kwargs) -> KvNormalizer:
    return KvNormalizer(
        field_map=FIELD_MAP,
        section="HHA_INCOME",
        source_doc_type="IRS Form 1040",
        **kwargs,
    )


def extraction(entries: list[KvEntry]) -> list[dict]:
    return [{"kv_entries": entries, "source_name": "test.pdf"}]


class TestValueParsing:
    def test_plain_integer(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value == Decimal("75000")

    def test_currency_symbol_and_commas(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "$75,000", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value == Decimal("75000")

    def test_parenthetical_negative(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("total tax", "(12,500)", Decimal("0.95"))]))
        assert result["fields"][0].extracted_value == Decimal("-12500")

    def test_blank_value_returns_none(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "N/A", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value is None

    def test_empty_string_returns_none(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value is None

    def test_percent_value(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("some rate", "12.5%", Decimal("0.90"))]))
        assert result["fields"][0].extracted_value == Decimal("0.125")

    def test_trailing_currency_code(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000 USD", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value == Decimal("75000")


class TestFieldNameResolution:
    def test_exact_match(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].field_name == "agi"

    def test_unknown_key_falls_back_to_snake_case(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("Other Income", "1000", Decimal("0.99"))]))
        assert result["fields"][0].field_name == "other_income"


class TestConfidenceFiltering:
    def test_low_confidence_entry_is_skipped(self):
        norm = make_normalizer(confidence_threshold=0.8)
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.5"))]))
        assert result["fields"] == []

    def test_entry_at_threshold_is_kept(self):
        norm = make_normalizer(confidence_threshold=0.8)
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.8"))]))
        assert len(result["fields"]) == 1


class TestSectionAndMetadata:
    def test_section_is_attached(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].section == "HHA_INCOME"

    def test_source_doc_type_is_attached(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].source_doc_type == "IRS Form 1040"
