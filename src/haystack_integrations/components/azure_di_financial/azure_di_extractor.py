# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Azure Document Intelligence extractor with 4-stage recovery chain,
multi-endpoint load distribution, and optional Azure OpenAI translation.

Stage 0 — Full document: submit raw bytes to Azure DI.
Stage 1 — Page splitter: chunk PDF into pages, submit in parallel.
Stage 2 — DPI reduction: re-compress PDF stream.
Stage 3 — Rotation block: try 0/90/180/270 degrees in sequence.

Multi-endpoint: provision multiple Azure DI resources and pass all endpoints
to distribute load across TPS quotas. Clients are selected per-document using
round-robin to spread requests evenly.

Rate limiting: exponential backoff with +/-20% jitter on 429 responses.

Translation (optional): Azure DI's AnalyzeResult already reports the detected
document language (``result.languages``) from the same call that returns
``content`` and ``key_value_pairs`` — no separate OCR/detection pass needed.
When translation is configured and a document isn't English, its content is
sent to Azure OpenAI for translation, repackaged into a PDF, and re-analyzed
so the final ``kv_entries``/``content`` are English — matching an English
``field_map`` in KvNormalizer regardless of the source document's language.

All tuneable parameters (max_workers, max_retries, poll_timeout_seconds,
page_chunk_size) are explicit constructor args — configure them per deployment.
"""

import io
import itertools
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

import pikepdf
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from haystack import component, default_from_dict, default_to_dict
from haystack.components.generators.azure import AzureOpenAIGenerator
from haystack.utils import Secret
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

from .document_ingestion import DocumentPayload
from .models.kv_entry import KvEntry

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 32.0
_JITTER_FACTOR = 0.2
_ROTATION_DEGREES = [0, 90, 180, 270]
_DEFAULT_PAGE_CHUNK_SIZE = 10
_DEFAULT_MAX_WORKERS = 4

_TRANSLATE_PROMPT = (
    "Translate the following document text into English. Preserve line "
    "breaks, labels, and numeric values exactly as written. Return only the "
    "translated text, with no commentary.\n\n{text}"
)
_LINE_WIDTH = 100
_PAGE_MARGIN = 40
_LINE_HEIGHT = 14


def _infer_form_type(entries: list[KvEntry]) -> str:
    """Heuristic form type detection from extracted key names."""
    keys_lower = {e.key.lower() for e in entries}
    if any("1040" in k for k in keys_lower):
        return "1040"
    if any("w-2" in k or "w2" in k or "employer" in k for k in keys_lower):
        return "W-2"
    if any("schedule c" in k or "profit or loss from business" in k for k in keys_lower):
        return "Schedule C"
    if any("schedule e" in k or "supplemental income" in k for k in keys_lower):
        return "Schedule E"
    if any("schedule k-1" in k or "partner's share" in k for k in keys_lower):
        return "Schedule K-1"
    if any("1065" in k for k in keys_lower):
        return "1065"
    if any("1120-s" in k for k in keys_lower):
        return "1120-S"
    if any("1120" in k for k in keys_lower):
        return "1120"
    return "unknown"


def _retry_after_ms(exc: HttpResponseError) -> float | None:
    """Parse Retry-After header (seconds) → seconds float."""
    try:
        header = exc.response.headers.get("Retry-After")
        if header:
            return float(header.strip())
    except Exception:
        pass
    return None


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


@component
class AzureDiExtractor:
    """Haystack component that extracts raw KV pairs from financial PDFs using
    Azure Document Intelligence with a 4-stage recovery chain, and optionally
    translates non-English documents to English via Azure OpenAI before the
    final extraction.

    Supports a **pool of Azure DI endpoints** for load distribution — provision
    multiple Azure DI resources and pass all endpoints to multiply effective TPS
    quota linearly. Documents are distributed round-robin across the pool.

    Single-endpoint usage (backward compatible)::

        AzureDiExtractor(
            endpoint="https://my-resource.cognitiveservices.azure.com/",
            api_key="...",
        )

    Multi-endpoint usage::

        AzureDiExtractor(
            endpoints=[
                {"endpoint": "https://resource-eastus.cognitiveservices.azure.com/", "api_key": "key1"},
                {"endpoint": "https://resource-westeu.cognitiveservices.azure.com/", "api_key": "key2"},
            ],
            max_workers=8,   # scale workers with endpoint count
        )

    With translation enabled — detects each document's language via Azure DI's
    own ``result.languages``; non-English documents are translated to English
    through Azure OpenAI and re-analyzed so ``kv_entries``/``content`` end up
    English::

        AzureDiExtractor(
            endpoint="https://my-resource.cognitiveservices.azure.com/",
            api_key="...",
            translation_azure_endpoint="https://my-openai-resource.openai.azure.com/",
            translation_azure_deployment="gpt-4o-mini",
            translation_api_key="...",
        )

    Args:
        endpoint:             Single Azure DI endpoint URL. Use this OR ``endpoints``.
        api_key:              API key for the single endpoint.
        endpoints:            List of ``{"endpoint": ..., "api_key": ...}`` dicts for
                              multi-endpoint pool. Overrides ``endpoint``/``api_key``
                              when provided.
        model_id:             Azure DI model ID. Default: ``prebuilt-document``.
        page_chunk_size:      Pages per parallel chunk in Stage 1. Default: 10.
        max_retries:          Max retry attempts on 429 rate-limit responses. Default: 5.
        poll_timeout_seconds: Timeout per Azure DI polling call in seconds. Default: 120.
        max_workers:          Thread pool size for parallel document and chunk processing.
                              Default: 4. Recommended: set to ``len(endpoints) * 4``
                              under load.
        translation_azure_endpoint:   Azure OpenAI resource endpoint. Provide together
                              with ``translation_azure_deployment``/``translation_api_key``
                              to enable non-English → English translation.
        translation_azure_deployment: Azure OpenAI chat-completion deployment name.
        translation_api_key:  API key for the Azure OpenAI resource.
        translation_api_version: Azure OpenAI API version. Defaults to the Haystack
                              client default when not set.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        endpoints: list[dict[str, str]] | None = None,
        model_id: str = "prebuilt-document",
        page_chunk_size: int = _DEFAULT_PAGE_CHUNK_SIZE,
        max_retries: int = _MAX_RETRIES,
        poll_timeout_seconds: int = 120,
        max_workers: int = _DEFAULT_MAX_WORKERS,
        translation_azure_endpoint: str | None = None,
        translation_azure_deployment: str | None = None,
        translation_api_key: str | None = None,
        translation_api_version: str | None = None,
    ) -> None:
        # Validate — must supply either endpoint+api_key or endpoints list
        if endpoints:
            for entry in endpoints:
                if "endpoint" not in entry or "api_key" not in entry:
                    raise ValueError("Each entry in 'endpoints' must have 'endpoint' and 'api_key' keys.")
            self.endpoints = endpoints
            # Backfill single-endpoint fields from first entry for serialisation
            self.endpoint = endpoints[0]["endpoint"]
            self.api_key  = endpoints[0]["api_key"]
        elif endpoint and api_key:
            self.endpoint  = endpoint
            self.api_key   = api_key
            self.endpoints = [{"endpoint": endpoint, "api_key": api_key}]
        else:
            raise ValueError("Provide either 'endpoint'+'api_key' or an 'endpoints' list.")

        self.model_id             = model_id
        self.page_chunk_size      = page_chunk_size
        self.max_retries          = max_retries
        self.poll_timeout_seconds = poll_timeout_seconds
        self.max_workers          = max_workers

        # Build one DocumentAnalysisClient per endpoint
        self._clients: list[DocumentAnalysisClient] = [
            DocumentAnalysisClient(
                endpoint=e["endpoint"],
                credential=AzureKeyCredential(e["api_key"]),
            )
            for e in self.endpoints
        ]

        # Thread-safe round-robin counter
        self._rr_lock    = threading.Lock()
        self._rr_counter = itertools.cycle(range(len(self._clients)))

        # Optional Azure OpenAI translation
        self.translation_azure_endpoint   = translation_azure_endpoint
        self.translation_azure_deployment = translation_azure_deployment
        self.translation_api_key          = translation_api_key
        self.translation_api_version      = translation_api_version
        self._translation_enabled = bool(
            translation_azure_endpoint and translation_azure_deployment and translation_api_key
        )
        self._translation_generator: AzureOpenAIGenerator | None = None
        if self._translation_enabled:
            generator_kwargs: dict[str, Any] = {
                "azure_endpoint": translation_azure_endpoint,
                "azure_deployment": translation_azure_deployment,
                "api_key": Secret.from_token(translation_api_key),
            }
            if translation_api_version:
                generator_kwargs["api_version"] = translation_api_version
            self._translation_generator = AzureOpenAIGenerator(**generator_kwargs)

        n = len(self._clients)
        logger.info(
            "AzureDiExtractor initialised — %d endpoint(s), max_workers=%d, model=%s, translation=%s",
            n, self.max_workers, self.model_id, self._translation_enabled,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @component.output_types(extractions=list[dict[str, Any]])
    def run(self, documents: list[DocumentPayload]) -> dict:
        """Extract KV pairs from a list of PDF documents.

        Documents are processed in parallel up to ``max_workers`` threads.
        Each document is dispatched to the next endpoint in the round-robin pool.

        Args:
            documents: Raw PDF payloads from an ingestion component.

        Returns:
            extractions: List of dicts, one per document::

                {
                    "document_id": str,
                    "source_name": str,
                    "metadata":    dict,
                    "kv_entries":  list[KvEntry],
                    "content":     str,   # full document text from Azure DI's AnalyzeResult
                    "language":    str,   # detected language locale, e.g. "en", "es"
                    "translated":  bool,  # True if non-English content was translated
                    "stage_used":  str,   # STAGE-0|STAGE-1|STAGE-2|STAGE-3|ERROR (+TRANSLATED)
                    "error":       str | None,
                    "endpoint_index": int,  # which pool slot handled this doc
                }

            Use :func:`get_kv_pairs` / :func:`get_content` to read the two most
            common fields off an extraction dict without depending on its exact shape.
        """
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._extract_with_recovery, doc, self._next_client_index()): doc
                for doc in documents
            }
            for future in as_completed(futures):
                results.append(future.result())
        return {"extractions": results}

    # ------------------------------------------------------------------
    # Round-robin client selection
    # ------------------------------------------------------------------

    def _next_client_index(self) -> int:
        with self._rr_lock:
            return next(self._rr_counter)

    def _client(self, index: int) -> DocumentAnalysisClient:
        return self._clients[index % len(self._clients)]

    # ------------------------------------------------------------------
    # Recovery chain + optional translation
    # ------------------------------------------------------------------

    def _extract_with_recovery(self, doc: DocumentPayload, client_index: int) -> dict:
        base = {
            "document_id":    doc.document_id,
            "source_name":    doc.source_name,
            "metadata":       doc.metadata,
            "endpoint_index": client_index,
        }
        start_ms = int(time.monotonic() * 1_000)
        try:
            entries, content, language, stage, az_di_ms, di_calls = self._run_recovery_chain(
                doc.bytes_, client_index
            )
            translated = False

            if self._translation_enabled and language != "en" and content.strip():
                logger.info(
                    "Document language detected as '%s' — translating to English before re-extraction",
                    language,
                )
                translated_bytes = self._to_pdf_bytes(self._translate(content))
                t_entries, t_content, _, t_az_ms, t_calls = self._analyze_bytes_timed(
                    translated_bytes, client_index
                )
                az_di_ms += t_az_ms
                di_calls += t_calls
                if t_entries or t_content:
                    entries, content, stage = t_entries, t_content, f"{stage}+TRANSLATED"
                    translated = True
                else:
                    logger.warning(
                        "Re-extraction on translated document returned nothing — "
                        "keeping original-language result"
                    )

            total_ms = int(time.monotonic() * 1_000) - start_ms
            form_type = _infer_form_type(entries)
            return {
                **base,
                "kv_entries":   entries,
                "content":      content,
                "language":     language,
                "translated":   translated,
                "stage_used":   stage,
                "form_type":    form_type,
                "kv_count":     len(entries),
                "di_calls":     di_calls,
                "az_di_ms":     az_di_ms,
                "total_ms":     total_ms,
                "error":        None,
            }
        except Exception as exc:
            total_ms = int(time.monotonic() * 1_000) - start_ms
            logger.error("All recovery stages failed for document [id redacted]: %s", exc)
            return {
                **base,
                "kv_entries":   [],
                "content":      "",
                "language":     "en",
                "translated":   False,
                "stage_used":   "ERROR",
                "form_type":    "unknown",
                "kv_count":     0,
                "di_calls":     0,
                "az_di_ms":     0,
                "total_ms":     total_ms,
                "error":        str(exc),
            }

    def _run_recovery_chain(
        self, pdf_bytes: bytes, client_index: int
    ) -> tuple[list[KvEntry], str, str, str, int, int]:
        """Returns (entries, content, language, stage, az_di_ms, di_calls)."""
        total_az_ms = 0
        total_calls = 0
        last_content = ""
        last_language = "en"

        entries, content, language, az_ms, calls = self._analyze_bytes_timed(pdf_bytes, client_index)
        total_az_ms += az_ms
        total_calls += calls
        last_content = content or last_content
        last_language = language or last_language
        if entries:
            return entries, content, language, "STAGE-0", total_az_ms, total_calls

        logger.debug("Stage 0 returned empty — trying page splitter")
        chunks = self._split_pages(pdf_bytes)
        entries, content, language, az_ms, calls = self._analyze_chunks_parallel_timed(chunks, client_index)
        total_az_ms += az_ms
        total_calls += calls
        last_content = content or last_content
        last_language = language or last_language
        if entries:
            return entries, content, language, "STAGE-1", total_az_ms, total_calls

        logger.debug("Stage 1 returned empty — trying DPI reduction")
        reduced = self._reduce_dpi(pdf_bytes)
        entries, content, language, az_ms, calls = self._analyze_bytes_timed(reduced, client_index)
        total_az_ms += az_ms
        total_calls += calls
        last_content = content or last_content
        last_language = language or last_language
        if entries:
            return entries, content, language, "STAGE-2", total_az_ms, total_calls

        logger.debug("Stage 2 returned empty — trying rotation block")
        for degrees in _ROTATION_DEGREES:
            rotated = self._rotate_pdf(pdf_bytes, degrees)
            entries, content, language, az_ms, calls = self._analyze_bytes_timed(rotated, client_index)
            total_az_ms += az_ms
            total_calls += calls
            last_content = content or last_content
            last_language = language or last_language
            if entries:
                return entries, content, language, f"STAGE-3 ({degrees}deg)", total_az_ms, total_calls

        return [], last_content, last_language, "EXHAUSTED", total_az_ms, total_calls

    # ------------------------------------------------------------------
    # Translation (Azure OpenAI)
    # ------------------------------------------------------------------

    def _translate(self, text: str) -> str:
        result = self._translation_generator.run(prompt=_TRANSLATE_PROMPT.format(text=text))
        return result["replies"][0]

    @staticmethod
    def _to_pdf_bytes(text: str) -> bytes:
        """Reflow translated text into a simple PDF for re-analysis by Azure DI."""
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

    # ------------------------------------------------------------------
    # Azure DI call with exponential backoff
    # ------------------------------------------------------------------

    def _analyze_bytes_timed(
        self, pdf_bytes: bytes, client_index: int
    ) -> tuple[list[KvEntry], str, str, int, int]:
        """Returns (entries, content, language, az_di_ms, di_calls)."""
        client = self._client(client_index)
        delay = _INITIAL_BACKOFF_S
        di_calls = 0
        az_di_ms = 0

        for attempt in range(self.max_retries):
            try:
                t0 = int(time.monotonic() * 1_000)
                di_calls += 1
                poller = client.begin_analyze_document(
                    self.model_id,
                    document=io.BytesIO(pdf_bytes),
                )
                result = poller.result(timeout=self.poll_timeout_seconds)
                az_di_ms += int(time.monotonic() * 1_000) - t0
                return (
                    self._to_kv_entries(result),
                    self._get_content(result),
                    self._get_language(result),
                    az_di_ms,
                    di_calls,
                )
            except HttpResponseError as exc:
                if exc.status_code == 429:
                    # Honor Retry-After header if present, else use backoff
                    retry_after = _retry_after_ms(exc)
                    jitter = delay * _JITTER_FACTOR * random.uniform(-1, 1)
                    sleep_for = retry_after if retry_after is not None else min(delay + jitter, _MAX_BACKOFF_S)
                    logger.warning(
                        "Azure DI rate limited on endpoint %d (attempt %d). Sleeping %.1fs",
                        client_index, attempt + 1, sleep_for,
                    )
                    time.sleep(sleep_for)
                    delay = min(delay * 2, _MAX_BACKOFF_S)
                elif exc.status_code == 403:
                    logger.error("Azure DI quota exhausted on endpoint %d (403).", client_index)
                    return [], "", "en", az_di_ms, di_calls
                else:
                    raise
        logger.error("Azure DI max retries (%d) exceeded on endpoint %d.", self.max_retries, client_index)
        return [], "", "en", az_di_ms, di_calls

    def _analyze_bytes(self, pdf_bytes: bytes, client_index: int) -> list[KvEntry]:
        """Backward-compatible wrapper (used by parallel chunk analysis)."""
        entries, _, _, _, _ = self._analyze_bytes_timed(pdf_bytes, client_index)
        return entries

    @staticmethod
    def _to_kv_entries(result: Any) -> list[KvEntry]:
        entries: list[KvEntry] = []
        if not result.key_value_pairs:
            return entries
        for pair in result.key_value_pairs:
            if pair.key and pair.value:
                key        = pair.key.content.strip()
                value      = pair.value.content.strip()
                confidence = Decimal(str(round(pair.confidence, 4))) if pair.confidence is not None else Decimal("0")
                entries.append(KvEntry(key=key, value=value, confidence=confidence))
        return entries

    @staticmethod
    def _get_content(result: Any) -> str:
        """Full document text from Azure DI's AnalyzeResult — the getContent path."""
        return result.content or ""

    @staticmethod
    def _get_language(result: Any) -> str:
        """Highest-confidence detected language locale from Azure DI's AnalyzeResult
        (e.g. "en", "es") — the getLanguage path. Defaults to "en" if undetected."""
        if not result.languages:
            return "en"
        best = max(result.languages, key=lambda lang: lang.confidence or 0.0)
        return best.locale or "en"

    # ------------------------------------------------------------------
    # PDF manipulation utilities
    # ------------------------------------------------------------------

    def _split_pages(self, pdf_bytes: bytes) -> list[bytes]:
        chunks: list[bytes] = []
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            total = len(pdf.pages)
            for start in range(0, total, self.page_chunk_size):
                end = min(start + self.page_chunk_size, total)
                out = pikepdf.Pdf.new()
                for i in range(start, end):
                    out.pages.append(pdf.pages[i])
                buf = io.BytesIO()
                out.save(buf)
                chunks.append(buf.getvalue())
        return chunks

    def _analyze_chunks_parallel(self, chunks: list[bytes], client_index: int) -> list[KvEntry]:
        entries, _, _, _, _ = self._analyze_chunks_parallel_timed(chunks, client_index)
        return entries

    def _analyze_chunks_parallel_timed(
        self, chunks: list[bytes], client_index: int
    ) -> tuple[list[KvEntry], str, str, int, int]:
        """Analyse page chunks in parallel. Returns (entries, content, language, az_di_ms, di_calls).

        Content is reassembled in original page-chunk order even though chunks
        complete out of order — entries are extended in completion order, same
        as before. Language is taken from the first page: a multi-page document
        is assumed to be in a single language.
        """
        entries: list[KvEntry] = []
        contents: list[str] = [""] * len(chunks)
        languages: list[str] = [""] * len(chunks)
        total_az_ms = 0
        total_calls = 0
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._analyze_bytes_timed, chunk, client_index): i
                for i, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                chunk_index = futures[future]
                chunk_entries, chunk_content, chunk_language, az_ms, calls = future.result()
                entries.extend(chunk_entries)
                contents[chunk_index] = chunk_content
                languages[chunk_index] = chunk_language
                total_az_ms += az_ms
                total_calls += calls
        language = next((lang for lang in languages if lang), "en")
        return entries, "\n".join(contents), language, total_az_ms, total_calls

    @staticmethod
    def _reduce_dpi(pdf_bytes: bytes) -> bytes:
        """Re-compress PDF stream via pikepdf (sufficient for most Azure DI size failures)."""
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            buf = io.BytesIO()
            pdf.save(buf, compress_streams=True, stream_decode_level=pikepdf.StreamDecodeLevel.generalized)
            return buf.getvalue()

    @staticmethod
    def _rotate_pdf(pdf_bytes: bytes, degrees: int) -> bytes:
        with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                page["/Rotate"] = degrees
            buf = io.BytesIO()
            pdf.save(buf)
            return buf.getvalue()

    # ------------------------------------------------------------------
    # Haystack serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return default_to_dict(
            self,
            endpoints=self.endpoints,
            model_id=self.model_id,
            page_chunk_size=self.page_chunk_size,
            max_retries=self.max_retries,
            poll_timeout_seconds=self.poll_timeout_seconds,
            max_workers=self.max_workers,
            translation_azure_endpoint=self.translation_azure_endpoint,
            translation_azure_deployment=self.translation_azure_deployment,
            translation_api_key=self.translation_api_key,
            translation_api_version=self.translation_api_version,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "AzureDiExtractor":
        return default_from_dict(cls, data)


# ---------------------------------------------------------------------------
# Extraction accessors — read one field off an AzureDiExtractor extraction
# dict without depending on its exact shape.
# ---------------------------------------------------------------------------

def get_kv_pairs(extraction: dict[str, Any]) -> list[KvEntry]:
    """Return the raw KV entries for one document from its extraction dict."""
    return extraction.get("kv_entries", [])


def get_content(extraction: dict[str, Any]) -> str:
    """Return the full document text for one document from its extraction dict."""
    return extraction.get("content", "")
