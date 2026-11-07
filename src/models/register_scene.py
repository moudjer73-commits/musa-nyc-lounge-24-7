from typing import Literal
from pydantic import BaseModel, Field, model_validator

SettingType = Literal["casual", "social", "professional", "intellectual"]
RelationshipType = Literal["equal", "hierarchical", "unfamiliar", "close"]
DirectnessLevel = Literal["diplomatic", "balanced", "firm"]
ToneFlavor = Literal["neutral", "warm", "serious", "humorous", "skeptical", "empathetic"]
LearningStage = Literal["S1", "S2", "S3", "S4", "S5"]
SwitchMode = Literal["a_bot_scene_change", "b_learner_requested", "c_mid_conversation_shift"]
EvidenceCap = Literal["E2", "E3_E4"]

SpeechFunction = Literal[
    "disagree",
    "decline",
    "request",
    "interrupt",
    "give_opinion",
    "deliver_bad_news",
    "challenge_idea",
    "set_boundaries",
]

DEFAULT_DIRECTNESS_MAP: dict[tuple[SpeechFunction, RelationshipType], DirectnessLevel] = {
    ("disagree", "hierarchical"): "diplomatic",
    ("disagree", "unfamiliar"): "diplomatic",
    ("disagree", "equal"): "balanced",
    ("disagree", "close"): "firm",
    ("decline", "hierarchical"): "diplomatic",
    ("decline", "close"): "balanced",
    ("deliver_bad_news", "hierarchical"): "diplomatic",
    ("set_boundaries", "equal"): "firm",
    ("set_boundaries", "hierarchical"): "diplomatic",
    ("challenge_idea", "hierarchical"): "diplomatic",
    ("challenge_idea", "equal"): "balanced",
}


def get_default_directness(function: SpeechFunction, relationship: RelationshipType) -> DirectnessLevel:
    if (function, relationship) in DEFAULT_DIRECTNESS_MAP:
        return DEFAULT_DIRECTNESS_MAP[(function, relationship)]
    if relationship in ("hierarchical", "unfamiliar"):
        return "diplomatic"
    if relationship == "close" and function in ("disagree", "set_boundaries"):
        return "firm"
    return "balanced"


class SceneConfig(BaseModel):
    room_id: str = "balanced_default"
    setting: SettingType = "social"
    relationship: RelationshipType = "equal"
    function: SpeechFunction = "give_opinion"
    directness: DirectnessLevel | None = None
    flavor: ToneFlavor = "neutral"
    learner_stage: LearningStage = "S1"
    topic: str = "everyday life and work"
    allow_humor_recognition_only: bool = True

    @model_validator(mode="after")
    def calibrate_scene(self) -> "SceneConfig":
        if self.directness is None:
            self.directness = get_default_directness(self.function, self.relationship)
        # Blueprint 4.2: Humor production starts at S3; recognition comes before production
        if self.flavor == "humorous" and self.learner_stage in ("S1", "S2"):
            self.flavor = "warm"
            self.allow_humor_recognition_only = True
        elif self.learner_stage in ("S3", "S4", "S5"):
            self.allow_humor_recognition_only = False
        return self


class SceneSwitchEvent(BaseModel):
    mode: SwitchMode
    previous_scene: SceneConfig
    new_scene: SceneConfig
    max_evidence_level: EvidenceCap = Field(default="E3_E4")

    @model_validator(mode="after")
    def assign_evidence_cap(self) -> "SceneSwitchEvent":
        # Blueprint 4.2: (b) is E2 evidence only; (a)/(c) is E3/E4 evidence
        self.max_evidence_level = "E2" if self.mode == "b_learner_requested" else "E3_E4"
        return self
