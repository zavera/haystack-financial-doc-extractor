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
        section="INCOME",
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
            section="INCOME",
            source_doc_type="W-2",
            non_negative_fields=["wages"],
        )
        result = norm.run(extraction([KvEntry("wages salaries tips", "(68,500)", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value == Decimal("68500")

    def test_parenthetical_still_negative_for_other_fields(self):
        norm = KvNormalizer(
            field_map={"capital gain or loss": "capital_gain"},
            section="INCOME",
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
        assert result["fields"][0].section == "INCOME"

    def test_source_doc_type_is_attached(self):
        norm = make_normalizer()
        result = norm.run(extraction([KvEntry("adjusted gross income", "75000", Decimal("0.99"))]))
        assert result["fields"][0].source_doc_type == "IRS Form 1040"


@pytest.mark.unit
class TestW2Payload:
    """Verify normalizer against exact Azure DI W-2 KV payload keys.

    Key/value pairs are taken verbatim from a real Azure DI prebuilt-document
    response on an IRS Form W-2. This class is the canonical regression suite
    for W-2 field resolution.
    """

    W2_FIELD_MAP = {
        "wages, tips, other compensation":  "wages_w2",
        "federal income tax withheld":      "federal_tax_withheld",
        "social security wages":            "ss_wages",
        "social security tax withheld":     "ss_tax_withheld",
        "medicare wages and tips":          "medicare_wages",
        "medicare tax withheld":            "medicare_tax_withheld",
        "dependent care benefits":          "dependent_care",
        "state wages, tips, etc.":          "state_wages",
        "state income tax":                 "state_income_tax",
        # Keys with newlines — user writes space, Azure DI sends \n
        "statutory employee":               "statutory_employee",
        "retirement plan":                  "retirement_plan",
        "third-party sick pay":             "third_party_sick_pay",
    }

    def _make_norm(self) -> KvNormalizer:
        return KvNormalizer(
            field_map=self.W2_FIELD_MAP,
            section="INCOME",
            source_doc_type="IRS Form W-2",
            confidence_threshold=0.50,
        )

    # --- Financial fields ---

    def test_wages_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Wages, tips, other compensation", "88,450.00", Decimal("0.681"))]))
        f = result["fields"][0]
        assert f.field_name == "wages_w2"
        assert f.extracted_value == Decimal("88450.00")

    def test_federal_tax_withheld_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Federal income tax withheld", "6,912.34", Decimal("0.637"))]))
        assert result["fields"][0].field_name == "federal_tax_withheld"
        assert result["fields"][0].extracted_value == Decimal("6912.34")

    def test_ss_wages_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Social security wages", "88,450.00", Decimal("0.526"))]))
        assert result["fields"][0].field_name == "ss_wages"

    def test_ss_tax_withheld_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Social security tax withheld", "5,485.90", Decimal("0.632"))]))
        assert result["fields"][0].field_name == "ss_tax_withheld"
        assert result["fields"][0].extracted_value == Decimal("5485.90")

    def test_medicare_wages_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Medicare wages and tips", "88,450.00", Decimal("0.682"))]))
        assert result["fields"][0].field_name == "medicare_wages"

    def test_medicare_tax_withheld_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Medicare tax withheld", "1,282.53", Decimal("0.879"))]))
        assert result["fields"][0].field_name == "medicare_tax_withheld"
        assert result["fields"][0].extracted_value == Decimal("1282.53")

    def test_dependent_care_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Dependent care benefits", "12,000.00", Decimal("0.621"))]))
        assert result["fields"][0].field_name == "dependent_care"
        assert result["fields"][0].extracted_value == Decimal("12000.00")

    def test_state_wages_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("State wages, tips, etc.", "88,450.00", Decimal("0.874"))]))
        assert result["fields"][0].field_name == "state_wages"

    def test_state_income_tax_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("State income tax", "1,845.67", Decimal("0.682"))]))
        assert result["fields"][0].field_name == "state_income_tax"
        assert result["fields"][0].extracted_value == Decimal("1845.67")

    # --- Newline keys — Azure DI sends \n, field_map uses space ---

    def test_statutory_employee_newline_key_resolves(self):
        """Azure DI returns 'Statutory\\nemployee' — field_map written with space must match."""
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Statutory\nemployee", ":unselected:", Decimal("0.902"))]))
        f = result["fields"][0]
        assert f.field_name == "statutory_employee"
        assert f.extracted_value is None  # :unselected: is non-financial

    def test_retirement_plan_newline_key_resolves(self):
        """Azure DI returns 'Retirement\\nplan' — field_map written with space must match."""
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Retirement\nplan", ":selected:", Decimal("0.902"))]))
        f = result["fields"][0]
        assert f.field_name == "retirement_plan"
        assert f.extracted_value is None  # :selected: is non-financial

    def test_third_party_sick_pay_newline_key_resolves(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Third-party\nsick pay", ":unselected:", Decimal("0.883"))]))
        f = result["fields"][0]
        assert f.field_name == "third_party_sick_pay"

    # --- Checkbox values ---

    def test_selected_checkbox_extracts_as_none(self):
        """:selected: is a checkbox state, not a numeric value — should be None."""
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Retirement\nplan", ":selected:", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value is None

    def test_unselected_checkbox_extracts_as_none(self):
        norm = self._make_norm()
        result = norm.run(extraction([KvEntry("Statutory\nemployee", ":unselected:", Decimal("0.99"))]))
        assert result["fields"][0].extracted_value is None

    # --- Below-threshold keys are filtered ---

    def test_below_threshold_filtered(self):
        """Control number (0.233) and employee address (0.276) are below 0.50 threshold."""
        norm = self._make_norm()
        result = norm.run(extraction([
            KvEntry("Control number", "ABC123", Decimal("0.233")),
            KvEntry("Employee's address and ZIP code", "5678 SAMPLE AVE\nAPT 9\nOAKLAND, CA 94607", Decimal("0.276")),
        ]))
        assert result["fields"] == []

    # --- Full payload run ---

    def test_full_w2_payload(self):
        """Run the complete W-2 payload. Assert all 9 financial fields resolve correctly."""
        norm = self._make_norm()
        # Exact keys from real Azure DI prebuilt-document W-2 response
        entries = [
            KvEntry("a Employee's social security number",       "***-**-1234",  Decimal("0.857")),
            KvEntry("OMB No.",                                   "1545-0008",    Decimal("0.901")),
            KvEntry("Employer identification number (EIN)",      "12-3456789",   Decimal("0.527")),
            KvEntry("Wages, tips, other compensation",           "88,450.00",    Decimal("0.681")),
            KvEntry("Federal income tax withheld",               "6,912.34",     Decimal("0.637")),
            KvEntry("Social security wages",                     "88,450.00",    Decimal("0.526")),
            KvEntry("Social security tax withheld",              "5,485.90",     Decimal("0.632")),
            KvEntry("Medicare wages and tips",                   "88,450.00",    Decimal("0.682")),
            KvEntry("Medicare tax withheld",                     "1,282.53",     Decimal("0.879")),
            KvEntry("Social security tips",                      "0",            Decimal("0.363")),  # below threshold
            KvEntry("Allocated tips",                            "0",            Decimal("0.438")),  # below threshold
            KvEntry("Control number",                            "ABC123",       Decimal("0.233")),  # below threshold
            KvEntry("Dependent care benefits",                   "12,000.00",    Decimal("0.621")),
            KvEntry("Statutory\nemployee",                       ":unselected:", Decimal("0.902")),
            KvEntry("Retirement\nplan",                          ":selected:",   Decimal("0.902")),
            KvEntry("Third-party\nsick pay",                     ":unselected:", Decimal("0.883")),
            KvEntry("State wages, tips, etc.",                   "88,450.00",    Decimal("0.874")),
            KvEntry("State income tax",                          "1,845.67",     Decimal("0.682")),
        ]
        result = norm.run(extraction(entries))
        by_name = {f.field_name: f for f in result["fields"]}

        # 9 financial fields + 3 checkbox fields (above threshold) = 12 resolved fields
        # 3 entries below 0.50 threshold are filtered out
        assert "wages_w2"           in by_name
        assert "federal_tax_withheld" in by_name
        assert "ss_wages"           in by_name
        assert "ss_tax_withheld"    in by_name
        assert "medicare_wages"     in by_name
        assert "medicare_tax_withheld" in by_name
        assert "dependent_care"     in by_name
        assert "state_wages"        in by_name
        assert "state_income_tax"   in by_name
        assert "statutory_employee" in by_name
        assert "retirement_plan"    in by_name
        assert "third_party_sick_pay" in by_name

        assert by_name["wages_w2"].extracted_value       == Decimal("88450.00")
        assert by_name["ss_tax_withheld"].extracted_value == Decimal("5485.90")
        assert by_name["dependent_care"].extracted_value  == Decimal("12000.00")
        assert by_name["retirement_plan"].extracted_value is None   # :selected:


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
