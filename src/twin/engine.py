import asyncio, random, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pydantic import BaseModel

from src.config import ECHO_TWIN_ENABLED
from src.db.repo import HABIT_LABELS, TwinRepository
from src.llm.client import GeminiClient
from src.twin.calques import detect_code_switch, is_repair_request, scan_transfers

ANGLE_META = {
    "casual_sharp": {
        "name": "Malik",
        "short_btn": "⚡ Malik · Everyday",
        "badge": "⚡ Malik · Everyday US",
        "opener": "Hey! Good timing—just taking a quick breather. How's your day going?",
        "voice_desc": (
            "You are Malik, a warm, sharp, down-to-earth American friend from Chicago. "
            "Natural spoken US English, contractions always, easy rapport, light dry wit—never mean, never long-winded."
        ),
    },
    "exec_clock": {
        "name": "Claire",
        "short_btn": "💼 Claire · Work/Exec",
        "badge": "💼 Claire · Strategy & Work",
        "opener": "Hey—glad I caught you between calls. Walk me through what you're tackling right now.",
        "voice_desc": (
            "You are Claire, a crisp, supportive US strategy director in New York. "
            "Collegial, practical, bottom-line-up-front spoken workplace English."
        ),
    },
    "seminar_skeptic": {
        "name": "Prof. Vance",
        "short_btn": "🎓 Prof. Vance · Seminar",
        "badge": "🎓 Prof. Vance · Seminar & Ideas",
        "opener": "Come on in—what's an argument or puzzle you've been turning over in your head lately?",
        "voice_desc": (
            "You are Prof. Nolan Vance (Ann Arbor), an engaging US political-science scholar. "
            "Spoken seminar English: contractions, thoughtful hedging, sharp constructive pushback."
        ),
    },
}

SPARK_BANK = {
    "casual_sharp": [
        "Quick gut check: a friend keeps canceling plans twenty minutes before meeting up, then texts 'my bad, crazy week.' Do you call them out or let it fade?",
        "Settle a debate for me—does working from coffee shops actually help people focus, or are we just paying six bucks to feel busy?",
        "What's one thing everybody around you seems hyped about lately that you're honestly skeptical of?",
        "Say you've got a completely free Saturday and decent weather—what does a genuinely good day look like for you?",
    ],
    "exec_clock": [
        "Leadership wants to launch a pilot in two weeks, and you know the operations side isn't ready. Give me your 20-second pushback.",
        "A colleague keeps dropping 'urgent' favors on you right at the end of the day. How do you push back firmly without burning bridges?",
        "You have sixty seconds with a mayor to pitch one reform that doesn't need a huge budget—what's your headline?",
        "Two options: launch an 80%-ready tool Monday or delay a month to polish it. Which call do you make?",
    ],
    "seminar_skeptic": [
        "Let's stress-test a classic claim: 'Digital participation platforms make local governments more accountable.' Where does that break down in practice?",
        "Push back on me: I'd argue most public consultations just amplify the loudest 5% of residents rather than real local needs. Am I wrong?",
        "When a governance model works in Barcelona or Tallinn, what's usually the first institutional bottleneck when another country imports it?",
        "A reviewer says: 'Compelling theory, but what's the concrete causal mechanism on the ground?' Give me your three-sentence answer.",
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
    hits = scan_transfers(learner_text)
    for _, _, tag in hits:
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
    if not fields["us"]:
        return segs, None
    t2 = fields["take2"]
    return segs, GhostTwinAnalysis(
        should_intervene=True,
        habit_key=fields["habit"],
        fix_summary=fields["fix"],
        rhythm_tip=fields["rhythm"],
        native_take=fields["us"],
        key_chunk=fields["chunk"],
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
        mem_blk = f"\nKNOWN SPEAKER CONTEXT (reference naturally only when relevant, never recite):\n{mem_text}" if mem_text else ""
        hab_blk = (
            "\nRECURRING HABITS RADAR (be extra vigilant if any appear): "
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
            "CONVERSATION RULES:",
            "- Match the speaker's length and energy: if they wrote 1-6 words, reply in 1 crisp sentence (6-18 words), NEVER a long paragraph.",
            "- Plain text only in your reply (no bold, no bullets, no grammar commentary inside your character reply).",
        ]
        if repair:
            r.append("- PRAGMATIC REPAIR: The speaker didn't catch your last line. Drop any joke immediately, explain warmly in plain US English ('Oh my bad—I just meant...'), and keep rolling.")
        elif two_q:
            r.append("- Your last two replies ended with questions: make a warm observation or statement WITHOUT ending with a question.")

        if detect_code_switch(text) and not repair:
            r.append("- L1 BRIDGE: Speaker mixed Arabic/French hunting for a US expression. Answer their point naturally AND include the exact US idiom in [square brackets] inside your reply.")
        if take2 and not repair:
            r.append(f"- TAKE 2 RETRY: You invited a retry ({take2}). Note briefly how much cleaner it landed, then continue.")
        for v in (due[:1] if not repair else []):
            if int(v["stage"]) == 0:
                r.append(f"- STEALTH MODEL: If natural, weave '{v['phrase']}' into your reply once.")
            else:
                r.append(f"- STEALTH BAIT: Invite the idea behind '{v['phrase']}' without saying the phrase yourself.")

        if ECHO_TWIN_ENABLED and not repair and not PURE_GREETING_RE.match(text.strip()) and wc >= 2:
            hint = f" (Pre-detected slips you MUST fix: {'; '.join(s + ' -> ' + u for s, u, _ in transfers)})" if transfers else ""
            allow_t2 = (wc >= 28 or len(transfers) >= 2) and not take2
            t2_line = "TAKE2: [1-sentence retry challenge ONLY if major, else NONE]" if allow_t2 else "TAKE2: NONE"
            r.append(
                f"\nSILENT COACH CHECK (APPEND AFTER YOUR CHARACTER REPLY):{hab_blk}"
                f"\nInspect the speaker's exact line for US English accuracy & naturalness.{hint}"
                "\n- Ignore casual texting punctuation/capitalization."
                "\n- If ALREADY 100% grammatically accurate AND natural spoken US English, do NOT append anything."
                "\n- If there is ANY grammar mistake, L1 Arabic/French calque, wrong word choice, or stiff phrasing—even in 2-5 words—append:"
                "\n===COACH==="
                "\nHABIT: [one of: irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register]"
                "\nFIX: [concise arrow fixes, e.g. buyed -> bought · many fruit -> a bunch of fruit]"
                "\nUS: [how a native American naturally says the speaker's exact sentence in 1-2 crisp spoken sentences]"
                "\nCHUNK: [best reusable 2-5 word US chunk from US]"
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

            raw = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.82, deep_chat=True)
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
                "MULTIMODAL SPOKEN TURN — LISTEN DIRECTLY TO THE SPEAKER'S VOICE:",
                "- Hear both WHAT they say (words, grammar, any Arabic/French code-switching) AND HOW they sound (stress-timed rhythm, word linking, flap-T, vowel reductions, hesitation).",
                "- Keep your character reply crisp, warm, and conversational (1-3 spoken sentences, plain text only).",
            ]
            if two_q:
                rules.append("- Your last two replies ended with questions: land a statement or reaction WITHOUT ending with a question.")
            if take2:
                rules.append(f"- TAKE 2 RETRY: You invited a retry ({take2}). Briefly note how much cleaner/punchier this spoken take sounded, then continue.")
            for v in due[:1]:
                if int(v["stage"]) == 0:
                    rules.append(f"- STEALTH MODEL: If natural, weave '{v['phrase']}' into your reply once.")
                else:
                    rules.append(f"- STEALTH BAIT: Invite the idea behind '{v['phrase']}' without saying it yourself.")

            rules.append(
                f"\nOUTPUT FORMAT (FOLLOW EXACTLY):{hab_blk}"
                "\n===HEARD==="
                "\n[Exact verbatim transcript preserving contractions, grammar slips, and any Arabic/French words; write [inaudible] ONLY if silent/unintelligible]"
                "\n===REPLY==="
                "\n[Your natural in-character spoken US reply; if speaker mixed Arabic/French, slip the US idiom in [brackets] inside your reply]"
                "\n===COACH==="
                "\n[Write NONE if grammar, phrasing, AND American spoken rhythm/delivery are already completely natural (or if speaker only said hi/thanks or asked what you meant). Otherwise provide:]"
                "\nHABIT: [one of: irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register | us_rhythm_linking | word_stress]"
                "\nFIX: [arrow fixes for grammar/phrasing slips, or NONE if purely delivery]"
                "\nRHYTHM: [1 specific spoken US prosody/linking/flap-T/stress tip based on the audio, e.g. Link 'picked_up_a' smoothly & flap the T in 'city', or NONE]"
                "\nUS: [how a sharp native American naturally says their exact thought out loud in 1-2 tight sentences]"
                "\nCHUNK: [best reusable 2-5 word US chunk from US]"
                "\nWHY: [2-5 word label]"
                "\nTAKE2: [1-sentence spoken retry challenge ONLY if major, else NONE]"
            )
            hist = "\n".join(f"{'You' if t['role']=='partner' else 'Speaker'}: {t['content']}" for t in recent[-10:])
            usr_p = f"RECENT DIALOGUE:\n{hist}\n\nListen to the attached voice note ({duration_s:.0f}s) and respond in the exact format." if hist else f"Listen to the attached voice note ({duration_s:.0f}s) and respond in the exact format."

            raw = await self.llm.generate_voice_turn(user_id, "\n".join(rules), audio_bytes, usr_p, mime_type=mime_type, temperature=0.82)
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
            "(work/research themes, interests, goals, ongoing projects) so conversation partners remember them naturally. "
            "Merge with existing notes, drop trivial greetings, plain text only, max 70 words total."
        )
        usr_p = f"Existing memory:\n{old_mem or '(none)'}\n\nRecent speaker lines:\n" + "\n".join(f"- {ln}" for ln in learner_lines[-8:])
        try:
            merged = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.25, deep_chat=False)
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

    async def shift_angle_live(self, user_id: int) -> tuple[str, str, str]:
        async with self._lock(user_id):
            new_a = self.repo.cycle_angle(user_id)
            meta = ANGLE_META[new_a]
            last_l = next((t["content"] for t in reversed(self.repo.recent_turns(user_id, 10)) if t["role"] == "learner" and len(t["content"].split()) >= 3), "")
            if not last_l:
                return new_a, meta["badge"], meta["opener"]
            sys_p = meta["voice_desc"] + "\nReact immediately to the speaker's LAST point below from your perspective in 1-2 crisp spoken US sentences. Plain text only."
            raw = await self.llm.generate_text(user_id, sys_p, f"Speaker's point: {last_l}\nYour reaction:", temperature=0.85, deep_chat=True)
            segs, _ = parse_reply_and_coach(raw)
            return new_a, meta["badge"], " ".join(segs)

    async def explain_registers(self, user_id: int, target_text: str) -> str:
        sys_p = "You are a concise US English pragmatics coach for an advanced speaker. Plain text only, zero fluff."
        usr_p = (
            f"Idea/Line: {target_text.strip()}\n\n"
            "Show how Americans naturally say this across 3 spoken registers (contractions, authentic chunks, 1 tight line each):\n"
            "• Everyday (Malik): ...\n• Work / Exec (Claire): ...\n• Seminar (Prof. Vance): ...\n• Pragmatic nuance: [1 short sentence]"
        )
        return await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.5, deep_chat=False)

    async def coach_on_demand(self, user_id: int, learner_text: str) -> GhostTwinAnalysis | None:
        if not learner_text.strip():
            return None
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        transfers = scan_transfers(learner_text)
        hint = "Slips spotted: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers) if transfers else "Point out any grammar/word-choice slip or give a sharper native US phrasing."
        sys_p = (
            "You are an elite spoken American English coach.\n"
            "Set should_intervene=true and fill:\n"
            "- habit_key: one of irregular_past | uncountable_noun | tense_aspect | article_prep | fr_calque | ar_calque | stiff_register.\n"
            "- fix_summary: Short arrow corrections of any slips, or empty if already grammatical.\n"
            "- rhythm_tip: Short tip on how to link/stress native_take when spoken aloud.\n"
            "- native_take: Their exact thought spoken naturally in first person by an American in 1-2 crisp sentences.\n"
            "- key_chunk: Best reusable 2-5 word American chunk from native_take.\n"
            "- why_tag: 2-5 word label."
        )
        usr_p = f"Context: {ANGLE_META[angle]['badge']}\n{hint}\nUser said: {learner_text}"
        res = await self.llm.generate_structured(user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=False)
        if not res.native_take.strip():
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take.strip(), res.why_tag or "us_coach")
        return res
