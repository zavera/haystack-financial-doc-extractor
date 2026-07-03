# SPDX-FileCopyrightText: 2026 Ambreen Zaver, Callisto Tech
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for AzureDiExtractor — multi-endpoint pool, max_workers config,
round-robin distribution, serialisation. No real Azure calls.
"""

from unittest.mock import MagicMock, patch

import pytest

from haystack_integrations.components.azure_di_financial.azure_di_extractor import (
    AzureDiExtractor,
    get_content,
    get_kv_pairs,
)
from haystack_integrations.components.azure_di_financial.document_ingestion import DocumentPayload

FAKE_ENDPOINT_1 = "https://resource-eastus.cognitiveservices.azure.com/"
FAKE_ENDPOINT_2 = "https://resource-westeu.cognitiveservices.azure.com/"
FAKE_KEY_1 = "key-eastus-fake"
FAKE_KEY_2 = "key-westeu-fake"


def make_extractor(**kwargs) -> AzureDiExtractor:
    with patch(
        "haystack_integrations.components.azure_di_financial.azure_di_extractor.DocumentAnalysisClient"
    ):
        return AzureDiExtractor(**kwargs)


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestInitialisation:

    def test_single_endpoint_accepted(self):
        ext = make_extractor(endpoint=FAKE_ENDPOINT_1, api_key=FAKE_KEY_1)
        assert ext.endpoint == FAKE_ENDPOINT_1
        assert len(ext._clients) == 1

    def test_multi_endpoint_builds_client_per_endpoint(self):
        ext = make_extractor(endpoints=[
            {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
            {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
        ])
        assert len(ext._clients) == 2

    def test_missing_endpoint_raises(self):
        with pytest.raises(ValueError, match="Provide either"):
            make_extractor(model_id="prebuilt-document")

    def test_malformed_endpoints_entry_raises(self):
        with pytest.raises(ValueError, match="endpoint.*api_key"):
            make_extractor(endpoints=[{"endpoint": FAKE_ENDPOINT_1}])  # missing api_key

    def test_max_workers_stored(self):
        ext = make_extractor(endpoint=FAKE_ENDPOINT_1, api_key=FAKE_KEY_1, max_workers=12)
        assert ext.max_workers == 12

    def test_max_workers_default_is_4(self):
        ext = make_extractor(endpoint=FAKE_ENDPOINT_1, api_key=FAKE_KEY_1)
        assert ext.max_workers == 4

    def test_all_config_params_stored(self):
        ext = make_extractor(
            endpoint=FAKE_ENDPOINT_1,
            api_key=FAKE_KEY_1,
            model_id="prebuilt-tax.us.1040",
            page_chunk_size=5,
            max_retries=3,
            poll_timeout_seconds=60,
            max_workers=8,
        )
        assert ext.model_id == "prebuilt-tax.us.1040"
        assert ext.page_chunk_size == 5
        assert ext.max_retries == 3
        assert ext.poll_timeout_seconds == 60
        assert ext.max_workers == 8


# ---------------------------------------------------------------------------
# Round-robin distribution
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRoundRobin:

    def test_single_endpoint_always_returns_index_0(self):
        ext = make_extractor(endpoint=FAKE_ENDPOINT_1, api_key=FAKE_KEY_1)
        indices = [ext._next_client_index() for _ in range(6)]
        assert all(i == 0 for i in indices)

    def test_two_endpoints_alternate(self):
        ext = make_extractor(endpoints=[
            {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
            {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
        ])
        indices = [ext._next_client_index() for _ in range(6)]
        assert indices == [0, 1, 0, 1, 0, 1]

    def test_round_robin_is_thread_safe(self):
        """Multiple threads calling _next_client_index() should not get duplicates."""
        import threading
        ext = make_extractor(endpoints=[
            {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
            {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
        ])
        results = []
        lock = threading.Lock()

        def grab():
            idx = ext._next_client_index()
            with lock:
                results.append(idx)

        threads = [threading.Thread(target=grab) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 20
        # Each index should appear exactly 10 times
        assert results.count(0) == 10
        assert results.count(1) == 10

    def test_client_method_wraps_on_overflow(self):
        ext = make_extractor(endpoints=[
            {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
            {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
        ])
        # Index 5 % 2 = 1 — should not raise
        client = ext._client(5)
        assert client is ext._clients[1]


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSerialisation:

    def test_single_endpoint_to_dict_round_trip(self):
        ext = make_extractor(
            endpoint=FAKE_ENDPOINT_1,
            api_key=FAKE_KEY_1,
            max_workers=6,
            max_retries=3,
        )
        d = ext.to_dict()
        assert d["init_parameters"]["max_workers"] == 6
        assert d["init_parameters"]["max_retries"] == 3
        assert d["init_parameters"]["endpoints"][0]["endpoint"] == FAKE_ENDPOINT_1

    def test_multi_endpoint_to_dict_preserves_all_endpoints(self):
        ext = make_extractor(endpoints=[
            {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
            {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
        ])
        d = ext.to_dict()
        assert len(d["init_parameters"]["endpoints"]) == 2
        assert d["init_parameters"]["endpoints"][1]["endpoint"] == FAKE_ENDPOINT_2

    def test_from_dict_restores_multi_endpoint(self):
        ext = make_extractor(endpoints=[
            {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
            {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
        ], max_workers=8)
        with patch(
            "haystack_integrations.components.azure_di_financial.azure_di_extractor.DocumentAnalysisClient"
        ):
            restored = AzureDiExtractor.from_dict(ext.to_dict())
        assert len(restored._clients) == 2
        assert restored.max_workers == 8

    def test_to_dict_type_path_is_correct(self):
        ext = make_extractor(endpoint=FAKE_ENDPOINT_1, api_key=FAKE_KEY_1)
        d = ext.to_dict()
        assert "azure_di_extractor.AzureDiExtractor" in d["type"]


# ---------------------------------------------------------------------------
# build_pipeline integration
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBuildPipeline:

    def test_build_pipeline_single_endpoint(self):
        from haystack import Pipeline
        from haystack_integrations.components.azure_di_financial import build_pipeline
        with patch(
            "haystack_integrations.components.azure_di_financial.azure_di_extractor.DocumentAnalysisClient"
        ):
            p = build_pipeline(
                azure_endpoint=FAKE_ENDPOINT_1,
                azure_api_key=FAKE_KEY_1,
                field_map={"amount from line 11a adjusted gross income": "agi"},
                section="INCOME",
                source_doc_type="IRS Form 1040",
                max_workers=4,
            )
        assert isinstance(p, Pipeline)

    def test_build_pipeline_multi_endpoint(self):
        from haystack import Pipeline
        from haystack_integrations.components.azure_di_financial import build_pipeline
        with patch(
            "haystack_integrations.components.azure_di_financial.azure_di_extractor.DocumentAnalysisClient"
        ):
            p = build_pipeline(
                azure_endpoints=[
                    {"endpoint": FAKE_ENDPOINT_1, "api_key": FAKE_KEY_1},
                    {"endpoint": FAKE_ENDPOINT_2, "api_key": FAKE_KEY_2},
                ],
                field_map={"amount from line 11a adjusted gross income": "agi"},
                section="INCOME",
                source_doc_type="IRS Form 1040",
                max_workers=8,
            )
        assert isinstance(p, Pipeline)

    def test_build_pipeline_no_endpoint_raises(self):
        from haystack_integrations.components.azure_di_financial import build_pipeline
        with pytest.raises(ValueError):
            build_pipeline(
                field_map={"agi": "agi"},
                section="INCOME",
                source_doc_type="IRS Form 1040",
            )

    def test_build_pipeline_max_workers_flows_to_extractor(self):
        from haystack_integrations.components.azure_di_financial import build_pipeline
        with patch(
            "haystack_integrations.components.azure_di_financial.azure_di_extractor.DocumentAnalysisClient"
        ):
            p = build_pipeline(
                azure_endpoint=FAKE_ENDPOINT_1,
                azure_api_key=FAKE_KEY_1,
                field_map={},
                section="INCOME",
                source_doc_type="IRS Form 1040",
                max_workers=16,
            )
        extractor = p.get_component("extractor")
        assert extractor.max_workers == 16


# ---------------------------------------------------------------------------
# getKvPair / getContent — reading both fields off an extraction dict
# ---------------------------------------------------------------------------

def _mock_pair(key: str, value: str, confidence: float = 0.9) -> MagicMock:
    pair = MagicMock()
    pair.key.content = key
    pair.value.content = value
    pair.confidence = confidence
    return pair


def _mock_analyze_result(content: str | None, pairs: list) -> MagicMock:
    result = MagicMock()
    result.content = content
    result.key_value_pairs = pairs
    return result


@pytest.mark.unit
class TestContentExtraction:

    def test_to_kv_entries_reads_pairs_from_result(self):
        result = _mock_analyze_result("Full raw document text", [_mock_pair("Wages", "50000")])
        entries = AzureDiExtractor._to_kv_entries(result)
        assert entries[0].key == "Wages"
        assert entries[0].value == "50000"

    def test_get_content_reads_content_from_result(self):
        result = _mock_analyze_result("Full raw document text", [])
        assert AzureDiExtractor._get_content(result) == "Full raw document text"

    def test_get_content_handles_none_content(self):
        result = _mock_analyze_result(None, [])
        assert AzureDiExtractor._get_content(result) == ""

    def test_run_includes_content_alongside_kv_entries(self):
        ext = make_extractor(endpoint=FAKE_ENDPOINT_1, api_key=FAKE_KEY_1)
        fake_result = _mock_analyze_result("Document body text", [_mock_pair("Wages", "50000")])
        fake_poller = MagicMock()
        fake_poller.result.return_value = fake_result
        ext._clients[0].begin_analyze_document = MagicMock(return_value=fake_poller)

        doc = DocumentPayload(bytes_=b"%PDF-fake%", document_id="doc-1", source_name="w2.pdf")
        extraction = ext.run([doc])["extractions"][0]

        assert extraction["content"] == "Document body text"
        assert get_content(extraction) == "Document body text"
        assert get_kv_pairs(extraction)[0].key == "Wages"

    def test_get_kv_pairs_and_get_content_default_to_empty(self):
        assert get_kv_pairs({}) == []
        assert get_content({}) == ""
