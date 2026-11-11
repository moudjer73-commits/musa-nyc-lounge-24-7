import logging, re, requests
from telegram import BotCommand, BotCommandScopeChat
from src.config import resolve_telegram_token

logger = logging.getLogger(__name__)

BOT_NAME = "Spoken English Lab"
SHORT_DESCRIPTION = "Spontaneous American English sparring with Malik, Claire & Prof. Vance + Native Recasts."
FULL_DESCRIPTION = (
    "🎙️ Spoken English Lab — Studio Edition\n\n"
    "• Talk naturally by voice or text with Malik (Everyday), Claire (Work/Exec), or Prof. Vance (Seminar).\n"
    "• Voice replies mirror you with tap-to-reveal spoiler transcripts.\n"
    "• 👥 Native Take & ✨ Coach Last show how a native American says your exact thought.\n"
    "• Mix Arabic or French mid-sentence anytime to get the exact US idiom [in brackets]."
)
COMMANDS = [
    {"command": "start", "description": "Open studio & refresh buttons"},
    {"command": "partner", "description": "Switch partner (Malik · Claire · Vance)"},
    {"command": "spark", "description": "Drop a fresh live topic or pushback"},
    {"command": "vault", "description": "Saved US phrases & 7-day pulse"},
    {"command": "reset", "description": "New chat topic or erase history"},
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
    try:
        r = requests.post(f"{base_url}/{method}", json=payload, timeout=15)
        data = r.json()
        if r.status_code == 429 or (isinstance(data, dict) and data.get("error_code") == 429):
            return {"_rate_limited": True}
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("Telegram %s skipped: %s", method, redact_token(str(exc), token))
        return None


def sync_telegram_bot_profile(token: str | None = None) -> None:
    tok = (token or resolve_telegram_token()).strip()
    if not tok:
        return
    base = f"https://api.telegram.org/bot{tok}"
    # Clear stale multi-language commands from v1 across global scopes, then set fresh commands
    for scope in ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"}):
        for lang in ("", "en", "ar", "fr"):
            lp = {"language_code": lang} if lang else {}
            if scope["type"] == "all_group_chats":
                res = _tg_call(base, "deleteMyCommands", {"scope": scope, **lp}, tok)
                if res and res.get("_rate_limited"):
                    return
                continue
            if lang == "fr":
                _tg_call(base, "deleteMyCommands", {"scope": scope, **lp}, tok)
                continue
            cur = _tg_call(base, "getMyCommands", {"scope": scope, **lp}, tok)
            if cur and cur.get("_rate_limited"):
                return
            existing = [{"command": c.get("command"), "description": c.get("description")} for c in (cur or {}).get("result", []) if isinstance(c, dict)]
            if existing != COMMANDS:
                res = _tg_call(base, "setMyCommands", {"commands": COMMANDS, "scope": scope, **lp}, tok)
                if res and res.get("_rate_limited"):
                    return

    _tg_call(base, "setChatMenuButton", {"menu_button": {"type": "commands"}}, tok)
    sd = _tg_call(base, "getMyShortDescription", {}, tok)
    if sd and not sd.get("_rate_limited") and (sd.get("result") or {}).get("short_description") != SHORT_DESCRIPTION:
        _tg_call(base, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION}, tok)
    fd = _tg_call(base, "getMyDescription", {}, tok)
    if fd and not fd.get("_rate_limited") and (fd.get("result") or {}).get("description") != FULL_DESCRIPTION:
        _tg_call(base, "setMyDescription", {"description": FULL_DESCRIPTION}, tok)


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
