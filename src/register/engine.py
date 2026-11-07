from src.models.register_scene import (
    DirectnessLevel,
    LearningStage,
    RelationshipType,
    SceneConfig,
    SceneSwitchEvent,
    SettingType,
    SpeechFunction,
    SwitchMode,
    ToneFlavor,
)

PRESET_ROOMS: dict[str, tuple[SettingType, RelationshipType, ToneFlavor]] = {
    "balanced_default": ("social", "equal", "neutral"),
    "casual_friends": ("casual", "close", "warm"),
    "casual_roommate": ("casual", "equal", "neutral"),
    "social_networking": ("social", "unfamiliar", "warm"),
    "pro_executive": ("professional", "hierarchical", "serious"),
    "pro_peer_brainstorm": ("professional", "equal", "neutral"),
    "intellectual_seminar": ("intellectual", "equal", "skeptical"),
    "intellectual_professor": ("intellectual", "hierarchical", "serious"),
}

SCOPED_USAGE_TAGS: dict[str, str] = {
    "bandwidth": "industry-specific (tech/corporate)",
    "ping me": "industry-specific (workplace messaging)",
    "double-click on that": "industry-specific (consulting/tech)",
    "lowkey": "generational (younger casual)",
    "bet": "generational/regional (casual agreement)",
    "y'all": "regional (Southern/General US conversational)",
    "wicked": "regional (New England)",
}


class RegisterEngine:
    def create_room_scene(
        self,
        room_id: str = "balanced_default",
        function: SpeechFunction = "give_opinion",
        directness: DirectnessLevel | None = None,
        flavor: ToneFlavor | None = None,
        learner_stage: LearningStage = "S1",
        topic: str = "current projects",
    ) -> SceneConfig:
        setting, relationship, default_flavor = PRESET_ROOMS.get(
            room_id, PRESET_ROOMS["balanced_default"]
        )
        return SceneConfig(
            room_id=room_id,
            setting=setting,
            relationship=relationship,
            function=function,
            directness=directness,
            flavor=flavor or default_flavor,
            learner_stage=learner_stage,
            topic=topic,
        )

    def switch_scene(
        self,
        current: SceneConfig,
        mode: SwitchMode,
        new_setting: SettingType | None = None,
        new_relationship: RelationshipType | None = None,
        new_directness: DirectnessLevel | None = None,
        new_flavor: ToneFlavor | None = None,
    ) -> SceneSwitchEvent:
        updated = SceneConfig(
            room_id=f"{new_setting or current.setting}_{new_relationship or current.relationship}",
            setting=new_setting or current.setting,
            relationship=new_relationship or current.relationship,
            function=current.function,
            directness=new_directness,
            flavor=new_flavor or current.flavor,
            learner_stage=current.learner_stage,
            topic=current.topic,
        )
        return SceneSwitchEvent(mode=mode, previous_scene=current, new_scene=updated)

    def requires_register_adjustment(self, scene_a: SceneConfig, scene_b: SceneConfig) -> bool:
        """Checks if moving from scene_a to scene_b requires changing hedging/lexis,
        or if an identical balanced sentence is naturally acceptable in both."""
        if scene_a.relationship == scene_b.relationship and scene_a.directness == scene_b.directness:
            return False
        return True

    def detect_scoped_usage(self, text: str) -> list[tuple[str, str]]:
        lowered = text.lower()
        return [(term, tag) for term, tag in SCOPED_USAGE_TAGS.items() if term in lowered]
