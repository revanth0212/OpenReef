"""Tests for the Tavily web search service (OpenReef feature 2)."""

import pytest

from app.utils import web_search as ws


@pytest.fixture(autouse=True)
def no_tavily_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)


def test_unconfigured_returns_clear_message():
    assert not ws.is_web_search_configured()
    result = ws.web_search("anything")
    assert "not configured" in result


def test_configured_detection(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    assert ws.is_web_search_configured()


def test_empty_query(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    assert ws.web_search("   ") == "Empty search query."


def test_result_formatting(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    def fake_post(payload):
        assert payload["api_key"] == "tvly-test"
        assert payload["query"] == "mirofish"
        assert payload["max_results"] == 5  # default
        return {
            "results": [
                {"title": "One", "url": "https://a.example", "content": "first snippet"},
                {"title": "Two", "url": "https://b.example", "content": "second snippet"},
            ]
        }

    monkeypatch.setattr(ws, "_post_search", fake_post)
    result = ws.web_search("mirofish")
    assert "1. One" in result
    assert "https://a.example" in result
    assert "second snippet" in result


def test_empty_results(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    monkeypatch.setattr(ws, "_post_search", lambda payload: {"results": []})
    assert ws.web_search("nothing") == "No web results found."


def test_http_error_is_caught(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    def boom(payload):
        raise RuntimeError("timeout")

    monkeypatch.setattr(ws, "_post_search", boom)
    result = ws.web_search("x")
    assert result.startswith("Web search failed:")


def test_max_results_clamped(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    captured = {}

    def fake_post(payload):
        captured.update(payload)
        return {"results": []}

    monkeypatch.setattr(ws, "_post_search", fake_post)
    ws.web_search("q", max_results=500)
    assert captured["max_results"] == ws.MAX_MAX_RESULTS

    ws.web_search("q", max_results="not-a-number")
    assert captured["max_results"] == ws.DEFAULT_MAX_RESULTS


def test_format_results_empty():
    assert ws.format_results([]) == "No web results found."
