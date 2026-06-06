# Copyright 2026 Ambreen Zaver, Callisto Tech
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Pre-wired Haystack pipeline for financial document KV extraction.

    BytesIngestionComponent
            ↓
    AzureDiExtractor
            ↓
    KvNormalizer
            ↓
    DeltaCalculator

Usage:

    from haystack_financial_doc_extractor import build_pipeline

    pipeline = build_pipeline(
        azure_endpoint="https://<resource>.cognitiveservices.azure.com/",
        azure_api_key="...",
        field_map={"adjusted gross income": "agi", "wages salaries tips": "wages"},
        section="HHA_INCOME",
        source_doc_type="IRS Form 1040",
    )

    result = pipeline.run({
        "ingest": {
            "bytes_list": [pdf_bytes],
            "document_ids": ["doc-001"],
            "source_names": ["1040-2023.pdf"],
        },
        "delta": {
            "reference_values": {"agi": 75000, "wages": 68000},
        },
    })

    fields = result["delta"]["fields"]
"""

from haystack import Pipeline

from .components.azure_di_extractor import AzureDiExtractor
from .components.delta_calculator import DeltaCalculator
from .components.document_ingestion import BytesIngestionComponent
from .components.kv_normalizer import KvNormalizer


def build_pipeline(
    azure_endpoint: str,
    azure_api_key: str,
    field_map: dict[str, str],
    section: str,
    source_doc_type: str,
    model_id: str = "prebuilt-document",
    confidence_threshold: float = 0.5,
    high_threshold: float = 500.0,
    medium_threshold: float = 100.0,
    max_workers: int = 4,
) -> Pipeline:
    """
    Build and connect the full extraction pipeline.

    Args:
        azure_endpoint:       Azure Document Intelligence endpoint URL.
        azure_api_key:        Azure DI API key.
        field_map:            Raw Azure DI key → canonical field name mapping.
        section:              Section label applied to all extracted fields (e.g. "HHA_INCOME").
        source_doc_type:      Human-readable document type (e.g. "IRS Form 1040").
        model_id:             Azure DI model. Default: prebuilt-document.
        confidence_threshold: Drop KV entries below this confidence.
        high_threshold:       Delta ≥ this → HIGH severity.
        medium_threshold:     Delta ≥ this → MEDIUM severity.
        max_workers:          Thread pool size for parallel page/chunk processing.

    Returns:
        A connected, runnable Haystack Pipeline.
    """
    pipeline = Pipeline()

    pipeline.add_component("ingest", BytesIngestionComponent())
    pipeline.add_component(
        "extractor",
        AzureDiExtractor(
            endpoint=azure_endpoint,
            api_key=azure_api_key,
            model_id=model_id,
            max_workers=max_workers,
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
        DeltaCalculator(
            high_threshold=high_threshold,
            medium_threshold=medium_threshold,
        ),
    )

    pipeline.connect("ingest.documents", "extractor.documents")
    pipeline.connect("extractor.extractions", "normalizer.extractions")
    pipeline.connect("normalizer.fields", "delta.fields")

    return pipeline