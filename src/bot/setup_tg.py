import logging, re, requests
from src.config import resolve_telegram_token

logger = logging.getLogger(__name__)

BOT_NAME = "Spoken English Lab"
SHORT_DESCRIPTION = "Echo Twin — Spontaneous US English, Native Recasts & Live Register Shifts."
FULL_DESCRIPTION = (
    "🎙️ Echo Twin — Zero-menu American English sparring.\n\n"
    "• Talk or send a voice note: voice replies mirror you with tap-to-reveal spoiler text.\n"
    "• Drop Arabic or French mid-thought to get the exact US idiom inline.\n"
    "• 👥 Native Take replays your own point the way a sharp American says it.\n"
    "• Tap ⚡ Shift anytime to flip the room on your exact last sentence."
)
COMMANDS = [
    {"command": "start", "description": "Wake up Echo Twin"},
    {"command": "pulse", "description": "7-day spoken pulse & upgrades"},
    {"command": "vault", "description": "Stolen US chunks & activations"},
    {"command": "wipe", "description": "Erase all my stored history"},
]


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
    for scope in ({"type": "default"}, {"type": "all_private_chats"}):
        cur = _tg_call(base, "getMyCommands", {"scope": scope}, tok)
        if cur and cur.get("_rate_limited"):
            return
        existing = [{"command": c.get("command"), "description": c.get("description")} for c in (cur or {}).get("result", []) if isinstance(c, dict)]
        if existing != COMMANDS:
            res = _tg_call(base, "setMyCommands", {"commands": COMMANDS, "scope": scope}, tok)
            if res and res.get("_rate_limited"):
                return
    sd = _tg_call(base, "getMyShortDescription", {}, tok)
    if sd and not sd.get("_rate_limited") and (sd.get("result") or {}).get("short_description") != SHORT_DESCRIPTION:
        _tg_call(base, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION}, tok)
    fd = _tg_call(base, "getMyDescription", {}, tok)
    if fd and not fd.get("_rate_limited") and (fd.get("result") or {}).get("description") != FULL_DESCRIPTION:
        _tg_call(base, "setMyDescription", {"description": FULL_DESCRIPTION}, tok)
