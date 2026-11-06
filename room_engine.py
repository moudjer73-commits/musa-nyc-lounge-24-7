"""Unified V4.0 Room, Tone & Scene Engine (Claude + ChatGPT + Grok + Meta AI)."""
from __future__ import annotations

import html
from typing import Any

from prompt_assembly import normalize_3d_config

MODES = ("casual", "diplomatic", "executive", "analytical")

ROOM_BADGES = {
    "casual": "🏠",
    "diplomatic": "🤝",
    "executive": "💼",
    "analytical": "🧠",
}

FLAVOR_BADGES = {
    "neutral": "",
    "warm": "💛",
    "witty": "😏",
}

ROOM_CARDS = {
    "casual": {
        "title": "🏠 CASUAL — Friends & Everyday Hangout",
        "world": "Kitchen table or coffee with a close friend. High trust, zero agenda.",
        "alex_voice": "Short, relaxed, elliptical ('Been there', 'Yeah, that's not gonna fly'). Phrasal verbs & contractions.",
        "judged_on": "Sounding like a real friend talking out loud—not a textbook or formal email. Fragments & spoken grammar are 100% natural.",
        "pivots": [
            "Yeah, that's not gonna fly.",
            "Honestly? Not really.",
            "That's on me.",
            "I think I'm gonna stay in tonight.",
        ],
    },
    "diplomatic": {
        "title": "🤝 DIPLOMATIC — Tactful Pushback & Delicate Moments",
        "world": "A delicate moment with a colleague, boss, or client where both the relationship and the truth matter.",
        "alex_voice": "Warm-professional, perceptive, firm but tactful. Uses bridges before a 'no' and frames issues around the situation, not the person.",
        "judged_on": "Acknowledging then pivoting ('I see where you're coming from, but...'), stating a reason + alternative. Flags blunt door-slams and robotic HR apologies.",
        "pivots": [
            "I like the direction, but...",
            "I'm with you on X. The part that worries me is Y.",
            "What would it take for this to work?",
            "I was leaning the other way, but...",
        ],
    },
    "executive": {
        "title": "💼 EXECUTIVE — Running the Room & Making the Call",
        "world": "Leading a meeting, pushing back on a bad plan, or reporting upward. Time is short; credibility and ownership are at stake.",
        "alex_voice": "Compressed, bottom-line first, plain verbs, zero corporate buzzwords. Probes for the call, the owner, and the deadline.",
        "judged_on": "Decision first, clear ownership ('I own this', 'That's on me'), concise trade-offs. Flags stacked hedges and fake MBA jargon ('synergy', 'circle back').",
        "pivots": [
            "Here's the constraint that kills it.",
            "I'm making the call.",
            "Bottom line me.",
            "That's not the priority.",
        ],
    },
    "analytical": {
        "title": "🧠 ANALYTICAL — Thinking Out Loud & Stress-Testing Ideas",
        "world": "Dissecting a complex claim, mechanism, or trade-off with a sharp intellectual equal.",
        "alex_voice": "Curious, exact, probes assumptions ('Walk me through the mechanism', 'Unless I'm missing a lever...').",
        "judged_on": "Spoken signposting, showing the causal mechanism, calibrating certainty. Flags unsupported flat verdicts and stiff essay connectors ('furthermore', 'moreover').",
        "pivots": [
            "Walk me through the mechanism.",
            "That premise only holds if...",
            "If we run it that way, we hit a wall on...",
            "Unless I'm missing a lever...",
        ],
    },
}

ALEX_ROOM_OPENERS = {
    "casual": {
        "soft": "Hey! Long day—I'm just taking it easy for a bit. How's your week going so far?",
        "balanced": "Yo, we're hitting that new spot tonight. You in, or are you staying in?",
        "direct": "Someone just cut you off in traffic and had the nerve to wave like it's fine. What comes out of your mouth?",
    },
    "diplomatic": {
        "soft": "Hey, I think we see the timeline on this a little differently, so I wanted to hear where you're coming from.",
        "balanced": "Hey, can you cover this weekend? I know it's super short notice, and we're short-handed.",
        "direct": "I think we should launch the new plan on Friday as-is. Unless you see a real reason to hold back?",
    },
    "executive": {
        "soft": "The team's been spinning on this decision all week. I need us to land on a call today—where do you stand?",
        "balanced": "We're past the deadline, and I need the headline. Where do things stand right now, and what's the recovery plan?",
        "direct": "I'm short on time, so bottom-line me: are we going with Plan A or Plan B, and who owns it?",
    },
    "analytical": {
        "soft": "I've been hearing people claim remote work kills productivity, but the evidence seems mixed. What's your read on it?",
        "balanced": "Everyone assumes the project flopped because of pricing, but I'm not seeing the causal link yet. What do you think actually drove it?",
        "direct": "Give me a claim you actually believe—or tell me why our last plan failed—and let's stress-test the mechanism out loud.",
    },
}


def format_alex_speaker_label(register_profile: str = "casual", flavor: str = "neutral") -> str:
    room, _, flv = normalize_3d_config(register_profile, "balanced", flavor)
    r_badge = ROOM_BADGES.get(room, "🏠")
    f_badge = FLAVOR_BADGES.get(flv, "")
    return f"Alex {r_badge}{f_badge}"


def render_world_card_html(
    register_profile: str = "casual",
    directness: str = "balanced",
    flavor: str = "neutral",
    coaching: str = "light",
) -> str:
    room, dir_clean, flv_clean = normalize_3d_config(register_profile, directness, flavor)
    card = ROOM_CARDS[room]
    r_badge = ROOM_BADGES[room]
    f_badge = f" {FLAVOR_BADGES[flv_clean]}" if FLAVOR_BADGES.get(flv_clean) else ""
    pivots_str = " · ".join(f"“{html.escape(p)}”" for p in card["pivots"][:3])
    return (
        f"🎛️ <b>{html.escape(card['title'])}</b>\n"
        f"• <b>Stance:</b> Flavor: <b>{html.escape(flv_clean.upper())}{f_badge}</b> | "
        f"Directness: <b>{html.escape(dir_clean.upper())}</b> | "
        f"Coaching: <b>{html.escape(coaching.upper())}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌎 <b>WORLD:</b> {html.escape(card['world'])}\n"
        f"🗣️ <b>ALEX ({r_badge}):</b> {html.escape(card['alex_voice'])}\n"
        f"🎯 <b>YOU'RE JUDGED ON:</b> {html.escape(card['judged_on'])}\n"
        f"🔑 <b>Spoken Pivots:</b> <i>{pivots_str}</i>"
    )


def get_alex_immediate_opener(
    register_profile: str = "casual",
    directness: str = "balanced",
    flavor: str = "neutral",
) -> str:
    room, dir_clean, flv_clean = normalize_3d_config(register_profile, directness, flavor)
    base = ALEX_ROOM_OPENERS.get(room, ALEX_ROOM_OPENERS["casual"]).get(dir_clean, ALEX_ROOM_OPENERS["casual"]["balanced"])
    if flv_clean == "warm" and room != "casual":
        return f"Hey—really glad we're talking this through. {base}"
    if flv_clean == "witty" and room == "casual":
        return "So... scale of 1 to 10, how much are we procrastinating right now, or did you actually get stuff done today?"
    return base
