# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Pre-wired Haystack pipeline for financial document KV extraction.

::

    BytesIngestionComponent
            |
    AzureDiExtractor  (single endpoint or multi-endpoint pool)
            |
    KvNormalizer
            |
    DeltaCalculator

Single-endpoint usage::

    from haystack_integrations.components.azure_di_financial import build_pipeline

    pipeline = build_pipeline(
        azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
        azure_api_key="...",
        field_map={"amount from line 11a adjusted gross income": "agi"},
        section="INCOME",
        source_doc_type="IRS Form 1040",
    )

Multi-endpoint usage (scales TPS quota linearly)::

    pipeline = build_pipeline(
        azure_endpoints=[
            {"endpoint": "https://resource-eastus.cognitiveservices.azure.com/", "api_key": "key1"},
            {"endpoint": "https://resource-westeu.cognitiveservices.azure.com/", "api_key": "key2"},
        ],
        field_map={"amount from line 11a adjusted gross income": "agi"},
        section="INCOME",
        source_doc_type="IRS Form 1040",
        max_workers=8,  # recommended: len(endpoints) * 4
    )

Running the pipeline::

    result = pipeline.run({
        "ingest": {
            "bytes_list":    [pdf_bytes],
            "document_ids":  ["doc-001"],
            "source_names":  ["1040-2023.pdf"],
        },
        "delta": {
            "reference_values": {"agi": 75000},
        },
    })

    fields = result["delta"]["fields"]
"""

from haystack import Pipeline

from .azure_di_extractor import AzureDiExtractor
from .delta_calculator import DeltaCalculator
from .document_ingestion import BytesIngestionComponent
from .kv_normalizer import KvNormalizer


def build_pipeline(
    field_map: dict[str, str],
    section: str,
    source_doc_type: str,
    # Single-endpoint (simple usage)
    azure_endpoint: str | None = None,
    azure_api_key: str | None = None,
    # Multi-endpoint pool (load distribution)
    azure_endpoints: list[dict[str, str]] | None = None,
    # Shared config
    model_id: str = "prebuilt-document",
    confidence_threshold: float = 0.5,
    high_threshold: float = 500.0,
    medium_threshold: float = 100.0,
    max_workers: int = 4,
    max_retries: int = 5,
    poll_timeout_seconds: int = 120,
    page_chunk_size: int = 10,
) -> Pipeline:
    """Build and connect the full extraction pipeline.

    Args:
        field_map:              Raw Azure DI key -> canonical field name mapping.
        section:                Section label applied to all extracted fields.
        source_doc_type:        Human-readable document type label.
        azure_endpoint:         Single Azure DI endpoint URL. Use this OR ``azure_endpoints``.
        azure_api_key:          API key for single endpoint.
        azure_endpoints:        List of ``{"endpoint": ..., "api_key": ...}`` dicts.
                                Overrides ``azure_endpoint``/``azure_api_key``.
                                Each additional endpoint multiplies effective TPS quota.
        model_id:               Azure DI model. Default: ``prebuilt-document``.
        confidence_threshold:   Drop KV entries below this confidence. Default: 0.5.
        high_threshold:         Delta >= this -> HIGH severity. Default: 500.
        medium_threshold:       Delta >= this -> MEDIUM severity. Default: 100.
        max_workers:            Thread pool size for parallel processing. Default: 4.
                                Recommended: ``len(azure_endpoints) * 4`` under load.
        max_retries:            Max retry attempts on 429 responses. Default: 5.
        poll_timeout_seconds:   Timeout per Azure DI call in seconds. Default: 120.
        page_chunk_size:        Pages per parallel chunk in Stage 1. Default: 10.

    Returns:
        A connected, runnable Haystack Pipeline.

    Raises:
        ValueError: If neither ``azure_endpoint`` nor ``azure_endpoints`` is provided.
    """
    if not azure_endpoints and not (azure_endpoint and azure_api_key):
        raise ValueError(
            "Provide either 'azure_endpoint'+'azure_api_key' "
            "or an 'azure_endpoints' list."
        )

    pipeline = Pipeline()

    pipeline.add_component("ingest", BytesIngestionComponent())
    pipeline.add_component(
        "extractor",
        AzureDiExtractor(
            endpoint=azure_endpoint,
            api_key=azure_api_key,
            endpoints=azure_endpoints,
            model_id=model_id,
            max_workers=max_workers,
            max_retries=max_retries,
            poll_timeout_seconds=poll_timeout_seconds,
            page_chunk_size=page_chunk_size,
        ),
    )
    pipeline.add_component(
        "normalizer",
        KvNormalizer(
            field_map=field_map,
            section=section,
            source_doc_type=source_doc_type,
            confidence_threshold=confidence_threshold,
        ),
    )
    pipeline.add_component(
        "delta",
        DeltaCalculator(high_threshold=high_threshold, medium_threshold=medium_threshold),
    )

    pipeline.connect("ingest.documents", "extractor.documents")
    pipeline.connect("extractor.extractions", "normalizer.extractions")
    pipeline.connect("normalizer.fields", "delta.fields")

    return pipeline
