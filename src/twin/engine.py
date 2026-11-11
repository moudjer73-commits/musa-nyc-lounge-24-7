import asyncio, random, re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pydantic import BaseModel

from src.config import ECHO_TWIN_ENABLED
from src.db.repo import TwinRepository
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
PURE_GREETING_RE = re.compile(r"^(?:hi|hey|hello|yo|hey there|hi there|good morning|good evening|thanks|thank you|great thank you|ok|okay|cool|nice)[!.?,\s]*$", re.IGNORECASE)


class GhostTwinAnalysis(BaseModel):
    should_intervene: bool = False
    fix_summary: str = ""
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


def parse_reply_and_coach(raw: str) -> tuple[list[str], GhostTwinAnalysis | None]:
    parts = COACH_SPLIT_RE.split(raw.strip(), maxsplit=1)
    segs = clean_partner_reply(parts[0])
    if len(parts) < 2:
        return segs, None
    block = parts[1].strip()
    if not block or block.upper().startswith("NONE"):
        return segs, None

    fields = {"fix": "", "us": "", "chunk": "", "why": "", "take2": ""}
    for line in block.splitlines():
        s = line.strip().lstrip("-•* ").strip()
        for k in ("FIX", "US", "CHUNK", "WHY", "TAKE2"):
            if s.upper().startswith(k + ":"):
                fields[k.lower()] = s.split(":", 1)[1].strip().strip('"“”')
                break
    if fields["us"].upper() in ("", "NONE", "N/A"):
        return segs, None
    t2 = fields["take2"] if fields["take2"].upper() not in ("", "NONE", "N/A", "FALSE") else ""
    return segs, GhostTwinAnalysis(
        should_intervene=True,
        fix_summary=fields["fix"] if fields["fix"].upper() not in ("NONE", "N/A") else "",
        native_take=fields["us"],
        key_chunk=fields["chunk"] if fields["chunk"].upper() not in ("NONE", "N/A") else "",
        why_tag=fields["why"] if fields["why"].upper() not in ("NONE", "N/A") else "",
        trigger_take2=bool(t2),
        take2_prompt=t2,
    )


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

    def _sys_prompt(self, angle: str, text: str, recent: list[dict], due: list[dict], take2: str, repair: bool) -> str:
        meta = ANGLE_META.get(angle, ANGLE_META["casual_sharp"])
        weekday = datetime.now(timezone.utc).strftime("%A")
        wc = len(text.split())
        bot_t = [t["content"] for t in recent if t["role"] == "partner"]
        two_q = len(bot_t) >= 2 and all(x.rstrip().endswith("?") for x in bot_t[-2:])
        transfers = scan_transfers(text)

        r = [
            f"{meta['voice_desc']} (Today is {weekday}.)",
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
                "\nSILENT COACH CHECK (APPEND AFTER YOUR CHARACTER REPLY):"
                f"\nInspect the speaker's exact line for US English accuracy & naturalness.{hint}"
                "\n- Ignore casual texting punctuation/capitalization (e.g. missing comma or period)."
                "\n- If the speaker's line is ALREADY 100% grammatically accurate AND natural spoken US English, do NOT append anything."
                "\n- If there is ANY grammar mistake (verb form like 'buyed', wrong tense/time word like 'now' with past, countable/uncountable like 'many fruit', article, preposition, word order), L1 Arabic/French calque, wrong word choice, or stiff non-native phrasing—even in a short 2-5 word message—you MUST append:"
                "\n===COACH==="
                "\nFIX: [concise arrow fixes, e.g. buyed -> bought · many fruit -> a bunch of fruit (uncountable)]"
                "\nUS: [how a native American naturally says the speaker's exact sentence in 1-2 crisp spoken sentences]"
                "\nCHUNK: [best reusable 2-5 word US chunk from US]"
                "\nWHY: [2-5 word tag, e.g. Irregular verb + uncountable]"
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

            sys_p = self._sys_prompt(angle, learner_text, recent, due, take2, repair)
            hist = "\n".join(f"{'You' if t['role']=='partner' else 'Speaker'}: {t['content']}" for t in recent[-10:])
            usr_p = f"RECENT DIALOGUE:\n{hist}\n\nSpeaker: {learner_text}\nYour reply:" if hist else f"Speaker: {learner_text}\nYour reply:"

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

            if inline_coach:
                self.repo.log_upgrade(user_id, learner_text, inline_coach.native_take, inline_coach.why_tag or "us_coach")
                if inline_coach.trigger_take2 and inline_coach.take2_prompt:
                    self.repo.set_pending_take2(user_id, inline_coach.take2_prompt)

            return TurnOutcome(segs, full, angle, ANGLE_META[angle]["badge"], vhits, bool(take2), repair, inline_coach)

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
            "- fix_summary: Short arrow corrections of any grammar/lexical slips (e.g. 'buyed -> bought · many fruit -> a bunch of fruit'), or empty if already grammatical.\n"
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
