# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
KV normalizer component.

Converts raw string key-value pairs from Azure DI into typed ExtractedField
objects with Decimal values. Handles common financial document formatting:

  - Currency symbols:        "$75,000"     -> Decimal("75000")
  - Parenthetical negatives: "(12,500)"    -> Decimal("-12500")
  - Trailing descriptors:    "75,000 USD"  -> Decimal("75000")
  - Blank / N/A values:      "N/A", ""     -> None
  - Percent values:          "12.5%"       -> Decimal("0.125")
"""

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

from haystack import component, default_from_dict, default_to_dict

from .models.extracted_field import ExtractedField
from .models.kv_entry import KvEntry

logger = logging.getLogger(__name__)

_CURRENCY_STRIP = re.compile(r"[$,€£¥\s]")
_PARENS_NEGATIVE = re.compile(r"^\(([0-9,.\s]+)\)$")
_TRAILING_ALPHA = re.compile(r"[A-Za-z%\s]+$")
_PERCENT = re.compile(r"^([0-9.]+)%$")
_BLANK_VALUES = {"n/a", "na", "none", "-", "", "not applicable"}


@component
class KvNormalizer:
    """Haystack component that normalises raw KV entries into typed ExtractedField objects.

    Field-name-to-canonical-name mapping is provided via ``field_map``. If a key
    from Azure DI does not match any entry in ``field_map``, it is still emitted with
    ``field_name`` equal to the lowercased, underscore-normalised raw key — nothing
    is silently dropped.

    Args:
        field_map:            Dict mapping Azure DI raw key patterns (lowercase) to
                              canonical field names.
                              Example: ``{"adjusted gross income": "agi"}``
        section:              The section label applied to all extracted fields
                              (e.g. ``"HHA_INCOME"``).
        source_doc_type:      Human-readable document type stored on every
                              ExtractedField (e.g. ``"IRS Form 1040"``).
        confidence_threshold: KvEntries below this confidence are skipped.
        non_negative_fields:  Canonical field names where parenthetical notation
                              means positive, not negative (e.g. W-2 box values).
    """

    def __init__(
        self,
        field_map: dict[str, str],
        section: str,
        source_doc_type: str,
        confidence_threshold: float = 0.5,
        non_negative_fields: list[str] | None = None,
    ) -> None:
        # Normalise keys: lowercase + collapse any multi-space runs
        self.field_map = {re.sub(r" +", " ", k.lower().strip()): v for k, v in field_map.items()}
        self.section = section
        self.source_doc_type = source_doc_type
        self.confidence_threshold = Decimal(str(confidence_threshold))
        self.non_negative_fields: set[str] = set(non_negative_fields or [])

    @component.output_types(fields=list[ExtractedField])
    def run(self, extractions: list[dict[str, Any]]) -> dict:
        """Normalise raw KV extractions into typed ExtractedField objects.

        Args:
            extractions: Output list from AzureDiExtractor.run() —
                         each item is a dict with a ``"kv_entries"`` key.

        Returns:
            fields: Flat list of ExtractedField across all input documents.
        """
        all_fields: list[ExtractedField] = []
        for extraction in extractions:
            kv_entries: list[KvEntry] = extraction.get("kv_entries", [])
            for entry in kv_entries:
                if entry.confidence < self.confidence_threshold:
                    logger.debug(
                        "Skipping low-confidence entry (confidence=%.2f < threshold=%.2f)",
                        entry.confidence,
                        self.confidence_threshold,
                    )
                    continue
                all_fields.append(self._normalise_entry(entry))
        return {"fields": all_fields}

    def _normalise_entry(self, entry: KvEntry) -> ExtractedField:
        canonical_name = self._resolve_field_name(entry.key)
        allow_negative = canonical_name not in self.non_negative_fields
        normalised_value, raw_value = self._parse_value(entry.value, allow_negative=allow_negative)
        return ExtractedField(
            field_name=canonical_name,
            extracted_value=normalised_value,
            raw_value=raw_value,
            confidence=entry.confidence,
            source_doc_type=self.source_doc_type,
            source_line_ref=None,
            section=self.section,
        )

    def _resolve_field_name(self, raw_key: str) -> str:
        lower = raw_key.lower().strip()
        if lower in self.field_map:
            return self.field_map[lower]
        # Strip special chars then collapse any multi-space runs so lookup is
        # robust against Azure DI keys with irregular internal spacing.
        simplified = re.sub(r" +", " ", re.sub(r"[^a-z0-9 ]", "", lower)).strip()
        if simplified in self.field_map:
            return self.field_map[simplified]
        return re.sub(r"\s+", "_", simplified)

    @staticmethod
    def _parse_value(raw: str, allow_negative: bool = True) -> tuple[Decimal | None, str]:
        stripped = raw.strip()
        if stripped.lower() in _BLANK_VALUES:
            return None, stripped

        pct_match = _PERCENT.match(stripped)
        if pct_match:
            try:
                return Decimal(pct_match.group(1)) / Decimal("100"), stripped
            except InvalidOperation:
                pass

        paren_match = _PARENS_NEGATIVE.match(stripped)
        working = paren_match.group(1) if paren_match else stripped
        negative = paren_match is not None and allow_negative

        working = _CURRENCY_STRIP.sub("", working)
        working = _TRAILING_ALPHA.sub("", working).strip()

        try:
            value = Decimal(working)
            if negative:
                value = -value
            return value, stripped
        except InvalidOperation:
            logger.debug("Could not parse raw value as Decimal — storing None (value redacted)")
            return None, stripped

    def to_dict(self) -> dict:
        return default_to_dict(
            self,
            field_map=self.field_map,
            section=self.section,
            source_doc_type=self.source_doc_type,
            confidence_threshold=float(self.confidence_threshold),
            non_negative_fields=list(self.non_negative_fields),
        )

    @classmethod
    def from_dict(cls, data: dict) -> "KvNormalizer":
        return default_from_dict(cls, data)
