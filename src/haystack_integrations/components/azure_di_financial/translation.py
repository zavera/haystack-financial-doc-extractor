# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Language detection + translation component.

Runs first in the pipeline, ahead of AzureDiExtractor. Each document's language
is detected from its embedded text; documents not already in English are sent
to a configurable AI endpoint for translation, and the translated text is
repackaged into a PDF that AzureDiExtractor consumes in place of the original.

Translating up front (rather than after extraction) lets Azure DI return
English-labelled KV pairs so KvNormalizer's field_map — written in English —
still resolves correctly, even for non-English source documents.

Note: the reconstructed PDF is plain reflowed text — original layout, tables,
and checkboxes are not preserved. Azure DI's KV extraction may perform worse
on the reconstructed document than on the original for heavily tabular forms.
"""

import io
import logging

from haystack import component, default_from_dict, default_to_dict
from haystack.components.generators import OpenAIGenerator
from haystack.utils import Secret
from langdetect import detect
from langdetect.lang_detect_exception import LangDetectException
from pypdf import PdfReader
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from .document_ingestion import DocumentPayload

logger = logging.getLogger(__name__)

_TRANSLATE_PROMPT = (
    "Translate the following document text into English. Preserve line "
    "breaks, labels, and numeric values exactly as written. Return only the "
    "translated text, with no commentary.\n\n{text}"
)
_LINE_WIDTH = 100
_PAGE_MARGIN = 40
_LINE_HEIGHT = 14


@component
class DocumentTranslationComponent:
    """Detects each document's language and translates non-English documents
    to English before they reach AzureDiExtractor.

    Args:
        model:    Chat-completion model name (e.g. "gpt-4o-mini").
        endpoint: Base URL of the AI endpoint (OpenAI-compatible).
        api_key:  API key for the endpoint.
    """

    def __init__(
        self,
        model: str,
        endpoint: str,
        api_key: str,
    ) -> None:
        self.model = model
        self.endpoint = endpoint
        self.api_key = api_key
        self._generator = OpenAIGenerator(
            api_key=Secret.from_token(api_key),
            model=model,
            api_base_url=endpoint,
        )

    @component.output_types(documents=list[DocumentPayload])
    def run(self, documents: list[DocumentPayload]) -> dict:
        """Detect language and translate non-English documents to English.

        Args:
            documents: Raw PDF payloads from an ingestion component.

        Returns:
            documents: Same-length list of DocumentPayload — untouched for
                       documents already in English, replaced with a
                       translated PDF otherwise.
        """
        results = []
        for doc in documents:
            text = self._extract_text(doc.bytes_)
            language = self.get_language(text)
            metadata = {**doc.metadata, "detected_language": language}

            if not text.strip() or language == "en":
                results.append(
                    DocumentPayload(
                        bytes_=doc.bytes_,
                        document_id=doc.document_id,
                        source_name=doc.source_name,
                        metadata=metadata,
                    )
                )
                continue

            logger.info(
                "Document language detected as '%s' — translating to English before extraction",
                language,
            )
            translated_text = self._translate(text)
            metadata["translated"] = True
            metadata["original_language"] = language

            results.append(
                DocumentPayload(
                    bytes_=self._to_pdf_bytes(translated_text),
                    document_id=doc.document_id,
                    source_name=doc.source_name,
                    metadata=metadata,
                )
            )
        return {"documents": results}

    @staticmethod
    def get_language(text: str) -> str:
        """Detect the ISO 639-1 language code of ``text``.

        Returns "en" if the text is blank or detection fails, so downstream
        callers never have to special-case an unknown result.
        """
        if not text.strip():
            return "en"
        try:
            return detect(text)
        except LangDetectException:
            return "en"

    def _translate(self, text: str) -> str:
        result = self._generator.run(prompt=_TRANSLATE_PROMPT.format(text=text))
        return result["replies"][0]

    @staticmethod
    def _extract_text(pdf_bytes: bytes) -> str:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    @staticmethod
    def _to_pdf_bytes(text: str) -> bytes:
        """Reflow translated text into a simple PDF for AzureDiExtractor to consume."""
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=LETTER)
        _, height = LETTER
        y = height - _PAGE_MARGIN

        for paragraph in text.splitlines() or [""]:
            for line in _wrap(paragraph, _LINE_WIDTH):
                if y < _PAGE_MARGIN:
                    c.showPage()
                    y = height - _PAGE_MARGIN
                c.drawString(_PAGE_MARGIN, y, line)
                y -= _LINE_HEIGHT

        c.save()
        return buf.getvalue()

    def to_dict(self) -> dict:
        return default_to_dict(
            self,
            model=self.model,
            endpoint=self.endpoint,
            api_key=self.api_key,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentTranslationComponent":
        return default_from_dict(cls, data)


def _wrap(text: str, width: int) -> list[str]:
    """Word-wrap without dropping characters (unlike naive truncation)."""
    if not text:
        return [""]
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines
