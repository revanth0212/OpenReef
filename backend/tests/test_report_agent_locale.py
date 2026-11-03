"""Tests for locale-aware prompt template selection in the report agent.

Chinese templates must remain the default for every locale except 'en';
English templates render only when the active locale is English. This
guarantees zero behavior change for existing (non-English) setups.
"""

import pytest

from app.services import report_agent as ra
from app.services.report_agent import ReportAgent, _pick_template
from app.utils.locale import set_locale


@pytest.fixture(autouse=True)
def restore_locale():
    set_locale('zh')
    yield
    set_locale('zh')


def test_pick_template_defaults_to_chinese():
    set_locale('zh')
    assert _pick_template("中文", "English") == "中文"


def test_pick_template_english_for_en_locale():
    set_locale('en')
    assert _pick_template("中文", "English") == "English"


@pytest.mark.parametrize("locale", ["es", "fr", "de", "pt", "ru"])
def test_pick_template_other_locales_keep_chinese(locale):
    set_locale(locale)
    assert _pick_template("中文", "English") == "中文"


def test_english_templates_have_same_format_placeholders():
    """Every placeholder in the Chinese user templates must exist in the English ones."""
    import string

    def placeholders(template):
        return {
            field
            for _, field, _, _ in string.Formatter().parse(template)
            if field
        }

    for zh_t, en_t in [
        (ra.PLAN_USER_PROMPT_TEMPLATE, ra.PLAN_USER_PROMPT_TEMPLATE_EN),
        (ra.SECTION_USER_PROMPT_TEMPLATE, ra.SECTION_USER_PROMPT_TEMPLATE_EN),
        (ra.SECTION_SYSTEM_PROMPT_TEMPLATE, ra.SECTION_SYSTEM_PROMPT_TEMPLATE_EN),
        (ra.REACT_OBSERVATION_TEMPLATE, ra.REACT_OBSERVATION_TEMPLATE_EN),
        (ra.REACT_INSUFFICIENT_TOOLS_MSG, ra.REACT_INSUFFICIENT_TOOLS_MSG_EN),
        (ra.REACT_INSUFFICIENT_TOOLS_MSG_ALT, ra.REACT_INSUFFICIENT_TOOLS_MSG_ALT_EN),
        (ra.REACT_TOOL_LIMIT_MSG, ra.REACT_TOOL_LIMIT_MSG_EN),
        (ra.CHAT_SYSTEM_PROMPT_TEMPLATE, ra.CHAT_SYSTEM_PROMPT_TEMPLATE_EN),
    ]:
        assert placeholders(en_t) == placeholders(zh_t), (
            f"placeholder mismatch between zh and en for {zh_t[:40]!r}"
        )


def test_english_templates_format_cleanly():
    """English templates must .format() without KeyError/IndexError using sample values."""
    values = {
        "simulation_requirement": "req",
        "total_nodes": 1,
        "total_edges": 2,
        "entity_types": ["PersonEntity"],
        "total_entities": 3,
        "related_facts_json": "[]",
        "report_title": "title",
        "report_summary": "summary",
        "section_title": "section",
        "tools_description": "tools",
        "previous_content": "none",
        "tool_name": "quick_search",
        "result": "{}",
        "tool_calls_count": 1,
        "max_tool_calls": 5,
        "used_tools_str": "quick_search",
        "min_tool_calls": 3,
        "unused_hint": "",
        "unused_list": "panorama_search",
        "report_content": "content",
    }
    for template in [
        ra.PLAN_USER_PROMPT_TEMPLATE_EN,
        ra.SECTION_USER_PROMPT_TEMPLATE_EN,
        ra.SECTION_SYSTEM_PROMPT_TEMPLATE_EN,
        ra.REACT_OBSERVATION_TEMPLATE_EN,
        ra.REACT_INSUFFICIENT_TOOLS_MSG_EN,
        ra.REACT_INSUFFICIENT_TOOLS_MSG_ALT_EN,
        ra.REACT_TOOL_LIMIT_MSG_EN,
        ra.REACT_UNUSED_TOOLS_HINT_EN,
        ra.CHAT_SYSTEM_PROMPT_TEMPLATE_EN,
    ]:
        template.format(**values)


def test_tools_description_switches_language():
    set_locale('zh')
    agent = ReportAgent.__new__(ReportAgent)
    agent.tools = {
        "quick_search": {
            "description": ra.TOOL_DESC_QUICK_SEARCH,
            "parameters": {"query": "搜索关键词"},
        }
    }
    zh_desc = ReportAgent._get_tools_description(agent)
    assert "可用工具" in zh_desc
    assert "Parameters:" not in zh_desc

    set_locale('en')
    en_desc = ReportAgent._get_tools_description(agent)
    assert "Available tools:" in en_desc
    assert "Parameters:" in en_desc
    assert ra.TOOL_DESC_QUICK_SEARCH_EN.splitlines()[0] in en_desc


def test_all_tool_names_have_english_descriptions():
    # Graph-backed tools resolve their EN description via the map; web_search
    # picks its EN text directly at definition time (its own constants).
    assert set(ra.TOOL_DESC_BY_NAME_EN) == (
        ReportAgent.VALID_TOOL_NAMES - {"web_search"}
    )
    assert "Web Search" in ra.TOOL_DESC_WEB_SEARCH_EN.split("【")[1] or "Web Search" in ra.TOOL_DESC_WEB_SEARCH_EN
