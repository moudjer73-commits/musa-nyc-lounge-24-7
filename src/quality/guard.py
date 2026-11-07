import re
from pydantic import BaseModel, Field
from src.models.register_scene import SceneConfig

FALSE_CASUAL_MARKERS = ("lit", "no cap", "dude", "bro", "fam", "bruh")
FALSE_FORMAL_MARKERS = (
    "moreover",
    "furthermore",
    "consequently",
    "heretofore",
    "i would prefer to",
    "it is imperative",
)
CORPORATE_BUZZWORDS = (
    "synergy",
    "circle back",
    "leverage",
    "touch base",
    "low-hanging fruit",
    "paradigm shift",
)
# Blueprint 4.11: Contractions including gonna/wanna are valid across ALL registers
SPOKEN_CONTRACTIONS = (
    "n't", "'m", "'re", "'ve", "'ll", "'d", "'s", "gonna", "wanna", "kinda", "gotta"
)


class QualityAuditResult(BaseModel):
    passed: bool = True
    false_casual_hits: list[str] = Field(default_factory=list)
    false_formal_hits: list[str] = Field(default_factory=list)
    corporate_buzzwords: list[str] = Field(default_factory=list)
    missing_contractions: bool = False
    question_ratio: float = 0.0
    interview_cadence_violation: bool = False
    reasons: list[str] = Field(default_factory=list)


class DriftCheckReport(BaseModel):
    drifted: bool = False
    early_audit: QualityAuditResult
    late_audit: QualityAuditResult
    question_ratio_delta: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class QualityGuard:
    def __init__(self, max_question_ratio: float = 0.5) -> None:
        self.max_question_ratio = max_question_ratio

    def _find_markers(self, text: str, markers: tuple[str, ...]) -> list[str]:
        lowered = text.lower()
        hits = []
        for m in markers:
            pattern = r"\b" + re.escape(m) + r"\b"
            if re.search(pattern, lowered):
                hits.append(m)
        return hits

    def _has_contraction(self, text: str) -> bool:
        lowered = text.lower()
        return any(c in lowered for c in SPOKEN_CONTRACTIONS)

    def audit_turns(self, alex_turns: list[str], scene: SceneConfig) -> QualityAuditResult:
        if not alex_turns:
            return QualityAuditResult(passed=True)
        joined = " ".join(alex_turns)
        fc_hits = self._find_markers(joined, FALSE_CASUAL_MARKERS)
        ff_hits = self._find_markers(joined, FALSE_FORMAL_MARKERS)
        corp_hits = self._find_markers(joined, CORPORATE_BUZZWORDS)

        word_count = len(joined.split())
        missing_contractions = word_count >= 18 and not self._has_contraction(joined)

        q_turns = sum(1 for t in alex_turns if t.strip().endswith("?"))
        q_ratio = round(q_turns / len(alex_turns), 2)
        cadence_fail = len(alex_turns) >= 2 and q_ratio > self.max_question_ratio

        reasons: list[str] = []
        if fc_hits:
            reasons.append(f"False casual markers: {fc_hits}")
        if ff_hits:
            reasons.append(f"False formal markers: {ff_hits}")
        if corp_hits:
            reasons.append(f"Corporate buzzwords: {corp_hits}")
        if missing_contractions:
            reasons.append("Missing natural spoken contractions across extended speech")
        if cadence_fail:
            reasons.append(f"Interview cadence violation: question ratio {q_ratio} > {self.max_question_ratio}")

        return QualityAuditResult(
            passed=(len(reasons) == 0),
            false_casual_hits=fc_hits,
            false_formal_hits=ff_hits,
            corporate_buzzwords=corp_hits,
            missing_contractions=missing_contractions,
            question_ratio=q_ratio,
            interview_cadence_violation=cadence_fail,
            reasons=reasons,
        )

    def check_drift(self, minute_2_turns: list[str], minute_11_turns: list[str], scene: SceneConfig) -> DriftCheckReport:
        early = self.audit_turns(minute_2_turns, scene)
        late = self.audit_turns(minute_11_turns, scene)
        q_delta = round(abs(late.question_ratio - early.question_ratio), 2)
        reasons: list[str] = []
        if not late.passed:
            reasons.extend(late.reasons)
        if q_delta > 0.4:
            reasons.append(f"Cadence drift detected between early and late window (delta={q_delta})")
        return DriftCheckReport(
            drifted=(len(reasons) > 0),
            early_audit=early,
            late_audit=late,
            question_ratio_delta=q_delta,
            reasons=reasons,
        )
