"""Gauge Telemetry: logs bot turns, tracks 🎭 'feels artificial' flags, /heard phrases, and weekly stats."""
from __future__ import annotations
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TurnLogEntry:
    msg_id: str
    user_id: int
    profile: str
    dice_id: str
    cards: list[str]
    n_words: int
    ends_with_question: bool
    guard_hits: list[str]
    first_word: str
    text: str
    flagged: bool = False
    context_snapshot: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)


class NaturalnessGauge:
    def __init__(self) -> None:
        self.logs: list[TurnLogEntry] = []
        self.heard_notebook: list[dict[str, Any]] = []

    def log_turn(
        self,
        msg_id: str,
        user_id: int,
        profile: str,
        dice_id: str,
        cards: list[str],
        text: str,
        n_words: int,
        ends_with_question: bool,
        guard_hits: list[str],
    ) -> TurnLogEntry:
        first_word = text.split()[0].lower().strip(".,!?") if text.split() else ""
        entry = TurnLogEntry(
            msg_id=str(msg_id),
            user_id=user_id,
            profile=profile,
            dice_id=dice_id,
            cards=list(cards),
            n_words=n_words,
            ends_with_question=ends_with_question,
            guard_hits=list(guard_hits),
            first_word=first_word,
            text=text,
        )
        self.logs.append(entry)
        return entry

    def flag_artificial(self, msg_id: str, context_turns: list[str] | None = None) -> bool:
        for entry in reversed(self.logs):
            if entry.msg_id == str(msg_id):
                entry.flagged = True
                entry.context_snapshot = list((context_turns or [])[-4:])
                return True
        return False

    def record_heard(self, user_id: int, phrase: str, context_note: str = "") -> dict[str, Any]:
        item = {
            "user_id": user_id,
            "phrase": phrase.strip(),
            "context_note": context_note.strip(),
            "ts": time.time(),
        }
        self.heard_notebook.append(item)
        return item

    def weekly_report(self, profile_filter: str | None = None) -> dict[str, Any]:
        subset = [e for e in self.logs if not profile_filter or e.profile == profile_filter]
        if not subset:
            return {"total_messages": 0}

        total = len(subset)
        word_counts = [e.n_words for e in subset]
        q_share = sum(1 for e in subset if e.ends_with_question) / total
        short_share = sum(1 for e in subset if e.n_words <= 5) / total
        guard_hit_msgs = sum(1 for e in subset if e.guard_hits) / total
        flagged_msgs = [asdict(e) for e in subset if e.flagged]
        openers = Counter(e.first_word for e in subset if e.first_word).most_common(5)
        hit_types = Counter(h for e in subset for h in e.guard_hits).most_common(10)

        return {
            "total_messages": total,
            "question_share": round(q_share, 3),
            "median_words": statistics.median(word_counts),
            "short_reply_share_le_5": round(short_share, 3),
            "guard_hit_rate": round(guard_hit_msgs, 3),
            "flagged_rate": round(len(flagged_msgs) / total, 3),
            "top_openers": openers,
            "guard_hits_by_type": hit_types,
            "top_flagged_messages": flagged_msgs[-10:],
            "meets_friend_text_targets": (
                q_share <= 0.35 and statistics.median(word_counts) <= 12 and short_share >= 0.20
            ),
        }
