import asyncio, re
from dataclasses import dataclass, field
from pydantic import BaseModel
from src.config import ECHO_TWIN_ENABLED
from src.db.repo import TwinRepository
from src.llm.client import GeminiClient
from src.twin.calques import detect_code_switch, scan_transfers

ANGLE_META = {
    "casual_sharp": {
        "badge": "⚡ Off-the-Clock (Malik)",
        "voice_desc": (
            "You are Malik, a sharp American friend from Chicago in real conversation—never a tutor, never reciting "
            "fake office/coffee chores. React directly to what the speaker ACTUALLY just did or said (if they send "
            "Hey twice, call it out playfully like a real friend). Dry wit, natural contractions, real opinions."
        ),
    },
    "seminar_skeptic": {
        "badge": "🎓 Seminar Pushback (Prof. Vance)",
        "voice_desc": (
            "You are Prof. Nolan Vance (Ann Arbor), a sharp political-science & policy peer in a hallway debate. "
            "Use spoken seminar US English with contractions, collaborative hedging, and surgical pushback on vague "
            "abstractions ('I buy that mechanism on paper, sure—how does it hold up when...')."
        ),
    },
    "exec_clock": {
        "badge": "⏱️ 30s Decision Room (Claire)",
        "voice_desc": (
            "You are Claire, a decisive US strategy lead with two minutes before her next call. Warm but allergic "
            "to throat-clearing. Demand bottom-line-up-front clarity, concrete bottlenecks, and crisp trade-offs."
        ),
    },
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

def clean_partner_reply(raw: str) -> list[str]:
    cleaned = []
    for p in raw.split("---"):
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", p.strip())
        s = re.sub(r"(?m)^\s*[-*•#]+\s+", "", s)
        s = re.sub(r"\s{2,}", " ", AI_OPENERS_RE.sub("", s).strip())
        if s:
            cleaned.append(s)
    return cleaned or ["Wait—say that again?"]

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

    def _sys_prompt(self, angle: str, text: str, recent: list[dict], due: list[dict], take2: str) -> str:
        meta = ANGLE_META.get(angle, ANGLE_META["casual_sharp"])
        bot_t = [t["content"] for t in recent if t["role"] == "partner"]
        two_q = len(bot_t) >= 2 and all(x.rstrip().endswith("?") for x in bot_t[-2:])
        r = [
            meta["voice_desc"],
            "RULES: Sound unmistakably human (3-8 words for quick reactions, 1-3 crisp spoken sentences for substance).",
            "Plain text only (no bold, no bullets, no stage directions). Never say delve, moreover, tapestry, feel free to.",
        ]
        if two_q:
            r.append("Your last two turns ended with questions: land a sharp statement WITHOUT ending with a question.")
        if detect_code_switch(text):
            r.append("L1 BRIDGE: Speaker mixed Arabic/French hunting for US phrasing. Answer their point naturally AND slip the exact US idiom in [brackets] inside your sentence.")
        elif len(text.split()) >= 45 or '"' in text or "“" in text:
            r.append("DEBATE SEED: Challenge the core assumption in their claim sharply.")
        if take2:
            r.append(f"TAKE 2 RETRY: You challenged them with ({take2}). Acknowledge how much tighter it landed in one quick clause, then continue.")
        for v in due[:1]:
            if int(v["stage"]) == 0:
                r.append(f"STEALTH MODEL: If natural, weave '{v['phrase']}' into your reply once.")
            else:
                r.append(f"STEALTH BAIT: Invite the idea behind '{v['phrase']}' without saying the phrase yourself.")
        return "\n".join(r)

    async def respond_turn(self, user_id: int, learner_text: str, *, chat_message_id: int | None = None, is_voice: bool = False, duration_s: float = 0.0, log_partner: bool = True) -> TurnOutcome:
        async with self._lock(user_id):
            u = self.repo.get_user(user_id)
            angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
            take2, recent, due = u["pending_take2"], self.repo.recent_turns(user_id, 12), self.repo.due_vault_phrases(user_id, 1)
            sys_p = self._sys_prompt(angle, learner_text, recent, due, take2)
            hist = "\n".join(f"{'You' if t['role']=='partner' else 'Speaker'}: {t['content']}" for t in recent[-10:])
            usr_p = f"RECENT DIALOGUE:\n{hist}\n\nSpeaker: {learner_text}\nYour reply:" if hist else f"Speaker: {learner_text}\nYour reply:"
            raw = await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.9, deep_chat=True)
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
            return TurnOutcome(segs, full, angle, ANGLE_META[angle]["badge"], vhits, bool(take2))

    async def shift_angle_live(self, user_id: int) -> tuple[str, str, str]:
        async with self._lock(user_id):
            new_a = self.repo.cycle_angle(user_id)
            meta = ANGLE_META[new_a]
            last_l = next((t["content"] for t in reversed(self.repo.recent_turns(user_id, 8)) if t["role"] == "learner"), "")
            if not last_l:
                openers = {
                    "casual_sharp": "Alright, off the clock—what's actually on your mind today?",
                    "seminar_skeptic": "Drop the core claim you're wrestling with right now—let's stress-test it.",
                    "exec_clock": "Got two minutes before my next call—give me the headline version.",
                }
                return new_a, meta["badge"], openers[new_a]
            sys_p = meta["voice_desc"] + "\nFlip the room instantly onto the speaker's LAST point below in 1-2 crisp spoken US sentences. Plain text only."
            raw = await self.llm.generate_text(user_id, sys_p, f"Speaker's point: {last_l}\nReact now:", temperature=0.88, deep_chat=True)
            return new_a, meta["badge"], " ".join(clean_partner_reply(raw))

    async def explain_swipe(self, user_id: int, target_text: str) -> str:
        sys_p = "You are a concise US English pragmatics coach for a C1/C2 speaker. Plain text only."
        usr_p = f"Line: {target_text.strip()}\nGive a 4-line US X-Ray:\n• Subtext: ...\n• Close friend: ...\n• Exec / Work: ...\n• Seminar: ..."
        return await self.llm.generate_text(user_id, sys_p, usr_p, temperature=0.5, deep_chat=False)

    async def analyze_ghost_twin(self, user_id: int, learner_text: str, *, is_voice: bool = False, duration_s: float = 0.0) -> GhostTwinAnalysis | None:
        if not ECHO_TWIN_ENABLED or len(learner_text.split()) < 5:
            return None
        words = learner_text.split()
        u = self.repo.get_user(user_id)
        transfers, has_cs = scan_transfers(learner_text), detect_code_switch(learner_text)
        if not (transfers or has_cs or (is_voice and len(words) >= 6) or len(words) >= 26 or (int(u["turns_count"]) % 3 == 0 and len(words) >= 10)):
            return None
        angle = u["angle"] if u["angle"] in ANGLE_META else "casual_sharp"
        hint = "Transfers: " + "; ".join(f"{s}->{up}" for s, up, _ in transfers) if transfers else "Check for French/Arabic calques or wordy phrasing."
        allow_t2 = (int(u["turns_count"]) % 4 == 0 or len(words) >= 32 or bool(transfers)) and not u["pending_take2"]
        t2_rule = "Set trigger_take2=true ONLY if a 10s retry locks in a big upgrade; put a 1-sentence challenge in take2_prompt." if allow_t2 else "Set trigger_take2=false."
        sys_p = (
            "You are the Native Ghost Twin for an advanced Arabic/French-speaking user mastering spoken US English.\n"
            "If already completely idiomatic and punchy, set should_intervene=false. Otherwise set should_intervene=true:\n"
            "- native_take: THEIR EXACT IDEA & STANCE in first person ('I...') as a sharp native American would say it out loud in 1-2 tight spoken sentences.\n"
            "- key_chunk: Best reusable 2-6 word chunk from native_take.\n"
            "- why_tag: 2-5 word reason.\n"
            f"- {t2_rule}"
        )
        usr_p = f"Angle: {ANGLE_META[angle]['badge']}\n{hint}\nSpeaker said: {learner_text}"
        res = await self.llm.generate_structured(user_id, sys_p, usr_p, GhostTwinAnalysis, GhostTwinAnalysis(), deep_chat=False)
        if not res.should_intervene or not res.native_take.strip():
            return None
        self.repo.log_upgrade(user_id, learner_text, res.native_take.strip(), res.why_tag or "native_flow")
        if res.trigger_take2 and res.take2_prompt.strip():
            self.repo.set_pending_take2(user_id, res.take2_prompt.strip())
        return res
