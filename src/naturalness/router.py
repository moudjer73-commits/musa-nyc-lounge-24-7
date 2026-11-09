"""Zero-LLM Router: maps room/mode + user message to Situation profile, Cast, Moves, Anchors, and Dice."""
from __future__ import annotations
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "naturalness"

ROOM_TO_PROFILE: dict[str, str] = {
    "casual_friends": "friend_text",
    "friend_text": "friend_text",
    "friend_voice": "friend_voice",
    "work_peer": "work_peer",
    "work_up": "work_up",
    "academic_seminar": "academic_seminar",
    "office_hours": "office_hours",
    "service_counter": "service_counter",
    "phone_call": "phone_call",
    "conference_networking": "conference_networking",
    "casual_roommate": "friend_text",
    "social_networking": "conference_networking",
    "pro_peer_brainstorm": "work_peer",
    "pro_executive": "work_up",
    "intellectual_seminar": "academic_seminar",
    "intellectual_professor": "office_hours",
    "friend_text": "friend_text",
    "friend_voice": "friend_voice",
    "family_phone": "family_phone",
    "social_warm": "friend_voice",
    "professional_work": "work_peer",
    "work_peer": "work_peer",
    "work_up": "work_up",
    "intellectual_debate": "academic_seminar",
    "academic_seminar": "academic_seminar",
    "office_hours": "office_hours",
    "conference_networking": "conference_networking",
    "service_stranger": "service_stranger",
}


@dataclass
class RouteBundle:
    profile_id: str
    profile: dict[str, Any]
    cast_name: str
    cast: dict[str, Any]
    moves: list[dict[str, Any]]
    anchors: list[dict[str, Any]]
    dice: dict[str, Any]
    window_directives: list[str] = field(default_factory=list)


class NaturalnessRouter:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.data_dir = data_dir
        self.profiles: dict[str, Any] = json.loads((data_dir / "profiles.json").read_text(encoding="utf-8"))
        self.cast: dict[str, Any] = json.loads((data_dir / "cast.json").read_text(encoding="utf-8"))
        self.dice_cfg: dict[str, Any] = json.loads((data_dir / "dice.json").read_text(encoding="utf-8"))
        self.moves: list[dict[str, Any]] = json.loads((data_dir / "moves.json").read_text(encoding="utf-8"))
        self.anchors: list[dict[str, Any]] = [
            json.loads(line)
            for line in (data_dir / "anchors.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def select_moves(self, user_text: str, tenor: str, max_cards: int = 2) -> list[dict[str, Any]]:
        low = user_text.lower()
        scored: list[tuple[int, dict[str, Any]]] = []
        for card in self.moves:
            hits = sum(1 for kw in card.get("keywords", []) if kw in low)
            if tenor == "speaker_audience" and card.get("category") == "academic":
                hits += 1
            if hits > 0:
                scored.append((hits, card))
        scored.sort(key=lambda x: x[0], reverse=True)
        if scored:
            return [c for _, c in scored[:max_cards]]
        return [self.moves[0]]

    def select_anchors(
        self, tenor: str, mode: str, last_fn: str | None = None, count: int = 3, seed: int = 0
    ) -> list[dict[str, Any]]:
        pool = [a for a in self.anchors if a.get("tenor") == tenor and a.get("mode") == mode and a.get("fn") != last_fn]
        if len(pool) < count:
            pool = [a for a in self.anchors if a.get("tenor") == tenor and a.get("fn") != last_fn]
        if not pool:
            pool = list(self.anchors)
        rng = random.Random(seed)
        return rng.sample(pool, min(count, len(pool)))

    def roll_dice(
        self,
        user_id: int,
        turn_no: int,
        recent_dice_ids: list[str] | None = None,
        window_overrides: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        recent = set((recent_dice_ids or [])[-3:])
        overrides = window_overrides or {}
        directives: list[str] = []

        items = self.dice_cfg["items"]
        if overrides.get("force_short_or_detail"):
            candidates = [i for i in items if i["id"] in ("short", "tiny_detail")]
        elif overrides.get("force_no_question"):
            candidates = [i for i in items if i["id"] in ("short", "react", "tiny_detail", "plain")]
            directives.append("Do NOT end this reply with a question.")
        else:
            candidates = [
                i for i in items
                if i["id"] not in recent and not (i["id"] == "drift" and turn_no < 6)
            ] or list(items)

        if overrides.get("avoid_opener"):
            directives.append(f"Do NOT start your reply with '{overrides['avoid_opener']}'.")

        rng = random.Random(f"{user_id}:{turn_no}")
        weights = [int(i.get("w", 1)) for i in candidates]
        chosen = rng.choices(candidates, weights=weights, k=1)[0]
        return chosen, directives

    def route(
        self,
        user_id: int,
        turn_no: int,
        user_text: str,
        room: str = "friend_text",
        voice_mode: bool = False,
        recent_dice_ids: list[str] | None = None,
        last_fn: str | None = None,
        window_overrides: dict[str, Any] | None = None,
    ) -> RouteBundle:
        profile_id = ROOM_TO_PROFILE.get(room, "friend_text")
        if voice_mode and profile_id == "friend_text":
            profile_id = "friend_voice"
        profile = dict(self.profiles.get(profile_id, self.profiles["friend_text"]))
        cast_name = profile.get("default_cast", "Sam")
        cast_card = dict(self.cast.get(cast_name, self.cast["Sam"]))
        tenor = profile.get("tenor", "close_friend")
        mode = "spoken" if voice_mode else profile.get("mode", "text")

        moves = self.select_moves(user_text, tenor=tenor)
        anchors = self.select_anchors(tenor=tenor, mode=mode, last_fn=last_fn, count=3, seed=user_id + turn_no)
        dice, directives = self.roll_dice(user_id, turn_no, recent_dice_ids, window_overrides)
        return RouteBundle(
            profile_id=profile_id,
            profile=profile,
            cast_name=cast_name,
            cast=cast_card,
            moves=moves,
            anchors=anchors,
            dice=dice,
            window_directives=directives,
        )
