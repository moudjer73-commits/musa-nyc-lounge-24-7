"""Podcast Pipeline Checks (Section 4) & Learner On-Demand Tools: Register Ladder & Situation Flip (Section 5)."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Any
from src.naturalness.guard import NaturalnessGuard


@dataclass
class PodcastCheckReport:
    passed: bool
    total_words: int
    total_turns: int
    short_turn_share: float
    max_turn_words: int
    missing_watchlist: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)


class PodcastPipelineValidator:
    """Implements Claude's Section 4 CHECKS (no LLM) and line_fixes applier."""

    def __init__(self, guard: NaturalnessGuard | None = None) -> None:
        self.guard = guard or NaturalnessGuard()

    @staticmethod
    def apply_critic_fixes(script_lines: list[str], critic_json: dict[str, Any]) -> list[str]:
        """Applies line_fixes from CALL 3 critic JSON without full rewrite."""
        out = list(script_lines)
        for fix in critic_json.get("line_fixes", []):
            idx = fix.get("line_index")
            replacement = fix.get("replacement", "").strip()
            if isinstance(idx, int) and 0 <= idx < len(out) and replacement:
                out[idx] = replacement
        return out

    def check_script(
        self,
        turns: list[dict[str, str]],
        watchlist: list[str] | None = None,
        min_words: int = 330,
        max_words: int = 420,
    ) -> PodcastCheckReport:
        fail_reasons: list[str] = []
        if not turns:
            return PodcastCheckReport(False, 0, 0, 0.0, 0, list(watchlist or []), ["empty_script"])

        turn_word_counts: list[int] = []
        full_text_parts: list[str] = []
        guard_hit_total = 0

        for t in turns:
            txt = t.get("text", "").strip()
            full_text_parts.append(txt)
            wc = len(txt.split())
            turn_word_counts.append(wc)
            res = self.guard.apply(txt, learner_used_emoji=False)
            guard_hit_total += len(res.guard_hits)
            if re.search(r"\[.*?\]|\(.*?laughs.*?\)|\(.*?sighs.*?\)", txt, flags=re.I):
                fail_reasons.append("stage_directions_present")

        full_text = " ".join(full_text_parts)
        full_low = full_text.lower()
        total_words = sum(turn_word_counts)
        total_turns = len(turns)
        short_turns = sum(1 for c in turn_word_counts if c <= 6)
        short_share = short_turns / total_turns
        max_turn = max(turn_word_counts)

        if not (min_words <= total_words <= max_words):
            fail_reasons.append(f"word_count_out_of_range:{total_words}")
        if short_share < 0.25:
            fail_reasons.append(f"short_turn_share_low:{short_share:.2f}")
        if max_turn > 70:
            fail_reasons.append(f"turn_over_70_words:{max_turn}")
        if "as you said" in full_low:
            fail_reasons.append("banned_phrase:as_you_said")
        if re.search(r"\b(in summary|to sum up|in conclusion)\b", full_low):
            fail_reasons.append("summary_ending_detected")
        if guard_hit_total > 0:
            fail_reasons.append(f"guard_hits_nonzero:{guard_hit_total}")

        missing_wl = [w for w in (watchlist or []) if w.lower() not in full_low]
        if missing_wl:
            fail_reasons.append("missing_watchlist_items")

        return PodcastCheckReport(
            passed=len(fail_reasons) == 0,
            total_words=total_words,
            total_turns=total_turns,
            short_turn_share=round(short_share, 3),
            max_turn_words=max_turn,
            missing_watchlist=missing_wl,
            fail_reasons=fail_reasons,
        )

    def prepare_turns_for_tts(self, turns: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {"speaker": t.get("speaker", "Maya"), "text": self.guard.prepare_for_tts(t.get("text", ""))}
            for t in turns
        ]


class LearnerLabTools:
    """Section 5 On-Demand Tools: Register Ladder & Flip Situation."""

    @staticmethod
    def build_register_ladder_prompt(intent_text: str) -> str:
        return (
            "Give the learner a 3-step American English Register Ladder for saying this naturally:\n"
            f'Learner intent: "{intent_text.strip()}"\n\n'
            "Format strictly in 3 short lines (no markdown bold, no intro):\n"
            "1. Close friend (text/casual): [natural phrase] -> Who/when: [1 short clause]\n"
            "2. Coworker (spoken work): [natural phrase] -> Who/when: [1 short clause]\n"
            "3. Formal / Boss / Seminar: [natural phrase] -> Who/when: [1 short clause]"
        )

    @staticmethod
    def build_flip_situation_prompt(original_message: str, target_profile_id: str) -> str:
        return (
            f"Rewrite this exact conversational move for the '{target_profile_id}' situation "
            "so the learner hears how the register shifts:\n"
            f'Original: "{original_message.strip()}"\n'
            "Return only the natural spoken/text line in that target situation (1-2 sentences max, plain text)."
        )
