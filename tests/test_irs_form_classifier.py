# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for IrsFormClassifier — no real Azure OpenAI calls."""

import json
from unittest.mock import MagicMock, patch

import pytest

from haystack_integrations.components.azure_di_financial.irs_form_classifier import IrsFormClassifier


def make_classifier(**kwargs) -> IrsFormClassifier:
    with patch("haystack_integrations.components.azure_di_financial.irs_form_classifier.AzureOpenAIGenerator"):
        return IrsFormClassifier(
            azure_endpoint="https://fake-openai.openai.azure.com/",
            azure_deployment="fake-deployment",
            api_key="fake-key",
            **kwargs,
        )


def extraction(source_name: str, content: str) -> dict:
    return {"document_id": source_name, "source_name": source_name, "content": content}


@pytest.mark.unit
class TestRun:
    def test_empty_extractions_short_circuits(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(side_effect=AssertionError("should not be called"))
        result = clf.run([])
        assert result == {"extractions": []}

    def test_single_document_single_form(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(return_value={"replies": [json.dumps({"1": ["Form 1040"]})]})

        result = clf.run([extraction("1040.pdf", "Form 1040 content...")])

        assert result["extractions"][0]["form_types"] == ["Form 1040"]

    def test_bundled_document_multiple_forms(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(
            return_value={"replies": [json.dumps({"1": ["Schedule C", "Schedule SE"]})]}
        )

        result = clf.run([extraction("bundle.pdf", "Schedule C content... Schedule SE content...")])

        assert result["extractions"][0]["form_types"] == ["Schedule C", "Schedule SE"]

    def test_batch_of_multiple_documents_maps_back_by_position(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(
            return_value={
                "replies": [json.dumps({"1": ["Form 1040"], "2": ["W-2"], "3": ["Schedule C", "Schedule SE"]})]
            }
        )

        result = clf.run([
            extraction("a.pdf", "1040 stuff"),
            extraction("b.pdf", "w2 stuff"),
            extraction("c.pdf", "schedule c and se stuff"),
        ])

        form_types = [e["form_types"] for e in result["extractions"]]
        assert form_types == [["Form 1040"], ["W-2"], ["Schedule C", "Schedule SE"]]

    def test_prompt_includes_document_id_name_and_content(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(return_value={"replies": [json.dumps({"1": ["W-2"]})]})

        clf.run([extraction("w2-2024.pdf", "Wages: 50000")])

        prompt = clf._generator.run.call_args.kwargs["prompt"]
        assert "Document ID: 1" in prompt
        assert "Document name: w2-2024.pdf" in prompt
        assert "Wages: 50000" in prompt
        assert "{{DOCUMENTS}}" not in prompt

    def test_malformed_json_response_yields_empty_classifications(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(return_value={"replies": ["not valid json"]})

        result = clf.run([extraction("a.pdf", "content")])

        assert result["extractions"][0]["form_types"] == []

    def test_strips_markdown_json_fences(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(
            return_value={"replies": ['```json\n{"1": ["Form 1040"]}\n```']}
        )

        result = clf.run([extraction("a.pdf", "content")])

        assert result["extractions"][0]["form_types"] == ["Form 1040"]

    def test_missing_document_id_in_response_defaults_to_empty_list(self):
        clf = make_classifier()
        clf._generator.run = MagicMock(return_value={"replies": [json.dumps({})]})

        result = clf.run([extraction("a.pdf", "content")])

        assert result["extractions"][0]["form_types"] == []


@pytest.mark.unit
class TestSerialisation:
    def test_to_dict_contains_config(self):
        clf = make_classifier(api_version="2024-12-01-preview")
        d = clf.to_dict()
        assert d["init_parameters"]["azure_endpoint"] == "https://fake-openai.openai.azure.com/"
        assert d["init_parameters"]["azure_deployment"] == "fake-deployment"
        assert d["init_parameters"]["api_version"] == "2024-12-01-preview"

    def test_from_dict_restores_state(self):
        clf = make_classifier()
        with patch("haystack_integrations.components.azure_di_financial.irs_form_classifier.AzureOpenAIGenerator"):
            restored = IrsFormClassifier.from_dict(clf.to_dict())
        assert restored.azure_deployment == "fake-deployment"
