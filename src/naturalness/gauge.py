"""Gauge Telemetry: logs bot turns, tracks 🎭 'feels artificial' flags, /heard phrases, and weekly stats.

Persistence: when a SQLite path is given (production: the shared lab.db, so the rows ride the encrypted snapshot
and survive every GitHub Actions restart) every write goes to nat_turn_log / nat_heard. Without a path the gauge
is purely in-memory (unit tests, notebooks).
"""
from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

WEEK_S = 7 * 24 * 3600


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
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path
        self.logs: list[TurnLogEntry] = []
        self.heard_notebook: list[dict[str, Any]] = []

    # ---- persistence helpers -------------------------------------------------------------------------------
    def _conn(self):
        from src.db.engine import get_connection

        return get_connection(self.db_path)

    def _db_write(self, sql: str, params: tuple) -> None:
        try:
            with self._conn() as conn:
                conn.execute(sql, params)
                conn.commit()
        except Exception:  # noqa: BLE001 - telemetry must never break a conversation
            import logging

            logging.getLogger(__name__).exception("gauge write failed")

    # ---- turns ---------------------------------------------------------------------------------------------
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
        words = text.split()
        first_word = words[0].lower().strip(".,!?") if words else ""
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
        if len(self.logs) > 2000:  # bounded memory on a 5-hour runner
            del self.logs[:500]
        if self.db_path:
            self._db_write(
                "INSERT INTO nat_turn_log (msg_id, user_id, ts, profile, dice_id, cards_json, n_words, "
                "ends_with_question, guard_hits_json, first_word, text, flagged, context_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,0,'[]')",
                (entry.msg_id, user_id, entry.ts, profile, dice_id, json.dumps(entry.cards), n_words,
                 int(ends_with_question), json.dumps(entry.guard_hits), first_word, text),
            )
        return entry

    def flag_artificial(self, msg_id: str, context_turns: list[str] | None = None, user_id: int | None = None) -> bool:
        ctx = list((context_turns or [])[-4:])
        hit = False
        for entry in reversed(self.logs):
            if entry.msg_id == str(msg_id) and (user_id is None or entry.user_id == user_id):
                entry.flagged = True
                entry.context_snapshot = ctx
                hit = True
                break
        if self.db_path:
            try:
                with self._conn() as conn:
                    sql = "UPDATE nat_turn_log SET flagged = 1, context_json = ? WHERE msg_id = ?"
                    params: list[Any] = [json.dumps(ctx), str(msg_id)]
                    if user_id is not None:
                        sql += " AND user_id = ?"
                        params.append(user_id)
                    hit = conn.execute(sql, tuple(params)).rowcount > 0 or hit
                    conn.commit()
            except Exception:  # noqa: BLE001
                pass
        return hit

    def count_turns(self, user_id: int) -> int:
        """Persisted turn counter: survives restarts, so the seeded dice never replay the same opening sequence."""
        if self.db_path:
            try:
                with self._conn() as conn:
                    row = conn.execute("SELECT COUNT(*) AS n FROM nat_turn_log WHERE user_id = ?", (user_id,)).fetchone()
                    return int(row["n"])
            except Exception:  # noqa: BLE001
                pass
        return sum(1 for e in self.logs if e.user_id == user_id)

    # ---- real-life notebook --------------------------------------------------------------------------------
    def record_heard(self, user_id: int, phrase: str, context_note: str = "") -> dict[str, Any]:
        item = {
            "user_id": user_id,
            "phrase": phrase.strip()[:300],
            "context_note": context_note.strip()[:300],
            "ts": time.time(),
        }
        self.heard_notebook.append(item)
        if self.db_path:
            self._db_write(
                "INSERT INTO nat_heard (user_id, phrase, context_note, ts) VALUES (?,?,?,?)",
                (user_id, item["phrase"], item["context_note"], item["ts"]),
            )
        return item

    def heard_for(self, user_id: int, limit: int = 10) -> list[str]:
        if self.db_path:
            try:
                with self._conn() as conn:
                    rows = conn.execute(
                        "SELECT phrase FROM nat_heard WHERE user_id = ? ORDER BY ts DESC LIMIT ?", (user_id, limit)
                    ).fetchall()
                    return [r["phrase"] for r in reversed(rows)]
            except Exception:  # noqa: BLE001
                pass
        return [h["phrase"] for h in self.heard_notebook if h["user_id"] == user_id][-limit:]

    # ---- report --------------------------------------------------------------------------------------------
    def _load_subset(self, profile_filter: str | None, user_id: int | None, since_ts: float) -> list[TurnLogEntry]:
        if self.db_path:
            try:
                sql = "SELECT * FROM nat_turn_log WHERE ts >= ?"
                params: list[Any] = [since_ts]
                if profile_filter:
                    sql += " AND profile = ?"
                    params.append(profile_filter)
                if user_id is not None:
                    sql += " AND user_id = ?"
                    params.append(user_id)
                with self._conn() as conn:
                    rows = conn.execute(sql, tuple(params)).fetchall()
                return [
                    TurnLogEntry(
                        msg_id=r["msg_id"], user_id=r["user_id"], profile=r["profile"], dice_id=r["dice_id"] or "",
                        cards=json.loads(r["cards_json"] or "[]"), n_words=r["n_words"],
                        ends_with_question=bool(r["ends_with_question"]),
                        guard_hits=json.loads(r["guard_hits_json"] or "[]"), first_word=r["first_word"] or "",
                        text=r["text"] or "", flagged=bool(r["flagged"]),
                        context_snapshot=json.loads(r["context_json"] or "[]"), ts=r["ts"],
                    )
                    for r in rows
                ]
            except Exception:  # noqa: BLE001
                pass
        return [
            e for e in self.logs
            if e.ts >= since_ts and (not profile_filter or e.profile == profile_filter)
            and (user_id is None or e.user_id == user_id)
        ]

    def weekly_report(
        self, profile_filter: str | None = None, user_id: int | None = None, since_s: float = WEEK_S
    ) -> dict[str, Any]:
        subset = self._load_subset(profile_filter, user_id, time.time() - since_s)
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

    def forget_user(self, user_id: int) -> None:
        self.logs = [e for e in self.logs if e.user_id != user_id]
        self.heard_notebook = [h for h in self.heard_notebook if h.get("user_id") != user_id]
