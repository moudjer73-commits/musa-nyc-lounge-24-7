"""V3.0 Seed Catalog & Subskills for Migration M004 (S0 Contracts, Section 4.4)."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from schemas_v3 import ContextFamily, LexicalStructureType

__all__ = [
    "CATALOG_VERSION",
    "SEED_SUBSKILLS",
    "SEED_PHRASES",
    "apply_m004_seed_catalog",
]

CATALOG_VERSION = "v3.0"

SEED_SUBSKILLS: tuple[dict[str, str], ...] = (
    {
        "subskill_id": "prag_soften_disagreement",
        "competency_id": "pragmatic_fluency",
        "name": "Softening Disagreement & Pushback",
        "description": "Pushing back on ideas or timelines in American English without sounding abrasive.",
        "catalog_version": CATALOG_VERSION,
    },
    {
        "subskill_id": "disc_frame_shift",
        "competency_id": "discourse_management",
        "name": "Framing & Redirecting Discussion",
        "description": "Steering a conversation back to the core issue or flagging a trade-off.",
        "catalog_version": CATALOG_VERSION,
    },
    {
        "subskill_id": "prag_align_expectations",
        "competency_id": "pragmatic_fluency",
        "name": "Setting & Aligning Expectations",
        "description": "Clarifying bandwidth, constraints, and realistic commitments.",
        "catalog_version": CATALOG_VERSION,
    },
    {
        "subskill_id": "lex_precision_stance",
        "competency_id": "lexical_precision",
        "name": "Expressing Nuanced Stance & Hedging",
        "description": "Signaling degree of certainty or tentative buy-in naturally.",
        "catalog_version": CATALOG_VERSION,
    },
)

SEED_PHRASES: tuple[dict[str, Any], ...] = (
    {
        "phrase_id": "push_back_softly",
        "canonical_form": "I see where you're coming from, but",
        "communicative_intention": "acknowledge the other person's point before introducing a counter-perspective",
        "construction_pattern": "I see where you're coming from, but + [clause]",
        "register_profiles": ["casual", "neutral"],
        "applicability": "core",
        "variety_tags": ["US"],
        "lexical_structure_type": LexicalStructureType.DISCOURSE_TEMPLATE.value,
        "context_families": [
            ContextFamily.WORKPLACE.value,
            ContextFamily.NEGOTIATION.value,
            ContextFamily.FRIENDSHIPS.value,
        ],
        "subskill_id": "prag_soften_disagreement",
        "arabic_gloss": "أتفهم وجهة نظرك، ولكن...",
        "acceptable_forms": [
            "I see where you're coming from, but",
            "I see where you are coming from, but",
            "I get where you're coming from, but",
            "I get where you are coming from, but",
        ],
        "meaning_prompt": "How do you acknowledge someone's point before politely pushing back?",
        "situation_prompt": "Your teammate suggests rushing a release, and you want to validate their urgency before explaining the risk.",
        "function_prompt": "Acknowledge the counterpart's perspective and pivot to a counter-argument.",
    },
    {
        "phrase_id": "flag_bandwidth_limit",
        "canonical_form": "I'm a bit stretched thin right now",
        "communicative_intention": "decline or negotiate extra workload by signaling limited capacity",
        "construction_pattern": "I'm (a bit / pretty) stretched thin (right now / this week)",
        "register_profiles": ["casual", "neutral"],
        "applicability": "situational",
        "variety_tags": ["US"],
        "lexical_structure_type": LexicalStructureType.SEMI_FIXED_FRAME.value,
        "context_families": [
            ContextFamily.WORKPLACE.value,
            ContextFamily.ACADEMIC.value,
            ContextFamily.NEGOTIATION.value,
        ],
        "subskill_id": "prag_align_expectations",
        "arabic_gloss": "وقتي وجهدي موزّعان على مهام كثيرة حالياً (لا أملك متسعاً إضافياً)",
        "acceptable_forms": [
            "I'm a bit stretched thin right now",
            "I am a bit stretched thin right now",
            "I'm stretched pretty thin right now",
            "I'm stretched thin this week",
        ],
        "meaning_prompt": "What natural American idiom tells a colleague you have too many commitments to take on more?",
        "situation_prompt": "A manager asks if you can take over a side project this week while your plate is already full.",
        "function_prompt": "Signal limited personal capacity without sounding uncooperative.",
    },
    {
        "phrase_id": "caveat_tentative_buyin",
        "canonical_form": "I'm on board in principle, provided that",
        "communicative_intention": "express conditional agreement while locking in a key requirement",
        "construction_pattern": "I'm on board in principle, provided (that) + [condition clause]",
        "register_profiles": ["neutral", "formal"],
        "applicability": "situational",
        "variety_tags": ["US"],
        "lexical_structure_type": LexicalStructureType.DISCOURSE_TEMPLATE.value,
        "context_families": [
            ContextFamily.NEGOTIATION.value,
            ContextFamily.WORKPLACE.value,
            ContextFamily.ACADEMIC.value,
        ],
        "subskill_id": "prag_align_expectations",
        "arabic_gloss": "أنا موافق من حيث المبدأ، شريطة أن...",
        "acceptable_forms": [
            "I'm on board in principle, provided that",
            "I am on board in principle, provided that",
            "I'm on board in principle, provided",
            "I'm on board in principle as long as",
        ],
        "meaning_prompt": "How do you express conditional buy-in to a proposal while attaching a clear requirement?",
        "situation_prompt": "You agree with the new workflow idea, but only if the deadline moves out by two days.",
        "function_prompt": "Grant principle approval contingent on a specific safeguard.",
    },
    {
        "phrase_id": "steer_back_to_core",
        "canonical_form": "Let's not lose sight of the main goal here",
        "communicative_intention": "redirect a side-tracked discussion back to the central objective",
        "construction_pattern": "Let's not lose sight of + [NP]",
        "register_profiles": ["neutral", "casual"],
        "applicability": "core",
        "variety_tags": ["US"],
        "lexical_structure_type": LexicalStructureType.SEMI_FIXED_FRAME.value,
        "context_families": [
            ContextFamily.WORKPLACE.value,
            ContextFamily.PRESENTATION.value,
            ContextFamily.CONFLICT.value,
        ],
        "subskill_id": "disc_frame_shift",
        "arabic_gloss": "دعنا لا نغفل عن الهدف الأساسي هنا",
        "acceptable_forms": [
            "Let's not lose sight of the main goal here",
            "Let's not lose sight of the bigger picture",
            "We shouldn't lose sight of the main goal",
        ],
        "meaning_prompt": "How do you bring a derailed meeting back to the central priority?",
        "situation_prompt": "The group has spent ten minutes arguing over minor formatting instead of the core launch plan.",
        "function_prompt": "Refocus participants on the primary objective.",
    },
    {
        "phrase_id": "hedge_initial_impression",
        "canonical_form": "Off the top of my head, I'd say",
        "communicative_intention": "offer a rough estimate or initial take without claiming exact figures",
        "construction_pattern": "Off the top of my head, I'd say + [estimate/clause]",
        "register_profiles": ["casual", "neutral"],
        "applicability": "core",
        "variety_tags": ["US"],
        "lexical_structure_type": LexicalStructureType.FIXED_CHUNK.value,
        "context_families": [
            ContextFamily.WORKPLACE.value,
            ContextFamily.FRIENDSHIPS.value,
            ContextFamily.ACADEMIC.value,
        ],
        "subskill_id": "lex_precision_stance",
        "arabic_gloss": "بشكل مبدئي غير دقيق (من الذاكرة الحاضرة)، أقول إن...",
        "acceptable_forms": [
            "Off the top of my head, I'd say",
            "Off the top of my head, I would say",
            "Off the top of my head",
        ],
        "meaning_prompt": "What fixed chunk signals that you are giving an approximate answer from memory?",
        "situation_prompt": "Someone asks you for the cost breakdown during a hallway chat before you have checked your notes.",
        "function_prompt": "Hedge an immediate estimate so you aren't held to exact numbers.",
    },
    {
        "phrase_id": "surface_hidden_tradeoff",
        "canonical_form": "The flip side of that is",
        "communicative_intention": "introduce a downside or unintended consequence of a proposed option",
        "construction_pattern": "The flip side (of that) is + [clause]",
        "register_profiles": ["casual", "neutral"],
        "applicability": "core",
        "variety_tags": ["US"],
        "lexical_structure_type": LexicalStructureType.DISCOURSE_TEMPLATE.value,
        "context_families": [
            ContextFamily.NEGOTIATION.value,
            ContextFamily.WORKPLACE.value,
            ContextFamily.SOCIAL_GROUP.value,
        ],
        "subskill_id": "disc_frame_shift",
        "arabic_gloss": "الوجه الآخر (أو الجانب المقابل) لهذا الخيار هو...",
        "acceptable_forms": [
            "The flip side of that is",
            "The flip side is that",
            "On the flip side,",
        ],
        "meaning_prompt": "How do you naturally highlight the downside or trade-off of an otherwise appealing idea?",
        "situation_prompt": "A friend points out that moving further out of town saves rent, and you want to mention the longer commute.",
        "function_prompt": "Contrast a benefit with its corresponding trade-off.",
    },
)


def apply_m004_seed_catalog(conn: sqlite3.Connection, now_iso: str) -> None:
    """Idempotently seeds competency_subskills and V3.0 phrase_catalog columns."""
    for sub in SEED_SUBSKILLS:
        conn.execute(
            """
            INSERT INTO competency_subskills (
                subskill_id, competency_id, name, description, catalog_version
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subskill_id) DO UPDATE SET
                competency_id = excluded.competency_id,
                name = excluded.name,
                description = excluded.description,
                catalog_version = excluded.catalog_version
            """,
            (
                sub["subskill_id"],
                sub["competency_id"],
                sub["name"],
                sub["description"],
                sub["catalog_version"],
            ),
        )

    existing_cols = {
        str(r[1]) for r in conn.execute("PRAGMA table_info(phrase_catalog)").fetchall()
    }
    for p in SEED_PHRASES:
        row_map: dict[str, Any] = {
            "phrase_id": p["phrase_id"],
            "canonical_form": p["canonical_form"],
            "communicative_intention": p["communicative_intention"],
            "construction_pattern": p["construction_pattern"],
            "register_profiles_json": json.dumps(p["register_profiles"], ensure_ascii=False),
            "applicability": p["applicability"],
            "variety_tags_json": json.dumps(p["variety_tags"], ensure_ascii=False),
            "catalog_version": CATALOG_VERSION,
            "lexical_structure_type": p["lexical_structure_type"],
            "context_families_json": json.dumps(p["context_families"], ensure_ascii=False),
            "subskill_id": p["subskill_id"],
            "arabic_gloss": p["arabic_gloss"],
            "acceptable_forms_json": json.dumps(p["acceptable_forms"], ensure_ascii=False),
            "meaning_prompt": p["meaning_prompt"],
            "situation_prompt": p["situation_prompt"],
            "function_prompt": p["function_prompt"],
        }
        # Support legacy table variants that also have phrase_text
        if "phrase_text" in existing_cols:
            row_map["phrase_text"] = p["canonical_form"]

        filtered = {k: v for k, v in row_map.items() if k in existing_cols}
        cols_sql = ", ".join(filtered.keys())
        vals_sql = ", ".join("?" for _ in filtered)
        update_sql = ", ".join(
            f"{k} = excluded.{k}" for k in filtered.keys() if k != "phrase_id"
        )
        conn.execute(
            f"INSERT INTO phrase_catalog ({cols_sql}) VALUES ({vals_sql}) "
            f"ON CONFLICT(phrase_id) DO UPDATE SET {update_sql}",
            tuple(filtered.values()),
        )
