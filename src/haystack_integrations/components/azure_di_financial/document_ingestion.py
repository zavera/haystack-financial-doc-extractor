# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Placeholder ingestion component.

In production integrations, replace this with your document management system's
fetch logic (e.g. OnBase, SharePoint, S3, local filesystem).

The contract is simple: produce raw PDF bytes + a metadata dict.
Downstream components (AzureDiExtractor) only care about bytes — they are
ingestion-source agnostic.
"""

from dataclasses import dataclass, field
from typing import Any

from haystack import component, default_from_dict, default_to_dict


@dataclass
class DocumentPayload:
    """Raw document ready for extraction."""

    bytes_: bytes
    document_id: str
    source_name: str
    metadata: dict[str, Any] = field(default_factory=dict)


@component
class DocumentIngestionComponent:
    """
    Stub ingestion component.

    Replace ``run()`` body with your DMS fetch logic. The output contract —
    a list of DocumentPayload — must remain stable so that AzureDiExtractor
    can consume it unchanged.

    Example sources to wire here:

    - Azure Blob Storage
    - AWS S3
    - Local filesystem (for testing)
    - OnBase REST API
    - SharePoint
    """

    @component.output_types(documents=list[DocumentPayload])
    def run(self, document_ids: list[str]) -> dict:
        """Fetch raw PDF bytes for each document_id.

        Args:
            document_ids: Opaque identifiers understood by your DMS.

        Returns:
            documents: List of DocumentPayload ready for AzureDiExtractor.
        """
        raise NotImplementedError(
            "DocumentIngestionComponent is a placeholder. "
            "Implement run() with your document source fetch logic."
        )

    def to_dict(self) -> dict:
        return default_to_dict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DocumentIngestionComponent":
        return default_from_dict(cls, data)


@component
class BytesIngestionComponent:
    """
    Convenience ingestion component for callers who already have PDF bytes.

    Use this in tests or when your upstream code fetches bytes itself and
    just needs to hand them into the Haystack pipeline.
    """

    @component.output_types(documents=list[DocumentPayload])
    def run(
        self,
        bytes_list: list[bytes],
        document_ids: list[str],
        source_names: list[str],
        metadata_list: list[dict[str, Any]] | None = None,
    ) -> dict:
        """Wrap pre-fetched bytes into DocumentPayload objects.

        Args:
            bytes_list:    Raw PDF bytes, one per document.
            document_ids:  Opaque document identifiers (never logged).
            source_names:  Human-readable source labels for audit.
            metadata_list: Optional per-document metadata dicts.

        Returns:
            documents: List of DocumentPayload ready for AzureDiExtractor.
        """
        if metadata_list is None:
            metadata_list = [{}] * len(bytes_list)

        if not (len(bytes_list) == len(document_ids) == len(source_names)):
            raise ValueError("bytes_list, document_ids, and source_names must have equal length")

        documents = [
            DocumentPayload(
                bytes_=b,
                document_id=doc_id,
                source_name=name,
                metadata=meta,
            )
            for b, doc_id, name, meta in zip(bytes_list, document_ids, source_names, metadata_list)
        ]
        return {"documents": documents}
