# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for KvNormalizer — no Azure DI or DB required."""

from decimal import Decimal

import pytest

from haystack_integrations.components.azure_di_financial.kv_normalizer import KvNormalizer
from haystack_integrations.components.azure_di_financial.models.kv_entry import KvEntry

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


@pytest.mark.unit
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


@pytest.mark.unit
class TestFieldNameResolution:
    def test_exact_match(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].field_name == "agi"

    def test_unknown_key_falls_back_to_snake_case(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("Other Income", "1000", Decimal("0.99"))]))
        assert result["fields"][0].field_name == "other_income"

    def test_key_with_special_chars_matches_field_map(self):
        """Azure DI raw keys contain punctuation — strip-and-match must still resolve."""
        norm = KvNormalizer(
            field_map={"subtract line 14 from line 11b if zero or less enter 0 this is your taxable income": "taxable_income"},
            section="INCOME",
            source_doc_type="IRS Form 1040",
        )
        raw_key = "Subtract line 14 from line 11b. If zero or less, enter -0-. This is your taxable income"
        result = norm.run(extraction([KvEntry(raw_key, "11500", Decimal("0.60"))]))
        assert result["fields"][0].field_name == "taxable_income"

    def test_key_with_irregular_spacing_still_matches(self):
        """Azure DI keys sometimes have double spaces — must collapse before lookup."""
        norm = KvNormalizer(
            field_map={"adjusted gross income": "agi"},
            section="INCOME",
            source_doc_type="IRS Form 1040",
        )
        raw_key = "Adjusted  Gross  Income"   # double spaces
        result = norm.run(extraction([KvEntry(raw_key, "75000", Decimal("0.70"))]))
        assert result["fields"][0].field_name == "agi"


@pytest.mark.unit
class TestConfidenceFiltering:
    def test_low_confidence_entry_is_skipped(self):
        norm = make_normalizer(confidence_threshold=0.8)
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.5"))]))
        assert result["fields"] == []

    def test_entry_at_threshold_is_kept(self):
        norm = make_normalizer(confidence_threshold=0.8)
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.8"))]))
        assert len(result["fields"]) == 1


@pytest.mark.unit
class TestNonNegativeFields:
    def test_parenthetical_treated_as_positive_for_non_negative_field(self):
        norm = KvNormalizer(
            field_map={"wages salaries tips": "wages"},
            section="HHA_INCOME",
            source_doc_type="W-2",
            non_negative_fields=["wages"],
        )
        result = norm.run(extraction([KvEntry("wages salaries tips", "(68,500)", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value == Decimal("68500")

    def test_parenthetical_still_negative_for_other_fields(self):
        norm = KvNormalizer(
            field_map={"capital gain or loss": "capital_gain"},
            section="HHA_INCOME",
            source_doc_type="IRS Form 1040",
            non_negative_fields=["wages"],
        )
        result = norm.run(extraction([KvEntry("capital gain or loss", "(5,000)", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value == Decimal("-5000")


@pytest.mark.unit
class TestSectionAndMetadata:
    def test_section_is_attached(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].section == "HHA_INCOME"

    def test_source_doc_type_is_attached(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].source_doc_type == "IRS Form 1040"


@pytest.mark.unit
class TestSerialization:
    def test_to_dict_round_trip(self):
        norm = make_normalizer(confidence_threshold=0.7, non_negative_fields=["wages"])
        d = norm.to_dict()
        restored = KvNormalizer.from_dict(d)
        assert restored.section == norm.section
        assert restored.source_doc_type == norm.source_doc_type
        assert float(restored.confidence_threshold) == pytest.approx(0.7)
        assert "wages" in restored.non_negative_fields
