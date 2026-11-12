import logging, re, time, requests
from telegram import BotCommand, BotCommandScopeChat
from src.config import resolve_telegram_token

logger = logging.getLogger(__name__)

BOT_NAME = "Spoken English Lab"
SHORT_DESCRIPTION = "Practice real spoken American English by voice or text with Malik, Claire & Prof. Vance + instant US coaching."
FULL_DESCRIPTION = (
    "🎙️ Spoken English Lab v3 — Real US English Studio\n\n"
    "Talk naturally by voice note or text with 3 sharp American partners:\n"
    "• ⚡ Malik — Everyday Chicago banter & real talk\n"
    "• 💼 Claire — Crisp NY workplace & executive pushback\n"
    "• 🎓 Prof. Vance — Seminar debates & big ideas\n\n"
    "🎯 Direct US audio ear coaches grammar, idioms, linking & spoken rhythm in real time.\n\n"
    "Tap Start below to jump in."
)
COMMANDS = [
    {"command": "start", "description": "Open studio & refresh keyboard"},
    {"command": "lab", "description": "US Prosody Lab, Audio Mirror & drills"},
    {"command": "studios", "description": "Open US speaking & listening studios"},
    {"command": "mirror", "description": "Hear your last voice side-by-side with US"},
    {"command": "shadow", "description": "3-beat connected US shadowing drill"},
    {"command": "partner", "description": "Switch partner (Malik, Claire, Vance)"},
    {"command": "vault", "description": "Habit radar, saved chunks & pulse"},
    {"command": "reset", "description": "Fresh topic or erase my history"},
]
_REFRESHED_CHATS: set[int] = set()


def redact_token(text: str, token: str | None = None) -> str:
    out = str(text)
    if token and token.strip():
        out = out.replace(token.strip(), "<REDACTED>")
    return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<REDACTED>", out)


def split_telegram_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks, rem = [], text
    while len(rem) > max_len:
        cut = rem[:max_len].rfind("\n")
        if cut <= 0:
            cut = rem[:max_len].rfind(" ")
        if cut <= 0:
            cut = max_len
        chunks.append(rem[:cut].rstrip())
        rem = rem[cut:].lstrip()
    if rem:
        chunks.append(rem)
    return chunks


def _tg_call(base_url: str, method: str, payload: dict, token: str) -> dict | None:
    for attempt in (1, 2):
        try:
            r = requests.post(f"{base_url}/{method}", json=payload, timeout=15)
            data = r.json()
            if r.status_code == 429 or (isinstance(data, dict) and data.get("error_code") == 429):
                wait_s = int(((data or {}).get("parameters") or {}).get("retry_after") or 0)
                if attempt == 1 and 0 < wait_s <= 10:
                    time.sleep(wait_s + 1)
                    continue
                return {"_rate_limited": True}
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("Telegram %s skipped: %s", method, redact_token(str(exc), token))
            return None
    return None


def _sync_field(base: str, tok: str, getter: str, setter: str, key: str, target: str) -> bool:
    for lang in ("en", "ar", "fr"):
        cur_l = _tg_call(base, getter, {"language_code": lang}, tok)
        if cur_l and cur_l.get("_rate_limited"):
            return False
        if ((cur_l or {}).get("result") or {}).get(key, ""):
            res_l = _tg_call(base, setter, {key: "", "language_code": lang}, tok)
            if res_l and res_l.get("_rate_limited"):
                return False
    cur = _tg_call(base, getter, {}, tok)
    if cur and cur.get("_rate_limited"):
        return False
    if ((cur or {}).get("result") or {}).get(key) != target:
        res = _tg_call(base, setter, {key: target}, tok)
        if res and res.get("_rate_limited"):
            return False
    return True


def sync_telegram_bot_profile(token: str | None = None) -> None:
    tok = (token or resolve_telegram_token()).strip()
    if not tok:
        return
    base = f"https://api.telegram.org/bot{tok}"
    if not _sync_field(base, tok, "getMyName", "setMyName", "name", BOT_NAME):
        return
    if not _sync_field(base, tok, "getMyShortDescription", "setMyShortDescription", "short_description", SHORT_DESCRIPTION):
        return
    if not _sync_field(base, tok, "getMyDescription", "setMyDescription", "description", FULL_DESCRIPTION):
        return
    for scope in ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"}):
        for lang in ("en", "ar", "fr"):
            _tg_call(base, "deleteMyCommands", {"scope": scope, "language_code": lang}, tok)
        if scope["type"] == "all_group_chats":
            _tg_call(base, "deleteMyCommands", {"scope": scope}, tok)
            continue
        cur = _tg_call(base, "getMyCommands", {"scope": scope}, tok)
        if cur and cur.get("_rate_limited"):
            return
        existing = [{"command": c.get("command"), "description": c.get("description")} for c in (cur or {}).get("result", []) if isinstance(c, dict)]
        if existing != COMMANDS:
            _tg_call(base, "setMyCommands", {"commands": COMMANDS, "scope": scope}, tok)
    _tg_call(base, "setChatMenuButton", {"menu_button": {"type": "commands"}}, tok)


async def refresh_chat_menu_once(bot, chat_id: int | None, force: bool = False) -> None:
    if not bot or not chat_id:
        return
    if not force and chat_id in _REFRESHED_CHATS:
        return
    _REFRESHED_CHATS.add(chat_id)
    scope = BotCommandScopeChat(chat_id=chat_id)
    bot_cmds = [BotCommand(c["command"], c["description"]) for c in COMMANDS]
    for lang in (None, "en", "ar", "fr"):
        fn_del = getattr(bot, "delete_my_commands", None)
        if callable(fn_del):
            try:
                await fn_del(scope=scope, language_code=lang)
            except Exception:
                pass
    fn_set = getattr(bot, "set_my_commands", None)
    if callable(fn_set):
        try:
            await fn_set(commands=bot_cmds, scope=scope)
        except Exception:
            pass
