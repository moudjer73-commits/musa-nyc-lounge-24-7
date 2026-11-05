"""podcast.py (V4.0): Personal Learning Podcast Generator (Alex & Maya)."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import uuid
from typing import Any, Literal, Optional

import edge_tts
from pydantic import BaseModel, Field

from config import TEMP_AUDIO_DIR
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase

logger = logging.getLogger(__name__)

ALEX_VOICE = "en-US-AndrewMultilingualNeural"
MAYA_VOICE = "en-US-AvaMultilingualNeural"
PODCAST_VOICE_TAG = "v4_duet_alex_maya"

PodcastMode = Literal["recap", "briefing", "narrative"]


class PodcastTurn(BaseModel):
    speaker: Literal["Alex", "Maya"] = "Alex"
    line: str = Field(..., min_length=2, max_length=320)


class FeaturedPhraseNote(BaseModel):
    phrase: str
    how_used_en: str
    arabic_anchor_ar: Optional[str] = None


class PodcastEpisodeScript(BaseModel):
    title: str
    mode: PodcastMode = "recap"
    context_hook: str
    turns: list[PodcastTurn] = Field(..., min_length=4, max_length=12)
    featured_phrases: list[FeaturedPhraseNote] = Field(default_factory=list)


PODCAST_SYSTEM_PROMPT = """You write brisk, natural 2-host American English mini-podcast episodes (hosts: Alex and Maya) for an advanced (B2+-C2) L1-Arabic learner.
Rules:
1. Sound like two sharp NYC colleagues grabbing coffee—use natural contractions, spoken rhythm, and authentic reactions. NEVER sound like an ESL textbook or say "Today we will learn".
2. Naturally weave the provided `target_items` into the conversation so the learner hears how native Americans deploy them in real context.
3. Keep the dialogue between 6 and 8 turns total (each turn 1-2 crisp spoken sentences, <= 32 words).
4. Return ONLY valid JSON matching:
{
  "title": "Short episode title",
  "mode": "recap|briefing|narrative",
  "context_hook": "1-sentence setting description",
  "turns": [
    {"speaker": "Alex", "line": "..."},
    {"speaker": "Maya", "line": "..."}
  ],
  "featured_phrases": [
    {
      "phrase": "exact target phrase",
      "how_used_en": "1 crisp sentence on the pragmatic punch of this phrase in the dialogue",
      "arabic_anchor_ar": "شرح وظيفي موجز بالعربية الفصحى (<= 140 حرفاً)"
    }
  ]
}"""


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def collect_learner_podcast_material(db: EventSourcedDatabase, learner_id: int, mode: PodcastMode) -> list[dict[str, str]]:
    """Gather real upgrades, due review items, or catalog phrases for this learner."""
    items: list[dict[str, str]] = []
    with db.read() as conn:
        if mode in ("recap", "narrative"):
            rows = conn.execute(
                """
                SELECT original_span, alternative, explanation_en, explanation_ar
                FROM feedback_item_projection
                WHERE learner_id = ?
                ORDER BY delivered_at_utc DESC, diagnostic_id DESC
                LIMIT 4
                """,
                (learner_id,),
            ).fetchall()
            for r in rows:
                alt = str(r["alternative"] or "").strip()
                if alt and not any(x["phrase"].lower() == alt.lower() for x in items):
                    items.append(
                        {
                            "phrase": alt,
                            "avoid_span": str(r["original_span"] or ""),
                            "note_en": str(r["explanation_en"] or "Natural American phrasing in context."),
                            "note_ar": str(r["explanation_ar"] or "صياغة أمريكية طبيعية في السياق المحكي."),
                        }
                    )

    if len(items) < 3:
        try:
            cand_ids = db.get_spontaneous_candidates(learner_id, "casual")
        except Exception:
            cand_ids = []
        for pid in cand_ids[:4]:
            entry = db.get_phrase_entry(str(pid))
            if entry and not any(x["phrase"].lower() == entry.canonical_form.lower() for x in items):
                items.append(
                    {
                        "phrase": entry.canonical_form,
                        "avoid_span": "",
                        "note_en": f"Used to {entry.communicative_intention.replace('_', ' ')}.",
                        "note_ar": "عبارة محورية من جدول المراجعة النشط لتعزيز الطلاقة التلقائية.",
                    }
                )

    if len(items) < 3:
        for p in DEFAULT_CURATED_PHRASES[:6]:
            phrase = str(p.get("canonical_form") if isinstance(p, dict) else (p[1] if len(p) > 1 else p[0])).strip()
            intent = str(p.get("communicative_intention", "natural flow") if isinstance(p, dict) else (p[2] if len(p) > 2 else "natural flow")).replace("_", " ")
            if phrase and not any(x["phrase"].lower() == phrase.lower() for x in items):
                items.append(
                    {
                        "phrase": phrase,
                        "avoid_span": "",
                        "note_en": f"High-leverage spoken move for {intent}.",
                        "note_ar": "تعبير أمريكي أصيل شائع في المواقف اليومية والمهنية.",
                    }
                )
            if len(items) >= 3:
                break
    return items[:3]


def build_deterministic_podcast(mode: PodcastMode, material: list[dict[str, str]]) -> PodcastEpisodeScript:
    """Zero-failure fallback episode built directly from the learner's real phrases."""
    p1 = material[0]["phrase"] if len(material) > 0 else "push back on that"
    p2 = material[1]["phrase"] if len(material) > 1 else "on the same page"
    p3 = material[2]["phrase"] if len(material) > 2 else "bandwidth"

    titles = {
        "recap": "NYC Coffee Debrief: Sharp Moves from Your Scene",
        "briefing": "Morning Commute Briefing: High-Leverage Spoken Moves",
        "narrative": "Inside the Studio: High-Stakes Project Pushback",
    }
    hooks = {
        "recap": "Alex and Maya unpack the exact phrasing shifts from your recent conversation.",
        "briefing": "A fast-paced morning catch-up weaving your active review phrases into natural flow.",
        "narrative": "Alex and Maya navigate a tight deadline dispute using your target expressions.",
    }
    turns = [
        PodcastTurn(speaker="Maya", line="Morning, Alex. You look like you've been putting out fires since seven."),
        PodcastTurn(speaker="Alex", line=f"Honestly, I'm always dealing with last-minute scope changes, so I finally had to {p1}."),
        PodcastTurn(speaker="Maya", line="Good for you. If you don't draw a line early, people just assume you have endless time."),
        PodcastTurn(speaker="Alex", line=f"Exactly. I told the team we need to get {p2} before we commit to another sprint."),
        PodcastTurn(speaker="Maya", line=f"How did they take it when you brought up the {p3} issue?"),
        PodcastTurn(speaker="Alex", line="Way better than I expected. Being direct without sounding defensive really shifts the room."),
    ]
    featured = [
        FeaturedPhraseNote(
            phrase=m["phrase"],
            how_used_en=m.get("note_en") or "Anchored naturally inside a spoken turn.",
            arabic_anchor_ar=m.get("note_ar") or "توظيف طبيعي للعبارة في سياق الحديث الأمريكي.",
        )
        for m in material[:3]
    ]
    return PodcastEpisodeScript(
        title=titles.get(mode, titles["recap"]),
        mode=mode,
        context_hook=hooks.get(mode, hooks["recap"]),
        turns=turns,
        featured_phrases=featured,
    )


def generate_podcast_episode(db: EventSourcedDatabase, ai: Any, learner_id: int, mode: PodcastMode = "recap") -> PodcastEpisodeScript:
    material = collect_learner_podcast_material(db, learner_id, mode)
    user_payload = json.dumps(
        {
            "episode_mode": mode,
            "target_items": material,
        },
        ensure_ascii=False,
    )

    def _validate(raw: dict[str, Any]) -> PodcastEpisodeScript:
        raw["mode"] = mode
        return PodcastEpisodeScript.model_validate(raw)

    if ai is not None and hasattr(ai, "_call_json"):
        try:
            res = ai._call_json(
                "debrief",
                PODCAST_SYSTEM_PROMPT,
                user_payload,
                _validate,
                deadline_s=18.0,
                temperature=0.6,
            )
            return res.parsed
        except Exception as err:
            logger.warning("Podcast LLM generation fell back to deterministic script: %s", err)
    return build_deterministic_podcast(mode, material)


def episode_script_hash(episode: PodcastEpisodeScript) -> str:
    joined = "|".join(f"{t.speaker}:{t.line}" for t in episode.turns)
    return hashlib.sha256(f"v4pod:{episode.mode}:{joined}".encode("utf-8")).hexdigest()[:32]


async def _synthesize_duet_async(turns: list[PodcastTurn], out_path: str) -> None:
    with open(out_path, "wb") as out_f:
        for turn in turns:
            voice = ALEX_VOICE if turn.speaker == "Alex" else MAYA_VOICE
            comm = edge_tts.Communicate(text=turn.line.strip(), voice=voice)
            async for chunk in comm.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    out_f.write(chunk["data"])


def synthesize_podcast_mp3(episode: PodcastEpisodeScript) -> str:
    os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)
    out_path = os.path.join(TEMP_AUDIO_DIR, f"podcast_{uuid.uuid4().hex[:10]}.mp3")
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_synthesize_duet_async(episode.turns, out_path))
    finally:
        loop.close()
    return out_path


def render_podcast_card_html(episode: PodcastEpisodeScript, *, show_transcript: bool = False, with_arabic: bool = True) -> str:
    mode_labels = {
        "recap": "🎙️ Session Recap Podcast",
        "briefing": "☕ Daily Briefing Podcast",
        "narrative": "🎬 Context Transfer Episode",
    }
    lines = [
        f"<b>{_e(mode_labels.get(episode.mode, '🎙️ Personal Podcast'))}: {_e(episode.title)}</b>",
        f"<i>{_e(episode.context_hook)}</i>",
        "",
        "🎯 <b>Featured Moves in This Episode:</b>",
    ]
    for fp in episode.featured_phrases[:3]:
        lines.append(f"• <b>“{_e(fp.phrase)}”</b> — {_e(fp.how_used_en)}")
        if with_arabic and fp.arabic_anchor_ar:
            lines.append(f"  🇦🇪 <tg-spoiler>{_e(fp.arabic_anchor_ar)}</tg-spoiler>")

    if show_transcript:
        lines.append("")
        lines.append("📜 <b>Full Episode Transcript (Alex &amp; Maya):</b>")
        for t in episode.turns:
            icon = "🗣️" if t.speaker == "Alex" else "💬"
            lines.append(f"{icon} <b>{_e(t.speaker)}:</b> {_e(t.line)}")
    else:
        lines.append("")
        lines.append("<i>🎧 Listen to the audio first, then tap <b>📜 Transcript</b> if you want to inspect every line.</i>")
    return "\n".join(lines)
