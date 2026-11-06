"""podcast.py (V4.0 Deep-Dive Edition): 3.5-to-5 Minute Personal Learning Podcast Generator (Alex & Maya)."""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import uuid
from typing import Any, Literal, Optional

import edge_tts
import requests
from pydantic import BaseModel, Field

import config
from config import TEMP_AUDIO_DIR
from database import DEFAULT_CURATED_PHRASES, EventSourcedDatabase

logger = logging.getLogger(__name__)

ALEX_VOICE = "en-US-AndrewMultilingualNeural"
MAYA_VOICE = "en-US-AvaMultilingualNeural"
PODCAST_VOICE_TAG = "v4_long_duet_alex_maya"

PodcastMode = Literal["recap", "briefing", "narrative"]


class PodcastTurn(BaseModel):
    speaker: Literal["Alex", "Maya"] = "Alex"
    line: str = Field(..., min_length=2, max_length=650)


class FeaturedPhraseNote(BaseModel):
    phrase: str
    how_used_en: str
    arabic_anchor_ar: Optional[str] = None


class PodcastEpisodeScript(BaseModel):
    title: str
    mode: PodcastMode = "recap"
    context_hook: str
    turns: list[PodcastTurn] = Field(..., min_length=14, max_length=30)
    featured_phrases: list[FeaturedPhraseNote] = Field(default_factory=list)


PODCAST_SYSTEM_PROMPT = """You write a natural, engaging back-and-forth dialogue between Alex and Maya, two late-20s/early-30s American friends, tailored for an advanced (B2+-C2) learner of casual American English.

Core Rules (Prompt 5 — Alex & Maya Banter):
1. Organically weave in the primary target chunk (and secondary targets from `target_items`) inside ordinary, everyday adult conversation — weekend plans, food, a small annoyance, roommates, neighbors, or texting a friend.
2. Bury The Chunk Rule: Each target chunk must appear naturally in the MIDDLE of a sentence, not as a forced punchline. The other speaker should just continue the conversation naturally without acting like a classroom teacher.
3. Zero Corporate or Meme Jargon: Never use corporate buzzwords (bandwidth, circle back, low-hanging fruit, move the needle, synergy, deep dive) or Gen Z meme slang.
4. Keep turns natural and conversational across the 14-18 turns, and end the way real conversations trail off mid-thought ("Yeah, I dunno, I'll... whatever, we'll figure it out. How would you say it?").
5. Never include stage directions like [laughs] or *sighs* inside `line` because `line` is fed directly to neural TTS.

Return ONLY valid JSON matching:
{
  "title": "Engaging everyday episode title",
  "mode": "recap|briefing|narrative",
  "context_hook": "1-2 sentence teaser describing the everyday scenario and spoken focus",
  "turns": [
    {"speaker": "Alex", "line": "1-3 natural spoken sentences..."},
    {"speaker": "Maya", "line": "1-3 natural spoken sentences..."}
  ],
  "featured_phrases": [
    {
      "phrase": "exact target phrase",
      "how_used_en": "Crisp explanation of the spoken American feel",
      "arabic_anchor_ar": "شرح وظيفي وتداولي دقيق بالعربية الفصحى (<= 160 حرفاً)"
    }
  ]
}"""


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def collect_learner_podcast_material(db: EventSourcedDatabase, learner_id: int, mode: PodcastMode) -> list[dict[str, str]]:
    """Gather up to 4 real upgrades, due review items, and recent learner context."""
    items: list[dict[str, str]] = []
    with db.read() as conn:
        rows = conn.execute(
            """
            SELECT original_span, alternative, kind, explanation_en, explanation_ar
            FROM feedback_item_projection
            WHERE learner_id = ?
            ORDER BY delivered_at_utc DESC, diagnostic_id DESC
            LIMIT 5
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
                        "kind": str(r["kind"] or "pragmatics"),
                        "note_en": str(r["explanation_en"] or "Natural American phrasing in context."),
                        "note_ar": str(r["explanation_ar"] or "صياغة أمريكية طبيعية تحافظ على القصد التداولي."),
                    }
                )

    if len(items) < 4:
        try:
            cand_ids = db.get_spontaneous_candidates(learner_id, "casual")
        except Exception:
            cand_ids = []
        for pid in cand_ids[:5]:
            entry = db.get_phrase_entry(str(pid))
            if entry and not any(x["phrase"].lower() == entry.canonical_form.lower() for x in items):
                items.append(
                    {
                        "phrase": entry.canonical_form,
                        "avoid_span": "",
                        "kind": "idiomatic_chunk",
                        "note_en": f"High-leverage spoken move used to {entry.communicative_intention.replace('_', ' ')}.",
                        "note_ar": "عبارة محورية من جدول المراجعة النشط لتعزيز الطلاقة التلقائية.",
                    }
                )

    if len(items) < 4:
        for p in DEFAULT_CURATED_PHRASES[:8]:
            phrase = str(p.get("canonical_form") if isinstance(p, dict) else (p[1] if len(p) > 1 else p[0])).strip()
            intent = str(
                p.get("communicative_intention", "natural flow")
                if isinstance(p, dict)
                else (p[2] if len(p) > 2 else "natural flow")
            ).replace("_", " ")
            if phrase and not any(x["phrase"].lower() == phrase.lower() for x in items):
                items.append(
                    {
                        "phrase": phrase,
                        "avoid_span": "",
                        "kind": "collocation",
                        "note_en": f"High-leverage spoken move for {intent}.",
                        "note_ar": "تعبير أمريكي أصيل شائع في المواقف اليومية والمهنية.",
                    }
                )
            if len(items) >= 4:
                break
    return items[:4]


def build_deterministic_podcast(mode: PodcastMode, material: list[dict[str, str]]) -> PodcastEpisodeScript:
    """Full 20-turn (~650-word, ~4-minute) deep-dive episode built directly from the learner's real phrases."""
    m1 = material[0] if len(material) > 0 else {"phrase": "push back on that", "avoid_span": "refuse that", "note_en": "Firm diplomatic disagreement.", "note_ar": "اعتراض مهني لبق."}
    m2 = material[1] if len(material) > 1 else {"phrase": "on the same page", "avoid_span": "agreeing together", "note_en": "Shared operational alignment.", "note_ar": "توافق في الرؤية وخطة العمل."}
    m3 = material[2] if len(material) > 2 else {"phrase": "get on my nerves", "avoid_span": "bother me a lot", "note_en": "High-frequency spoken chunk for everyday annoyance.", "note_ar": "تعبير محكي شائع للتعبير عن الانزعاج اليومي."}
    m4 = material[3] if len(material) > 3 else {"phrase": "make a call on", "avoid_span": "take a decision", "note_en": "Decide decisively in real time.", "note_ar": "اتخاذ قرار حاسم في موقف عملي."}

    p1, p2, p3, p4 = m1["phrase"], m2["phrase"], m3["phrase"], m4["phrase"]
    av1 = m1.get("avoid_span") or "the literal phrasing"

    titles = {
        "recap": "NYC Deep-Dive: Mastering Pragmatic Pushback & Real-Time Flow",
        "briefing": "Morning Studio Briefing: High-Leverage Chunks for High-Stakes Days",
        "narrative": "Inside the Room: Navigating Workload & Strategy Like a Native",
    }
    hooks = {
        "recap": "A full 3-part studio breakdown of your recent conversation shifts, register pivots, and collocation traps.",
        "briefing": "Alex and Maya run through a 4-minute deep dive into your active review targets across casual and executive settings.",
        "narrative": "First, hear your target moves inside a live project standoff; then unpack exactly why each phrase changes your status in the room.",
    }

    turns = [
        PodcastTurn(
            speaker="Maya",
            line="Welcome back to the Spoken English Lab studio. Alex, you walked in here five minutes ago looking like you just survived a three-hour budget meeting. What happened upstairs?",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"Honestly, Maya, you're not far off. Operations tried to dump two extra client migrations onto my team's plate for Friday, and I finally had to {p1} right there in the room.",
        ),
        PodcastTurn(
            speaker="Maya",
            line="Wait, seriously? Usually you try to absorb the extra work and figure it out later. What made you draw a hard line this time?",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"Because I'm always dealing with these last-minute scope surprises, and it's burning people out. I told the director, look, we simply don't have the {p3} to take this on without dropping our core deliverables.",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"That is such a clean way to frame it. And once you put it in terms of capacity, did they actually get {p2}, or did they keep pressing?",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"At first there was a little friction, sure. Folks at the top hate hearing no. But I asked leadership to {p4} which project they wanted us to pause if this new one was truly urgent.",
        ),
        PodcastTurn(
            speaker="Maya",
            line="Oh, that is a textbook executive pivot. The moment you ask a manager to choose which priority gets cut, the fake urgency disappears real fast.",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"Exactly. And let's pause right here and unpack what just happened linguistically, because our listener has been working on these exact moves—especially around '{p1}'.",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"Let's zoom in on that first one. In our recent session notes, the original temptation was around '{av1}', whereas native speakers naturally reach for '{p1}'. Why does that shift matter so much to an American ear?",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"It comes down to register and emotional control. {m1.get('note_en', '')} When you use '{p1}', you sound composed and collaborative rather than blunt or translated word-for-word.",
        ),
        PodcastTurn(
            speaker="Maya",
            line="Spot on. And notice another subtle grammar point we talked about earlier: when someone is annoyed about a recurring burden, Americans love using 'always' with the continuous '-ing' form—like 'I'm always dealing with' or 'I'm always having to fix'.",
        ),
        PodcastTurn(
            speaker="Alex",
            line="Right! If a textbook tells you to flatten that into simple present—like 'I always have tough work'—it completely kills the annoyance you're trying to express. The real native trick is keeping the continuous tense, but pairing it with 'having to do' or 'getting stuck with'.",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"That distinction is huge at the C1 and C2 level. Now let's look at the second anchor we wove into the story: '{p2}'. Where do people usually slip up when trying to express shared alignment?",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"A lot of smart speakers overcomplicate it with formal written clauses like 'we must ensure our perspectives coincide'. In spoken American English, whether you're in a coffee shop or a boardroom, you just say 'let's make sure we're {p2}'.",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"And it pairs so naturally with verbs like 'get' and 'stay'—you 'get {p2}' before a launch, and you 'stay {p2}' when things get chaotic. What about '{p3}'?",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"'{p3}' is one of those effortless high-status words. Instead of saying 'I am very busy and tired', saying 'I don't have the {p3} for that right now' sounds objective, professional, and impossible to argue with.",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"And then you closed the loop with '{p4}'. Instead of the classic Arabic light-verb translation 'take a decision', Americans 'make a decision' or, even more idiomatically in fast speech, '{p4}'.",
        ),
        PodcastTurn(
            speaker="Alex",
            line="Let's run a quick sixty-second pivot so you can hear how these same moves sound outside the office—say, planning a complicated weekend trip with friends who keep changing the plan.",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"Ooh, let's do it. Imagine a friend wants to add a four-hour detour on Sunday night. In a warm, witty register, you'd say: 'Look, I love the ambition, but I have zero {p3} for a four-hour drive on a Sunday night.'",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"And if they keep insisting, you follow up with: 'I'm gonna have to {p1} a little bit—let's get {p2} about the route now so we aren't arguing at the gas station.'",
        ),
        PodcastTurn(
            speaker="Maya",
            line=f"See how flexible that is? Same core chunks, completely different social vibe. Before we wrap up, here is your spoken challenge right after this audio finishes.",
        ),
        PodcastTurn(
            speaker="Alex",
            line=f"Jump back into the chat with me or open Pragmatic Replay, and give me two sentences about a real pressure point in your week using '{p1}' or '{p3}'. Don't overthink it—just let it fly!",
        ),
    ]

    featured = [
        FeaturedPhraseNote(
            phrase=m["phrase"],
            how_used_en=m.get("note_en") or "Anchored across both workplace negotiation and casual problem-solving.",
            arabic_anchor_ar=m.get("note_ar") or "توظيف تداولي طبيعي للعبارة في السياقين المهني واليومي.",
        )
        for m in material[:4]
    ]
    return PodcastEpisodeScript(
        title=titles.get(mode, titles["recap"]),
        mode=mode,
        context_hook=hooks.get(mode, hooks["recap"]),
        turns=turns,
        featured_phrases=featured,
    )


def _call_gemini_long_podcast(user_payload: str, mode: PodcastMode) -> Optional[PodcastEpisodeScript]:
    """Dedicated high-token Gemini call (maxOutputTokens=4096) so 20-turn scripts never truncate."""
    keys = list(getattr(config, "GEMINI_KEYS_LIST", []) or [])
    models = list(getattr(config, "GEMINI_MODEL_CHAIN", []) or ["gemini-3.5-flash", "gemini-3-flash-preview", "gemini-3.1-flash-lite"])
    if not keys:
        return None

    for model in models[:3]:
        for key in keys[:2]:
            clean_key = re.sub(r"[^A-Za-z0-9._\-]", "", str(key).strip())
            if len(clean_key) < 15:
                continue
            url = "https" + f"://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={clean_key}"
            body = {
                "systemInstruction": {"parts": [{"text": PODCAST_SYSTEM_PROMPT}]},
                "contents": [{"role": "user", "parts": [{"text": user_payload}]}],
                "generationConfig": {
                    "temperature": 0.65,
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                    "thinkingConfig": {"thinkingBudget": 0},
                },
            }
            try:
                resp = requests.post(url, json=body, timeout=32)
                if resp.status_code == 400 and "thinking" in resp.text.lower():
                    body["generationConfig"].pop("thinkingConfig", None)
                    resp = requests.post(url, json=body, timeout=32)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                candidates = data.get("candidates") or []
                if not candidates:
                    continue
                parts = candidates[0].get("content", {}).get("parts") or []
                raw_text = "".join(str(p.get("text", "")) for p in parts).strip()
                if raw_text.startswith("```"):
                    raw_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.DOTALL).strip()
                parsed_dict = json.loads(raw_text)
                parsed_dict["mode"] = mode
                ep = PodcastEpisodeScript.model_validate(parsed_dict)
                total_words = sum(len(t.line.split()) for t in ep.turns)
                if len(ep.turns) >= 14 and total_words >= 420:
                    return ep
            except Exception as err:
                logger.warning("Long podcast generation attempt on %s failed: %s", model, err)
                continue
    return None


def generate_podcast_episode(db: EventSourcedDatabase, ai: Any, learner_id: int, mode: PodcastMode = "recap") -> PodcastEpisodeScript:
    material = collect_learner_podcast_material(db, learner_id, mode)
    user_payload = json.dumps(
        {
            "episode_mode": mode,
            "target_word_count": "650 to 850 words (18 to 24 dialogue turns, ~4 minutes spoken duration)",
            "target_items": material,
        },
        ensure_ascii=False,
    )

    if ai is not None:
        ep = _call_gemini_long_podcast(user_payload, mode)
        if ep is not None:
            return ep

    return build_deterministic_podcast(mode, material)


def episode_script_hash(episode: PodcastEpisodeScript) -> str:
    joined = "|".join(f"{t.speaker}:{t.line}" for t in episode.turns)
    return hashlib.sha256(f"v4longpod:{episode.mode}:{joined}".encode("utf-8")).hexdigest()[:32]


async def _synthesize_single_turn(idx: int, turn: PodcastTurn, sem: asyncio.Semaphore) -> tuple[int, bytes]:
    voice = ALEX_VOICE if turn.speaker == "Alex" else MAYA_VOICE
    buf = bytearray()
    async with sem:
        comm = edge_tts.Communicate(text=turn.line.strip(), voice=voice)
        async for chunk in comm.stream():
            if chunk.get("type") == "audio" and chunk.get("data"):
                buf.extend(chunk["data"])
    return idx, bytes(buf)


async def _synthesize_duet_async(turns: list[PodcastTurn], out_path: str) -> None:
    sem = asyncio.Semaphore(6)
    tasks = [_synthesize_single_turn(idx, turn, sem) for idx, turn in enumerate(turns)]
    results = await asyncio.gather(*tasks)
    results.sort(key=lambda x: x[0])
    with open(out_path, "wb") as out_f:
        for _, audio_bytes in results:
            out_f.write(audio_bytes)


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
        "recap": "🎙️ Session Recap Deep-Dive",
        "briefing": "☕ Daily Briefing Podcast",
        "narrative": "🎬 Context Transfer Episode",
    }
    total_words = sum(len(t.line.split()) for t in episode.turns)
    est_mins = max(3, round(total_words / 160))
    lines = [
        f"<b>{_e(mode_labels.get(episode.mode, '🎙️ Personal Podcast'))}: {_e(episode.title)}</b>",
        f"<i>{_e(episode.context_hook)}</i>",
        f"⏱️ <b>Length:</b> ~{est_mins} min ({len(episode.turns)} turns · {total_words} words)",
        "",
        "🎯 <b>Featured Moves in This Episode:</b>",
    ]
    for fp in episode.featured_phrases[:4]:
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
        lines.append("<i>🎧 Listen to the full multi-part episode below, then tap <b>📜 Show Transcript</b> to read along.</i>")

    full_html = "\n".join(lines)
    if len(full_html) > 3950:
        # Keep valid HTML within Telegram's 4096-character limit
        trimmed_lines = []
        acc = 0
        for ln in lines:
            if acc + len(ln) + 1 > 3850:
                trimmed_lines.append("<i>... (Transcript continues in audio)</i>")
                break
            trimmed_lines.append(ln)
            acc += len(ln) + 1
        return "\n".join(trimmed_lines)
    return full_html
