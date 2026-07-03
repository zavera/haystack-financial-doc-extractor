# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
IRS form-type classifier using a single batched Azure OpenAI call.

Runs after AzureDiExtractor, before KvNormalizer. A single document's content
can contain more than one distinct IRS form (e.g. a bundled Schedule C
immediately followed by a Schedule SE on the same upload), so classification
is per-document-content and returns a list of form names, not one label.

All documents in a batch are classified in one LLM call: extraction dicts are
assigned sequential numeric IDs, their document name + full extracted content
(see :func:`get_content`) are laid out in the prompt, and the model returns a
single JSON object keyed by those numeric IDs.
"""

import json
import logging
import re
from typing import Any

from haystack import component, default_from_dict, default_to_dict
from haystack.components.generators.azure import AzureOpenAIGenerator
from haystack.utils import Secret

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = (
    "Classify each tax document by its IRS form type.\n"
    "\n"
    "Use the document name and the full extracted text content shown below.\n"
    "A single document's content may contain more than one distinct IRS form — for\n"
    "example a bundled upload with a Schedule C immediately followed by a Schedule SE\n"
    "on the same file. Identify EVERY distinct IRS form type present in the content,\n"
    "in the order they appear. Most documents contain exactly one form.\n"
    "Apply your knowledge of IRS tax forms — there is no restricted label list.\n"
    'Return the standard IRS form name exactly as it appears on the form (e.g. "Form 1040", '
    '"Schedule C", "W-2", "Schedule 2", "Schedule 3", "Form 1099-R").\n'
    "\n"
    "{{DOCUMENTS}}\n"
    "\n"
    "Return a JSON object only — no explanation, no markdown fences.\n"
    "Keys are the Document IDs shown above (numeric strings). Each value is an ARRAY\n"
    "of IRS form type names present in that document — use a single-element array\n"
    "when only one form is present.\n"
    'Example: {"123": ["Form 1040"], "456": ["Schedule C", "Schedule SE"], "789": ["W-2"]}'
)

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@component
class IrsFormClassifier:
    """Classifies every extracted document's IRS form type(s) in one batched
    Azure OpenAI call.

    Args:
        azure_endpoint:   Azure OpenAI resource endpoint.
        azure_deployment: Azure OpenAI chat-completion deployment name.
        api_key:          API key for the Azure OpenAI resource.
        api_version:      Azure OpenAI API version. Defaults to the Haystack
                          client default when not set.
    """

    def __init__(
        self,
        azure_endpoint: str,
        azure_deployment: str,
        api_key: str,
        api_version: str | None = None,
    ) -> None:
        self.azure_endpoint = azure_endpoint
        self.azure_deployment = azure_deployment
        self.api_key = api_key
        self.api_version = api_version

        generator_kwargs: dict[str, Any] = {
            "azure_endpoint": azure_endpoint,
            "azure_deployment": azure_deployment,
            "api_key": Secret.from_token(api_key),
        }
        if api_version:
            generator_kwargs["api_version"] = api_version
        self._generator = AzureOpenAIGenerator(**generator_kwargs)

    @component.output_types(extractions=list[dict[str, Any]])
    def run(self, extractions: list[dict[str, Any]]) -> dict:
        """Classify IRS form type(s) present in each extraction's content.

        Args:
            extractions: Output list from AzureDiExtractor.run() — each item
                         must have ``"content"`` and ``"source_name"`` keys.

        Returns:
            extractions: Same list, each dict augmented with a
                         ``"form_types": list[str]`` key — every distinct IRS
                         form type detected in that document's content, in the
                         order they appear. Empty list if none were detected
                         or classification failed.
        """
        if not extractions:
            return {"extractions": extractions}

        id_map = {str(i + 1): extraction for i, extraction in enumerate(extractions)}
        prompt = _CLASSIFY_PROMPT.replace("{{DOCUMENTS}}", self._build_documents_block(id_map))

        result = self._generator.run(prompt=prompt)
        classifications = self._parse_response(result["replies"][0])

        enriched = [
            {**extraction, "form_types": classifications.get(doc_id, [])}
            for doc_id, extraction in id_map.items()
        ]
        return {"extractions": enriched}

    @staticmethod
    def _build_documents_block(id_map: dict[str, dict[str, Any]]) -> str:
        blocks = []
        for doc_id, extraction in id_map.items():
            blocks.append(
                f"Document ID: {doc_id}\n"
                f"Document name: {extraction.get('source_name', 'unknown')}\n"
                f"Content:\n{extraction.get('content', '')}"
            )
        return "\n\n---\n\n".join(blocks)

    @staticmethod
    def _parse_response(reply: str) -> dict[str, list[str]]:
        cleaned = _JSON_FENCE.sub("", reply.strip())
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("IRS form classifier returned non-JSON output — treating all documents as unclassified")
            return {}
        return {str(doc_id): list(form_types) for doc_id, form_types in parsed.items()}

    def to_dict(self) -> dict:
        return default_to_dict(
            self,
            azure_endpoint=self.azure_endpoint,
            azure_deployment=self.azure_deployment,
            api_key=self.api_key,
            api_version=self.api_version,
        )

    @classmethod
    def from_dict(cls, data: dict) -> "IrsFormClassifier":
        return default_from_dict(cls, data)
