"""S4: the V3 Analyst prompt (prompts/analyst_v3_system.md) and the raw JSON shape it teaches.

The worked examples in the prompt are run through ``validation.validate_analysis``: a prompt whose own examples
lose findings would teach the model a shape that code then drops.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import ai_engine
import schemas
import validation as V

ROOT = Path(__file__).resolve().parents[1]
PROMPT = (ROOT / "prompts" / "analyst_v3_system.md").read_text(encoding="utf-8")

HELD_BACK = {"diagnostic_id", "span_start", "span_end", "confidence_score", "confidence_band", "acoustic_confidence"}
REQUIRED_RAW = {
    "original_span", "normalized_pattern_id", "kind", "severity", "alternative_status", "explanation_en",
    "preserves_intended_meaning", "context_fit_verified", "repair_scope", "communicative_impact", "learning_value",
}


def examples() -> list[tuple[str, dict]]:
    """(learner_message, raw analysis) for every worked example in the prompt."""
    section = PROMPT.split("## 4. WORKED EXAMPLES", 1)[1]
    messages = re.findall(r'Input `learner_message`: "(.*?)"', section)
    blocks = re.findall(r"```json\n(.*?)\n```", section, flags=re.DOTALL)
    assert len(messages) == len(blocks) >= 3
    return [(m, json.loads(b)) for m, b in zip(messages, blocks)]


def validate(message: str, raw: dict):
    return V.validate_analysis(raw, turn_event_id="ev_turn0001", learner_text=message)


# ------------------------------------------------------------------------------ the loader


def test_the_loader_returns_the_file_and_the_v2_prompt_is_untouched():
    assert ai_engine.load_analyst_v3_prompt() == PROMPT.strip()
    assert "diagnostician" in ai_engine.ANALYST_SYSTEM_PROMPT  # the v2 pipeline keeps its own prompt until the rewire


def test_the_prompt_has_no_template_slots_so_it_can_be_sent_as_is():
    assert "{{" not in PROMPT and "{%" not in PROMPT


# ------------------------------------------------------------------------------ the worked examples


def test_every_worked_example_validates_with_nothing_dropped():
    for message, raw in examples():
        result = validate(message, raw)
        assert result.dropped == [], (message, result.notes)
        assert len(result.analysis.diagnostics) == len(raw["diagnostics"]), message


def test_example_findings_keep_the_fields_the_prompt_asks_for():
    (_, first), (_, second), (_, third) = examples()[:3]
    d = validate(*examples()[0]).analysis.diagnostics[0]
    assert d.original_span == "did an effort" and d.alternative == "made an effort"
    assert d.kind.value == "collocation" and d.alternative_status.value == "required"
    assert d.classification.value == "error" and d.domain.value == "linguistic"  # derived by code, not sent
    assert d.l1_transfer_likely is True and d.explanation_ar
    assert validate(*examples()[1]).analysis.diagnostics == []
    pragmatic = validate(*examples()[2]).analysis.diagnostics[0]
    assert pragmatic.kind.value == "pragmatics" and pragmatic.domain.value == "pragmatic"


def test_examples_use_the_raw_shape_only():
    for _message, raw in examples():
        assert set(raw) == {"communicative", "p0", "diagnostics", "target_assessments"}
        for diag in raw["diagnostics"]:
            assert not (set(diag) & HELD_BACK), "the prompt must never show a code-owned field"
            assert REQUIRED_RAW <= set(diag)
            assert set(diag) <= set(schemas.DiagnosticV2.model_fields)
            assert "linguistic_confidence" in diag or "pragmatic_confidence" in diag


def test_the_output_skeleton_matches_the_communicative_and_p0_models():
    skeleton = json.loads(re.search(r"```json\n(\{\n  \"communicative\".*?)\n```", PROMPT, flags=re.DOTALL).group(1))
    assert set(skeleton["communicative"]) <= set(schemas.CommunicativeAssessment.model_fields)
    assert set(skeleton["p0"]) <= set(schemas.P0Signal.model_fields)
    result = V.validate_analysis(skeleton, turn_event_id="ev_t", learner_text="Fine.")
    assert result.dropped == [] and result.analysis.diagnostics == []


# ------------------------------------------------------------------------------ drift against the schema


@pytest.mark.parametrize(
    "enum",
    [schemas.IssueKind, schemas.Severity, schemas.AlternativeStatus, schemas.Domain, schemas.RepairScope,
     schemas.Timing, schemas.ProductionEvidence],
)
def test_every_enum_value_is_named_in_the_prompt(enum):
    missing = [m.value for m in enum if m.value not in PROMPT]
    assert missing == [], f"{enum.__name__} values the prompt never mentions: {missing}"


def test_the_prompt_names_every_required_raw_field_and_forbids_the_held_back_ones():
    for name in REQUIRED_RAW:
        assert f"`{name}`" in PROMPT, name
    forbidden = PROMPT.split("NEVER output:", 1)[1].split("\n", 1)[0]
    for name in HELD_BACK - {"confidence_score"}:
        assert name in forbidden, name
    assert "confidence_score" in forbidden


def test_the_prompt_declares_text_only_and_no_acoustic_judgement():
    assert "TEXT" in PROMPT and "pronunciation" in PROMPT and "never judge" in PROMPT
    assert "never `phonological`" in PROMPT


# ------------------------------------------------------------------------------ Arabic accuracy rule


def test_the_prompt_never_teaches_the_false_arabic_frame():
    # The prompt may state the rule and name the forbidden frame once, in the prohibition itself.
    assert "ناقش الموضوعَ" in PROMPT and "directly transitive" in PROMPT
    for line in PROMPT.splitlines():
        if V.has_false_arabic_frame(line):
            assert "never write" in line and "«ناقش في»" in line, line
    for _message, raw in examples():
        for diag in raw["diagnostics"]:
            for key in ("explanation_ar", "l1_source_ar", "l1_transfer_evidence", "explanation_en"):
                assert not V.has_false_arabic_frame(diag.get(key)), (key, diag.get(key))


def test_the_taxonomy_codes_in_the_prompt_are_codes_the_validator_knows():
    quoted = set(re.findall(r"`((?:COLLOC|GRAM|DISC|PRAG)_[A-Z_]+)`", PROMPT))
    assert quoted and quoted <= set(V.L1_PATTERN_CODES), sorted(quoted - set(V.L1_PATTERN_CODES))
