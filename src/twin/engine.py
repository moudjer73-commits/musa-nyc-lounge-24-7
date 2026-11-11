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

ANGLE_META = {
    "casual_sharp": {
        "name": "Malik",
        "short_btn": "⚡ Malik · Everyday",
        "badge": "⚡ Malik · Everyday US",
        "opener": "Hey, what's going on? How's your week going so far?",
        "voice_desc": (
            "You are Malik, an observant, easygoing 32-year-old American friend. "
            "Speak plain, relaxed everyday US English—natural contractions, common phrasal verbs (end up, figure out, "
            "turn out, run into), understated human warmth. Zero forced slang, zero trying to sound hip or theatrical."
        ),
    },
    "exec_clock": {
        "name": "Claire",
        "short_btn": "💼 Claire · Work/Exec",
        "badge": "💼 Claire · Workplace US",
        "opener": "Hey—got a few minutes before my next meeting. What are you working on?",
        "voice_desc": (
            "You are Claire, a sharp, grounded American colleague and program lead talking 1-on-1. "
            "Speak plain, direct, human workplace English—zero MBA buzzwords ('synergy', 'circle back', 'bandwidth', 'deliverables'). "
            "Real senior US colleagues say 'What's the main holdup?', 'Let's hold off for now', 'My only worry is...'. "
            "If the speaker mentions everyday life, respond like a normal human colleague without forcing work jargon onto it."
        ),
    },
    "seminar_skeptic": {
        "name": "Prof. Vance",
        "short_btn": "🎓 Prof. Vance · Seminar",
        "badge": "🎓 Prof. Vance · Seminar US",
        "opener": "Hey, come on in. What's an idea or question you've been thinking through lately?",
        "voice_desc": (
            "You are Nolan Vance, an American political-science professor talking in office hours or a seminar room. "
            "Speak authentic SPOKEN academic US English: 90% plain everyday spoken grammar (contractions ALWAYS, "
            "'I mean', 'the tricky part is', 'how does that play out when...', 'holds up', 'comes down to') paired with "
            "clear conceptual thinking. NEVER talk like a stiff written journal article."
        ),
    },
}

SPARK_BANK = {
    "casual_sharp": [
        "A friend keeps canceling twenty minutes before you're supposed to meet up, then texts 'my bad, crazy week.' Do you say something or just stop making plans?",
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
    has_findings: bool = False
    habit_key: str = 'us_rhythm_linking'
    segmental_fix: str = ''
    prosody_fix: str = ''
    focus_stress: str = ''
    shadow_chunk: str = ''
    native_recast: str = ''
    grammar_fix: str = ''

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
            "- Match the speaker's length and energy: if they wrote 1-6 words, reply in 1 plain, relaxed sentence (6-16 words), NEVER a long paragraph.",
            "- Plain text only in your character reply (no bold, no bullets, no grammar teaching inside character dialogue).",
        ]
        if repair:
            r.append("- PRAGMATIC REPAIR: The speaker didn't catch your last line. Drop any joke immediately, explain warmly in plain US English ('Oh my bad—I just meant...'), and keep rolling.")
        elif two_q:
            r.append("- Your last two replies ended with questions: respond with a grounded observation or take WITHOUT ending with a question.")

        if detect_code_switch(text) and not repair:
            r.append("- L1 BRIDGE: Speaker mixed Arabic/French hunting for a US expression. Answer their point naturally AND include the exact plain spoken US idiom in [square brackets] inside your reply.")
        if take2 and not repair:
            r.append(f"- TAKE 2 RETRY: You invited a retry ({take2}). Note briefly in 3-6 words what sounded cleaner, then continue.")
        for v in (due[:1] if not repair else []):
            if int(v["stage"]) == 0:
                r.append(f"- STEALTH MODEL: Only if it fits completely naturally, use '{v['phrase']}' once.")
            else:
                r.append(f"- STEALTH BAIT: Create a natural opening for the idea behind '{v['phrase']}' without saying the phrase yourself.")

        if ECHO_TWIN_ENABLED and not repair and not PURE_GREETING_RE.match(text.strip()) and wc >= 2:
            hint = f" (Pre-detected slips you MUST fix: {'; '.join(s + ' -> ' + u for s, u, _ in transfers)})" if transfers else ""
            allow_t2 = (wc >= 28 or len(transfers) >= 2) and not take2
            t2_line = "TAKE2: [1-sentence retry challenge ONLY if major, else NONE]" if allow_t2 else "TAKE2: NONE"
            r.append(
                f"\nSILENT COACH CHECK (APPEND AFTER YOUR CHARACTER REPLY):{hab_blk}"
                f"\nInspect LATEST SPEAKER MESSAGE for US English accuracy & genuine spoken naturalness.{hint}"
                "\n- Ignore casual texting punctuation/capitalization."
                "\n- If ALREADY 100% grammatically accurate AND natural spoken US English, do NOT append anything."
                "\n- If there is ANY grammar slip, Arabic/French calque, wrong preposition/tense, or stiff non-native wording, append:"
                "\n===COACH==="
                "\nHABIT: [irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register]"
                "\nFIX: [concise arrow fixes, e.g. buyed -> bought · many fruit -> a bunch of fruit]"
                "\nUS: [the simplest, most natural sentence a real American actually says out loud—ZERO forced slang, ZERO fancy thesaurus words]"
                "\nCHUNK: [reusable 2-5 word everyday US chunk from US]"
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
                "MULTIMODAL SPOKEN TURN — LISTEN DIRECTLY TO THE SPEAKER'S VOICE:",
                "- Hear both WHAT they say and HOW they sound (content-word stress, consonant-to-vowel linking, flap-T between vowels, function-word reduction like 'to' -> /tə/, 'of' -> /ə/).",
                "- Keep your character reply relaxed, natural, and concise (1-3 spoken sentences, plain text only).",
            ]
            if two_q:
                rules.append("- Your last two replies ended with questions: land a statement or reaction WITHOUT ending with a question.")
            if take2:
                rules.append(f"- TAKE 2 RETRY: You invited a retry ({take2}). Briefly note how this spoken take landed, then continue.")
            for v in due[:1]:
                if int(v["stage"]) == 0:
                    rules.append(f"- STEALTH MODEL: Only if natural, use '{v['phrase']}' once.")
                else:
                    rules.append(f"- STEALTH BAIT: Invite the idea behind '{v['phrase']}' without saying it yourself.")

            rules.append(
                f"\nOUTPUT FORMAT (FOLLOW EXACTLY):{hab_blk}"
                "\n===HEARD==="
                "\n[Exact verbatim transcript preserving contractions, slips, and any Arabic/French words; write [inaudible] ONLY if silent]"
                "\n===REPLY==="
                "\n[Your natural in-character spoken US reply; if speaker mixed Arabic/French, slip the plain US idiom in [brackets] inside your reply]"
                "\n===COACH==="
                "\n[Write NONE if grammar, phrasing, AND American spoken rhythm/linking are already completely natural. Otherwise provide:]"
                "\nHABIT: [irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register | us_rhythm_linking | word_stress]"
                "\nFIX: [arrow fixes for grammar/wording slips, or NONE if purely prosody]"
                "\nRHYTHM: [1 concrete physical US pronunciation tip heard in the clip: linking, flap-T, schwa reduction, or primary stress—never vague advice, or NONE]"
                "\nUS: [the simplest, most authentic spoken US phrasing of their exact thought—ZERO slang, ZERO fancy thesaurus words]"
                "\nCHUNK: [reusable 2-5 word everyday US chunk from US]"
                "\nWHY: [2-5 word label]"
                "\nTAKE2: [1-sentence spoken retry challenge ONLY if major, else NONE]"
            )
            hist = "\n".join(f"{'You' if t['role']=='partner' else 'Speaker'}: {t['content']}" for t in recent[-10:])
            usr_p = f"RECENT DIALOGUE:\n{hist}\n\nListen to the attached voice note ({duration_s:.0f}s) and respond in the exact format." if hist else f"Listen to the attached voice note ({duration_s:.0f}s) and respond in the exact format."

            raw = await self.llm.generate_voice_turn(user_id, "\n".join(rules), audio_bytes, usr_p, mime_type=mime_type, temperature=0.62)
            heard, segs, inline_coach = parse_voice_turn_output(raw)
            full = "\n\n".join(segs)

            if heard.lower() in ("[inaudible]", "[silence]", "inaudible"):
                return TurnOutcome(segs, full, angle, meta["badge"], "", [], False, True, None)

            transcript = heard or "(voice note)"
            repair = is_repair_request(transcript)
            if repair:
                inline_coach = None
            vhits = self.detect_vault_hits(user_id, transcript) if heard else []

            self.repo.log_turn(user_id, "learner", angle, transcript, chat_message_id=chat_message_id, is_voice=True, duration_s=duration_s)
            if log_partner:
                self.repo.log_turn(user_id, "partner", angle, full, is_voice=True)
            if take2:
                self.repo.set_pending_take2(user_id, "")
            if due and int(due[0]["stage"]) == 0 and due[0]["phrase"].lower() in full.lower():
                self.repo.mark_vault_hit(user_id, due[0]["phrase"])

            if heard:
                self._apply_habit_tracking(user_id, transcript, inline_coach, repair)
            return TurnOutcome(segs, full, angle, meta["badge"], transcript, vhits, bool(take2), repair, inline_coach)

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
            "(work/research themes, interests, ongoing projects) so partners remember them naturally. "
            "Merge with existing notes, drop trivial greetings, plain text only, max 70 words total."
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
                "1. `reframed_user_point`: Show how a native American would naturally express the speaker's exact point in THIS register ('"
                + meta["badge"]
                + "'). Fix any grammar slip first. NEVER invent fake corporate/academic jargon! If an American says this everyday sentence the same plain way here, set `same_across_registers=true` and give that plain natural US sentence.\n"
                "2. `partner_reply`: Respond naturally in character (1-2 crisp spoken sentences) to what the speaker said—if their topic is everyday life, respond like a normal human without forcing work/academic buzzwords onto it!\n"
                "3. `key_chunk`: Reusable 2-5 word spoken US chunk from `reframed_user_point`."
            )
            fallback = ShiftTurnOutput(
                reframed_user_point="",
                same_across_registers=False,
                partner_reply=meta["opener"],
                key_chunk="",
            )
            res = await self.llm.generate_structured(
                user_id, sys_p, f"Speaker's last message: \"{last_l}\"", ShiftTurnOutput, fallback, deep_chat=True, temperature=0.25
            )
            if has_contrived_buzz(f"{res.reframed_user_point} {res.partner_reply}"):
                res = await self.llm.generate_structured(
                    user_id,
                    sys_p + "\nCRITICAL: Strip ALL buzzwords, slang, or stiff thesaurus words. Keep every word plain spoken US English.",
                    f"Speaker's last message: \"{last_l}\"",
                    ShiftTurnOutput,
                    res,
                    deep_chat=True,
                    temperature=0.15,
                )
            reframed = scrub_contrived_english(res.reframed_user_point)
            reply_clean = " ".join(clean_partner_reply(res.partner_reply)) if res.partner_reply.strip() else meta["opener"]
            chunk_clean = scrub_contrived_english(res.key_chunk)
            return ShiftOutcome(
                angle=new_a,
                badge=meta["badge"],
                partner_name=meta["name"],
                partner_reply=reply_clean,
                reframed_point=reframed,
                same_across_registers=bool(res.same_across_registers),
                key_chunk=chunk_clean,
            )

    async def explain_registers(self, user_id: int, target_text: str) -> str:
        clean_in = target_text.strip()
        sys_p = "You are a corpus-grounded American English pragmatics specialist (COCA Spoken standard).\n\n" + REGISTER_CORPUS_RULES
        usr_p = (
            f"Utterance: \"{clean_in}\"\n\n"
            "Give the authentic spoken American phrasing across Everyday, Workplace, and Seminar settings:\n"
            "- Fix any grammar or L1 slip first.\n"
            "- Remember: NEVER invent thesaurus differences! If Americans use the exact same plain words across settings, set is_register_neutral=true.\n"
            "- In `pragmatic_note`, explain in 1 short plain sentence what actually changes (or why it stays plain)."
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
            return (
                f"• Everyday: \"{cas}\"\n"
                f"• Workplace: \"{wrk}\"\n"
                f"• Seminar: \"{sem}\"\n"
                f"💡 Real US usage: {note}"
            )
        return (
            f"• Everyday (Malik): \"{cas}\"\n"
            f"• Workplace (Claire): \"{wrk}\"\n"
            f"• Seminar (Prof. Vance): \"{sem}\"\n"
            f"💡 What actually shifts: {note}"
        )

    async def coach_on_demand(self, user_id: int, learner_text: str) -> GhostTwinAnalysis | None:
        if not learner_text.strip():
            return None
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        transfers = scan_transfers(learner_text)
        hint = "Slips spotted: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers) if transfers else "Point out any slip or give the plainest, most natural spoken US phrasing."
        sys_p = (
            "You are a corpus-grounded spoken American English coach.\n"
            + SPOKEN_US_DNA
            + "\nSet should_intervene=true and fill:\n"
            "- habit_key: irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register.\n"
            "- fix_summary: Short arrow fixes of any grammar/lexical slips, or empty if already grammatical.\n"
            "- rhythm_tip: Concrete US linking, flap-T, reduction, or stress tip when saying native_take aloud.\n"
            "- native_take: Their exact thought spoken naturally in first person by an American in 1-2 plain, crisp sentences (zero slang/buzzwords).\n"
            "- key_chunk: Reusable 2-5 word everyday American chunk from native_take.\n"
            "- why_tag: 2-5 word label."
        )
        usr_p = f"Context: {ANGLE_META[angle]['badge']}\n{hint}\nUser said: {learner_text}"
        res = await self.llm.generate_structured(user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=True, temperature=0.25)
        res.native_take = scrub_contrived_english(res.native_take)
        res.key_chunk = scrub_contrived_english(res.key_chunk)
        if not res.native_take:
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take, res.why_tag or "us_coach")
        return res

    async def run_deep_prosody_audit(
        self, user_id: int, audio_bytes: bytes, transcript: str, *, mime_type: str = 'audio/ogg'
    ) -> tuple[ProsodyAuditOutput | None, str, str]:
        from src.voice.acoustics import analyze_voice_bytes
        wc = len(transcript.split()) if transcript and transcript != '(voice note)' else 0
        if is_repair_request(transcript) or PURE_GREETING_RE.match(transcript.strip()):
            return None, '', ''
        dsp = await asyncio.to_thread(analyze_voice_bytes, audio_bytes, wc)
        if wc < 3 and dsp.voiced_s < 0.9:
            return None, '', ''
        u = self.repo.get_user(user_id)
        angle = u['angle'] if u['angle'] in ANGLE_META else 'casual_sharp'
        transfers = scan_transfers(transcript) if transcript else []
        tr_hint = ('Pre-detected lexical/grammar slips: ' + '; '.join(f'{s}->{up}' for s, up, _ in transfers)) if transfers else 'None'
        sys_p = (
            'You are an acoustic American English pronunciation & prosody specialist coaching an advanced Maghrebi Arabic / French-bilingual speaker.\n'
            'CRITICAL RULE: Diagnose ONLY what the speaker ACTUALLY pronounced in the attached audio—NEVER invent errors from the suggested recast.\n'
            'Ground your listening in the objective DSP telemetry provided:\n'
            '1. WORD STRESS & VOWEL REDUCTION: Spot French/L2 final-syllable stress shifts (e.g. dev-el-OP, par-ti-ci-PA-tive) or unreduced vowels where US English uses schwa /ə/ (to /tə/, of /ə/, for /fər/, can /kən/).\n'
            '2. CONNECTED SPEECH & RHYTHM: Check consonant-to-vowel linking, intervocalic Flap-T /ɾ/ (better, city, put_it_on), unreleased Stop-T /t̚/, and choppy mid-phrase pauses.\n'
            '3. SEGMENTAL PRECISION: Spot /p/ vs /b/ voicing, dental /t,d/ vs alveolar, th-stopping, or non-rhotic /r/ drops ONLY if audible.\n'
            '4. THOUGHT-GROUP FOCUS: Mark primary nuclear stress in CAPS inside `focus_stress` on the 3-7 word `shadow_chunk` the speaker should drill.\n'
            '5. Grammar/phrasing: Put brief arrow fixes in `grammar_fix` ONLY if the speaker made an actual grammar/lexical slip, and give plain natural spoken US in `native_recast` (zero slang, zero stiff thesaurus words).\n'
            'If pronunciation, rhythm, AND grammar already sound completely natural American, set has_findings=false.'
        )
        usr_p = (
            f'Register: {ANGLE_META[angle]["badge"]}\n'
            f'{dsp.to_prompt_summary()}\n'
            f'Verbatim transcript heard: "{transcript}"\n'
            f'{tr_hint}\n'
            'Audit the attached voice recording carefully.'
        )
        fb = ProsodyAuditOutput(has_findings=False)
        res = await self.llm.audit_voice_prosody(user_id, sys_p, audio_bytes, usr_p, ProsodyAuditOutput, fb, mime_type=mime_type)
        if not res.has_findings:
            return None, dsp.compact_badge(), ''
        res.native_recast = scrub_contrived_english(res.native_recast)
        res.shadow_chunk = scrub_contrived_english(res.shadow_chunk)
        h_key = res.habit_key.strip().lower() if res.habit_key.strip().lower() in HABIT_LABELS else 'us_rhythm_linking'
        lbl, hits = self.repo.record_habit_hit(
            user_id, h_key, transcript[:120], res.prosody_fix or res.segmental_fix or res.grammar_fix
        )
        badge_h = f'Habit #{hits}: {lbl}' if hits >= 2 else ''
        if res.native_recast:
            self.repo.log_upgrade(user_id, transcript, res.native_recast, lbl)
        return res, dsp.compact_badge(), badge_h
