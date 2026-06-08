# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Azure Document Intelligence extractor with 4-stage recovery chain
and multi-endpoint load distribution.

Stage 0 — Full document: submit raw bytes to Azure DI.
Stage 1 — Page splitter: chunk PDF into pages, submit in parallel.
Stage 2 — DPI reduction: re-compress PDF stream.
Stage 3 — Rotation block: try 0/90/180/270 degrees in sequence.

Multi-endpoint: provision multiple Azure DI resources and pass all endpoints
to distribute load across TPS quotas. Clients are selected per-document using
round-robin to spread requests evenly.

Rate limiting: exponential backoff with +/-20% jitter on 429 responses.

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


@component
class AzureDiExtractor:
    """Haystack component that extracts raw KV pairs from financial PDFs using
    Azure Document Intelligence with a 4-stage recovery chain.

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

        n = len(self._clients)
        logger.info(
            "AzureDiExtractor initialised — %d endpoint(s), max_workers=%d, model=%s",
            n, self.max_workers, self.model_id,
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
                    "stage_used":  str,   # STAGE-0|STAGE-1|STAGE-2|STAGE-3|ERROR
                    "error":       str | None,
                    "endpoint_index": int,  # which pool slot handled this doc
                }
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
    # Recovery chain
    # ------------------------------------------------------------------

    def _extract_with_recovery(self, doc: DocumentPayload, client_index: int) -> dict:
        base = {
            "document_id":    doc.document_id,
            "source_name":    doc.source_name,
            "metadata":       doc.metadata,
            "endpoint_index": client_index,
        }
        try:
            entries, stage = self._run_recovery_chain(doc.bytes_, client_index)
            return {**base, "kv_entries": entries, "stage_used": stage, "error": None}
        except Exception as exc:
            logger.error("All recovery stages failed for document [id redacted]: %s", exc)
            return {**base, "kv_entries": [], "stage_used": "ERROR", "error": str(exc)}

    def _run_recovery_chain(self, pdf_bytes: bytes, client_index: int) -> tuple[list[KvEntry], str]:
        entries = self._analyze_bytes(pdf_bytes, client_index)
        if entries:
            return entries, "STAGE-0"

        logger.debug("Stage 0 returned empty — trying page splitter")
        chunks = self._split_pages(pdf_bytes)
        entries = self._analyze_chunks_parallel(chunks, client_index)
        if entries:
            return entries, "STAGE-1"

        logger.debug("Stage 1 returned empty — trying DPI reduction")
        reduced = self._reduce_dpi(pdf_bytes)
        entries = self._analyze_bytes(reduced, client_index)
        if entries:
            return entries, "STAGE-2"

        logger.debug("Stage 2 returned empty — trying rotation block")
        for degrees in _ROTATION_DEGREES:
            rotated = self._rotate_pdf(pdf_bytes, degrees)
            entries = self._analyze_bytes(rotated, client_index)
            if entries:
                return entries, f"STAGE-3 ({degrees}deg)"

        return [], "EXHAUSTED"

    # ------------------------------------------------------------------
    # Azure DI call with exponential backoff
    # ------------------------------------------------------------------

    def _analyze_bytes(self, pdf_bytes: bytes, client_index: int) -> list[KvEntry]:
        client = self._client(client_index)
        delay  = _INITIAL_BACKOFF_S
        for attempt in range(self.max_retries):
            try:
                poller = client.begin_analyze_document(
                    self.model_id,
                    document=io.BytesIO(pdf_bytes),
                )
                result = poller.result(timeout=self.poll_timeout_seconds)
                return self._to_kv_entries(result)
            except HttpResponseError as exc:
                if exc.status_code == 429:
                    jitter     = delay * _JITTER_FACTOR * random.uniform(-1, 1)
                    sleep_for  = min(delay + jitter, _MAX_BACKOFF_S)
                    logger.warning(
                        "Azure DI rate limited on endpoint %d (attempt %d). Sleeping %.1fs",
                        client_index, attempt + 1, sleep_for,
                    )
                    time.sleep(sleep_for)
                    delay = min(delay * 2, _MAX_BACKOFF_S)
                elif exc.status_code == 403:
                    logger.error("Azure DI quota exhausted on endpoint %d (403).", client_index)
                    return []
                else:
                    raise
        logger.error("Azure DI max retries (%d) exceeded on endpoint %d.", self.max_retries, client_index)
        return []

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
        """Analyse page chunks in parallel, all on the same endpoint as the parent doc."""
        entries: list[KvEntry] = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = [executor.submit(self._analyze_bytes, chunk, client_index) for chunk in chunks]
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
            endpoints=self.endpoints,
            model_id=self.model_id,
            page_chunk_size=self.page_chunk_size,
            max_retries=self.max_retries,
            poll_timeout_seconds=self.poll_timeout_seconds,
            max_workers=self.max_workers,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "AzureDiExtractor":
        return default_from_dict(cls, data)
