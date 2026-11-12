import asyncio, json, random, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pydantic import BaseModel

from src.config import ECHO_TWIN_ENABLED
from src.db.repo import HABIT_LABELS, TwinRepository
from src.llm.client import GeminiClient
from src.twin.calques import detect_code_switch, is_repair_request, scan_transfers
from src.twin.corpus_guard import (
    REGISTER_CORPUS_RULES,
    SPOKEN_US_DNA,
    has_contrived_buzz,
    scrub_contrived_english,
)
from src.twin.linguistics_bank import (
    EAR_DECODER_BANK,
    INTONATION_SUBTEXT_BANK,
    MONOLOGUE_PROMPTS,
    PRAGMATIC_SCENARIOS,
    SPOKEN_FRAME_BANK,
    derive_frame_pattern,
    normalize_ear_tokens,
)
from src.voice.acoustics import (
    AcousticMetrics,
    MonologueArc,
    analyze_monologue_bytes,
    analyze_voice_bytes,
    compare_metrics,
    transcode_for_gemini,
)

ANGLE_META = {
    "casual_sharp": {
        "name": "Malik",
        "short_btn": "⚡ Malik · Everyday",
        "badge": "⚡ Malik · Everyday US",
        "opener": "Hey, what's going on? How's your week going so far?",
        "voice_desc": (
            "You are Malik, an observant, easygoing 32-year-old American friend. "
            "Speak plain, relaxed everyday US English—natural contractions, common phrasal verbs "
            "(end up, figure out, turn out, run into), understated human warmth. Zero forced slang."
        ),
    },
    "exec_clock": {
        "name": "Claire",
        "short_btn": "💼 Claire · Work/Exec",
        "badge": "💼 Claire · Workplace US",
        "opener": "Hey—got a few minutes before my next meeting. What are you working on?",
        "voice_desc": (
            "You are Claire, a sharp, grounded American colleague talking 1-on-1. "
            "Speak plain, direct workplace English—zero MBA buzzwords ('synergy', 'circle back'). "
            "Use natural phrasing like 'What's the main holdup?', 'Let's hold off for now'."
        ),
    },
    "seminar_skeptic": {
        "name": "Prof. Vance",
        "short_btn": "🎓 Prof. Vance · Seminar",
        "badge": "🎓 Prof. Vance · Seminar US",
        "opener": "Hey, come on in. What's an idea or question you've been thinking through lately?",
        "voice_desc": (
            "You are Nolan Vance, an American political-science professor in office hours. "
            "Speak authentic SPOKEN academic US English: contractions always, plain verbs ('holds up', "
            "'plays out', 'comes down to') paired with sharp analytical thinking—never stiff prose."
        ),
    },
}

SPARK_BANK = {
    "casual_sharp": [
        "A friend keeps canceling twenty minutes before meeting up, then texts 'my bad, crazy week.' Do you say something or let it fade?",
        "Do you actually get more done working outside the house, or is it mostly just a change of scenery?",
        "What's something everyone around you seems hyped about lately that you're still not sold on?",
        "If you get a rare day with zero obligations, do you like having a plan or just winging it?",
    ],
    "exec_clock": [
        "Your manager wants to roll something out in two weeks, and you already know it isn't ready. How do you push back without sounding defensive?",
        "Someone on your team keeps dropping 'quick favors' on you right at the end of the day. How do you set a boundary without making things weird?",
        "If you had two minutes to convince a city director to test one practical fix on a tight budget, how would you pitch it?",
        "Would you rather launch something that's 80% ready on Monday or hold off a month to get the details right?",
    ],
    "seminar_skeptic": [
        "People love claiming digital platforms make local government more accountable, but where does that usually break down on the ground?",
        "Here's a pushback I hear a lot: public consultations mostly amplify the loudest five percent of residents. How do you get around that?",
        "When a city policy works well in Barcelona or Tallinn, what usually trips it up when someone tries copying it elsewhere?",
        "If a skeptical reviewer asks what your actual causal mechanism is—not just the broad theory—how do you explain it in three plain sentences?",
    ],
}

LAB_DRILL_BANK = {
    "linking": [
        (
            "I ended up putting it off till a little bit later.",
            "I ˈEND-əd‿up | ˈPUT-ɪŋ‿ɪɾ‿ˈOFF | tɪl‿ə ˈLIɾ-əl bɪt̚ ˈLA-ɾər ↘",
            "Flap /t/ [ɾ] in 'putting_it_off', 'little', 'later'; Stop-T [t̚] on 'bit'.",
        ),
        (
            "We ran out of time to figure it out on our own.",
            "we ˈRAN‿aʊɾ‿ə ˈTIME | tə ˈFIG-yər‿ɪɾ‿ˈOUT | ɑn‿ɑr‿ˈOWN ↘",
            "Crush 'out of' -> [aʊɾə] and 'to' -> /tə/; link 'figure_it_out' as one word.",
        ),
        (
            "What did you end up getting at the market?",
            "ˈWʌDʒ-ə ˈEND‿up ˈGEɾ-ɪŋ | ət̚ ðə ˈMAR-kɪt̚ ↘",
            "Palatalize 'What did you' -> [wʌdʒə]; Stop-T [t̚] on 'at' and 'market'; falling tone ↘.",
        ),
    ],
    "stress": [
        (
            "How does that actually hold up at the municipal level?",
            "ˈHOW dəz ðæt̚ ˈAC-tʃu-ə-li ˈHOLD‿ˈUP | ət̚ ðə myu-ˈNI-sə-pəl ˈLE-vəl ↘",
            "Stress 2nd syllable in mu-NI-ci-pal (crush remaining vowels to /ə/).",
        ),
        (
            "Local officials want to participate, but the budget isn't there.",
            "ˈLO-kəl ə-ˈFI-ʃəlz ˈWAN-ə pɑr-ˈTI-sə-peɪt̚ | bət̚ ðə ˈBUD-dʒɪt̚ ˈIZ-ənt ˈTHERE ↘",
            "Stress par-TI-ci-pate & ə-FFI-cials; reduce unstressed vowels to schwa /ə/.",
        ),
        (
            "It comes down to whether citizens actually trust the process.",
            "ɪt̚ ˈCOMES ˈDOWN tə | ˈWHE-ðər ˈCI-ɾə-zənz ˈAC-tʃu-ə-li ˈTRUST ðə ˈPRO-sɛs ↘",
            "Stretch stressed vowels in DOWN, TRUST, PRO-cess; flap 'citizens' [ˈsɪɾəzənz].",
        ),
    ],
    "intonation": [
        (
            "It looks great on paper, though it's tough to pull off.",
            "ɪt̚ ˈLOOKS ˈGREAT ɑn ˈPA-pər ↗ | ðoʊ ɪts ˈTOUGH tə ˈPULL‿ˈOFF ↘",
            "Continuation rise ↗ on 'PA-per', then decisive pitch fall ↘ on 'OFF'.",
        ),
        (
            "I didn't say the policy failed—I said the timing was off.",
            "I ˈDID-ənt ˈSAY ðə ˈPA-lə-si ˈFAILED ↗ | I ˈSAID ðə ˈTI-mɪŋ wəz ˈOFF ↘",
            "Contrastive nuclear peaks on PA-licy FAILED vs TI-ming OFF ↘.",
        ),
        (
            "My main worry isn't the technology, it's the follow-through.",
            "my ˈMAIN ˈWOR-ri ˈIZ-ənt ðə tɛk-ˈNA-lə-dʒi ↗ | ɪts ðə ˈFA-loʊ-θru ↘",
            "Slight rise ↗ after tech-NO-lo-gy, then clean pitch fall through FOL-low-through ↘.",
        ),
    ],
}

AI_OPENERS_RE = re.compile(
    r"^(?:certainly|absolutely|great question|that's a great point|i'd be happy to|of course|sure thing)[!.,:\s]+",
    re.IGNORECASE,
)
COACH_SPLIT_RE = re.compile(r"\n*={2,}\s*COACH\s*={2,}\s*\n*", re.IGNORECASE)
PURE_GREETING_RE = re.compile(
    r"^(?:hi|hey|hello|yo|hey there|hi there|good morning|good evening|thanks|thank you|ok|okay|cool|nice)[!.?,\s]*$",
    re.IGNORECASE,
)
GENERIC_TAG_RE = re.compile(
    r"(?:^|\n)\s*(?:={2,}\s*)?([A-Z_]{3,16})(?:\s*={2,}|\s*:)\s*",
    re.IGNORECASE,
)


def _clean_field(val: str) -> str:
    v = (val or "").strip().strip('"“”')
    return "" if v.upper() in ("", "NONE", "N/A", "FALSE") else v


def parse_kv_tags(raw: str, keys: tuple[str, ...]) -> dict[str, str]:
    out = {k.lower(): "" for k in keys}
    if not raw or not raw.strip():
        return out
    allowed = set(out.keys())
    parts = GENERIC_TAG_RE.split(raw.strip())
    if len(parts) < 3:
        first_k = keys[0].lower()
        out[first_k] = raw.strip().splitlines()[0][:260]
        return out
    for idx in range(1, len(parts) - 1, 2):
        k = parts[idx].strip().lower()
        if k in allowed:
            out[k] = _clean_field(parts[idx + 1])
    return out


def parse_tagged_voice_audit(raw: str) -> dict[str, str]:
    d = parse_kv_tags(
        raw,
        (
            "HEARD", "REPLY", "SCORE", "HABIT", "MAP", "SOUNDS",
            "STRESS", "RHYTHM", "PEAK", "GRAMMAR", "RECAST", "VERDICT",
        ),
    )
    if not d["score"]:
        d["score"] = "78"
    if not d["habit"]:
        d["habit"] = "us_rhythm_linking"
    return d


class HybridVoiceTurnOutput(BaseModel):
    verbatim_heard: str = ""
    partner_reply: str = ""
    phonetic_score: int = 80
    has_findings: bool = True
    habit_key: str = "us_rhythm_linking"
    prosodic_map: str = ""
    word_pronunciations: str = ""
    stress_and_schwa: str = ""
    prosody_fix: str = ""
    focus_stress: str = ""
    shadow_chunk: str = ""
    native_recast: str = ""
    grammar_fix: str = ""
    retake_verdict: str = ""


class ProsodyAuditOutput(BaseModel):
    verbatim_heard: str = ""
    phonetic_score: int = 80
    has_findings: bool = True
    habit_key: str = "us_rhythm_linking"
    prosodic_map: str = ""
    word_pronunciations: str = ""
    stress_and_schwa: str = ""
    segmental_fix: str = ""
    prosody_fix: str = ""
    focus_stress: str = ""
    shadow_chunk: str = ""
    native_recast: str = ""
    grammar_fix: str = ""
    retake_verdict: str = ""


class GhostTwinAnalysis(BaseModel):
    should_intervene: bool = False
    habit_key: str = ""
    habit_badge: str = ""
    fix_summary: str = ""
    rhythm_tip: str = ""
    native_take: str = ""
    key_chunk: str = ""
    why_tag: str = ""
    trigger_take2: bool = False
    take2_prompt: str = ""


class RegisterBreakdown(BaseModel):
    is_register_neutral: bool = False
    casual_us: str = ""
    work_us: str = ""
    seminar_us: str = ""
    pragmatic_note: str = ""


class ShiftTurnOutput(BaseModel):
    reframed_user_point: str = ""
    same_across_registers: bool = False
    partner_reply: str = ""
    key_chunk: str = ""


@dataclass
class ShiftOutcome:
    angle: str
    badge: str
    partner_name: str
    partner_reply: str
    reframed_point: str = ""
    same_across_registers: bool = False
    key_chunk: str = ""


@dataclass
class VoiceLabReport:
    audit: ProsodyAuditOutput
    dsp: AcousticMetrics
    dsp_badge: str
    overall_score: int = 80
    habit_badge: str = ""
    delta_block: str = ""
    is_retake: bool = False


@dataclass
class EarEvalResult:
    target_full: str
    user_heard: str
    prosodic_map: str
    erosion_rule: str
    accuracy_pct: int
    missed_words: list[str]
    caught_weak_forms: bool
    voice_feedback: str = ""


@dataclass
class FrameEvalResult:
    frame_label: str
    used_flexibly: bool
    heard_or_text: str
    feedback: str
    native_model: str
    prosody_note: str = ""
    vault_advanced: bool = False


@dataclass
class PragmaticEvalResult:
    move_name: str
    score: int
    heard_or_text: str
    calibration_note: str
    native_recast: str
    key_frame: str
    prosody_note: str = ""


@dataclass
class IntonationEvalResult:
    target_sentence: str
    target_intent: str
    blueprint: str
    subtext_landed: bool
    score: int
    dsp_line: str
    pitch_feedback: str


@dataclass
class MonologueReport:
    verbatim_heard: str
    arc: MonologueArc
    overall_score: int
    discourse_feedback: str
    stamina_feedback: str
    sounds_feedback: str
    tight_us_20s: str
    key_chunk: str


@dataclass
class TurnOutcome:
    segments: list[str]
    full_reply: str
    angle: str
    angle_badge: str
    transcript: str = ""
    vault_celebrations: list[str] = field(default_factory=list)
    completed_take2: bool = False
    was_repair: bool = False
    inline_coach: GhostTwinAnalysis | None = None


def clean_partner_reply(raw: str) -> list[str]:
    cleaned = []
    for p in (raw or "").split("---"):
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", p.strip())
        s = re.sub(r"(?m)^\s*[-*•#]+\s+", "", s)
        s = re.sub(r"\s{2,}", " ", AI_OPENERS_RE.sub("", s).strip())
        s = scrub_contrived_english(s)
        if s:
            cleaned.append(s)
    return cleaned or ["Got it—say that one more time?"]


def _word_overlap_ratio(a: str, b: str) -> float:
    wa = set(re.findall(r"[a-z']+", (a or "").lower()))
    wb = set(re.findall(r"[a-z']+", (b or "").lower()))
    if len(wa) < 2 or len(wb) < 2:
        return 1.0 if (wa and wa == wb) else 0.0
    return len(wa & wb) / float(min(len(wa), len(wb)))


def _phrase_matched(phrase: str, text: str) -> bool:
    low = (text or "").lower()
    if phrase.lower() in low:
        return True
    _, pat = derive_frame_pattern(phrase)
    if pat and re.search(pat, low):
        return True
    toks = [w for w in re.findall(r"[a-z']+", phrase.lower()) if len(w) > 2]
    if len(toks) >= 3:
        hits = sum(1 for t in toks if re.search(r"\b" + re.escape(t[:4]), low))
        return hits >= len(toks) - 1
    return False


def infer_habit_key(
    learner_text: str, fix_summary: str, rhythm_tip: str, raw_habit: str
) -> str:
    cand = (raw_habit or "").strip().lower().replace(" ", "_")
    if cand in HABIT_LABELS:
        return cand
    low_t = learner_text.lower()
    if re.search(r"\b(?:buyed|goed|wented|teached|thinked|catched|bringed|finded)\b", low_t):
        return "irregular_past"
    if re.search(r"\bmany\s+(?:fruit|fruits|information|advice|equipment|research)\b|\b(?:informations|advices|researches)\b", low_t):
        return "uncountable_noun"
    for _, _, tag in scan_transfers(learner_text):
        if "fr" in tag:
            return "fr_calque"
        if "ar" in tag:
            return "ar_calque"
    if rhythm_tip and not fix_summary:
        return "us_rhythm_linking"
    return "stiff_register"


def parse_reply_and_coach(raw: str) -> tuple[list[str], GhostTwinAnalysis | None]:
    parts = COACH_SPLIT_RE.split((raw or "").strip(), maxsplit=1)
    segs = clean_partner_reply(parts[0])
    if len(parts) < 2:
        return segs, None
    block = parts[1].strip()
    if not block or block.upper().startswith("NONE"):
        return segs, None
    fields = {"habit": "", "fix": "", "rhythm": "", "us": "", "chunk": "", "why": "", "take2": ""}
    for line in block.splitlines():
        s = line.strip().lstrip("-•* ").strip()
        for k in ("HABIT", "FIX", "RHYTHM", "US", "CHUNK", "WHY", "TAKE2"):
            if s.upper().startswith(k + ":"):
                fields[k.lower()] = _clean_field(s.split(":", 1)[1])
                break
    us_clean = scrub_contrived_english(fields["us"])
    if not us_clean:
        return segs, None
    t2 = fields["take2"]
    return segs, GhostTwinAnalysis(
        should_intervene=True,
        habit_key=fields["habit"],
        fix_summary=fields["fix"],
        rhythm_tip=fields["rhythm"],
        native_take=us_clean,
        key_chunk=scrub_contrived_english(fields["chunk"]),
        why_tag=fields["why"],
        trigger_take2=bool(t2),
        take2_prompt=t2,
    )


PROSODY_AUDITOR_PROMPT = (
    "You are an elite General American English Phonetics & Suprasegmental Prosody Examiner.\n"
    "MANDATORY MISSION: Assess pronunciation on 100% of recordings—WHATEVER the speaker says.\n"
    "1. Anchor `MAP`, `SOUNDS`, `STRESS`, and `RHYTHM` strictly to THEIR exact spoken words in `HEARD`.\n"
    "2. Check vowels (/ɪ/ vs /iː/, /æ/, /ʌ/, r-colored /ɝ,ɚ/), consonants (US /ɹ/, /θ,ð/, [pʰ,tʰ,kʰ], dark /ɫ/), "
    "syllable stress (watch French final-syllable shifts & compressed syllables like com-for-ta-ble -> ˈKʌMF-tər-bəl), "
    "schwa /ə/ reduction, Flap-T [ɾ], Stop-T [t̚], C-V linking ‿, and nuclear intonation (↘ vs ↘↗)."
)

FAST_TAGGED_AUDIT_FORMAT = (
    "RETURN PLAIN TEXT WITH THESE EXACT TAGS IN ORDER (1 concise line per tag, NO JSON):\n"
    "===HEARD===\n[Exact words spoken; write [silence] ONLY if zero speech]\n"
    "===REPLY===\n[Natural 1-2 sentence spoken US reply; write NONE in Pure Lab Mode]\n"
    "===SCORE===\n[Integer 20-99 for General American pronunciation]\n"
    "===HABIT===\n[us_rhythm_linking | word_stress | flap_stop_t | nuclear_intonation | weak_form_erosion | fr_calque | ar_calque | stiff_register]\n"
    "===MAP===\n[US map of THEIR exact words: ˈCAPS stress, /ə/ schwa, ‿ link, [ɾ] Flap-T, [t̚] Stop-T, | pause, ↘/↗]\n"
    "===SOUNDS===\n[Specific words & phonemes: `word: [heard] -> [US target]` or confirm clean sounds + 1 native polish]\n"
    "===STRESS===\n[Primary stress placement & schwa /ə/ reduction on their words]\n"
    "===RHYTHM===\n[Connected speech, C-V linking, Flap-T [ɾ], Stop-T [t̚], and intonation tip]\n"
    "===PEAK===\n[Main nuclear stressed word in CAPS + ↘/↗]\n"
    "===GRAMMAR===\n[NONE if grammatical, else concise arrow fix]\n"
    "===RECAST===\n[NONE if grammatical, else plain spoken US sentence]\n"
    "===VERDICT===\n[NONE unless A/B retake applies]"
)


class EchoTwinEngine:
    def __init__(self, repo: TwinRepository, llm: GeminiClient) -> None:
        self.repo, self.llm, self._locks = repo, llm, {}

    def _lock(self, uid: int) -> asyncio.Lock:
        if uid not in self._locks:
            self._locks[uid] = asyncio.Lock()
        return self._locks[uid]

    def detect_vault_hits(self, uid: int, text: str) -> list[str]:
        hits = []
        for item in self.repo.all_vault_phrases(uid, limit=30):
            if _phrase_matched(item["phrase"], text):
                self.repo.mark_vault_hit(uid, item["phrase"])
                hits.append(item["phrase"])
        return hits

    def _apply_habit_tracking(
        self, uid: int, learner_text: str, coach: GhostTwinAnalysis | None, repair: bool
    ) -> None:
        if repair or PURE_GREETING_RE.match(learner_text.strip()):
            return
        if coach is not None:
            h_key = infer_habit_key(
                learner_text, coach.fix_summary, coach.rhythm_tip, coach.habit_key
            )
            coach.habit_key = h_key
            lbl, hits = self.repo.record_habit_hit(
                uid, h_key, learner_text[:120],
                coach.fix_summary or coach.rhythm_tip or coach.native_take[:120],
            )
            if hits >= 2:
                coach.habit_badge = f"Habit #{hits}: {lbl}"
            self.repo.log_upgrade(uid, learner_text, coach.native_take, coach.why_tag or lbl)
            if coach.trigger_take2 and coach.take2_prompt:
                self.repo.set_pending_take2(uid, coach.take2_prompt)
        elif len(learner_text.split()) >= 4:
            self.repo.record_clean_turn(uid)

    def _context_blocks(self, uid: int) -> tuple[str, str]:
        mem_text, _ = self.repo.get_memory(uid)
        habits = self.repo.top_habits(uid, limit=3)
        mem_blk = f"\nKNOWN SPEAKER CONTEXT:\n{mem_text}" if mem_text else ""
        hab_blk = (
            "\nRECURRING HABITS RADAR: "
            + "; ".join(f"{h['habit_key']} ({h['hits']}x)" for h in habits)
            if habits
            else ""
        )
        return mem_blk, hab_blk

    def _sys_prompt(
        self, uid: int, angle: str, text: str, recent: list[dict], due: list[dict], take2: str, repair: bool
    ) -> str:
        meta = ANGLE_META.get(angle, ANGLE_META["casual_sharp"])
        weekday = datetime.now(timezone.utc).strftime("%A")
        wc = len(text.split())
        bot_t = [t["content"] for t in recent if t["role"] == "partner"]
        two_q = len(bot_t) >= 2 and all(x.rstrip().endswith("?") for x in bot_t[-2:])
        transfers = scan_transfers(text)
        mem_blk, hab_blk = self._context_blocks(uid)

        r = [
            f"{meta['voice_desc']} (Today is {weekday}.){mem_blk}",
            SPOKEN_US_DNA,
            "- Match the speaker's length and energy; plain text only in your character reply.",
        ]
        if repair:
            r.append("- PRAGMATIC REPAIR: Clarify warmly in plain US English ('Oh my bad—I just meant...').")
        elif two_q:
            r.append("- End with a grounded observation WITHOUT asking a question.")
        if detect_code_switch(text) and not repair:
            r.append("- L1 BRIDGE: Include the exact plain spoken US expression in [square brackets].")
        if take2 and not repair:
            r.append(f"- TAKE 2 RETRY: Briefly acknowledge cleaner phrasing ({take2}) and continue.")
        for v in (due[:1] if not repair else []):
            if int(v["stage"]) == 0:
                r.append(f"- STEALTH MODEL: Only if natural, use '{v['phrase']}' once.")
            else:
                r.append(f"- STEALTH BAIT: Create a natural opening for '{v['phrase']}' without saying it.")

        if ECHO_TWIN_ENABLED and not repair and not PURE_GREETING_RE.match(text.strip()) and wc >= 2:
            hint = f" (Fix slips: {'; '.join(s + '->' + u for s, u, _ in transfers)})" if transfers else ""
            r.append(
                f"\nSILENT COACH CHECK (APPEND AFTER REPLY):{hab_blk}\n"
                f"Inspect speaker's English accuracy & spoken US naturalness (spoken grammar & discourse frames).{hint}\n"
                "- If already 100% natural spoken US English, append NOTHING.\n"
                "- Else append:\n===COACH===\n"
                "HABIT: [fr_calque | ar_calque | stiff_register | discourse_staging | irregular_past | uncountable_noun]\n"
                "FIX: [concise arrow fix]\n"
                "US: [plainest natural spoken US sentence—zero buzzwords]\n"
                "CHUNK: [reusable 2-5 word spoken US frame]\n"
                "WHY: [2-5 word tag]\nTAKE2: NONE"
            )
        return "\n".join(r)

    async def respond_turn(
        self,
        user_id: int,
        learner_text: str,
        *,
        chat_message_id: int | None = None,
        is_voice: bool = False,
        duration_s: float = 0.0,
        log_partner: bool = True,
    ) -> TurnOutcome:
        async with self._lock(user_id):
            u = self.repo.get_user(user_id)
            angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
            take2 = u["pending_take2"]
            recent = self.repo.recent_turns(user_id, 10)
            due = self.repo.due_vault_phrases(user_id, 1)
            repair = is_repair_request(learner_text)
            sys_p = self._sys_prompt(user_id, angle, learner_text, recent, due, take2, repair)
            hist = "\n".join(
                f"{'You' if t['role'] == 'partner' else 'Speaker'}: {t['content']}"
                for t in recent[-8:]
            )
            usr_p = (
                f"RECENT DIALOGUE:\n{hist}\n\nLATEST SPEAKER MESSAGE: {learner_text}\nYour reply:"
                if hist
                else f"LATEST SPEAKER MESSAGE: {learner_text}\nYour reply:"
            )
            raw = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.62, deep_chat=True)
            segs, inline_coach = parse_reply_and_coach(raw)
            full = "\n\n".join(segs)
            vhits = self.detect_vault_hits(user_id, learner_text)
            self.repo.log_turn(
                user_id, "learner", angle, learner_text,
                chat_message_id=chat_message_id, is_voice=is_voice, duration_s=duration_s,
            )
            if log_partner:
                self.repo.log_turn(user_id, "partner", angle, full)
            if take2:
                self.repo.set_pending_take2(user_id, "")
            if due and int(due[0]["stage"]) == 0 and due[0]["phrase"].lower() in full.lower():
                self.repo.mark_vault_hit(user_id, due[0]["phrase"])
            self._apply_habit_tracking(user_id, learner_text, inline_coach, repair)
            return TurnOutcome(
                segs, full, angle, ANGLE_META[angle]["badge"],
                learner_text, vhits, bool(take2), repair, inline_coach,
            )

    def _finalize_lab_report(
        self,
        user_id: int,
        angle: str,
        heard_final: str,
        parsed: dict[str, str],
        dsp: AcousticMetrics,
        prev_target: str,
        prev_recast: str,
        prev_dsp: AcousticMetrics | None,
        force_retake: bool,
    ) -> VoiceLabReport:
        wc_final = len(heard_final.split())
        if wc_final > 0 and dsp.voiced_s >= 0.30:
            dsp.articulation_wpm = round((wc_final / dsp.voiced_s) * 60.0, 1)

        is_retake = bool(
            force_retake
            or (
                prev_dsp
                and (
                    _word_overlap_ratio(heard_final, prev_target) >= 0.55
                    or _word_overlap_ratio(heard_final, prev_recast) >= 0.55
                )
            )
        )
        try:
            raw_sc = int(re.findall(r"\d+", parsed.get("score", "78"))[0])
        except Exception:
            raw_sc = 78
        p_sc = max(20, min(99, raw_sc))

        g_fix = parsed.get("grammar", "")
        recast = scrub_contrived_english(parsed.get("recast", ""))
        audit = ProsodyAuditOutput(
            verbatim_heard=heard_final,
            phonetic_score=p_sc,
            has_findings=(p_sc < 93 or bool(g_fix)),
            habit_key=parsed.get("habit") or "us_rhythm_linking",
            prosodic_map=parsed.get("map") or heard_final,
            word_pronunciations=(
                parsed.get("sounds")
                or "Keep General American vowels tense/lax distinct and final stops unreleased [t̚]."
            ),
            stress_and_schwa=(
                parsed.get("stress")
                or "Stretch primary stressed vowels and reduce function words (to, of, for) to /ə/."
            ),
            prosody_fix=(
                parsed.get("rhythm")
                or "Link final consonants smoothly into following vowels inside each breath group."
            ),
            focus_stress=parsed.get("peak", ""),
            native_recast=recast,
            grammar_fix=g_fix,
            retake_verdict=parsed.get("verdict", ""),
        )
        spoken_or_fixed = recast if (g_fix and recast) else heard_final
        audit.shadow_chunk = scrub_contrived_english(" ".join(spoken_or_fixed.split()[:8]))

        if dsp.voiced_s >= 0.40 and dsp.prosody_score > 0:
            overall = int(round(0.65 * p_sc + 0.35 * dsp.prosody_score))
        else:
            overall = p_sc
        dsp.prosody_score = overall

        hab_badge = ""
        if audit.has_findings or overall < 92:
            h_key = audit.habit_key.strip().lower()
            if h_key not in HABIT_LABELS:
                h_key = "us_rhythm_linking"
            lbl, hits = self.repo.record_habit_hit(
                user_id, h_key, heard_final[:120], audit.word_pronunciations[:120]
            )
            if hits >= 2:
                hab_badge = f"Habit #{hits}: {lbl}"
            if g_fix and recast:
                self.repo.log_upgrade(user_id, heard_final, recast, lbl)
        elif wc_final >= 3:
            self.repo.record_clean_turn(user_id)

        delta_txt = compare_metrics(prev_dsp, dsp) if (is_retake and prev_dsp) else ""
        if is_retake and not audit.retake_verdict and prev_dsp:
            diff_sc = dsp.prosody_score - prev_dsp.prosody_score
            audit.retake_verdict = (
                f"Cleaner General American delivery (+{diff_sc} pts) on this retake."
                if diff_sc >= 3
                else "Steady contour—push schwa reduction and C-V linking one notch further."
            )

        if dsp.voiced_s > 0.15:
            meter = (
                f"Overall {overall}/100 · Sounds {p_sc}/100 · "
                f"nPVI {dsp.npvi:.0f} ({dsp.rhythm_label()}) · "
                f"Pitch {dsp.pitch_span_st:.1f}st ({dsp.boundary_tone})"
            )
        else:
            meter = f"Pronunciation Score: {overall}/100"

        self.repo.save_lab_baseline(
            user_id, heard_final, spoken_or_fixed, dsp.to_json(), arm_retake=False
        )
        return VoiceLabReport(
            audit=audit,
            dsp=dsp,
            dsp_badge=meter,
            overall_score=overall,
            habit_badge=hab_badge,
            delta_block=delta_txt,
            is_retake=is_retake,
        )

    async def respond_hybrid_voice_turn(
        self,
        user_id: int,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/ogg",
        chat_message_id: int | None = None,
        duration_s: float = 0.0,
        log_partner: bool = False,
    ) -> tuple[TurnOutcome, VoiceLabReport | None]:
        async with self._lock(user_id):
            dsp, (fast_bytes, fast_mime) = await asyncio.gather(
                asyncio.to_thread(analyze_voice_bytes, audio_bytes, 0),
                asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type),
            )
            u = self.repo.get_user(user_id)
            angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
            meta = ANGLE_META[angle]
            weekday = datetime.now(timezone.utc).strftime("%A")
            take2 = u["pending_take2"]
            recent = self.repo.recent_turns(user_id, 6)
            due = self.repo.due_vault_phrases(user_id, 1)
            mem_blk, hab_blk = self._context_blocks(user_id)
            bot_t = [t["content"] for t in recent if t["role"] == "partner"]
            two_q = len(bot_t) >= 2 and all(x.rstrip().endswith("?") for x in bot_t[-2:])

            lab_state = self.repo.get_lab_state(user_id)
            prev_target = lab_state.get("target_text", "")
            prev_recast = lab_state.get("recast_text", "")
            prev_dsp = AcousticMetrics.from_json(lab_state.get("metrics_json", ""))

            sys_lines = [
                f"PART 1 — PARTNER ({meta['badge']}, {weekday}): {meta['voice_desc']}{mem_blk}",
                SPOKEN_US_DNA,
                "- Write a natural 1-2 sentence spoken US reply in ===REPLY===.",
                "\nPART 2 — MANDATORY PRONUNCIATION EXAMINER (ASSESS 100% OF CLIPS):",
                PROSODY_AUDITOR_PROMPT,
                hab_blk,
                "\n" + FAST_TAGGED_AUDIT_FORMAT,
            ]
            if two_q:
                sys_lines.insert(2, "- End ===REPLY=== with a statement, NOT a question.")

            hist = "\n".join(
                f"{'You' if t['role'] == 'partner' else 'Speaker'}: {t['content']}"
                for t in recent[-5:]
            )
            retake_hint = (
                f"PREVIOUS TAKE: \"{prev_recast or prev_target}\". If repeated, fill ===VERDICT===; else NONE."
                if (prev_recast or prev_target)
                else "First take (===VERDICT=== NONE)."
            )
            usr_p = (
                (f"RECENT DIALOGUE:\n{hist}\n\n" if hist else "")
                + f"{dsp.to_prompt_summary()}\n{retake_hint}\n"
                + f"Listen to this {duration_s:.0f}s clip and output the exact tags."
            )

            parsed: dict[str, str] = {}
            try:
                raw = await self.llm.generate_fast_multimodal(
                    user_id, "\n".join(sys_lines), fast_bytes, usr_p, mime_type=fast_mime
                )
                parsed = parse_tagged_voice_audit(raw)
            except Exception:
                parsed = {}

            heard_final = (parsed.get("heard") or "").strip()
            if heard_final.lower() in ("[silence]", "[inaudible]", "silence"):
                segs = clean_partner_reply("Didn't catch clear speech there—try once more?")
                return TurnOutcome(segs, segs[0], angle, meta["badge"], "", [], False, True, None), None

            if not heard_final:
                if dsp.voiced_s < 0.20:
                    segs = ["Couldn't detect speech in that clip—hold the mic a bit closer."]
                    return TurnOutcome(segs, segs[0], angle, meta["badge"], "", [], False, True, None), None
                heard_final = "(voice clip analyzed via acoustic DSP)"
                parsed["sounds"] = "Acoustic waveform scored directly—send one more clip for word-by-word IPA."

            segs = clean_partner_reply(
                parsed.get("reply") or "Got your note—check your pronunciation card above."
            )
            full = "\n\n".join(segs)
            repair = is_repair_request(heard_final)
            vhits = self.detect_vault_hits(user_id, heard_final)

            self.repo.log_turn(
                user_id, "learner", angle, heard_final,
                chat_message_id=chat_message_id, is_voice=True, duration_s=duration_s,
            )
            if log_partner:
                self.repo.log_turn(user_id, "partner", angle, full, is_voice=True)
            if take2:
                self.repo.set_pending_take2(user_id, "")
            if due and int(due[0]["stage"]) == 0 and due[0]["phrase"].lower() in full.lower():
                self.repo.mark_vault_hit(user_id, due[0]["phrase"])

            report = self._finalize_lab_report(
                user_id, angle, heard_final, parsed, dsp,
                prev_target, prev_recast, prev_dsp, force_retake=False,
            )
            outcome = TurnOutcome(
                segs, full, angle, meta["badge"], heard_final, vhits, bool(take2), repair, None
            )
            return outcome, report

    async def run_deep_prosody_audit(
        self,
        user_id: int,
        audio_bytes: bytes,
        transcript: str = "",
        *,
        mime_type: str = "audio/ogg",
        force_retake: bool = False,
        log_learner_turn: bool = False,
        chat_message_id: int | None = None,
        duration_s: float = 0.0,
    ) -> VoiceLabReport | None:
        clean_hint = transcript.strip() if transcript and transcript != "(voice note)" else ""
        dsp, (fast_bytes, fast_mime) = await asyncio.gather(
            asyncio.to_thread(analyze_voice_bytes, audio_bytes, len(clean_hint.split())),
            asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type),
        )
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        lab_state = self.repo.get_lab_state(user_id)
        prev_target = lab_state.get("target_text", "")
        prev_recast = lab_state.get("recast_text", "")
        prev_dsp = AcousticMetrics.from_json(lab_state.get("metrics_json", ""))
        is_retake = bool(force_retake or lab_state.get("pending_retake"))

        sys_p = PROSODY_AUDITOR_PROMPT + "\n\n" + FAST_TAGGED_AUDIT_FORMAT
        retake_txt = (
            f"A/B RETAKE ACTIVE on: \"{prev_recast or prev_target}\". Fill ===VERDICT===."
            if is_retake
            else f"Previous take: \"{prev_recast or prev_target}\"."
        )
        usr_p = (
            f"Register: {ANGLE_META[angle]['badge']}\n{dsp.to_prompt_summary()}\n"
            f"Hint: \"{clean_hint}\"\n{retake_txt}\nAudit every sound & rhythm detail now."
        )
        parsed: dict[str, str] = {}
        try:
            raw = await self.llm.generate_fast_multimodal(
                user_id, sys_p, fast_bytes, usr_p, mime_type=fast_mime
            )
            parsed = parse_tagged_voice_audit(raw)
        except Exception:
            parsed = {}

        heard_final = (parsed.get("heard") or clean_hint).strip()
        if heard_final.lower() in ("[silence]", "[inaudible]", "silence"):
            return None
        if not heard_final:
            if dsp.voiced_s < 0.20:
                return None
            heard_final = "(voice clip analyzed via acoustic DSP)"

        if log_learner_turn:
            self.repo.log_turn(
                user_id, "learner", angle, heard_final,
                chat_message_id=chat_message_id, is_voice=True, duration_s=duration_s,
            )
            self.detect_vault_hits(user_id, heard_final)

        return self._finalize_lab_report(
            user_id, angle, heard_final, parsed, dsp,
            prev_target, prev_recast, prev_dsp, force_retake=is_retake,
        )

    # ================= PILLAR 1: PERCEPTUAL EAR DECODER =================
    def start_ear_challenge(self, user_id: int) -> dict:
        u = self.repo.get_user(user_id)
        pref = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        st = self.repo.get_studio_state(user_id)
        last_full = ""
        if st.get("mode_payload"):
            try:
                last_full = json.loads(st["mode_payload"]).get("full", "")
            except Exception:
                pass
        pool = [x for x in EAR_DECODER_BANK if x["full"] != last_full] or EAR_DECODER_BANK
        same = [x for x in pool if x["angle"] == pref]
        chosen = random.choice(same if (same and random.random() < 0.6) else pool)
        self.repo.set_active_studio(user_id, "ear_decode", json.dumps(chosen))
        return chosen

    def _grade_ear_text(self, item: dict, attempt_text: str, voice_note: str = "") -> EarEvalResult:
        target = item["full"]
        ref_toks = normalize_ear_tokens(target)
        usr_toks = normalize_ear_tokens(attempt_text)
        bag = list(usr_toks)
        matched = 0
        missed: list[str] = []
        for t in ref_toks:
            if t in bag:
                bag.remove(t)
                matched += 1
            else:
                missed.append(t)
        acc = int(round(100.0 * matched / max(1, len(ref_toks))))
        weak_list = [w.lower() for w in item.get("weak", [])]
        caught_weak = all(w in usr_toks for w in weak_list)
        if not caught_weak:
            self.repo.record_habit_hit(user_id=0, habit_key="weak_form_erosion", example=attempt_text[:90], fix=item["rule"][:110]) if False else None
        return EarEvalResult(
            target_full=target,
            user_heard=attempt_text.strip(),
            prosodic_map=item["map"],
            erosion_rule=item["rule"],
            accuracy_pct=acc,
            missed_words=missed,
            caught_weak_forms=caught_weak,
            voice_feedback=voice_note,
        )

    def evaluate_ear_text(self, user_id: int, attempt_text: str) -> EarEvalResult | None:
        st = self.repo.get_studio_state(user_id)
        if st.get("active_mode") != "ear_decode" or not st.get("mode_payload"):
            return None
        item = json.loads(st["mode_payload"])
        res = self._grade_ear_text(item, attempt_text)
        if not res.caught_weak_forms:
            self.repo.record_habit_hit(
                user_id, "weak_form_erosion", attempt_text[:100], item["rule"][:110]
            )
        self.repo.clear_active_studio(user_id)
        self.repo.save_lab_baseline(user_id, item["full"], item["full"], "", arm_retake=True)
        return res

    async def evaluate_ear_voice(
        self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> EarEvalResult | None:
        st = self.repo.get_studio_state(user_id)
        if st.get("active_mode") != "ear_decode" or not st.get("mode_payload"):
            return None
        item = json.loads(st["mode_payload"])
        fast_b, fast_m = await asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type)
        sys_p = (
            "Transcribe exact words heard in ===HEARD=== and give 1 crisp sentence on how well "
            "the speaker linked & reduced vowels compared to General American in ===LINKING===."
        )
        usr_p = f"Reference sentence attempted: \"{item['full']}\"\nOutput ===HEARD=== and ===LINKING===."
        raw = await self.llm.generate_fast_multimodal(
            user_id, sys_p, fast_b, usr_p, mime_type=fast_m
        )
        kv = parse_kv_tags(raw, ("HEARD", "LINKING"))
        heard = kv["heard"] or "(inaudible)"
        res = self._grade_ear_text(item, heard, voice_note=kv["linking"])
        self.repo.clear_active_studio(user_id)
        self.repo.save_lab_baseline(user_id, item["full"], item["full"], "", arm_retake=True)
        return res

    # ================= PILLAR 2: GENERATIVE LEXICAL FRAMES & PUSHED OUTPUT =================
    def start_frame_challenge(self, user_id: int) -> dict:
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        due = self.repo.due_vault_phrases(user_id, 2)
        if due:
            ph = due[0]["phrase"]
            frame_lbl, pat = derive_frame_pattern(ph)
            item = {
                "angle": angle,
                "vault_phrase": ph,
                "frame": frame_lbl,
                "regex": pat,
                "example": due[0].get("context_example") or f"In practice, {ph} really shapes the outcome.",
                "why": "Active Recall from your personal Vault—use it flexibly in a fresh sentence!",
                "prompt": f"Respond to {ANGLE_META[angle]['name']} in 2–3 spoken sentences on any current project or idea, weaving in \"{ph}\" naturally.",
            }
        else:
            same = [x for x in SPOKEN_FRAME_BANK if x["angle"] == angle] or SPOKEN_FRAME_BANK
            chosen = random.choice(same)
            item = dict(chosen, vault_phrase="")
        self.repo.set_active_studio(user_id, "frame_push", json.dumps(item))
        return item

    async def evaluate_frame_response(
        self,
        user_id: int,
        *,
        text_input: str = "",
        audio_bytes: bytes = b"",
        mime_type: str = "audio/ogg",
    ) -> FrameEvalResult | None:
        st = self.repo.get_studio_state(user_id)
        if st.get("active_mode") != "frame_push" or not st.get("mode_payload"):
            return None
        item = json.loads(st["mode_payload"])
        sys_p = (
            "You are a COCA Spoken American English Lexical-Frame & Spoken-Grammar Coach.\n"
            + SPOKEN_US_DNA
            + "\nReturn plain text tags:\n"
            "===HEARD===\n[Exact transcript if audio, else echo user text]\n"
            "===USED===\n[YES if they deployed the target lexical frame grammatically & naturally, else NO]\n"
            "===FEEDBACK===\n[1-2 crisp sentences on how natural their slot-filling & spoken grammar sounded]\n"
            "===MODEL===\n[Plain, idiomatic spoken US version of THEIR exact thought using the target frame]\n"
            "===PROSODY===\n[If audio: 1 sentence on linking/stress across the frame; else NONE]"
        )
        usr_ctx = (
            f"Target Frame: {item['frame']}\nPrompt: {item['prompt']}\n"
            + (f"User wrote: \"{text_input}\"" if text_input else "Listen to the attached voice response.")
        )
        if audio_bytes:
            fb, fm = await asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type)
            raw = await self.llm.generate_fast_multimodal(user_id, sys_p, fb, usr_ctx, mime_type=fm)
        else:
            raw = await self.llm.generate_text(user_id, sys_p, usr_ctx, temperature=0.25, deep_chat=False)

        kv = parse_kv_tags(raw, ("HEARD", "USED", "FEEDBACK", "MODEL", "PROSODY"))
        utterance = kv["heard"] or text_input.strip()
        regex_hit = bool(re.search(item["regex"], utterance.lower())) if item.get("regex") else False
        used_ok = (kv["used"].upper().startswith("Y")) or regex_hit
        advanced = False
        if used_ok and item.get("vault_phrase"):
            self.repo.mark_vault_hit(user_id, item["vault_phrase"])
            advanced = True
        elif not used_ok:
            self.repo.record_habit_hit(user_id, "discourse_staging", utterance[:100], item["frame"])

        model_clean = scrub_contrived_english(kv["model"] or item["example"])
        self.repo.clear_active_studio(user_id)
        self.repo.save_lab_baseline(user_id, utterance, model_clean, "", arm_retake=False)
        return FrameEvalResult(
            frame_label=item["frame"],
            used_flexibly=used_ok,
            heard_or_text=utterance,
            feedback=kv["feedback"] or "Keep the frame unstressed and put nuclear weight on your new content noun/verb.",
            native_model=model_clean,
            prosody_note=kv["prosody"],
            vault_advanced=advanced,
        )

    # ================= PILLAR 3: INTERACTIONAL PRAGMATICS LAB =================
    def start_pragmatic_scenario(self, user_id: int) -> dict:
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "exec_clock"
        same = [x for x in PRAGMATIC_SCENARIOS if x["angle"] == angle]
        chosen = random.choice(same or PRAGMATIC_SCENARIOS)
        self.repo.set_active_studio(user_id, "pragmatics", json.dumps(chosen))
        return chosen

    async def evaluate_pragmatic_response(
        self,
        user_id: int,
        *,
        text_input: str = "",
        audio_bytes: bytes = b"",
        mime_type: str = "audio/ogg",
    ) -> PragmaticEvalResult | None:
        st = self.repo.get_studio_state(user_id)
        if st.get("active_mode") != "pragmatics" or not st.get("mode_payload"):
            return None
        sc = json.loads(st["mode_payload"])
        sys_p = (
            "You are an Interactional Pragmatics Coach for C1/C2 Spoken American English.\n"
            "Watch for L1 Arabic/French pragmatic transfer (overly blunt directness, stiff formal clichés like "
            "'With respect I disagree', or over-apologetic softening).\n"
            + SPOKEN_US_DNA
            + "\nReturn plain text tags:\n"
            "===HEARD===\n[Exact transcript if audio, else user text]\n"
            "===SCORE===\n[0-100 pragmatic calibration score]\n"
            "===NOTE===\n[1-2 crisp sentences evaluating hedging, concession-before-pushback, and register tone]\n"
            "===RECAST===\n[How a sharp, diplomatic native American says THEIR exact response in 1-2 sentences]\n"
            "===FRAME===\n[Reusable 3-6 word pragmatic opener/hedge from RECAST]\n"
            "===PROSODY===\n[If audio: 1 sentence on tone warmth, pace & pitch hedging; else NONE]"
        )
        usr_ctx = (
            f"Move: {sc['move']}\nSituation: {sc['situation']}\nGoal: {sc['goal']}\n"
            + (f"User said: \"{text_input}\"" if text_input else "Evaluate the attached voice response.")
        )
        if audio_bytes:
            fb, fm = await asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type)
            raw = await self.llm.generate_fast_multimodal(user_id, sys_p, fb, usr_ctx, mime_type=fm)
        else:
            raw = await self.llm.generate_text(user_id, sys_p, usr_ctx, temperature=0.25, deep_chat=False)

        kv = parse_kv_tags(raw, ("HEARD", "SCORE", "NOTE", "RECAST", "FRAME", "PROSODY"))
        try:
            sc_num = max(25, min(99, int(re.findall(r"\d+", kv["score"] or "80")[0])))
        except Exception:
            sc_num = 80
        utterance = kv["heard"] or text_input.strip()
        recast = scrub_contrived_english(kv["recast"] or utterance)
        frame = scrub_contrived_english(kv["frame"] or "Fair point, though my one worry is")
        if sc_num < 82:
            self.repo.record_habit_hit(user_id, "pragmatic_bluntness", utterance[:100], frame)
        self.repo.clear_active_studio(user_id)
        self.repo.save_lab_baseline(user_id, utterance, recast, "", arm_retake=False)
        return PragmaticEvalResult(
            move_name=sc["move"],
            score=sc_num,
            heard_or_text=utterance,
            calibration_note=kv["note"] or "Solid pragmatic stance—soften the pivot with a brief concession first.",
            native_recast=recast,
            key_frame=frame,
            prosody_note=kv["prosody"],
        )

    # ================= PILLAR 4: PRAGMATIC INTONATION & CONTRASTIVE FOCUS =================
    def start_intonation_drill(self, user_id: int) -> dict:
        chosen = random.choice(INTONATION_SUBTEXT_BANK)
        self.repo.set_active_studio(user_id, "intonation", json.dumps(chosen))
        return chosen

    async def evaluate_intonation_voice(
        self, user_id: int, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> IntonationEvalResult | None:
        st = self.repo.get_studio_state(user_id)
        if st.get("active_mode") != "intonation" or not st.get("mode_payload"):
            return None
        item = json.loads(st["mode_payload"])
        dsp, (fb, fm) = await asyncio.gather(
            asyncio.to_thread(analyze_voice_bytes, audio_bytes, len(item["sentence"].split())),
            asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type),
        )
        sys_p = (
            "You are an American English Suprasegmental Intonation & Contrastive Focus Examiner.\n"
            "Listen carefully to WHERE the speaker placed the nuclear pitch peak and HOW their final boundary tone moved.\n"
            "Return plain text tags:\n"
            "===LANDED===\n[YES if their nuclear stress placement & boundary contour conveyed the target subtext, else NO]\n"
            "===SCORE===\n[25-99 intonation subtext accuracy]\n"
            "===FEEDBACK===\n[2 crisp sentences: which word sounded loudest/highest in their clip vs target, and how to shape the ending pitch]"
        )
        usr_p = (
            f"Sentence: \"{item['sentence']}\"\n"
            f"Target Subtext: {item['intent']}\n"
            f"Target Blueprint: {item['blueprint']} ({item['coach_rule']})\n"
            f"DSP Telemetry: {dsp.to_prompt_summary()}\nAudit nuclear focus & pitch contour now."
        )
        raw = await self.llm.generate_fast_multimodal(user_id, sys_p, fb, usr_p, mime_type=fm)
        kv = parse_kv_tags(raw, ("LANDED", "SCORE", "FEEDBACK"))
        landed = kv["landed"].upper().startswith("Y")
        try:
            sc_num = max(25, min(99, int(re.findall(r"\d+", kv["score"] or "76")[0])))
        except Exception:
            sc_num = 76
        if not landed:
            self.repo.record_habit_hit(
                user_id, "contrastive_pitch", item["sentence"], item["coach_rule"][:110]
            )
            self.repo.save_lab_baseline(
                user_id, item["sentence"], item["sentence"], dsp.to_json(), arm_retake=False
            )
        else:
            self.repo.clear_active_studio(user_id)
            self.repo.save_lab_baseline(
                user_id, item["sentence"], item["sentence"], dsp.to_json(), arm_retake=False
            )
        dsp_line = (
            f"Pitch Span {dsp.pitch_span_st:.1f}st · Contrast {dsp.stress_contrast_db:.1f}dB · "
            f"End {dsp.boundary_tone}"
        )
        return IntonationEvalResult(
            target_sentence=item["sentence"],
            target_intent=item["intent"],
            blueprint=item["blueprint"],
            subtext_landed=landed,
            score=sc_num,
            dsp_line=dsp_line,
            pitch_feedback=kv["feedback"] or item["coach_rule"],
        )

    # ================= PILLAR 5: 60s EXTENDED DISCOURSE & SPOKEN GRAMMAR =================
    def start_monologue_prompt(self, user_id: int) -> tuple[str, str, str]:
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "seminar_skeptic"
        prompt = random.choice(MONOLOGUE_PROMPTS[angle])
        self.repo.set_active_studio(
            user_id, "monologue", json.dumps({"angle": angle, "prompt": prompt})
        )
        return angle, ANGLE_META[angle]["badge"], prompt

    async def evaluate_monologue_voice(
        self,
        user_id: int,
        audio_bytes: bytes,
        *,
        mime_type: str = "audio/ogg",
        chat_message_id: int | None = None,
        duration_s: float = 0.0,
    ) -> MonologueReport | None:
        arc, (fb, fm) = await asyncio.gather(
            asyncio.to_thread(analyze_monologue_bytes, audio_bytes, 0),
            asyncio.to_thread(transcode_for_gemini, audio_bytes, mime_type),
        )
        st = self.repo.get_studio_state(user_id)
        prompt_used = ""
        if st.get("mode_payload"):
            try:
                prompt_used = json.loads(st["mode_payload"]).get("prompt", "")
            except Exception:
                pass
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "seminar_skeptic"

        sys_p = (
            "You are an elite C1/C2 Spoken American Discourse & Prosodic Stamina Coach.\n"
            + SPOKEN_US_DNA
            + "\nReturn plain text tags:\n"
            "===HEARD===\n[Verbatim transcript of their monologue]\n"
            "===SCORE===\n[25-99 overall spoken C1/C2 effectiveness]\n"
            "===DISCOURSE===\n[Evaluate spoken grammar: topic fronting ('The catch is...'), natural self-repair, concession bridges ('That said,') vs stiff written connectors]\n"
            "===STAMINA===\n[How their rhythm, vowel reduction & pacing held up across Opening 1/3 -> Middle 1/3 -> Closing 1/3]\n"
            "===SOUNDS===\n[Top 2 specific word stress / vowel / linking fixes heard in the clip]\n"
            "===US20S===\n[Crisp, punchy 3-sentence (20-second) native spoken American distillation of THEIR exact argument—zero buzzwords]\n"
            "===CHUNK===\n[Best 3-6 word spoken discourse frame from US20S]"
        )
        usr_p = (
            f"Register: {ANGLE_META[angle]['badge']}\nPrompt: \"{prompt_used}\"\n"
            f"{arc.to_prompt_summary()}\nDuration: {duration_s:.0f}s. Audit extended monologue now."
        )
        raw = await self.llm.generate_fast_multimodal(user_id, sys_p, fb, usr_p, mime_type=fm)
        kv = parse_kv_tags(
            raw, ("HEARD", "SCORE", "DISCOURSE", "STAMINA", "SOUNDS", "US20S", "CHUNK")
        )
        heard = kv["heard"].strip()
        if not heard or heard.lower() in ("[silence]", "[inaudible]"):
            return None

        wc = len(heard.split())
        if wc > 0 and arc.overall.voiced_s >= 0.5:
            arc.overall.articulation_wpm = round((wc / arc.overall.voiced_s) * 60.0, 1)

        try:
            p_sc = max(25, min(99, int(re.findall(r"\d+", kv["score"] or "80")[0])))
        except Exception:
            p_sc = 80

        overall = (
            int(round(0.60 * p_sc + 0.40 * arc.overall.prosody_score))
            if arc.overall.voiced_s >= 1.0 and arc.overall.prosody_score > 0
            else p_sc
        )
        arc.overall.prosody_score = overall
        us20 = scrub_contrived_english(kv["us20s"] or heard[:260])
        chunk = scrub_contrived_english(kv["chunk"] or " ".join(us20.split()[:5]))

        self.repo.clear_active_studio(user_id)
        self.repo.log_turn(
            user_id, "learner", angle, heard,
            chat_message_id=chat_message_id, is_voice=True, duration_s=duration_s,
        )
        self.repo.save_lab_baseline(
            user_id, heard[:260], us20[:260], arc.overall.to_json(), arm_retake=False
        )
        return MonologueReport(
            verbatim_heard=heard,
            arc=arc,
            overall_score=overall,
            discourse_feedback=kv["discourse"] or "Use spoken pivots ('That said,', 'The catch is') instead of bookish transitions.",
            stamina_feedback=kv["stamina"] or "Keep stretching stressed content vowels through the closing third.",
            sounds_feedback=kv["sounds"] or "Maintain schwa reduction on unstressed function words.",
            tight_us_20s=us20,
            key_chunk=chunk,
        )

    # ================= BONUS: PERSONALIZED HABIT REMEDIAL SYNTHESIZER =================
    async def build_remedial_drill(self, user_id: int) -> tuple[str, str, str]:
        habits = self.repo.top_habits(user_id, limit=3)
        if not habits:
            return self.pick_prosody_drill(user_id, "linking")
        h_desc = "; ".join(
            f"{HABIT_LABELS.get(h['habit_key'], h['habit_key'])} (e.g. {h['last_example']})"
            for h in habits
        )
        sys_p = (
            "Create ONE natural, plain 12-18 word spoken American sentence that simultaneously "
            "trains the learner's recurring weak spots.\n" + SPOKEN_US_DNA + "\n"
            "Return plain tags:\n===SENTENCE===\n[Plain spoken US sentence]\n"
            "===MAP===\n[US prosodic map with ˈCAPS, /ə/, ‿, [ɾ], [t̚], ↘]\n"
            "===FOCUS===\n[1-line articulatory & grammatical focus note]"
        )
        try:
            raw = await self.llm.generate_text(
                user_id, sys_p, f"Top recurring habits: {h_desc}", temperature=0.35, deep_chat=False
            )
            kv = parse_kv_tags(raw, ("SENTENCE", "MAP", "FOCUS"))
            sent = scrub_contrived_english(kv["sentence"])
            if sent and len(sent.split()) >= 5:
                mp = kv["map"] or sent
                fc = kv["focus"] or f"Targets: {h_desc[:100]}"
                self.repo.save_lab_baseline(user_id, sent, sent, "", arm_retake=True)
                return sent, mp, fc
        except Exception:
            pass
        return self.pick_prosody_drill(user_id, "stress")

    def pick_prosody_drill(self, user_id: int, category: str = "linking") -> tuple[str, str, str]:
        pool = LAB_DRILL_BANK.get(category) or LAB_DRILL_BANK["linking"]
        sentence, p_map, focus_note = random.choice(pool)
        self.repo.save_lab_baseline(user_id, sentence, sentence, "", arm_retake=True)
        return sentence, p_map, focus_note

    async def maybe_sync_memory(self, user_id: int) -> bool:
        u = self.repo.get_user(user_id)
        turns_now = int(u["turns_count"])
        old_mem, last_synced = self.repo.get_memory(user_id)
        if turns_now - last_synced < 6:
            return False
        learner_lines = [
            t["content"] for t in self.repo.recent_turns(user_id, 16)
            if t["role"] == "learner" and not t["content"].startswith("(voice")
        ]
        if len(learner_lines) < 3:
            return False
        sys_p = (
            "Extract up to 5 concise bullet points ('• ...') of durable context about this speaker. "
            "Merge with existing notes, plain text only, max 70 words."
        )
        usr_p = f"Existing:\n{old_mem or '(none)'}\n\nRecent:\n" + "\n".join(
            f"- {ln}" for ln in learner_lines[-8:]
        )
        try:
            merged = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.2, deep_chat=False)
            if merged and merged.strip():
                self.repo.save_memory(user_id, merged, turns_now)
                return True
        except Exception:
            return False
        return False

    def switch_partner(self, user_id: int, angle: str) -> tuple[str, str, str]:
        chosen = self.repo.set_angle(user_id, angle)
        meta = ANGLE_META[chosen]
        return chosen, meta["badge"], meta["opener"]

    def spark_topic(self, user_id: int) -> tuple[str, str, str]:
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        recent_partner = {t["content"] for t in self.repo.recent_turns(user_id, 10) if t["role"] == "partner"}
        pool = [s for s in SPARK_BANK[angle] if s not in recent_partner] or SPARK_BANK[angle]
        line = random.choice(pool)
        self.repo.set_pending_take2(user_id, "")
        return angle, ANGLE_META[angle]["badge"], line

    async def shift_angle_live(self, user_id: int) -> ShiftOutcome:
        async with self._lock(user_id):
            new_a = self.repo.cycle_angle(user_id)
            meta = ANGLE_META[new_a]
            last_l = next(
                (
                    t["content"] for t in reversed(self.repo.recent_turns(user_id, 10))
                    if t["role"] == "learner"
                    and not PURE_GREETING_RE.match(t["content"].strip())
                    and len(t["content"].split()) >= 2
                ),
                "",
            )
            if not last_l:
                return ShiftOutcome(new_a, meta["badge"], meta["name"], meta["opener"])
            sys_p = (
                f"{meta['voice_desc']}\n\n{SPOKEN_US_DNA}\n\n{REGISTER_CORPUS_RULES}\n\n"
                f"1. `reframed_user_point`: How a native American expresses the speaker's exact point in {meta['badge']}. "
                "If everyday speech stays identical, set `same_across_registers=true`.\n"
                "2. `partner_reply`: Reply naturally in character (1-2 crisp sentences).\n"
                "3. `key_chunk`: Reusable 2-5 word spoken US chunk."
            )
            fallback = ShiftTurnOutput(partner_reply=meta["opener"])
            res = await self.llm.generate_structured(
                user_id, sys_p, f"Speaker's last message: \"{last_l}\"",
                ShiftTurnOutput, fallback, deep_chat=True, temperature=0.25,
            )
            reframed = scrub_contrived_english(res.reframed_user_point)
            reply_clean = (
                " ".join(clean_partner_reply(res.partner_reply))
                if res.partner_reply.strip()
                else meta["opener"]
            )
            return ShiftOutcome(
                angle=new_a,
                badge=meta["badge"],
                partner_name=meta["name"],
                partner_reply=reply_clean,
                reframed_point=reframed,
                same_across_registers=bool(res.same_across_registers),
                key_chunk=scrub_contrived_english(res.key_chunk),
            )

    async def explain_registers(self, user_id: int, target_text: str) -> str:
        clean_in = target_text.strip()
        sys_p = (
            "You are a corpus-grounded American English pragmatics specialist (COCA Spoken).\n\n"
            + REGISTER_CORPUS_RULES
        )
        usr_p = (
            f"Utterance: \"{clean_in}\"\n"
            "Give authentic spoken US phrasing across Everyday, Workplace, and Seminar settings. "
            "Never invent thesaurus jargon; if plain words stay identical, set is_register_neutral=true."
        )
        fallback = RegisterBreakdown(
            is_register_neutral=True,
            casual_us=clean_in,
            work_us=clean_in,
            seminar_us=clean_in,
            pragmatic_note="Plain spoken US phrasing works naturally across settings.",
        )
        res = await self.llm.generate_structured(
            user_id, sys_p, usr_p, RegisterBreakdown, fallback, deep_chat=True, temperature=0.25
        )
        cas = scrub_contrived_english(res.casual_us or clean_in)
        wrk = scrub_contrived_english(res.work_us or cas)
        sem = scrub_contrived_english(res.seminar_us or wrk)
        note = scrub_contrived_english(res.pragmatic_note)
        if res.is_register_neutral:
            return f"• Everyday: \"{cas}\"\n• Workplace: \"{wrk}\"\n• Seminar: \"{sem}\"\n💡 Real US usage: {note}"
        return f"• Everyday (Malik): \"{cas}\"\n• Workplace (Claire): \"{wrk}\"\n• Seminar (Prof. Vance): \"{sem}\"\n💡 What shifts: {note}"

    async def coach_on_demand(self, user_id: int, learner_text: str) -> GhostTwinAnalysis | None:
        if not learner_text.strip():
            return None
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        transfers = scan_transfers(learner_text)
        hint = (
            "Slips spotted: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers)
            if transfers
            else "Give the plainest spoken US phrasing & spoken grammar frame."
        )
        sys_p = (
            "You are a corpus-grounded spoken American English coach.\n"
            + SPOKEN_US_DNA
            + "\nSet should_intervene=true and fill habit_key, fix_summary, rhythm_tip, native_take, key_chunk, why_tag."
        )
        usr_p = f"Context: {ANGLE_META[angle]['badge']}\n{hint}\nUser said: {learner_text}"
        res = await self.llm.generate_structured(
            user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=True, temperature=0.25
        )
        res.native_take = scrub_contrived_english(res.native_take)
        res.key_chunk = scrub_contrived_english(res.key_chunk)
        if not res.native_take:
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take, res.why_tag or "us_coach")
        return res
