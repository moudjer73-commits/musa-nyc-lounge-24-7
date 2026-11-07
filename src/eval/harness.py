import json
from pathlib import Path
from pydantic import BaseModel, Field
from src.config import DATA_DIR
from src.models.register_scene import DirectnessLevel, SceneConfig, SettingType
from src.quality.guard import QualityGuard

TWELVE_CASE_SPECS: list[tuple[int, str, SettingType, DirectnessLevel]] = [
    (1, "Casual: friends roast a bad date", "casual", "firm"),
    (2, "Casual: complaint to a roommate", "casual", "balanced"),
    (3, "Social: party networking", "social", "balanced"),
    (4, "Social: disagreement with a friend", "social", "firm"),
    (5, "Professional: negative executive feedback", "professional", "firm"),
    (6, "Professional: diplomatic client pushback", "professional", "diplomatic"),
    (7, "Professional: peer brainstorm", "professional", "balanced"),
    (8, "Intellectual: seminar contribution", "intellectual", "balanced"),
    (9, "Intellectual: disagreeing with a professor", "intellectual", "diplomatic"),
    (10, "Casual chat interrupted by a professional call (clean switch)", "professional", "diplomatic"),
    (11, "10-minute professional scene (holds register without drift)", "professional", "balanced"),
    (12, "Same content in casual / executive / intellectual", "intellectual", "balanced"),
]

CASUAL_RESIDUE_MARKERS = ("roast", "ghosted", "hang out", "couch", "trash", "wild")
STIFF_OVERHEDGED_MARKERS = ("perchance", "if i may humbly", "i might possibly suggest")


class CaseEvalResult(BaseModel):
    case_id: int
    description: str
    passed: bool
    reasons: list[str] = Field(default_factory=list)


class ReleaseEvalHarness:
    def __init__(self, guard: QualityGuard | None = None) -> None:
        self.guard = guard or QualityGuard()

    def evaluate_single_case(self, case_id: int, payload: dict) -> CaseEvalResult:
        spec = dict((cid, (desc, s, d)) for cid, desc, s, d in TWELVE_CASE_SPECS)[case_id]
        desc, setting, directness = spec
        scene = SceneConfig(setting=setting, directness=directness)
        reasons: list[str] = []

        if case_id == 10:
            post_switch_turns: list[str] = payload.get("post_switch_turns", [])
            audit = self.guard.audit_turns(post_switch_turns, scene)
            reasons.extend(audit.reasons)
            joined = " ".join(post_switch_turns).lower()
            residue = [m for m in CASUAL_RESIDUE_MARKERS if m in joined]
            if residue:
                reasons.append(f"Register residue after switch: {residue}")
        elif case_id == 11:
            drift = self.guard.check_drift(payload.get("minute_2_turns", []), payload.get("minute_11_turns", []), scene)
            if drift.drifted:
                reasons.extend(drift.reasons)
        elif case_id == 12:
            versions: dict[str, str] = payload.get("versions", {})
            vals = [v.strip() for v in versions.values() if v.strip()]
            if len(vals) < 3 or len(set(v.lower() for v in vals)) < 3:
                reasons.append("Case 12 requires 3 clearly distinct register versions")
            for reg_key, text in versions.items():
                reg_scene = SceneConfig(setting="professional" if reg_key == "executive" else reg_key)  # type: ignore[arg-type]
                a = self.guard.audit_turns([text], reg_scene)
                reasons.extend(a.reasons)
        else:
            turns: list[str] = payload.get("turns", [])
            audit = self.guard.audit_turns(turns, scene)
            reasons.extend(audit.reasons)
            joined = " ".join(turns).lower()
            if directness == "firm" and any(m in joined for m in STIFF_OVERHEDGED_MARKERS):
                reasons.append("Systematic mismatch: over-hedged in a firm/direct scene")

        return CaseEvalResult(case_id=case_id, description=desc, passed=(len(reasons) == 0), reasons=reasons)

    def run_12_case_battery(self, battery_inputs: dict[int, dict]) -> list[CaseEvalResult]:
        return [self.evaluate_single_case(cid, battery_inputs.get(cid, {})) for cid, _, _, _ in TWELVE_CASE_SPECS]

    def run_gold_set_eval(self, predictions: dict[str, dict], gold_path: Path = DATA_DIR / "gold_set.json") -> tuple[bool, list[str]]:
        gold_items = json.loads(gold_path.read_text(encoding="utf-8"))
        failures: list[str] = []
        for item in gold_items:
            gid = item["gold_id"]
            pred = predictions.get(gid)
            if not pred:
                failures.append(f"{gid}: missing prediction")
                continue
            if pred.get("category") != item["expected_category"]:
                failures.append(f"{gid}: expected category {item['expected_category']}, got {pred.get('category')}")
            exp_alt = item["expected_alternative_contains"].lower()
            if exp_alt and exp_alt not in pred.get("suggested_alternative", "").lower():
                failures.append(f"{gid}: alternative missing '{exp_alt}'")
        return (len(failures) == 0), failures
