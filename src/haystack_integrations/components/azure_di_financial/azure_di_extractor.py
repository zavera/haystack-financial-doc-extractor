# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Azure Document Intelligence extractor with 4-stage recovery chain.

Stage 0 — Full document: submit raw bytes to Azure DI.
Stage 1 — Page splitter: chunk PDF into pages, submit in parallel if Stage 0 returns empty.
Stage 2 — DPI reduction: re-compress PDF stream if splitter still yields empty pages.
Stage 3 — Rotation block: try 0°/90°/180°/270° in sequence on any still-empty pages.

Rate limiting: exponential backoff with +/-20% jitter on 429 responses.
"""

import io
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Any

import pikepdf
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError
from haystack import component, default_from_dict, default_to_dict

from .document_ingestion import DocumentPayload
from .models.kv_entry import KvEntry

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_INITIAL_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 32.0
_JITTER_FACTOR = 0.2
_ROTATION_DEGREES = [0, 90, 180, 270]
_DEFAULT_PAGE_CHUNK_SIZE = 10


@component
class AzureDiExtractor:
    """Haystack component that extracts raw KV pairs from financial PDFs using
    Azure Document Intelligence with a 4-stage recovery chain.

    Output is a list of extraction result dicts — one per input document.
    Documents that fail all recovery stages return an empty KV list rather
    than raising, so the pipeline continues for other documents.

    Args:
        endpoint:             Azure Document Intelligence endpoint URL.
        api_key:              Azure DI API key.
        model_id:             Azure DI model. Default: ``prebuilt-document``.
        page_chunk_size:      Pages per parallel chunk in Stage 1. Default: 10.
        max_retries:          Max retry attempts on 429 rate-limit responses.
        poll_timeout_seconds: Timeout for each Azure DI polling call.
        max_workers:          Thread pool size for parallel document/chunk processing.
    """

    def __init__(
        self,
        endpoint: str,
        api_key: str,
        model_id: str = "prebuilt-document",
        page_chunk_size: int = _DEFAULT_PAGE_CHUNK_SIZE,
        max_retries: int = _MAX_RETRIES,
        poll_timeout_seconds: int = 120,
        max_workers: int = 4,
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.model_id = model_id
        self.page_chunk_size = page_chunk_size
        self.max_retries = max_retries
        self.poll_timeout_seconds = poll_timeout_seconds
        self.max_workers = max_workers
        self._client = DocumentAnalysisClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(api_key),
        )

    @component.output_types(extractions=list[dict[str, Any]])
    def run(self, documents: list[DocumentPayload]) -> dict:
        """Extract KV pairs from a list of PDF documents.

        Args:
            documents: Raw PDF payloads from an ingestion component.

        Returns:
            extractions: List of dicts, one per document::

                {
                    "document_id": str,
                    "source_name": str,
                    "metadata":    dict,
                    "kv_entries":  list[KvEntry],
                    "stage_used":  str,   # STAGE-0|STAGE-1|STAGE-2|STAGE-3|ERROR
                    "error":       str | None,
                }
        """
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(self._extract_with_recovery, doc): doc for doc in documents}
            for future in as_completed(futures):
                results.append(future.result())
        return {"extractions": results}

    # ------------------------------------------------------------------
    # Recovery chain
    # ------------------------------------------------------------------

    def _extract_with_recovery(self, doc: DocumentPayload) -> dict:
        base = {"document_id": doc.document_id, "source_name": doc.source_name, "metadata": doc.metadata}
        try:
            entries, stage = self._run_recovery_chain(doc.bytes_)
            return {**base, "kv_entries": entries, "stage_used": stage, "error": None}
        except Exception as exc:
            logger.error("All recovery stages failed for document [id redacted]: %s", exc)
            return {**base, "kv_entries": [], "stage_used": "ERROR", "error": str(exc)}

    def _run_recovery_chain(self, pdf_bytes: bytes) -> tuple[list[KvEntry], str]:
        entries = self._analyze_bytes(pdf_bytes)
        if entries:
            return entries, "STAGE-0"

        logger.debug("Stage 0 returned empty — trying page splitter")
        chunks = self._split_pages(pdf_bytes)
        entries = self._analyze_chunks_parallel(chunks)
        if entries:
            return entries, "STAGE-1"

        logger.debug("Stage 1 returned empty — trying DPI reduction")
        reduced = self._reduce_dpi(pdf_bytes)
        entries = self._analyze_bytes(reduced)
        if entries:
            return entries, "STAGE-2"

        logger.debug("Stage 2 returned empty — trying rotation block")
        for degrees in _ROTATION_DEGREES:
            rotated = self._rotate_pdf(pdf_bytes, degrees)
            entries = self._analyze_bytes(rotated)
            if entries:
                return entries, f"STAGE-3 ({degrees}deg)"

        return [], "EXHAUSTED"

    # ------------------------------------------------------------------
    # Azure DI call with exponential backoff
    # ------------------------------------------------------------------

    def _analyze_bytes(self, pdf_bytes: bytes) -> list[KvEntry]:
        delay = _INITIAL_BACKOFF_S
        for attempt in range(self.max_retries):
            try:
                poller = self._client.begin_analyze_document(
                    self.model_id,
                    document=io.BytesIO(pdf_bytes),
                )
                result = poller.result(timeout=self.poll_timeout_seconds)
                return self._to_kv_entries(result)
            except HttpResponseError as exc:
                if exc.status_code == 429:
                    jitter = delay * _JITTER_FACTOR * random.uniform(-1, 1)
                    sleep_for = min(delay + jitter, _MAX_BACKOFF_S)
                    logger.warning("Azure DI rate limited (attempt %d). Sleeping %.1fs", attempt + 1, sleep_for)
                    time.sleep(sleep_for)
                    delay = min(delay * 2, _MAX_BACKOFF_S)
                elif exc.status_code == 403:
                    logger.error("Azure DI quota exhausted (403). Aborting retries.")
                    return []
                else:
                    raise
        logger.error("Azure DI max retries (%d) exceeded.", self.max_retries)
        return []

    @staticmethod
    def _to_kv_entries(result: Any) -> list[KvEntry]:
        entries: list[KvEntry] = []
        if not result.key_value_pairs:
            return entries
        for pair in result.key_value_pairs:
            if pair.key and pair.value:
                key = pair.key.content.strip()
                value = pair.value.content.strip()
                confidence = Decimal(str(round(pair.confidence, 4))) if pair.confidence is not None else Decimal("0")
                entries.append(KvEntry(key=key, value=value, confidence=confidence))
        return entries

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

    def _analyze_chunks_parallel(self, chunks: list[bytes]) -> list[KvEntry]:
        entries: list[KvEntry] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._analyze_bytes, chunk) for chunk in chunks]
            for future in as_completed(futures):
                entries.extend(future.result())
        return entries

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
            endpoint=self.endpoint,
            api_key=self.api_key,
            model_id=self.model_id,
            page_chunk_size=self.page_chunk_size,
            max_retries=self.max_retries,
            poll_timeout_seconds=self.poll_timeout_seconds,
            max_workers=self.max_workers,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "AzureDiExtractor":
        return default_from_dict(cls, data)
