from src.models.profile import LearnerProfile
from src.models.prosody_phase3 import (
    LearnerAmbition,
    ProsodyDrillItem,
    ProsodySessionBlock,
    ProsodyTargetCode,
)

SHARED_CORE_DRILLS: list[ProsodyDrillItem] = [
    ProsodyDrillItem(
        target_code="p_vs_b",
        title="Voiceless /p/ Aspiration vs. /b/",
        minimal_pair_or_prompt="Wait till I pack the bag vs. back the plan.",
    ),
    ProsodyDrillItem(
        target_code="american_r",
        title="Retroflex/Bunched American /r/ Coloring",
        minimal_pair_or_prompt="The first report covered four major hurdles.",
    ),
    ProsodyDrillItem(
        target_code="word_stress_and_vowel_reduction",
        title="Primary Stress & Unstressed Vowel Reduction (Schwa)",
        minimal_pair_or_prompt="We need to phoTOGraph the deVElopment imMEdiately.",
    ),
    ProsodyDrillItem(
        target_code="sentence_intonation",
        title="Nuclear Stress & Thought-Group Intonation",
        minimal_pair_or_prompt="I didn't say we should cancel it—I said we should delay it.",
    ),
]

RECOGNITION_ONLY_ITEMS: list[ProsodyDrillItem] = [
    ProsodyDrillItem(
        target_code="creaky_voice",
        title="Phrase-Final Creaky Voice (Vocal Fry) — Recognition Only",
        recognition_only=True,
        recheck_in_free_talk_weekly=False,
        minimal_pair_or_prompt="[Listen only] Notice how the speaker's pitch drops into creak at the turn end.",
    ),
    ProsodyDrillItem(
        target_code="wide_pitch_excursions",
        title="Wide Emphatic Pitch Excursions — Recognition Only",
        recognition_only=True,
        recheck_in_free_talk_weekly=False,
        minimal_pair_or_prompt="[Listen only] Identify whether the wide pitch jump signals surprise or skepticism.",
    ),
]

DIALECT_HYPOTHESIS_DRILLS: dict[str, ProsodyDrillItem] = {
    "Maghrebi": ProsodyDrillItem(
        target_code="word_stress_and_vowel_reduction",
        title="Maghrebi Consonant-Cluster & English Stress-Timing Hypothesis",
        dialect_hypothesis="Maghrebi",
        evidence_strength="thin_evidence_hypothesis",
        minimal_pair_or_prompt="Keep full English vowel duration on stressed syllables: 'TIME-line', 'BUD-get'.",
    ),
    "Egyptian": ProsodyDrillItem(
        target_code="epenthetic_vowel_clusters",
        title="Egyptian Epenthetic Vowel Insertion in Final Clusters (Hypothesis)",
        dialect_hypothesis="Egyptian",
        evidence_strength="thin_evidence_hypothesis",
        minimal_pair_or_prompt="Link smoothly without an extra vowel: 'next month', 'asked them'.",
    ),
    "Levantine": ProsodyDrillItem(
        target_code="p_vs_b",
        title="Levantine Initial Stop Voicing & Intonation Contour (Hypothesis)",
        dialect_hypothesis="Levantine",
        evidence_strength="thin_evidence_hypothesis",
        minimal_pair_or_prompt="Hold strong aspiration on initial /p/: 'point blank', 'push back'.",
    ),
    "Gulf": ProsodyDrillItem(
        target_code="american_r",
        title="Gulf Trilled/Tapped /r/ to Approximant /r/ Transition (Hypothesis)",
        dialect_hypothesis="Gulf",
        evidence_strength="thin_evidence_hypothesis",
        minimal_pair_or_prompt="Keep the tongue tip from touching the ridge on 'right around the corner'.",
    ),
}

FRENCH_TRANSFER_DRILL = ProsodyDrillItem(
    target_code="french_uvular_r_and_final_stress_transfer",
    title="French Phrase-Final Stress & Lexical Cognate Stress Shift",
    requires_french_background=True,
    evidence_strength="thin_evidence_hypothesis",
    minimal_pair_or_prompt="Shift stress earlier on English cognates: 'OR-gan-ize', 'DE-vel-op', 'COM-fort-a-ble'.",
)


class ProsodyLabEngine:
    def __init__(self) -> None:
        self._ambitions: dict[int, LearnerAmbition] = {}
        self._verified_speech_targets: dict[int, set[ProsodyTargetCode]] = {}

    def set_learner_ambition(self, user_id: int, ambition: LearnerAmbition) -> LearnerAmbition:
        self._ambitions[user_id] = ambition
        return ambition

    def record_speech_verified_target(self, user_id: int, target: ProsodyTargetCode) -> None:
        self._verified_speech_targets.setdefault(user_id, set()).add(target)

    def build_5min_prosody_block(
        self, profile: LearnerProfile, include_recognition_items: bool = False
    ) -> ProsodySessionBlock:
        drills = list(SHARED_CORE_DRILLS)
        # Dialect hypothesis added ONLY if learner's speech supported it or verified
        d_name = profile.dialect.dialect_name
        if d_name in DIALECT_HYPOTHESIS_DRILLS and (
            profile.dialect.status == "supported" or profile.user_id in self._verified_speech_targets
        ):
            drills.append(DIALECT_HYPOTHESIS_DRILLS[d_name])

        # Blueprint 4.8: French-related items ONLY if the learner reports French
        has_french = any("french" in lang.lower() or "فرنس" in lang for lang in profile.other_languages)
        if has_french:
            drills.append(FRENCH_TRANSFER_DRILL)

        if include_recognition_items:
            drills.extend(RECOGNITION_ONLY_ITEMS)

        return ProsodySessionBlock(
            user_id=profile.user_id,
            duration_minutes=5,
            isolated_from_free_talk=True,
            ambition=self._ambitions.get(profile.user_id, "intelligibility_and_presence"),
            active_drills=drills,
        )

    def can_train_for_production(self, drill: ProsodyDrillItem) -> bool:
        """Blueprint 4.8: Creaky voice and wide pitch excursions are for recognition ONLY."""
        if drill.target_code in ("creaky_voice", "wide_pitch_excursions") or drill.recognition_only:
            return False
        return True

    def get_weekly_free_talk_recheck_targets(self, block: ProsodySessionBlock) -> list[ProsodyTargetCode]:
        return [d.target_code for d in block.active_drills if d.recheck_in_free_talk_weekly and not d.recognition_only]
