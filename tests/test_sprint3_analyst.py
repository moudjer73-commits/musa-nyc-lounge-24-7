from pathlib import Path
import pytest
from src.alex.responder import AlexResponder
from src.analyst.calques import detect_seed_calques
from src.analyst.engine import AnalystEngine
from src.bot.analyst_handlers import handle_analyzed_conversation_turn
from src.db.analyst_repo import AnalystRepository
from src.db.profile_repo import ProfileRepository
from src.db.scene_repo import SceneRepository
from src.models.analyst import AnalystLLMOutput
from src.models.profile import LearnerProfile
from src.models.register_scene import SceneConfig


class MockAnalystLLM:
    def __init__(self, outputs: list[AnalystLLMOutput], text_Reply: str = "Yeah, let's finish that thought first.") -> None:
        self.outputs = outputs
        self.idx = 0
        self.text_reply = text_Reply
        self.last_system_prompt = ""

    async def generate_structured(self, user_id: int, system_prompt: str, user_prompt: str, schema_cls, fallback_instance):
        out = self.outputs[min(self.idx, len(self.outputs) - 1)]
        self.idx += 1
        return out

    async def generate_text(self, user_id: int, system_prompt: str, user_prompt: str, temperature: float = 0.7) -> str:
        self.last_system_prompt = system_prompt
        return self.text_reply


def test_seed_calques_and_depend_from_uncertainty():
    hits = detect_seed_calques("Wait, let me complete my idea because it will depend from the budget.")
    ids = {h.pattern_id: h for h in hits}
    assert "calque_complete_idea" in ids
    assert ids["calque_complete_idea"].natural_alternative == "finish my thought"
    assert "calque_depend_from" in ids
    assert ids["calque_depend_from"].cause_status == "uncertain"
    assert ids["calque_depend_from"].verified_by_native is False


@pytest.mark.asyncio
async def test_confidence_gate_and_recurring_grammar_silence(tmp_path: Path):
    repo = AnalystRepository(db_path=tmp_path / "analyst.db")
    profile = LearnerProfile(user_id=55, correction_style="selective")
    scene = SceneConfig()

    llm = MockAnalystLLM([
        # 1. Low confidence (< 0.75) -> gated out silently
        AnalystLLMOutput(
            category="A", error_type="grammar", error_signature="past_tense",
            evidence_span="I go yesterday", suggested_alternative="I went yesterday", confidence=0.55
        ),
        # 2. High confidence recurring grammar in natural-sounding speech -> logged for summary, silent mid-turn
        AnalystLLMOutput(
            category="A", error_type="grammar", error_signature="third_person_s",
            evidence_span="he don't mind", suggested_alternative="he doesn't mind",
            confidence=0.92, is_recurring_natural_grammar=True
        ),
    ])
    engine = AnalystEngine(repo=repo, llm=llm)

    d1 = await engine.analyze_turn(profile, scene, "s1", "I go yesterday.")
    assert d1.action == "silent_log_only"
    assert d1.visible_to_learner is False

    d2 = await engine.analyze_turn(profile, scene, "s1", "Honestly, he don't mind helping out.")
    assert d2.action == "end_of_session_summary"
    assert d2.visible_to_learner is False


@pytest.mark.asyncio
async def test_first_vs_second_occurrence_and_priority_summary(tmp_path: Path):
    db_file = tmp_path / "turn.db"
    a_repo = AnalystRepository(db_path=db_file)
    p_repo = ProfileRepository(db_path=db_file)
    s_repo = SceneRepository(db_path=db_file)

    calque_out = AnalystLLMOutput(
        category="B", error_type="lexis", error_signature="calque_complete_idea",
        evidence_span="complete my idea", suggested_alternative="finish my thought", confidence=0.95
    )
    reg_out = AnalystLLMOutput(
        category="D", error_type="register", error_signature="stiff_preference",
        evidence_span="I would prefer to decline", suggested_alternative="I'm gonna have to pass", confidence=0.90
    )
    pron_out = AnalystLLMOutput(
        category="A", error_type="pronunciation", error_signature="p_b_swap",
        evidence_span="ben", suggested_alternative="pen", confidence=0.88
    )

    llm = MockAnalystLLM([calque_out, calque_out, reg_out, pron_out])
    analyst = AnalystEngine(repo=a_repo, llm=llm)
    responder = AlexResponder(scene_repo=s_repo, llm=llm)

    # 1st occurrence -> silent recast passed to Alex, no explicit correction message
    msgs_1 = await handle_analyzed_conversation_turn(
        10, "sess_1", "Let me complete my idea.", p_repo, s_repo, analyst, responder
    )
    assert len(msgs_1) == 1
    assert "SILENT RECAST DIRECTIVE" in llm.last_system_prompt
    assert "finish my thought" in llm.last_system_prompt

    # 2nd occurrence -> Alex replies + explicit correction with retry prompt
    msgs_2 = await handle_analyzed_conversation_turn(
        10, "sess_1", "Wait, let me complete my idea first.", p_repo, s_repo, analyst, responder
    )
    assert len(msgs_2) == 2
    assert "finish my thought" in msgs_2[1]
    assert "shot" in msgs_2[1]

    # Log 2 more error types (register & pronunciation) and verify summary keeps MAX 2 ordered by priority (lexis > register)
    profile = p_repo.get_or_create_profile(10)
    scene = s_repo.get_active_scene(10)
    await analyst.analyze_turn(profile, scene, "sess_1", "I would prefer to decline.")
    await analyst.analyze_turn(profile, scene, "sess_1", "Pass me the ben.")

    summary = analyst.format_end_of_session_summary(10, "sess_1")
    assert "finish my thought" in summary
    assert "I'm gonna have to pass" in summary
    assert "pen" not in summary  # 3rd priority item cut by max_targets=2


@pytest.mark.asyncio
async def test_english_hint_ladder_progression(tmp_path: Path):
    repo = AnalystRepository(db_path=tmp_path / "hints.db")
    llm = MockAnalystLLM([], text_Reply="Here is your stepped hint.")
    engine = AnalystEngine(repo=repo, llm=llm)
    profile = LearnerProfile(user_id=88, arabic_support="none")

    s1, _ = await engine.get_next_hint(profile, "push back")
    s2, _ = await engine.get_next_hint(profile, "push back")
    s3, _ = await engine.get_next_hint(profile, "push back")
    s4_no_ar, _ = await engine.get_next_hint(profile, "push back", ask_arabic=False)
    s4_with_ar, _ = await engine.get_next_hint(profile, "push back", ask_arabic=True)

    assert [s1, s2, s3, s4_no_ar, s4_with_ar] == [
        "describe_it",
        "first_letter",
        "sentence_starter",
        "sentence_starter",
        "arabic_fallback",
    ]
