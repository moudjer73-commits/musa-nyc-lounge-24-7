import asyncio, random, re
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
from src.voice.acoustics import AcousticMetrics, analyze_voice_bytes, compare_metrics

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
            "Speak plain, direct workplace English—zero MBA buzzwords ('synergy', 'circle back', 'bandwidth'). "
            "Use natural phrasing like 'What's the main holdup?', 'Let's hold off for now', 'My only worry is...'."
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
            "'plays out', 'comes down to') paired with sharp analytical thinking—never stiff journal prose."
        ),
    },
}

SPARK_BANK = {
    "casual_sharp": [
        "A friend keeps canceling twenty minutes before you're supposed to meet up, then texts 'my bad, crazy week.' Do you say something or let it fade?",
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
        "When a city policy works well in places like Barcelona or Tallinn, what usually trips it up when someone tries copying it elsewhere?",
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
            "Palatalize 'What did you' -> [wʌdʒə]; Stop-T [t̚] on 'at' and 'market'; falling WH-tone ↘.",
        ),
    ],
    "stress": [
        (
            "How does that actually hold up at the municipal level?",
            "ˈHOW dəz ðæt̚ ˈAC-tʃu-ə-li ˈHOLD‿ˈUP | ət̚ ðə myu-ˈNI-sə-pəl ˈLE-vəl ↘",
            "Stress 2nd syllable in mu-NI-ci-pal (crush remaining vowels to /ə/); avoid French-style final stress.",
        ),
        (
            "Local officials want to participate, but the budget isn't there.",
            "ˈLO-kəl ə-ˈFI-ʃəlz ˈWAN-ə pɑr-ˈTI-sə-peɪt̚ | bət̚ ðə ˈBUD-dʒɪt̚ ˈIZ-ənt ˈTHERE ↘",
            "Stress par-TI-ci-pate & ə-FFI-cials; crush 'officials/participate/but' unstressed syllables to schwa.",
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
            "Continuation rise ↗ on 'PA-per' (unfinished thought), then decisive pitch fall ↘ on 'OFF'.",
        ),
        (
            "I didn't say the policy failed—I said the timing was off.",
            "I ˈDID-ənt ˈSAY ðə ˈPA-lə-si ˈFAILED ↗ | I ˈSAID ðə ˈTI-mɪŋ wəz ˈOFF ↘",
            "Contrastive nuclear peaks on PA-licy FAILED vs TI-ming OFF ↘; crush 'was' -> /wəz/.",
        ),
        (
            "My main worry isn't the technology, it's the follow-through.",
            "my ˈMAIN ˈWOR-ri ˈIZ-ənt ðə tɛk-ˈNA-lə-dʒi ↗ | ɪts ðə ˈFA-loʊ-θru ↘",
            "Hold slight rise ↗ after tech-NO-lo-gy, then drop pitch cleanly through FOL-low-through ↘.",
        ),
    ],
}

AI_OPENERS_RE = re.compile(
    r"^(?:certainly|absolutely|great question|that's a great point|i'd be happy to|of course|sure thing)[!.,:\s]+",
    re.IGNORECASE,
)
COACH_SPLIT_RE = re.compile(r"\n*={2,}\s*COACH\s*={2,}\s*\n*", re.IGNORECASE)
VOICE_HEARD_RE = re.compile(
    r"={2,}\s*HEARD\s*={2,}\s*(.*?)\s*={2,}\s*REPLY\s*={2,}\s*(.*)",
    re.IGNORECASE | re.DOTALL,
)
PURE_GREETING_RE = re.compile(
    r"^(?:hi|hey|hello|yo|hey there|hi there|good morning|good evening|thanks|thank you|great thank you|ok|okay|cool|nice)[!.?,\s]*$",
    re.IGNORECASE,
)


class ProsodyAuditOutput(BaseModel):
    verbatim_heard: str = ""
    has_findings: bool = True
    habit_key: str = "us_rhythm_linking"
    prosodic_map: str = ""
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
    habit_badge: str = ""
    delta_block: str = ""
    is_retake: bool = False


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
    for p in raw.split("---"):
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", p.strip())
        s = re.sub(r"(?m)^\s*[-*•#]+\s+", "", s)
        s = re.sub(r"\s{2,}", " ", AI_OPENERS_RE.sub("", s).strip())
        s = scrub_contrived_english(s)
        if s:
            cleaned.append(s)
    return cleaned or ["Sorry, lost my train of thought—say that one more time?"]


def _clean_field(val: str) -> str:
    v = val.strip().strip('"“”')
    return "" if v.upper() in ("", "NONE", "N/A", "FALSE") else v


def _word_overlap_ratio(a: str, b: str) -> float:
    wa = set(re.findall(r"[a-z']+", a.lower()))
    wb = set(re.findall(r"[a-z']+", b.lower()))
    if len(wa) < 3 or len(wb) < 3:
        return 0.0
    return len(wa & wb) / float(min(len(wa), len(wb)))


def infer_habit_key(learner_text: str, fix_summary: str, rhythm_tip: str, raw_habit: str) -> str:
    cand = raw_habit.strip().lower().replace(" ", "_")
    if cand in HABIT_LABELS:
        return cand
    low_t = learner_text.lower()
    if re.search(r"\b(?:buyed|goed|wented|teached|thinked|catched|bringed|finded|knowed|selled|telled)\b", low_t):
        return "irregular_past"
    if re.search(r"\bmany\s+(?:fruit|fruits|information|advice|furniture|equipment|work|research)\b|\b(?:informations|advices|researches)\b", low_t):
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
    parts = COACH_SPLIT_RE.split(raw.strip(), maxsplit=1)
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


def parse_voice_turn_output(raw: str) -> tuple[str, list[str], GhostTwinAnalysis | None]:
    m = VOICE_HEARD_RE.search(raw.strip())
    if m:
        heard = m.group(1).strip().strip('"“”')
        segs, coach = parse_reply_and_coach(m.group(2))
        return heard, segs, coach
    segs, coach = parse_reply_and_coach(raw)
    return "", segs, coach


def _phrase_matched(phrase: str, text: str) -> bool:
    low = text.lower()
    if phrase.lower() in low:
        return True
    toks = [w for w in re.findall(r"[a-z']+", phrase.lower()) if len(w) > 2]
    if len(toks) >= 3:
        return sum(1 for t in toks if re.search(r"\b" + re.escape(t[:5]), low)) >= len(toks) - 1
    return False


PROSODY_AUDITOR_PROMPT = (
    "You are a clinical & studio American English Suprasegmental Prosody Coach working with an advanced "
    "Maghrebi Arabic / French-bilingual researcher.\n"
    "CRITICAL SCIENTIFIC RULES:\n"
    "1. Diagnose STRICTLY what is physically audible in the attached recording + the Praat-style DSP telemetry—NEVER invent errors from your suggested recast.\n"
    "2. Provide `verbatim_heard`: exact words spoken in the audio.\n"
    "3. Build `prosodic_map`: render the target natural US sentence as an immediate visual performance score using:\n"
    "   - CAPS + primary stress mark ˈ on stressed syllables (e.g. ˈPICKED, mu-ˈNI-sə-pəl)\n"
    "   - ‿ for consonant-to-vowel/glide linking (e.g. picked‿up‿ə)\n"
    "   - /ə/ or phonetic respelling for crushed function words (tə, ə, fər, kən, ən)\n"
    "   - [ɾ] for intervocalic Flap-T and [t̚] for unreleased Stop-T where relevant\n"
    "   - | for thought-group pauses and ↘ / ↗ for boundary pitch direction.\n"
    "4. `segmental_fix`: ONLY if audible, pinpoint lexical stress shifts (e.g. French L2 final-syllable stress like de-ve-LOP -> de-ˈVEL-əp), unreduced full vowels that need schwa /ə/, /p/-/b/, alveolar /t,d/, or American /ɹ/. Leave empty if clean.\n"
    "5. `prosody_fix`: Concrete connected-speech & rhythm instruction tied to their nPVI, stress contrast dB, pause ratio, Flap-T [ɾ], Stop-T [t̚], or C-V linking.\n"
    "6. `focus_stress`: The 2-5 word nuclear peak phrase with the single peak syllable in CAPS + pitch arrow (e.g. 'BUNCH of FRUIT ↘').\n"
    "7. `native_recast`: Plain, authentic spoken American phrasing of their exact thought (ZERO slang, ZERO corporate/academic thesaurus words).\n"
    "8. `shadow_chunk`: Best 4-9 word phrase from `native_recast` for 3-beat shadowing.\n"
    "9. `habit_key`: Choose ONE from [us_rhythm_linking, word_stress, flap_stop_t, nuclear_intonation, fr_calque, ar_calque, irregular_past, uncountable_noun, stiff_register].\n"
    "10. If this is an A/B RETAKE, fill `retake_verdict` with a 1-sentence concrete comparison of what sounded more American on this take."
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

    def _apply_habit_tracking(self, uid: int, learner_text: str, coach: GhostTwinAnalysis | None, repair: bool) -> None:
        if repair or PURE_GREETING_RE.match(learner_text.strip()):
            return
        if coach is not None:
            h_key = infer_habit_key(learner_text, coach.fix_summary, coach.rhythm_tip, coach.habit_key)
            coach.habit_key = h_key
            lbl, hits = self.repo.record_habit_hit(
                uid, h_key, learner_text[:120], coach.fix_summary or coach.rhythm_tip or coach.native_take[:120]
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
        mem_blk = f"\nKNOWN SPEAKER CONTEXT (use naturally only when relevant, never recite):\n{mem_text}" if mem_text else ""
        hab_blk = (
            "\nRECURRING HABITS RADAR: "
            + "; ".join(f"{h['habit_key']} ({h['hits']}x, e.g. '{h['last_example']}')" for h in habits)
            if habits
            else ""
        )
        return mem_blk, hab_blk

    def _sys_prompt(self, uid: int, angle: str, text: str, recent: list[dict], due: list[dict], take2: str, repair: bool) -> str:
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
            "- Match the speaker's length and energy: if they wrote 1-6 words, reply in 1 plain sentence (6-16 words).",
            "- Plain text only in your character reply (no bold, no bullets, no grammar commentary).",
        ]
        if repair:
            r.append("- PRAGMATIC REPAIR: Explain warmly in plain US English ('Oh my bad—I just meant...') and keep rolling.")
        elif two_q:
            r.append("- Your last two replies ended with questions: respond with a grounded take WITHOUT ending with a question.")
        if detect_code_switch(text) and not repair:
            r.append("- L1 BRIDGE: Answer naturally AND include the exact plain spoken US idiom in [square brackets] inside your reply.")
        if take2 and not repair:
            r.append(f"- TAKE 2 RETRY: Briefly note in 3-6 words what sounded cleaner ({take2}), then continue.")
        for v in (due[:1] if not repair else []):
            if int(v["stage"]) == 0:
                r.append(f"- STEALTH MODEL: Only if natural, use '{v['phrase']}' once.")
            else:
                r.append(f"- STEALTH BAIT: Create a natural opening for '{v['phrase']}' without saying it yourself.")

        if ECHO_TWIN_ENABLED and not repair and not PURE_GREETING_RE.match(text.strip()) and wc >= 2:
            hint = f" (Pre-detected slips you MUST fix: {'; '.join(s + ' -> ' + u for s, u, _ in transfers)})" if transfers else ""
            allow_t2 = (wc >= 28 or len(transfers) >= 2) and not take2
            t2_line = "TAKE2: [1-sentence retry challenge ONLY if major, else NONE]" if allow_t2 else "TAKE2: NONE"
            r.append(
                f"\nSILENT COACH CHECK (APPEND AFTER YOUR CHARACTER REPLY):{hab_blk}"
                f"\nInspect LATEST SPEAKER MESSAGE for US English accuracy & genuine spoken naturalness.{hint}"
                "\n- Ignore casual texting punctuation/capitalization."
                "\n- If ALREADY 100% grammatically accurate AND natural spoken US English, do NOT append anything."
                "\n- Otherwise append:\n===COACH==="
                "\nHABIT: [irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register]"
                "\nFIX: [concise arrow fixes]"
                "\nUS: [plainest natural sentence a real American says out loud—zero slang/buzzwords]"
                "\nCHUNK: [reusable 2-5 word everyday US chunk]"
                "\nWHY: [2-5 word tag]"
                f"\n{t2_line}"
            )
        return "\n".join(r)

    async def respond_turn(
        self, user_id: int, learner_text: str, *, chat_message_id: int | None = None, is_voice: bool = False, duration_s: float = 0.0, log_partner: bool = True
    ) -> TurnOutcome:
        async with self._lock(user_id):
            u = self.repo.get_user(user_id)
            angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
            take2, recent, due = u["pending_take2"], self.repo.recent_turns(user_id, 12), self.repo.due_vault_phrases(user_id, 1)
            repair = is_repair_request(learner_text)
            sys_p = self._sys_prompt(user_id, angle, learner_text, recent, due, take2, repair)
            hist = "\n".join(f"{'You' if t['role']=='partner' else 'Speaker'}: {t['content']}" for t in recent[-10:])
            usr_p = f"RECENT DIALOGUE:\n{hist}\n\nLATEST SPEAKER MESSAGE: {learner_text}\nYour reply:" if hist else f"LATEST SPEAKER MESSAGE: {learner_text}\nYour reply:"
            raw = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.62, deep_chat=True)
            segs, inline_coach = parse_reply_and_coach(raw)
            full = "\n\n".join(segs)
            vhits = self.detect_vault_hits(user_id, learner_text)
            self.repo.log_turn(user_id, "learner", angle, learner_text, chat_message_id=chat_message_id, is_voice=is_voice, duration_s=duration_s)
            if log_partner:
                self.repo.log_turn(user_id, "partner", angle, full)
            if take2:
                self.repo.set_pending_take2(user_id, "")
            if due and int(due[0]["stage"]) == 0 and due[0]["phrase"].lower() in full.lower():
                self.repo.mark_vault_hit(user_id, due[0]["phrase"])
            self._apply_habit_tracking(user_id, learner_text, inline_coach, repair)
            return TurnOutcome(segs, full, angle, ANGLE_META[angle]["badge"], learner_text, vhits, bool(take2), repair, inline_coach)

    async def respond_voice_turn(
        self, user_id: int, audio_bytes: bytes, *, mime_type: str = "audio/ogg", chat_message_id: int | None = None, duration_s: float = 0.0, log_partner: bool = True
    ) -> TurnOutcome:
        async with self._lock(user_id):
            u = self.repo.get_user(user_id)
            angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
            meta = ANGLE_META[angle]
            weekday = datetime.now(timezone.utc).strftime("%A")
            take2, recent, due = u["pending_take2"], self.repo.recent_turns(user_id, 12), self.repo.due_vault_phrases(user_id, 1)
            mem_blk, hab_blk = self._context_blocks(user_id)
            bot_t = [t["content"] for t in recent if t["role"] == "partner"]
            two_q = len(bot_t) >= 2 and all(x.rstrip().endswith("?") for x in bot_t[-2:])

            rules = [
                f"{meta['voice_desc']} (Today is {weekday}.){mem_blk}",
                SPOKEN_US_DNA,
                "MULTIMODAL SPOKEN TURN: Listen to the speaker's voice note and reply naturally in 1-2 crisp spoken US sentences (plain text only).",
            ]
            if two_q:
                rules.append("- Your last two replies ended with questions: land a statement WITHOUT ending with a question.")
            rules.append(
                f"\nOUTPUT FORMAT:{hab_blk}"
                "\n===HEARD===\n[Exact verbatim transcript; write [inaudible] ONLY if silent]"
                "\n===REPLY===\n[Your natural in-character spoken US reply]"
                "\n===COACH===\nNONE"
            )
            hist = "\n".join(f"{'You' if t['role']=='partner' else 'Speaker'}: {t['content']}" for t in recent[-10:])
            usr_p = f"RECENT DIALOGUE:\n{hist}\n\nListen to the voice note ({duration_s:.0f}s) and respond." if hist else f"Listen to the voice note ({duration_s:.0f}s) and respond."
            raw = await self.llm.generate_voice_turn(user_id, "\n".join(rules), audio_bytes, usr_p, mime_type=mime_type, temperature=0.62)
            heard, segs, _ = parse_voice_turn_output(raw)
            full = "\n\n".join(segs)
            if heard.lower() in ("[inaudible]", "[silence]", "inaudible"):
                return TurnOutcome(segs, full, angle, meta["badge"], "", [], False, True, None)
            transcript = heard or "(voice note)"
            repair = is_repair_request(transcript)
            vhits = self.detect_vault_hits(user_id, transcript) if heard else []
            self.repo.log_turn(user_id, "learner", angle, transcript, chat_message_id=chat_message_id, is_voice=True, duration_s=duration_s)
            if log_partner:
                self.repo.log_turn(user_id, "partner", angle, full, is_voice=True)
            if take2:
                self.repo.set_pending_take2(user_id, "")
            if due and int(due[0]["stage"]) == 0 and due[0]["phrase"].lower() in full.lower():
                self.repo.mark_vault_hit(user_id, due[0]["phrase"])
            return TurnOutcome(segs, full, angle, meta["badge"], transcript, vhits, bool(take2), repair, None)

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
        wc_init = len(transcript.split()) if transcript and transcript != "(voice note)" else 0
        dsp = await asyncio.to_thread(analyze_voice_bytes, audio_bytes, wc_init)

        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        lab_state = self.repo.get_lab_state(user_id)
        prev_target = lab_state.get("target_text", "")
        prev_recast = lab_state.get("recast_text", "")
        prev_dsp = AcousticMetrics.from_json(lab_state.get("metrics_json", ""))

        auto_repeat = bool(
            transcript
            and (_word_overlap_ratio(transcript, prev_target) >= 0.55 or _word_overlap_ratio(transcript, prev_recast) >= 0.55)
        )
        is_retake = bool(force_retake or lab_state.get("pending_retake") or auto_repeat)

        transfers = scan_transfers(transcript) if transcript else []
        tr_hint = ("Pre-detected grammar/calque slips: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers)) if transfers else "None"
        if is_retake:
            retake_ctx = (
                f"A/B RETAKE MODE ACTIVE — Target practiced: \"{prev_recast or prev_target}\"\n"
                f"Previous DSP baseline: {prev_dsp.lab_meter_line() if prev_dsp else 'N/A'}\n"
                "Fill `retake_verdict` with a concrete 1-sentence comparison of what improved."
            )
        elif prev_recast or prev_target:
            retake_ctx = (
                f"PREVIOUS TAKE ON FILE: \"{prev_recast or prev_target}\" "
                f"(Baseline: {prev_dsp.lab_meter_line() if prev_dsp else 'N/A'}).\n"
                "If this new audio repeats/practices that same sentence, treat as an A/B RETAKE and fill `retake_verdict`; "
                "if it is a brand-new sentence, leave `retake_verdict` empty."
            )
        else:
            retake_ctx = "First take on this thought (`retake_verdict` = empty)."
        usr_p = (
            f"Register context: {ANGLE_META[angle]['badge']}\n"
            f"{dsp.to_prompt_summary()}\n"
            f"Transcript hint: \"{transcript or '(transcribe directly from audio)'}\"\n"
            f"{tr_hint}\n"
            f"{retake_ctx}\n"
            "Audit the attached voice recording now."
        )
        fb = ProsodyAuditOutput(
            verbatim_heard=transcript,
            has_findings=False,
            prosodic_map=transcript,
            prosody_fix="Keep content words long and crush function words (to, of, for) to schwa /ə/.",
            native_recast=transcript,
            shadow_chunk=" ".join(transcript.split()[:7]),
        )
        res = await self.llm.audit_voice_prosody(user_id, PROSODY_AUDITOR_PROMPT, audio_bytes, usr_p, ProsodyAuditOutput, fb, mime_type=mime_type)

        heard_final = (res.verbatim_heard or transcript).strip()
        if not heard_final or heard_final.lower() in ("[inaudible]", "[silence]", "inaudible"):
            return None

        # Refine articulation WPM using exact verbatim word count when transcript wasn't known beforehand
        wc_final = len(heard_final.split())
        if wc_init == 0 and wc_final > 0 and dsp.voiced_s >= 0.4:
            dsp.articulation_wpm = round((wc_final / dsp.voiced_s) * 60.0, 1)

        if not is_retake and prev_dsp and (
            _word_overlap_ratio(heard_final, prev_target) >= 0.55 or _word_overlap_ratio(heard_final, prev_recast) >= 0.55
        ):
            is_retake = True

        if log_learner_turn:
            self.repo.log_turn(user_id, "learner", angle, heard_final, chat_message_id=chat_message_id, is_voice=True, duration_s=duration_s)
            self.detect_vault_hits(user_id, heard_final)

        res.native_recast = scrub_contrived_english(res.native_recast or heard_final)
        res.shadow_chunk = scrub_contrived_english(res.shadow_chunk or " ".join(res.native_recast.split()[:7]))
        if not res.prosodic_map.strip():
            res.prosodic_map = res.native_recast
        if not res.prosody_fix.strip():
            res.prosody_fix = "Clean connected US rhythm—keep stretching nuclear stressed vowels."

        hab_badge = ""
        if res.has_findings:
            h_key = res.habit_key.strip().lower()
            if h_key not in HABIT_LABELS:
                h_key = "us_rhythm_linking"
            lbl, hits = self.repo.record_habit_hit(
                user_id, h_key, heard_final[:120], res.prosody_fix or res.segmental_fix or res.grammar_fix
            )
            if hits >= 2:
                hab_badge = f"Habit #{hits}: {lbl}"
            if res.native_recast:
                self.repo.log_upgrade(user_id, heard_final, res.native_recast, lbl)
        elif wc_final >= 4:
            self.repo.record_clean_turn(user_id)

        delta_txt = compare_metrics(prev_dsp, dsp) if (is_retake and prev_dsp) else ""
        if is_retake and not res.retake_verdict.strip() and prev_dsp:
            diff_sc = dsp.prosody_score - prev_dsp.prosody_score
            if diff_sc >= 3:
                res.retake_verdict = f"Stronger stress-timed rhythm (+{diff_sc} pts) and cleaner syllable contrast on this take."
            elif diff_sc <= -3:
                res.retake_verdict = "Slightly flatter than Take 1—crush function words more and stretch the peak vowel."
            else:
                res.retake_verdict = "Consistent acoustic contour—push stressed vowel length a bit further on the next rep."
        self.repo.save_lab_baseline(user_id, heard_final, res.native_recast, dsp.to_json(), arm_retake=False)
        return VoiceLabReport(
            audit=res,
            dsp=dsp,
            dsp_badge=dsp.lab_meter_line(),
            habit_badge=hab_badge,
            delta_block=delta_txt,
            is_retake=is_retake,
        )

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
        learner_lines = [t["content"] for t in self.repo.recent_turns(user_id, 16) if t["role"] == "learner" and t["content"] != "(voice note)"]
        if len(learner_lines) < 3:
            return False
        sys_p = (
            "Extract up to 5 ultra-concise bullet points ('• ...') capturing durable context about this speaker "
            "(work/research themes, interests, projects). Merge with existing notes, plain text only, max 70 words."
        )
        usr_p = f"Existing memory:\n{old_mem or '(none)'}\n\nRecent speaker lines:\n" + "\n".join(f"- {ln}" for ln in learner_lines[-8:])
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
                (t["content"] for t in reversed(self.repo.recent_turns(user_id, 10))
                 if t["role"] == "learner" and not PURE_GREETING_RE.match(t["content"].strip()) and len(t["content"].split()) >= 2),
                "",
            )
            if not last_l:
                return ShiftOutcome(new_a, meta["badge"], meta["name"], meta["opener"])
            sys_p = (
                f"{meta['voice_desc']}\n\n{SPOKEN_US_DNA}\n\n{REGISTER_CORPUS_RULES}\n\n"
                "TASK FOR LIVE REGISTER SHIFT:\n"
                f"1. `reframed_user_point`: How a native American naturally expresses the speaker's exact point in {meta['badge']}. "
                "NEVER invent fake corporate/academic jargon! If everyday speech stays identical, set `same_across_registers=true`.\n"
                "2. `partner_reply`: Respond naturally in character (1-2 crisp spoken sentences).\n"
                "3. `key_chunk`: Reusable 2-5 word spoken US chunk."
            )
            fallback = ShiftTurnOutput(partner_reply=meta["opener"])
            res = await self.llm.generate_structured(
                user_id, sys_p, f"Speaker's last message: \"{last_l}\"", ShiftTurnOutput, fallback, deep_chat=True, temperature=0.25
            )
            if has_contrived_buzz(f"{res.reframed_user_point} {res.partner_reply}"):
                res = await self.llm.generate_structured(
                    user_id,
                    sys_p + "\nCRITICAL: Strip ALL buzzwords, slang, or stiff thesaurus words. Plain spoken US words only.",
                    f"Speaker's last message: \"{last_l}\"",
                    ShiftTurnOutput,
                    res,
                    deep_chat=True,
                    temperature=0.15,
                )
            reframed = scrub_contrived_english(res.reframed_user_point)
            reply_clean = " ".join(clean_partner_reply(res.partner_reply)) if res.partner_reply.strip() else meta["opener"]
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
        sys_p = "You are a corpus-grounded American English pragmatics specialist (COCA Spoken standard).\n\n" + REGISTER_CORPUS_RULES
        usr_p = (
            f"Utterance: \"{clean_in}\"\n\n"
            "Give authentic spoken American phrasing across Everyday, Workplace, and Seminar settings:\n"
            "- Fix any grammar or L1 slip first.\n"
            "- NEVER invent thesaurus differences! If Americans use the exact same plain words across settings, set is_register_neutral=true.\n"
            "- In `pragmatic_note`, explain in 1 short plain sentence what actually shifts (or why it stays plain)."
        )
        fallback = RegisterBreakdown(
            is_register_neutral=True,
            casual_us=clean_in,
            work_us=clean_in,
            seminar_us=clean_in,
            pragmatic_note="Everyday spoken US phrasing stays plain across registers.",
        )
        res = await self.llm.generate_structured(
            user_id, sys_p, usr_p, RegisterBreakdown, fallback, deep_chat=True, temperature=0.25
        )
        if has_contrived_buzz(f"{res.casual_us} {res.work_us} {res.seminar_us}"):
            res = await self.llm.generate_structured(
                user_id,
                sys_p + "\nCRITICAL: Zero corporate clichés, zero slang, zero stiff written words ('procure', 'utilize', 'produce'). Plain spoken words only!",
                usr_p,
                RegisterBreakdown,
                res,
                deep_chat=True,
                temperature=0.15,
            )
        cas = scrub_contrived_english(res.casual_us or clean_in)
        wrk = scrub_contrived_english(res.work_us or cas)
        sem = scrub_contrived_english(res.seminar_us or wrk)
        note = scrub_contrived_english(res.pragmatic_note or "Shifts via tone and hedging, never fancy vocabulary.")
        if res.is_register_neutral:
            return f"• Everyday: \"{cas}\"\n• Workplace: \"{wrk}\"\n• Seminar: \"{sem}\"\n💡 Real US usage: {note}"
        return f"• Everyday (Malik): \"{cas}\"\n• Workplace (Claire): \"{wrk}\"\n• Seminar (Prof. Vance): \"{sem}\"\n💡 What actually shifts: {note}"

    async def coach_on_demand(self, user_id: int, learner_text: str) -> GhostTwinAnalysis | None:
        if not learner_text.strip():
            return None
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        transfers = scan_transfers(learner_text)
        hint = "Slips spotted: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers) if transfers else "Give the plainest, most natural spoken US phrasing."
        sys_p = (
            "You are a corpus-grounded spoken American English coach.\n"
            + SPOKEN_US_DNA
            + "\nSet should_intervene=true and fill habit_key, fix_summary, rhythm_tip, native_take, key_chunk, why_tag."
        )
        usr_p = f"Context: {ANGLE_META[angle]['badge']}\n{hint}\nUser said: {learner_text}"
        res = await self.llm.generate_structured(user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=True, temperature=0.25)
        res.native_take = scrub_contrived_english(res.native_take)
        res.key_chunk = scrub_contrived_english(res.key_chunk)
        if not res.native_take:
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take, res.why_tag or "us_coach")
        return res
