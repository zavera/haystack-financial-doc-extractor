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
  - Checkbox states:         ":selected:", ":unselected:" -> None (non-financial)
  - Newlines in keys:        "Statutory\\nemployee" matches field_map key
                             "statutory employee" transparently

Key resolution order
--------------------
1. Whitespace-normalised exact match — collapses \\t / \\n / multiple spaces to
   a single space, then lower-cases. Punctuation (commas, apostrophes, etc.) is
   preserved so "Wages, tips, other compensation" still hits a field_map key
   written with commas.
2. Simplified match — strips all non-alphanumeric-non-space chars, then looks up
   in a pre-computed simplified map. Lets users write "wages tips other
   compensation" and still match "Wages, tips, other compensation" from Azure DI.
3. Fallback — returns the simplified form as snake_case. Nothing is dropped.
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
_BLANK_VALUES = {
    "n/a", "na", "none", "-", "", "not applicable",
    # Azure DI checkbox states — not financial values
    ":selected:", ":unselected:",
}


def _ws_normalise(s: str) -> str:
    """Lowercase + collapse all whitespace (including \\n, \\t) to single space."""
    return re.sub(r"\s+", " ", s.lower()).strip()


def _simplify(s: str) -> str:
    """Lowercase, collapse whitespace, strip all non-alphanumeric-non-space chars."""
    ws = _ws_normalise(s)
    stripped = re.sub(r"[^a-z0-9 ]", "", ws)
    return re.sub(r" +", " ", stripped).strip()


@component
class KvNormalizer:
    """Haystack component that normalises raw KV entries into typed ExtractedField objects.

    Field-name-to-canonical-name mapping is provided via ``field_map``. If a key
    from Azure DI does not match any entry in ``field_map``, it is still emitted with
    ``field_name`` equal to the lowercased, underscore-normalised raw key — nothing
    is silently dropped.

    **Key matching is whitespace- and punctuation-tolerant.** You can write field_map
    keys with spaces where Azure DI may return newlines, and with or without commas —
    the normaliser will still resolve the match. See module docstring for details.

    Args:
        field_map:            Dict mapping Azure DI raw key patterns to canonical
                              field names. Keys are matched case-insensitively with
                              whitespace and punctuation tolerance.
                              Example: ``{"wages, tips, other compensation": "wages"}``
        section:              The section label applied to all extracted fields
                              (e.g. ``"INCOME"``).
        source_doc_type:      Human-readable document type stored on every
                              ExtractedField (e.g. ``"IRS Form W-2"``).
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
        # Store original keys (lowercased) for to_dict round-trip
        self.field_map = {k.lower(): v for k, v in field_map.items()}
        self.section = section
        self.source_doc_type = source_doc_type
        self.confidence_threshold = Decimal(str(confidence_threshold))
        self.non_negative_fields: set[str] = set(non_negative_fields or [])

        # Stage-1 lookup: whitespace-normalised (collapses \n / \t), keeps punctuation
        self._ws_map: dict[str, str] = {_ws_normalise(k): v for k, v in field_map.items()}

        # Stage-2 lookup: fully simplified — no special chars, collapsed whitespace
        self._simplified_map: dict[str, str] = {_simplify(k): v for k, v in field_map.items()}

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
        # Stage 1: whitespace-normalised exact match (preserves punctuation)
        ws = _ws_normalise(raw_key)
        if ws in self._ws_map:
            return self._ws_map[ws]

        # Stage 2: simplified match (strips special chars — handles comma variants,
        # apostrophes, etc. on either the raw key or the field_map key side)
        simplified = _simplify(raw_key)
        if simplified in self._simplified_map:
            return self._simplified_map[simplified]

        # Fallback: emit as snake_case — nothing is silently dropped
        return re.sub(r" +", "_", simplified)

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
