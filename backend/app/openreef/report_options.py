"""Report customization options (OpenReef feature 4).

Per-report overrides for language, tone, audience, length, and complexity,
threaded into the report agent's prompts. Optional in every dimension:
no options = upstream behavior exactly (reports follow the UI locale,
standard length, no style directives).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

SUPPORTED_LANGUAGES = ("zh", "en", "es", "fr", "pt", "ru", "de")
LENGTHS = ("short", "standard", "long")
LENGTH_TARGETS = {
    "short": {"section_words": 400, "total_words": 1200},
    "standard": {"section_words": 800, "total_words": 3000},
    "long": {"section_words": 1500, "total_words": 6000},
}
COMPLEXITY_LEVELS = ("general", "informed", "expert")

# Known tones get crafted directives; anything else is injected verbatim.
TONE_DIRECTIVES_ZH = {
    "professional": "专业、克制、基于事实，避免营销式措辞",
    "analytical": "分析性写作：先证据后结论，明确区分事实与推断",
    "journalistic": "新闻综述风格：清晰、及时、客观，导语先给要点",
    "vivid": "生动叙事：用具体场景与引语带动论述，仍保持预测严谨",
}
TONE_DIRECTIVES_EN = {
    "professional": "Professional and restrained, fact-based, no marketing language",
    "analytical": "Analytical writing: evidence before conclusions, clearly separate fact from inference",
    "journalistic": "Journalistic style: clear, timely, objective, lead with the key point",
    "vivid": "Vivid narrative: concrete scenes and quotes drive the analysis, while staying rigorous",
}
COMPLEXITY_DIRECTIVES_ZH = {
    "general": "面向普通读者：避免专业术语，必须使用的概念用一句话解释",
    "informed": "面向关注该领域的读者：可使用常见术语，给出一句话背景即可",
    "expert": "面向领域专家：直接使用术语，分析机制与二阶效应，不解释基础概念",
}
COMPLEXITY_DIRECTIVES_EN = {
    "general": "For a general audience: avoid jargon; explain any unavoidable concept in one sentence",
    "informed": "For engaged readers: common domain terms are fine with a one-line background",
    "expert": "For domain experts: use technical terms freely, analyze mechanisms and second-order effects, no basic explanations",
}

PRESETS: Dict[str, Dict[str, Any]] = {
    "standard": {},
    "executive": {
        "length": "short",
        "max_sections": 3,
        "tone": "professional",
        "audience": "busy decision-makers",
        "complexity": "informed",
    },
    "deep_dive": {
        "length": "long",
        "max_sections": 5,
        "tone": "analytical",
        "audience": "domain experts",
        "complexity": "expert",
    },
}

_MAX_FREE_TEXT = 120
_MAX_SECTIONS_RANGE = (2, 5)


@dataclass(frozen=True)
class ReportOptions:
    """Normalized report customization. All-default == upstream behavior."""

    preset: str = "standard"
    language: Optional[str] = None      # None = follow UI locale
    tone: Optional[str] = None
    audience: Optional[str] = None
    length: str = "standard"
    max_sections: Optional[int] = None
    complexity: Optional[str] = None

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "ReportOptions":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError("report_options must be a JSON object")

        preset = str(raw.get("preset", "standard")).strip().lower()
        if preset not in PRESETS:
            raise ValueError(
                f"unknown preset {preset!r}; expected one of {sorted(PRESETS)}"
            )

        merged: Dict[str, Any] = dict(PRESETS[preset])
        for key, value in raw.items():
            if key != "preset" and value is not None:
                merged[key] = value

        language = merged.get("language")
        if language is not None:
            language = str(language).strip().lower()
            if language not in SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"unsupported report language {language!r}; "
                    f"expected one of {list(SUPPORTED_LANGUAGES)}"
                )

        length = str(merged.get("length", "standard")).strip().lower()
        if length not in LENGTHS:
            raise ValueError(f"unknown length {length!r}; expected one of {list(LENGTHS)}")

        complexity = merged.get("complexity")
        if complexity is not None:
            complexity = str(complexity).strip().lower()
            if complexity not in COMPLEXITY_LEVELS:
                raise ValueError(
                    f"unknown complexity {complexity!r}; expected one of {list(COMPLEXITY_LEVELS)}"
                )

        max_sections = merged.get("max_sections")
        if max_sections is not None:
            try:
                max_sections = int(max_sections)
            except (TypeError, ValueError):
                raise ValueError("max_sections must be an integer") from None
            low, high = _MAX_SECTIONS_RANGE
            if not low <= max_sections <= high:
                raise ValueError(f"max_sections must be between {low} and {high}")

        tone = _clean_free_text(merged.get("tone"), "tone")
        audience = _clean_free_text(merged.get("audience"), "audience")

        return cls(
            preset=preset,
            language=language,
            tone=tone,
            audience=audience,
            length=length,
            max_sections=max_sections,
            complexity=complexity,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "preset": self.preset,
            "language": self.language,
            "tone": self.tone,
            "audience": self.audience,
            "length": self.length,
            "max_sections": self.max_sections,
            "complexity": self.complexity,
        }

    @property
    def is_default(self) -> bool:
        return self == ReportOptions()

    def tone_directive(self, english: bool) -> str:
        table = TONE_DIRECTIVES_EN if english else TONE_DIRECTIVES_ZH
        key = (self.tone or "").lower()
        if key in table:
            return table[key]
        return self.tone or ""

    def complexity_directive(self, english: bool) -> str:
        table = COMPLEXITY_DIRECTIVES_EN if english else COMPLEXITY_DIRECTIVES_ZH
        key = (self.complexity or "").lower()
        return table.get(key, self.complexity or "")

    @property
    def has_style_customization(self) -> bool:
        """Any style directive beyond the default (language alone doesn't count)."""
        default = ReportOptions()
        return (
            self.preset != default.preset
            or self.tone != default.tone
            or self.audience != default.audience
            or self.length != default.length
            or self.max_sections != default.max_sections
            or self.complexity != default.complexity
        )

    def customization_block(self, english: bool, for_plan: bool) -> str:
        """Style directives injected into the outline/section system prompts.

        Returns "" when nothing was customized (upstream behavior).
        Language is NOT handled here — it switches the templates/instruction.
        """
        if not self.has_style_customization:
            return ""
        targets = LENGTH_TARGETS[self.length]
        lines = []
        if english:
            lines.append("【Report customization】")
            lines.append(
                f"- Length: each section ~{targets['section_words']} words; "
                f"the full report ~{targets['total_words']} words."
            )
            if self.tone:
                lines.append(f"- Tone: {self.tone_directive(True)}")
            if self.audience:
                lines.append(f"- Audience: {self.audience}")
            if self.complexity:
                lines.append(f"- Complexity: {self.complexity_directive(True)}")
            if for_plan and self.max_sections:
                lines.append(f"- Section count: at most {self.max_sections} sections.")
        else:
            lines.append("【报告定制要求】")
            lines.append(
                f"- 篇幅：每个章节约{targets['section_words']}字，全篇约{targets['total_words']}字"
            )
            if self.tone:
                lines.append(f"- 语气：{self.tone_directive(False)}")
            if self.audience:
                lines.append(f"- 目标读者：{self.audience}")
            if self.complexity:
                lines.append(f"- 复杂度：{self.complexity_directive(False)}")
            if for_plan and self.max_sections:
                lines.append(f"- 章节数量：最多{self.max_sections}个章节")
        return "\n".join(lines)


def _clean_free_text(value: Optional[Any], field_name: str) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > _MAX_FREE_TEXT:
        raise ValueError(f"{field_name} must be at most {_MAX_FREE_TEXT} characters")
    return text
