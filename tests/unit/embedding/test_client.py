"""
Tests for HuggingFaceEmbeddingClient's HTTP timeout and retry behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from cks_runtime.embedding.client import HuggingFaceEmbeddingClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "fake-token-for-test")
    return HuggingFaceEmbeddingClient()


def _mock_response(payload):
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


def _mock_http_error_response(status_code):
    resp = MagicMock()
    err = requests.exceptions.HTTPError(f"{status_code} error")
    err.response = MagicMock(status_code=status_code)
    resp.raise_for_status.side_effect = err
    return resp


def test_missing_hf_token_raises(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    with pytest.raises(ValueError, match="HF_TOKEN"):
        HuggingFaceEmbeddingClient()


def test_embed_batch_passes_timeout(client):
    with patch("requests.post", return_value=_mock_response([[0.1, 0.2, 0.3]])) as mock_post:
        client.embed_batch(["hello"])
        _, kwargs = mock_post.call_args
        assert kwargs.get("timeout") is not None, "requests.post must be called with a timeout"


def test_embed_batch_retries_on_connection_error_then_succeeds(client, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    call_count = {"n": 0}

    def flaky_post(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise requests.exceptions.ConnectionError("simulated network blip")
        return _mock_response([[0.5, 0.5]])

    with patch("requests.post", side_effect=flaky_post):
        result = client.embed_batch(["hello"])

    assert call_count["n"] == 3
    assert len(result) == 1


def test_embed_batch_retries_on_429_then_gives_up(client, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    call_count = {"n": 0}

    def always_rate_limited(*args, **kwargs):
        call_count["n"] += 1
        return _mock_http_error_response(429)

    with patch("requests.post", side_effect=always_rate_limited):
        with pytest.raises(requests.exceptions.HTTPError):
            client.embed_batch(["hello"])

    assert call_count["n"] == 3, "should retry up to the configured max before giving up"


def test_embed_batch_does_not_retry_on_client_error(client, monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    call_count = {"n": 0}

    def bad_request(*args, **kwargs):
        call_count["n"] += 1
        return _mock_http_error_response(400)

    with patch("requests.post", side_effect=bad_request):
        with pytest.raises(requests.exceptions.HTTPError):
            client.embed_batch(["hello"])

    assert call_count["n"] == 1, "a 400 is the caller's fault and must not be retried"


def test_dimension_detected_lazily_from_first_response(client):
    with patch("requests.post", return_value=_mock_response([[0.1, 0.2, 0.3, 0.4]])):
        assert client.dimension == 4


def test_dimension_explicit_env_var_skips_probe(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "fake-token-for-test")
    monkeypatch.setenv("CKS_EMBEDDING_DIMENSION", "777")
    c = HuggingFaceEmbeddingClient()
    with patch("requests.post") as mock_post:
        assert c.dimension == 777
        mock_post.assert_not_called()
