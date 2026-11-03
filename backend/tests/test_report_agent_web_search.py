"""Tests for web-search gating in the report agent + sim config schema."""

import json
import os
from types import SimpleNamespace

import pytest

from app.config import Config
from app.services.report_agent import ReportAgent
from app.services.simulation_config_generator import SimulationParameters


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(Config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "ZEP_API_KEY", "test-zep-key")


def make_agent(tmp_path, monkeypatch, sim_config=None, simulation_id="sim_ws"):
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    if sim_config is not None:
        sim_dir = tmp_path / simulation_id
        sim_dir.mkdir(parents=True, exist_ok=True)
        with open(sim_dir / "simulation_config.json", "w", encoding="utf-8") as handle:
            json.dump(sim_config, handle)
    return ReportAgent(
        graph_id="g1",
        simulation_id=simulation_id,
        simulation_requirement="req",
        llm_client=SimpleNamespace(),
        zep_tools=SimpleNamespace(),
    )


def test_tool_absent_without_key(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, monkeypatch, sim_config={"web_search": {"enabled": True}})
    assert "web_search" not in agent.tools
    assert "insight_forge" in agent.tools


def test_tool_absent_without_config_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    agent = make_agent(tmp_path, monkeypatch, sim_config={"web_search": {"enabled": False}})
    assert "web_search" not in agent.tools


def test_tool_absent_when_config_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    agent = make_agent(tmp_path, monkeypatch, sim_config=None)
    assert "web_search" not in agent.tools


def test_tool_present_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    agent = make_agent(tmp_path, monkeypatch, sim_config={"web_search": {"enabled": True}})
    assert "web_search" in agent.tools
    description = agent.tools["web_search"]["description"]
    assert "联网搜索" in description  # zh locale default


def test_tool_description_english_for_en_locale(tmp_path, monkeypatch):
    from app.utils.locale import set_locale

    set_locale("en")
    try:
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        agent = make_agent(tmp_path, monkeypatch, sim_config={"web_search": {"enabled": True}})
        assert "Web Search" in agent.tools["web_search"]["description"]
    finally:
        set_locale("zh")


def test_web_search_in_valid_tool_names():
    assert "web_search" in ReportAgent.VALID_TOOL_NAMES


def test_execute_tool_dispatches_to_web_search(tmp_path, monkeypatch):
    from app.services import report_agent as ra_module

    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    agent = make_agent(tmp_path, monkeypatch, sim_config={"web_search": {"enabled": True}})

    calls = []

    def fake_search(query, max_results=5):
        calls.append((query, max_results))
        return "RESULTS"

    monkeypatch.setattr(ra_module, "web_search", fake_search)
    result = agent._execute_tool("web_search", {"query": "bitcoin", "max_results": "3"})
    assert result == "RESULTS"
    assert calls == [("bitcoin", 3)]


# ── SimulationParameters schema ─────────────────────────────────────

def test_sim_params_default_has_web_search_disabled():
    params = SimulationParameters(
        simulation_id="s", project_id="p", graph_id="g", simulation_requirement="r"
    )
    assert params.web_search_enabled is False
    assert params.to_dict()["web_search"] == {"enabled": False}


def test_sim_params_serializes_web_search():
    params = SimulationParameters(
        simulation_id="s",
        project_id="p",
        graph_id="g",
        simulation_requirement="r",
        web_search_enabled=True,
    )
    data = json.loads(params.to_json())
    assert data["web_search"] == {"enabled": True}
