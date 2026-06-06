"""
Delta calculator component.

Computes the difference between extracted field values and reference values
(e.g. values from an authoritative system like PowerFAIDS), then assigns
severity based on configurable thresholds.

Severity logic:
  HIGH   — delta exceeds high_threshold (default $500)
  MEDIUM — delta exceeds medium_threshold (default $100)
  LOW    — any non-zero delta below medium_threshold

Reference values are supplied as a dict keyed by canonical field_name.
Fields with no corresponding reference value are emitted unchanged
(delta=None, severity=None).
"""

from decimal import Decimal
from typing import Any

from haystack import component, default_from_dict, default_to_dict

from ..models.extracted_field import ExtractedField, Severity


@component
class DeltaCalculator:
    """
    Haystack component that annotates ExtractedField objects with delta and severity
    by comparing extracted_value against a provided reference dict.

    Args:
        high_threshold:   Absolute delta (inclusive) that triggers HIGH severity.
        medium_threshold: Absolute delta (inclusive) that triggers MEDIUM severity.
    """

    def __init__(
        self,
        high_threshold: float = 500.0,
        medium_threshold: float = 100.0,
    ) -> None:
        self.high_threshold = Decimal(str(high_threshold))
        self.medium_threshold = Decimal(str(medium_threshold))

    @component.output_types(fields=list[ExtractedField])
    def run(
        self,
        fields: list[ExtractedField],
        reference_values: dict[str, Any],
    ) -> dict:
        """
        Args:
            fields:           Normalised ExtractedField list from KvNormalizer.
            reference_values: Dict mapping canonical field_name → numeric reference value.
                              Values can be int, float, str, or Decimal.

        Returns:
            fields: Same list with delta and severity populated where a reference exists.
        """
        ref = {k: self._to_decimal(v) for k, v in reference_values.items()}
        annotated: list[ExtractedField] = []
        for f in fields:
            if f.field_name in ref and f.extracted_value is not None and ref[f.field_name] is not None:
                ref_val = ref[f.field_name]
                delta = ref_val - f.extracted_value
                f.reference_value = ref_val
                f.delta = delta
                f.severity = self._severity(delta)
            annotated.append(f)
        return {"fields": annotated}

    def _severity(self, delta: Decimal) -> Severity:
        abs_delta = abs(delta)
        if abs_delta >= self.high_threshold:
            return Severity.HIGH
        if abs_delta >= self.medium_threshold:
            return Severity.MEDIUM
        if abs_delta > Decimal("0"):
            return Severity.LOW
        return Severity.LOW

    @staticmethod
    def _to_decimal(value: Any) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None

    def to_dict(self) -> dict:
        return default_to_dict(
            self,
            high_threshold=float(self.high_threshold),
            medium_threshold=float(self.medium_threshold),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "DeltaCalculator":
        return default_from_dict(cls, data)
