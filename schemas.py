from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class DialogueTurn(BaseModel):
    speaker: Literal["Marcus", "Chloe", "Jake"] = "Marcus"
    text: str = Field(..., min_length=2)


class MirrorEditSchema(BaseModel):
    wrong_span: str = Field(..., description="ONLY the specific non-idiomatic or ungrammatical phrase from the learner's input.")
    natural_us: str = Field(..., description="The natural Spoken American replacement for that specific span.")
    trigger: Literal["collocation", "register", "grammar", "us_preference", "pragmatic"] = "collocation"
    why_en: str = Field(..., description="Calm, accurate 1-sentence explanation mentioning natural American alternatives without stigmatizing tone.")
    why_ar: str = Field(..., description="Accurate 1-sentence Arabic explanation.")


class SpokenMoveSchema(BaseModel):
    template_en: str = Field(..., description="Reusable chunk pattern with slot, e.g. 'push back on [something]'")
    used_in_dialogue: str = Field(..., description="Exact substring used in the dialogue above")
    arabic_meaning: str = Field(..., description="Accurate Arabic translation of the template")
    functional_category: str = Field(..., description="Communicative function, e.g. 'Diplomatic Pushback'")
    does: str = Field(..., description="What this move achieves pragmatically in 1 short phrase")
    say_it_when: str = Field(..., description="Concrete everyday situation when Americans say this")


class UnifiedBanterOutput(BaseModel):
    coaching_decision: Literal["silence", "surgical_mirror", "recast_move"] = "silence"
    turns: List[DialogueTurn] = Field(..., min_length=2, max_length=3)
    mirror: Optional[MirrorEditSchema] = None
    spoken_move: Optional[SpokenMoveSchema] = None


class MetalinguisticOutput(BaseModel):
    coach_explanation_en: str = Field(..., description="3-4 sentences distinguishing comprehensibility from idiomatic US collocation/nuance.")
    coach_explanation_ar: str = Field(..., description="1 accurate Arabic summary sentence.")
    counterpart_speaker: Literal["Marcus", "Chloe", "Jake"] = "Marcus"
    scene_resume_line: str = Field(..., description="1-2 natural sentences addressing the learner as 'you' to resume the paused topic.")


class NegotiationTurnOutput(BaseModel):
    evaluation_stage: Literal["setup_or_continue", "beat1_cavein", "beat2_compromise"] = "setup_or_continue"
    counterpart_speaker: Literal["Marcus", "Chloe", "Jake"] = "Marcus"
    counterpart_reply: str = Field(..., description="In-character reply from the counterpart.")
    how_it_lands: Optional[str] = None
    acknowledged_pressure: bool = True
    preserved_core_constraint: bool = True
    retry_prompt: Optional[str] = None
    attempt1_vs_attempt2_comparison: Optional[str] = None
    functional_category: str = "Scope & Priority Negotiation"


class DevilsAdvocateOutput(BaseModel):
    challenger: Literal["Marcus", "Chloe", "Jake"] = "Marcus"
    fair_restatement: str = Field(..., description="1 sentence showing fair understanding of the learner's position before challenging it.")
    counter_argument: str = Field(..., description="2-3 sentences presenting a sharp, realistic counter-perspective without lecturing on grammar.")
    probing_question: str = Field(..., description="1 natural question testing the learner's reasoning.")
    mirror: Optional[MirrorEditSchema] = None
