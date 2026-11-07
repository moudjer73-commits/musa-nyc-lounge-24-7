import re
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from src.models.register_scene import SettingType, SpeechFunction

ELevel = Literal["E0", "E1", "E2", "E3", "E4"]
ReviewSpeed = Literal["effortless", "slow", "hesitant", "failed", "successful_transfer"]
RetentionState = Literal["active", "retained", "weakened", "temporarily_inaccessible", "uncertain"]

ARABIC_CHAR_REGEX = re.compile(r"[\u0600-\u06FF]")


class FunctionalChunk(BaseModel):
    chunk_id: str
    phrase: str
    function: SpeechFunction
    register_type: SettingType = Field(alias="register")
    structure: str
    collocations: list[str] = Field(min_length=1)
    common_learner_errors: list[str] = Field(default_factory=list)
    examples: list[str] = Field(min_length=2)
    retrieval_prompts: list[str] = Field(min_length=2)

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def forbid_arabic_gloss_on_card(self) -> "FunctionalChunk":
        # Blueprint 4.4: No literal Arabic gloss on cards
        text_blobs = [self.phrase, self.structure] + self.collocations + self.examples + self.retrieval_prompts
        for blob in text_blobs:
            if ARABIC_CHAR_REGEX.search(blob):
                raise ValueError("Literal Arabic glosses are strictly forbidden on chunk cards.")
        return self


class ProductionAttempt(BaseModel):
    session_id: str
    context_register: SettingType
    target_displayed: bool = False
    assisted_by_hint: bool = False
    collocation_accurate: bool = True
    meaning_and_function_accurate: bool = True
    immediate_echo_of_alex: bool = False
    transferred_to_new_context: bool = False
    speed: ReviewSpeed = "effortless"


class ChunkMasteryRecord(BaseModel):
    user_id: int
    chunk_id: str
    function: SpeechFunction
    baseline_register: SettingType
    e_level: ELevel = "E0"
    unprompted_session_ids: list[str] = Field(default_factory=list)
    succeeded_registers: list[str] = Field(default_factory=list)
    interval_days: int = 0
    next_review_at: str | None = None
    retry_in_same_session: bool = False
    last_prompt_idx: int = -1
    retention_state: RetentionState = "active"

    @property
    def context_key(self) -> str:
        return f"{self.function}:{self.baseline_register}"
