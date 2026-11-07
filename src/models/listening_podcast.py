from typing import Literal
from pydantic import BaseModel, Field, model_validator
from src.models.register_scene import SettingType, SpeechFunction

ListeningLevel = Literal["L1", "L2", "L3", "L4"]
ListeningTaskType = Literal["reconstruct_full_form", "stance_from_intonation", "reformulate_in_register"]
EpisodeType = Literal["two_host_dialogue", "personal_short_episode", "curated_human_link"]
PodcastFlowStep = Literal[
    "first_listen",
    "gist_question",
    "detail_question",
    "transcript_unlocked",
    "second_listen",
    "shadow_15s",
    "reformulate_register",
    "voice_reply_third_speaker",
]


class ListeningTaskItem(BaseModel):
    task_id: str
    level: ListeningLevel
    task_type: ListeningTaskType
    spoken_clip_text: str
    intonation_cue: str
    expected_answer: str
    target_register: SettingType


class PodcastTurn(BaseModel):
    speaker: Literal["Host_A", "Host_B"]
    text: str
    pause_after_ms: int = Field(default=220, ge=80, le=800)


class PodcastScript(BaseModel):
    episode_type: EpisodeType = "two_host_dialogue"
    register_label: SettingType = "social"
    focus_function: SpeechFunction = "disagree"
    level: ListeningLevel = "L2"
    topic: str = "workplace and everyday decisions"
    target_chunks: list[str] = Field(min_length=2, max_length=5)
    turns: list[PodcastTurn] = Field(min_length=4)
    has_misunderstanding_and_repair: bool = True
    gist_question: str = "What is the main disagreement between the two hosts?"
    gist_expected_keyword: str = "timeline"
    detail_question: str = "How did Host B clarify the misunderstanding?"
    detail_expected_keyword: str = "meant"
    shadow_segment_15s: str = ""
    ai_generated_label: str = "AI-generated"

    @model_validator(mode="after")
    def enforce_ai_label_and_shadow(self) -> "PodcastScript":
        self.ai_generated_label = "AI-generated"
        if not self.shadow_segment_15s and self.turns:
            self.shadow_segment_15s = self.turns[0].text
        return self


class ScriptAuditReport(BaseModel):
    passed: bool = True
    reasons: list[str] = Field(default_factory=list)
    embedded_chunks_found: int = 0
    has_backchannels: bool = False
    has_repair: bool = False


class PodcastEpisodeRecord(BaseModel):
    episode_id: str
    cache_key: str
    user_id: int | None = None
    script: PodcastScript
    script_version: int = 1
    audit_report: ScriptAuditReport
    voice_settings: dict[str, str] = Field(
        default_factory=lambda: {"Host_A": "en-US-AndrewNeural", "Host_B": "en-US-AvaNeural"}
    )
    audio_path: str = ""
    label: str = "AI-generated"


class LearnerPodcastState(BaseModel):
    user_id: int
    episode_id: str
    step: PodcastFlowStep = "first_listen"
    first_listen_done: bool = False
    gist_answered: bool = False
    detail_answered: bool = False
    question_accuracy: float = 0.0
    replayed_count: int = 0
    speed_used: str = "1.0x"
    shadowing_attempted: bool = False
    voice_reply_submitted: bool = False

    @property
    def transcript_visible(self) -> bool:
        return self.first_listen_done and self.gist_answered and self.detail_answered
