import asyncio, random, re
from dataclasses import dataclass, field
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
        "opener": "Hey! Good timing—I just stepped outside for five minutes. What's going on with you today?",
        "voice_desc": (
            "You are Malik, a warm, perceptive, quick-witted American friend from Chicago. "
            "Speak natural everyday American English with contractions, grounded human curiosity, and light dry humor—"
            "NEVER mean, NEVER combative, NEVER condescending. If the speaker just says 'Hey', greet them warmly like a normal "
            "friend ('Hey! What's going on?'). If you teased lightly once, move straight into genuine conversation on the next turn."
        ),
    },
    "exec_clock": {
        "name": "Claire",
        "short_btn": "💼 Claire · Work/Exec",
        "badge": "💼 Claire · Strategy & Work",
        "opener": "Hey—glad I caught you between calls. Walk me through what you're tackling right now.",
        "voice_desc": (
            "You are Claire, a sharp, supportive American strategy director in New York. "
            "Collegial, practical, and time-conscious: you appreciate bottom-line-up-front clarity, realistic trade-offs, "
            "and crisp pushback, while keeping the tone encouraging and human."
        ),
    },
    "seminar_skeptic": {
        "name": "Prof. Vance",
        "short_btn": "🎓 Prof. Vance · Seminar",
        "badge": "🎓 Prof. Vance · Seminar & Ideas",
        "opener": "Come on in—what's an idea or argument you've been turning over in your head lately?",
        "voice_desc": (
            "You are Prof. Nolan Vance (Ann Arbor), an engaging American political-science and policy scholar. "
            "Use authentic spoken seminar English (contractions always, thoughtful hedging, intellectually generous pushback "
            "like 'I buy that on paper, though how does it hold up when...'). Treat the speaker as a sharp peer."
        ),
    },
}

SPARK_BANK = {
    "casual_sharp": [
        "Quick gut check: a close friend keeps canceling plans twenty minutes before we're supposed to meet, then texts 'my bad, crazy week.' Do you call them out or let it fade?",
        "Okay, settle a debate for me—does working from cafés actually make people productive, or are we all just paying six bucks to pretend?",
        "Honest question: what's one thing everybody around you seems obsessed with right now that you just don't buy into?",
        "Say you've got one free Saturday with zero obligations and decent weather—what does a genuinely good day look like for you?",
    ],
    "exec_clock": [
        "Picture this: leadership wants to launch a flagship pilot in two weeks, and you already know the data pipeline isn't ready. Give me your 20-second pushback.",
        "Quick scenario—a colleague keeps dropping 'urgent' requests on you at 4:30 PM. How do you draw a firm line without burning the relationship?",
        "You have sixty seconds with a city mayor to pitch one practical reform that doesn't require a massive budget—what's your headline?",
        "Two options on the table: ship an 80%-ready tool next Monday or delay a month to get it polished. Which call do you make and why?",
    ],
    "seminar_skeptic": [
        "Let's stress-test a classic claim: 'Digital participation platforms make local governments more accountable.' Where does that break down in real municipalities?",
        "Push back on me here—I'd argue most public consultations just amplify the loudest 5% of residents rather than real community priorities. Am I wrong?",
        "When a reform looks brilliant in Barcelona or Tallinn, what's usually the first institutional bottleneck when someone tries importing it elsewhere?",
        "Suppose a reviewer tells you: 'Your theoretical framework is compelling, but what's the concrete causal mechanism?' How do you answer in three spoken sentences?",
    ],
}

AI_OPENERS_RE = re.compile(
    r"^(?:certainly|absolutely|great question|that's a great point|i'd be happy to|of course|sure thing)[!.,:\s]+",
    re.IGNORECASE,
)


class GhostTwinAnalysis(BaseModel):
    should_intervene: bool = False
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


def clean_partner_reply(raw: str) -> list[str]:
    cleaned = []
    for p in raw.split("---"):
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", p.strip())
        s = re.sub(r"(?m)^\s*[-*•#]+\s+", "", s)
        s = re.sub(r"\s{2,}", " ", AI_OPENERS_RE.sub("", s).strip())
        if s:
            cleaned.append(s)
    return cleaned or ["Sorry, lost my train of thought—say that one more time?"]


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
        bot_t = [t["content"] for t in recent if t["role"] == "partner"]
        two_q = len(bot_t) >= 2 and all(x.rstrip().endswith("?") for x in bot_t[-2:])
        r = [
            meta["voice_desc"],
            "CORE CONVERSATION RULES:",
            "- Sound unmistakably human, warm, and engaged (1-3 crisp spoken sentences; shorter when natural).",
            "- Plain text only (no bold, no bullets, no stage directions). Never say delve, moreover, tapestry, feel free to.",
            "- Never scold, mock, or stonewall the speaker. Keep the rapport collaborative and natural.",
        ]
        if repair:
            r.append(
                "- PRAGMATIC REPAIR (CRITICAL): The speaker didn't catch or understand your last line/idiom. "
                "Drop any joke or sarcasm IMMEDIATELY, explain what you meant warmly in plain everyday American English "
                "('Oh my bad—I just meant...'), and ask an easy, friendly follow-up to keep rolling."
            )
        elif two_q:
            r.append("- Your last two turns ended with questions: land a warm reaction, take, or observation WITHOUT ending with a question.")

        if detect_code_switch(text) and not repair:
            r.append(
                "- L1 BRIDGE: Speaker mixed Arabic or French while reaching for an American expression. "
                "Respond naturally to their point AND slip the exact idiomatic US phrasing in [square brackets] right inside your sentence."
            )
        elif len(text.split()) >= 45 or '"' in text or "“" in text:
            r.append("- DEBATE HOOK: Engage the most interesting tension in their claim and test it constructively.")

        if take2 and not repair:
            r.append(
                f"- TAKE 2 FOLLOW-UP: You just invited a tighter retry ({take2}). Briefly note what landed cleaner in half a clause, then continue."
            )
        for v in (due[:1] if not repair else []):
            if int(v["stage"]) == 0:
                r.append(f"- STEALTH MODEL: If it fits organically, weave '{v['phrase']}' into your reply once.")
            else:
                r.append(f"- STEALTH ELICIT: Steer the topic so the speaker naturally wants to express the idea behind '{v['phrase']}' without saying it yourself.")
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

            raw = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.86, deep_chat=True)
            segs = clean_partner_reply(raw)
            full = "\n\n".join(segs)
            vhits = self.detect_vault_hits(user_id, learner_text)

            self.repo.log_turn(user_id, "learner", angle, learner_text, chat_message_id=chat_message_id, is_voice=is_voice, duration_s=duration_s)
            if log_partner:
                self.repo.log_turn(user_id, "partner", angle, full)
            if take2:
                self.repo.set_pending_take2(user_id, "")
            if due and int(due[0]["stage"]) == 0 and due[0]["phrase"].lower() in full.lower():
                self.repo.mark_vault_hit(user_id, due[0]["phrase"])
            return TurnOutcome(segs, full, angle, ANGLE_META[angle]["badge"], vhits, bool(take2), repair)

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
            last_l = next(
                (t["content"] for t in reversed(self.repo.recent_turns(user_id, 10)) if t["role"] == "learner" and len(t["content"].split()) >= 3),
                "",
            )
            if not last_l:
                return new_a, meta["badge"], meta["opener"]
            sys_p = meta["voice_desc"] + "\nReact immediately to the speaker's LAST point below from your perspective in 1-2 crisp spoken US sentences. Plain text only."
            raw = await self.llm.generate_text(user_id, sys_p, f"Speaker's point: {last_l}\nYour reaction:", temperature=0.85, deep_chat=True)
            return new_a, meta["badge"], " ".join(clean_partner_reply(raw))

    async def explain_registers(self, user_id: int, target_text: str) -> str:
        sys_p = "You are a concise US English pragmatics coach for an advanced C1/C2 speaker. Plain text only, zero fluff."
        usr_p = (
            f"Idea/Line: {target_text.strip()}\n\n"
            "Show how Americans naturally say this across 3 spoken registers (contractions, authentic chunks, 1 tight line each):\n"
            "• Everyday (Malik): ...\n"
            "• Work / Exec (Claire): ...\n"
            "• Seminar (Prof. Vance): ...\n"
            "• Pragmatic nuance: [1 short sentence on tone/subtext]"
        )
        return await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.5, deep_chat=False)

    async def coach_on_demand(self, user_id: int, learner_text: str) -> GhostTwinAnalysis | None:
        if not learner_text.strip():
            return None
        u = self.repo.get_user(user_id)
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        transfers = scan_transfers(learner_text)
        hint = "Transfers spotted: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers) if transfers else "Give the most idiomatic, natural US spoken upgrade."
        sys_p = (
            "You are an elite spoken American English coach for an advanced speaker.\n"
            "Always set should_intervene=true and provide:\n"
            "- native_take: Their EXACT thought spoken in first person as a sharp native American says it out loud in this context (1-2 crisp sentences).\n"
            "- key_chunk: The single best reusable 2-6 word American chunk from native_take.\n"
            "- why_tag: 3-6 word insight explaining what makes the US version sound native.\n"
            "- trigger_take2: false."
        )
        usr_p = f"Context: {ANGLE_META[angle]['badge']}\n{hint}\nUser said: {learner_text}"
        res = await self.llm.generate_structured(user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=False)
        if not res.native_take.strip():
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take.strip(), res.why_tag or "on_demand_upgrade")
        return res

    async def analyze_ghost_twin(self, user_id: int, learner_text: str, *, is_voice: bool = False, duration_s: float = 0.0) -> GhostTwinAnalysis | None:
        if not ECHO_TWIN_ENABLED or is_repair_request(learner_text) or len(learner_text.split()) < 6:
            return None
        words = learner_text.split()
        u = self.repo.get_user(user_id)
        transfers, has_cs = scan_transfers(learner_text), detect_code_switch(learner_text)
        if not (transfers or has_cs or (is_voice and len(words) >= 7) or len(words) >= 26 or (int(u["turns_count"]) % 3 == 0 and len(words) >= 11)):
            return None
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        hint = "Transfers: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers) if transfers else "Check for French/Arabic calques or textbook stiffness."
        allow_t2 = (int(u["turns_count"]) % 5 == 0 or len(words) >= 34 or bool(transfers)) and not u["pending_take2"]
        t2_rule = "Set trigger_take2=true ONLY if a quick 10s retry locks in a major upgrade; put a warm, crisp 1-sentence prompt in take2_prompt." if allow_t2 else "Set trigger_take2=false."
        sys_p = (
            "You are the Native Ghost Twin for an advanced Arabic/French-speaking user mastering spoken US English.\n"
            "If already completely idiomatic and natural, set should_intervene=false. Otherwise set should_intervene=true:\n"
            "- native_take: THEIR EXACT IDEA & STANCE in first person ('I...') as a sharp native American says it out loud in 1-2 tight spoken sentences.\n"
            "- key_chunk: Best reusable 2-6 word chunk from native_take.\n"
            "- why_tag: 2-5 word reason.\n"
            f"- {t2_rule}"
        )
        usr_p = f"Context: {ANGLE_META[angle]['badge']}\n{hint}\nSpeaker said: {learner_text}"
        res = await self.llm.generate_structured(user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=False)
        if not res.should_intervene or not res.native_take.strip():
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take.strip(), res.why_tag or "native_flow")
        if res.trigger_take2 and res.take2_prompt.strip():
            self.repo.set_pending_take2(user_id, res.take2_prompt.strip())
        return res
