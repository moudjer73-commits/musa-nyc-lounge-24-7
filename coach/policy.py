"""The deterministic intervention policy: `decide(analysis, budget, state)`.

The Analyst *diagnoses*; this module *decides* what (if anything) the learner sees.

Ladder, in order of precedence:
  1. Comprehension breakdown  -> `clarify`: the character asks a natural question in scene.
  2. Critical pragmatic/register problem (severity 3) -> `offer_card` at a reduced gap,
     else `defer` to the end-of-scene recap.
  3. Priority >= card threshold -> `offer_card` if the correction budget allows, else a silent
     `recast`, else `defer`.
  4. Priority >= recast threshold and a usable recast form -> `recast` (invisible).
  5. A pattern seen for the 3rd time -> `queue_drill` (a 2-minute mini-exercise at the next lull).
  6. Anything else of severity >= 2 -> `defer` to the recap; the rest -> `silent` (logged only).

Priority = impact x frequency x learnability - load
  impact       severity x category weight (pragmatic and comprehension outrank grammar)
  frequency    1 + 0.5 per earlier occurrence of the same pattern_key (capped)
  learnability 1.0 if teachable now in one chunk, else 0.5
  load         base + learner strain (+ extra for B1): the cost of interrupting right now

The correction budget caps *visible* corrections (the 💡 offer). Recasts are invisible and
do not spend it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .models import Analysis, Finding
from .state import LearnerState

CATEGORY_WEIGHT: Mapping[str, float] = {
    "comprehension": 1.6,
    "pragmatic": 1.5,
    "register": 1.3,
    "collocation": 1.0,
    "grammar": 0.8,
    "fluency": 0.6,
}
CARD_THRESHOLD = 2.0
RECAST_THRESHOLD = 0.9
# Higher-level learners get more "sound native" feedback; lower levels get less, bigger items.
CEFR_THRESHOLD_ADJUST: Mapping[str, float] = {"B1": 0.4, "B2": 0.0, "C1": -0.2, "C2": -0.4}
DRILL_THRESHOLD = 3
WARMUP_TURNS = 2
CRITICAL_CATEGORIES = frozenset({"pragmatic", "register", "comprehension"})


# --------------------------------------------------------------------------------------
# Correction budget
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CorrectionBudget:
    """At most one visible correction per `gap` learner turns, with session and strain limits."""

    gaps: Mapping[str, int] = field(default_factory=lambda: {"light": 6, "normal": 4, "coach": 2})
    critical_gap: int = 2
    strain_threshold: float = 0.5
    strain_extra_gap: int = 2
    max_cards_per_session: int = 6
    warmup_turns: int = WARMUP_TURNS

    def effective_gap(self, state: LearnerState) -> int:
        gap = self.gaps[state.strictness]
        if state.strain >= self.strain_threshold:
            gap += self.strain_extra_gap
        return gap

    def can_show(self, state: LearnerState, *, critical: bool = False) -> bool:
        if critical:
            return state.turns_since_card >= self.critical_gap
        if state.turn_index <= self.warmup_turns:
            return False
        if state.cards_this_session >= self.max_cards_per_session:
            return False
        return state.turns_since_card >= self.effective_gap(state)


# --------------------------------------------------------------------------------------
# Which discourse states allow which kinds of intervention
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Permissions:
    clarify: bool
    card: bool
    recast: bool
    drill: bool
    defer: bool


FLOW_PERMISSIONS: Mapping[str, Permissions] = {
    #                         clarify card   recast drill  defer
    "normal":       Permissions(True,  True,  True,  True,  True),
    "storytelling": Permissions(True,  False, True,  False, True),   # let the story run
    "emotional":    Permissions(True,  False, False, False, True),   # respond to the person, not the grammar
    "frustrated":   Permissions(False, False, False, False, False),  # add no load at all
    "out_of_frame": Permissions(False, False, False, False, False),  # they stepped out; just answer them
}


# --------------------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Decision:
    action: str                                   # silent|recast|offer_card|queue_drill|clarify|defer
    finding: Finding | None = None                # the finding the action is about
    recast_also: bool = False                     # with offer_card: also recast silently in the reply
    drill_patterns: tuple[str, ...] = ()          # pattern_keys to queue for a mini-drill
    deferred: tuple[Finding, ...] = ()            # findings to carry into the end-of-scene recap
    reasons: tuple[str, ...] = ()
    scores: tuple[tuple[str, float], ...] = ()    # (pattern_key, priority), for logs

    @property
    def wants_recast(self) -> bool:
        return self.finding is not None and (self.action == "recast" or self.recast_also)


def priority(finding: Finding, prior_count: int, state: LearnerState) -> float:
    impact = finding.severity * CATEGORY_WEIGHT[finding.category]
    frequency = 1.0 + 0.5 * min(prior_count, 4)
    learnability = 1.0 if finding.teachable_now else 0.5
    load = 0.5 + 1.0 * state.strain + (0.3 if state.cefr == "B1" else 0.0)
    return round(impact * frequency * learnability - load, 3)


def decide(analysis: Analysis, budget: CorrectionBudget, state: LearnerState) -> Decision:
    perms = FLOW_PERMISSIONS[analysis.discourse_state]
    reasons: list[str] = [f"discourse_state={analysis.discourse_state}"]

    # 1. comprehension breakdown: a real person would ask
    if analysis.clarification_needed and perms.clarify:
        comp = next((f for f in analysis.findings if f.category == "comprehension"), None)
        reasons.append("comprehension breakdown: clarify in character")
        return Decision("clarify", comp, reasons=tuple(reasons))

    if not analysis.findings:
        return Decision("silent", reasons=tuple(reasons + ["no findings"]))

    scored = sorted(
        ((priority(f, state.pattern_counts.get(f.pattern_key, 0), state), f) for f in analysis.findings),
        key=lambda t: -t[0],
    )
    scores = tuple((f.pattern_key, s) for s, f in scored)
    top_score, top = scored[0]
    adjust = CEFR_THRESHOLD_ADJUST[state.cefr]
    card_thr, recast_thr = CARD_THRESHOLD + adjust, RECAST_THRESHOLD + adjust

    drill_keys = tuple(
        f.pattern_key
        for _, f in scored
        if state.pattern_counts.get(f.pattern_key, 0) + 1 >= DRILL_THRESHOLD
        and f.pattern_key not in state.drilled_recently
        and f.pattern_key not in state.queued_drills
    )
    if not perms.drill:
        drill_keys = ()

    critical = top.severity == 3 and top.category in CRITICAL_CATEGORIES
    action = "silent"
    recast_also = False
    primary = top
    can_recast = perms.recast and bool(top.recast_form)

    if critical:
        if perms.card and budget.can_show(state, critical=True):
            action, recast_also = "offer_card", can_recast
            reasons.append("critical pragmatic/register issue: card at reduced gap")
        elif perms.defer:
            action = "defer"
            reasons.append("critical issue but card not allowed now: defer to recap")
    elif top_score >= card_thr:
        if perms.card and budget.can_show(state):
            action, recast_also = "offer_card", can_recast
            reasons.append(f"priority {top_score} >= {card_thr}: card within budget")
        elif can_recast:
            action = "recast"
            reasons.append("card warranted but budget/flow closed: silent recast instead")
        elif perms.defer:
            action = "defer"
            reasons.append("card warranted but budget/flow closed: defer to recap")
    elif top_score >= recast_thr and can_recast:
        action = "recast"
        reasons.append(f"priority {top_score} >= {recast_thr}: silent recast")
    elif perms.defer and top.severity >= 2:
        action = "defer"
        reasons.append("worth remembering but not worth interrupting: defer to recap")
    else:
        reasons.append("below every threshold: log only")

    if drill_keys and action in ("silent", "defer"):
        action = "queue_drill"
        primary = next(f for _, f in scored if f.pattern_key == drill_keys[0])
        reasons.append(f"pattern {drill_keys[0]} reached {DRILL_THRESHOLD} occurrences: queue a mini-drill")
    elif drill_keys:
        reasons.append(f"also queue drill for {drill_keys[0]}")

    shown_key = primary.pattern_key if action not in ("silent",) else None
    deferred: tuple[Finding, ...] = ()
    if perms.defer:
        deferred = tuple(
            f for _, f in scored
            if f.severity >= 2 and (f.pattern_key != shown_key or action == "defer")
        )

    return Decision(
        action=action,
        finding=primary if action != "silent" else None,
        recast_also=recast_also,
        drill_patterns=drill_keys,
        deferred=deferred,
        reasons=tuple(reasons),
        scores=scores,
    )


def suggest_drill_mode(finding: Finding) -> str:
    """Which practice mode fits a recurring pattern best."""
    pat = (finding.l1_pattern or "").upper()
    key = finding.pattern_key.upper()
    if finding.category == "pragmatic" and ("DIRECTNESS" in pat or "SPEECH_ACT" in pat or "DIRECTNESS" in key):
        return "pragmatic_dilemma"
    if finding.category in ("register", "pragmatic") or pat in {"ADDRESS_TERM", "OVER_FORMAL", "RHETORICAL_INTENSITY"}:
        return "register_shift"
    if pat == "DISCOURSE_MARKER_GAP":
        return "think_aloud"
    return "phrase_workout"
