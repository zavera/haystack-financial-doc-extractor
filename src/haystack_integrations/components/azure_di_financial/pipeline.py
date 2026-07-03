# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Pre-wired Haystack pipeline for financial document KV extraction.

::

    BytesIngestionComponent
            |
    AzureDiExtractor  (single endpoint or multi-endpoint pool;
                        translates non-English documents to English
                        via Azure OpenAI when configured)
            |
    IrsFormClassifier  (optional — classifies IRS form type(s) via Azure OpenAI)
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

With translation enabled — detects each document's language via Azure DI's own
``result.languages``; non-English documents are translated to English through
Azure OpenAI and re-analyzed so extracted fields match an English field_map::

    pipeline = build_pipeline(
        azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
        azure_api_key="...",
        translation_azure_endpoint="https://<openai-resource>.openai.azure.com/",
        translation_azure_deployment="gpt-4o-mini",
        translation_api_key="...",
        field_map={"amount from line 11a adjusted gross income": "agi"},
        section="INCOME",
        source_doc_type="IRS Form 1040",
    )

With IRS form classification enabled — every document's extracted content is
classified by IRS form type(s) via one batched Azure OpenAI call, populating
``form_types`` on each extraction (a document can contain more than one form,
e.g. a bundled Schedule C + Schedule SE upload)::

    pipeline = build_pipeline(
        azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
        azure_api_key="...",
        classification_azure_endpoint="https://<openai-resource>.openai.azure.com/",
        classification_azure_deployment="gpt-4o-mini",
        classification_api_key="...",
        field_map={"amount from line 11a adjusted gross income": "agi"},
        section="INCOME",
        source_doc_type="IRS Form 1040",
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
from .irs_form_classifier import IrsFormClassifier
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
    # Translation (optional) — Azure OpenAI. Detects non-English documents via
    # Azure DI's own language detection and translates them to English before
    # the final extraction.
    translation_azure_endpoint: str | None = None,
    translation_azure_deployment: str | None = None,
    translation_api_key: str | None = None,
    translation_api_version: str | None = None,
    # IRS form classification (optional) — Azure OpenAI. Classifies every
    # document's IRS form type(s) in one batched call.
    classification_azure_endpoint: str | None = None,
    classification_azure_deployment: str | None = None,
    classification_api_key: str | None = None,
    classification_api_version: str | None = None,
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
        translation_azure_endpoint:   Azure OpenAI resource endpoint. Provide together
                                with ``translation_azure_deployment``/``translation_api_key``
                                to enable non-English -> English translation inside
                                ``AzureDiExtractor``.
        translation_azure_deployment: Azure OpenAI chat-completion deployment name.
        translation_api_key:    API key for the Azure OpenAI translation resource.
        translation_api_version: Azure OpenAI API version for translation (optional).
        classification_azure_endpoint:   Azure OpenAI resource endpoint. Provide together
                                with ``classification_azure_deployment``/``classification_api_key``
                                to enable the ``IrsFormClassifier`` stage.
        classification_azure_deployment: Azure OpenAI chat-completion deployment name.
        classification_api_key: API key for the Azure OpenAI classification resource.
        classification_api_version: Azure OpenAI API version for classification (optional).
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
            translation_azure_endpoint=translation_azure_endpoint,
            translation_azure_deployment=translation_azure_deployment,
            translation_api_key=translation_api_key,
            translation_api_version=translation_api_version,
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

    classification_enabled = bool(
        classification_azure_endpoint and classification_azure_deployment and classification_api_key
    )
    if classification_enabled:
        pipeline.add_component(
            "classify",
            IrsFormClassifier(
                azure_endpoint=classification_azure_endpoint,
                azure_deployment=classification_azure_deployment,
                api_key=classification_api_key,
                api_version=classification_api_version,
            ),
        )
        pipeline.connect("extractor.extractions", "classify.extractions")
        pipeline.connect("classify.extractions", "normalizer.extractions")
    else:
        pipeline.connect("extractor.extractions", "normalizer.extractions")

    pipeline.connect("normalizer.fields", "delta.fields")

    return pipeline
