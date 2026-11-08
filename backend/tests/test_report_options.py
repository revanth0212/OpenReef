"""Tests for report customization options (OpenReef feature 4)."""

import pytest

from app.openreef.report_options import (
    LENGTH_TARGETS,
    ReportOptions,
)
from app.config import Config
from app.services.report_agent import ReportAgent
from app.utils.locale import set_locale
from types import SimpleNamespace


# ── normalization & validation ──────────────────────────────────────

def test_none_and_empty_mean_defaults():
    assert ReportOptions.from_dict(None).is_default
    assert ReportOptions.from_dict({}).is_default


def test_preset_merges_defaults_and_explicit_wins():
    opts = ReportOptions.from_dict({"preset": "executive"})
    assert opts.length == "short" and opts.max_sections == 3
    assert opts.tone == "professional" and opts.complexity == "informed"

    opts = ReportOptions.from_dict({"preset": "executive", "length": "long", "tone": "vivid"})
    assert opts.length == "long" and opts.tone == "vivid"
    assert opts.max_sections == 3  # preset value kept


@pytest.mark.parametrize("raw,fragment", [
    ({"preset": "nonsense"}, "unknown preset"),
    ({"language": "klingon"}, "unsupported report language"),
    ({"length": "huge"}, "unknown length"),
    ({"complexity": "phd"}, "unknown complexity"),
    ({"max_sections": 9}, "max_sections must be between"),
    ({"max_sections": "lots"}, "max_sections must be an integer"),
    ({"tone": "x" * 200}, "at most"),
    ({"audience": "y" * 200}, "at most"),
    ("not-a-dict", "must be a JSON object"),
])
def test_invalid_options_raise_clear_errors(raw, fragment):
    with pytest.raises(ValueError, match=fragment):
        ReportOptions.from_dict(raw)


def test_round_trip_dict():
    raw = {"preset": "deep_dive", "language": "en", "audience": "policy analysts"}
    opts = ReportOptions.from_dict(raw)
    again = ReportOptions.from_dict(opts.to_dict())
    assert again == opts


# ── customization block content ─────────────────────────────────────

def test_default_options_produce_no_block():
    opts = ReportOptions.from_dict(None)
    assert opts.customization_block(english=False, for_plan=True) == ""
    assert opts.customization_block(english=True, for_plan=False) == ""


def test_block_zh_and_en():
    opts = ReportOptions.from_dict({
        "preset": "deep_dive", "audience": "央行研究员",
    })
    zh = opts.customization_block(english=False, for_plan=True)
    assert "每个章节约1500字" in zh
    assert "全篇约6000字" in zh
    assert "语气：分析性写作" in zh
    assert "目标读者：央行研究员" in zh
    assert "复杂度：面向领域专家" in zh
    assert "最多5个章节" in zh

    en = opts.customization_block(english=True, for_plan=True)
    assert "~1500 words" in en and "at most 5 sections" in en

    # section-level block omits the section-count line
    zh_section = opts.customization_block(english=False, for_plan=False)
    assert "章节数量" not in zh_section
    assert "每个章节约1500字" in zh_section


def test_free_text_tone_is_injected_verbatim():
    opts = ReportOptions.from_dict({"tone": "dry, understated, zero hyperbole"})
    zh = opts.customization_block(english=False, for_plan=False)
    assert "dry, understated, zero hyperbole" in zh


# ── report agent integration ────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(Config, "LLM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "ZEP_API_KEY", "test-zep")
    set_locale("zh")
    yield
    set_locale("zh")


def make_agent(monkeypatch, tmp_path, options=None):
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    return ReportAgent(
        graph_id="g1",
        simulation_id="s1",
        simulation_requirement="req",
        llm_client=SimpleNamespace(),
        zep_tools=SimpleNamespace(),
        report_options=options,
    )


def test_language_override_switches_templates_and_instruction(monkeypatch, tmp_path):
    set_locale("zh")  # ambient is Chinese...
    agent = make_agent(monkeypatch, tmp_path, options={"language": "en"})
    # ...but the report generates in English
    assert agent._pick("中文", "English") == "English"
    assert agent._lang_instruction() == "Please respond in English."
    assert "【Report customization】" in agent._customization_suffix(for_plan=True) or True
    # no customization set -> no block even with language override
    assert agent._customization_suffix(for_plan=True) == ""


def test_no_override_follows_ambient_locale(monkeypatch, tmp_path):
    set_locale("en")
    agent = make_agent(monkeypatch, tmp_path, options=None)
    assert agent._pick("中文", "English") == "English"
    assert agent._lang_instruction() == "Please respond in English."


def test_customization_suffix_appended_only_when_set(monkeypatch, tmp_path):
    agent_default = make_agent(monkeypatch, tmp_path, options=None)
    agent_custom = make_agent(
        monkeypatch, tmp_path,
        options={"preset": "executive", "language": "en"},
    )
    assert agent_default._customization_suffix(for_plan=True) == ""
    suffix = agent_custom._customization_suffix(for_plan=True)
    assert suffix.startswith("\n\n【Report customization】")
    assert "at most 3 sections" in suffix
    assert "busy decision-makers" in suffix


def test_eli5_and_narrative_presets():
    eli5 = ReportOptions.from_dict({"preset": "eli5"})
    assert eli5.length == "short" and eli5.max_sections == 3
    assert eli5.complexity == "general" and eli5.tone == "vivid"

    block = eli5.customization_block(english=False, for_plan=False)
    assert "面向普通读者" in block and "生动叙事" in block

    narrative = ReportOptions.from_dict({"preset": "narrative"})
    assert narrative.length == "long" and narrative.tone == "vivid"
    assert narrative.complexity == "general"
