# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for DocumentTranslationComponent — no real AI endpoint calls."""

import io
from unittest.mock import MagicMock, patch

import pytest
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from haystack_integrations.components.azure_di_financial.document_ingestion import DocumentPayload
from haystack_integrations.components.azure_di_financial.translation import DocumentTranslationComponent


def _pdf_with_text(text: str) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.drawString(40, 700, text)
    c.save()
    return buf.getvalue()


def make_component(**kwargs) -> DocumentTranslationComponent:
    with patch("haystack_integrations.components.azure_di_financial.translation.OpenAIGenerator"):
        return DocumentTranslationComponent(
            model="fake-model",
            endpoint="https://fake.endpoint/v1",
            api_key="fake-key",
            **kwargs,
        )


@pytest.mark.unit
class TestGetLanguage:
    def test_detects_english(self):
        comp = make_component()
        assert comp.get_language("This is a normal English sentence about wages and taxes.") == "en"

    def test_detects_non_english(self):
        comp = make_component()
        assert comp.get_language("Esto es una oración en español sobre salarios e impuestos.") != "en"

    def test_blank_text_defaults_to_english(self):
        comp = make_component()
        assert comp.get_language("") == "en"
        assert comp.get_language("   ") == "en"


@pytest.mark.unit
class TestRun:
    def test_english_document_passes_through_unchanged(self):
        comp = make_component()
        comp._generator.run = MagicMock(side_effect=AssertionError("should not be called"))

        original_bytes = _pdf_with_text("This is a normal English sentence about wages and taxes.")
        doc = DocumentPayload(bytes_=original_bytes, document_id="doc-1", source_name="w2.pdf")

        result = comp.run([doc])
        out = result["documents"][0]

        assert out.bytes_ == original_bytes
        assert out.metadata["detected_language"] == "en"
        assert "translated" not in out.metadata

    def test_non_english_document_is_translated_before_extraction(self):
        comp = make_component()
        comp._generator.run = MagicMock(return_value={"replies": ["Wages: 50000"]})

        original_bytes = _pdf_with_text("Esto es una oración en español sobre salarios e impuestos.")
        doc = DocumentPayload(bytes_=original_bytes, document_id="doc-1", source_name="w2.pdf")

        result = comp.run([doc])
        out = result["documents"][0]

        comp._generator.run.assert_called_once()
        assert out.bytes_ != original_bytes
        assert out.metadata["translated"] is True
        assert out.metadata["original_language"] != "en"
        # Translated bytes must still be a valid, extractable PDF for AzureDiExtractor
        assert comp._extract_text(out.bytes_).strip() == "Wages: 50000"


@pytest.mark.unit
class TestSerialisation:
    def test_to_dict_contains_config(self):
        comp = make_component()
        d = comp.to_dict()
        assert d["init_parameters"]["model"] == "fake-model"
        assert d["init_parameters"]["endpoint"] == "https://fake.endpoint/v1"

    def test_from_dict_restores_state(self):
        with patch("haystack_integrations.components.azure_di_financial.translation.OpenAIGenerator"):
            original = make_component()
            restored = DocumentTranslationComponent.from_dict(original.to_dict())
        assert restored.model == "fake-model"
        assert restored.endpoint == "https://fake.endpoint/v1"
