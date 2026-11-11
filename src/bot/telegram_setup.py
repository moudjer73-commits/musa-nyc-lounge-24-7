import logging
import re
import requests
from src.config import resolve_telegram_token

BOT_NAME = "Spoken English Lab"
SHORT_DESCRIPTION = (
    "Natural spoken American English across 9 real-life situations with Sam, Maya, Dana & Prof. Ruiz."
)
FULL_DESCRIPTION = (
    "Welcome to Spoken English Lab 🎙️\n\n"
    "Text and talk in natural American English with 6 realistic partners across 9 situations:\n"
    "• Close Friend Text & Voice (Sam & Maya)\n"
    "• Work Peer & Executive Updates (Sam & Dana)\n"
    "• Academic Seminar, Office Hours & Conferences (Prof. Elena Ruiz)\n"
    "• Everyday Service & Phone Calls\n\n"
    "Tap Start below to open your Lab Dashboard."
)

COMMANDS = [
    {"command": "start", "description": "🏠 Open Naturalness Lab Dashboard"},
    {"command": "room", "description": "🎭 Switch Situation & Partner (9 Real-Life Profiles)"},
    {"command": "flip", "description": "🔄 Flip Last Reply to Another Situation"},
    {"command": "ladder", "description": "🪜 3-Step Register Ladder (Friend → Work → Formal)"},
    {"command": "heard", "description": "👂 Save a Real-Life Phrase to Your Notebook"},
    {"command": "podcast", "description": "🎧 Maya & Jordan Natural Dialogue Podcast"},
    {"command": "voice", "description": "🔊 Toggle Voice Replies On or Off"},
    {"command": "stats_naturalness", "description": "📈 View Naturalness Gauge Metrics"},
    {"command": "privacy", "description": "🔒 Privacy & Erase Stored Data"},
]

logger = logging.getLogger(__name__)

COMMAND_LANGS = ("", "en", "ar")
COMMAND_SCOPES = ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"})
_CLEARED_CHATS: set[int] = set()


class _TelegramRateLimited(Exception):
    pass


def redact_token(text: str, token: str | None = None) -> str:
    out = str(text)
    if token and token.strip():
        out = out.replace(token.strip(), "<REDACTED>")
    return re.sub(r"bot\d+:[A-Za-z0-9_-]+", "bot<REDACTED>", out)


def split_telegram_text(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    rem = text
    while len(rem) > max_len:
        window = rem[:max_len]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            matches = list(re.finditer(r"(?<=[.!?])\s+", window))
            if matches:
                cut = matches[-1].end()
        if cut <= 0:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = max_len
        chunks.append(rem[:cut].rstrip())
        rem = rem[cut:].lstrip()
    if rem:
        chunks.append(rem)
    return chunks or [text[:max_len]]


async def safe_reply_text(message, text: str, **kwargs):
    chunks = split_telegram_text(text, max_len=4000)
    if len(chunks) == 1:
        return await message.reply_text(chunks[0], **kwargs)
    markup = kwargs.pop("reply_markup", None)
    last_res = None
    for idx, chunk in enumerate(chunks):
        if idx == len(chunks) - 1 and markup is not None:
            last_res = await message.reply_text(chunk, reply_markup=markup, **kwargs)
        else:
            last_res = await message.reply_text(chunk, **kwargs)
    return last_res


def _extract_token_from_url(base_url: str) -> str:
    idx = base_url.rfind("/bot")
    return base_url[idx + 4 :] if idx != -1 else ""


def _call_tg(base_url: str, method: str, payload: dict, timeout: int = 20, token: str = "") -> dict | None:
    tok = token or _extract_token_from_url(base_url)
    for attempt in (1, 2):
        try:
            r = requests.post(f"{base_url}/{method}", json=payload, timeout=timeout)
            data = r.json()
            if getattr(r, "status_code", None) == 429 or (isinstance(data, dict) and data.get("error_code") == 429):
                raise _TelegramRateLimited()
            return data if isinstance(data, dict) else None
        except _TelegramRateLimited:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram %s failed (attempt %d): %s", method, attempt, redact_token(str(exc), tok))
    return None


def _post(base_url: str, method: str, payload: dict, timeout: int = 20) -> bool:
    try:
        data = _call_tg(base_url, method, payload, timeout=timeout)
        return bool(data and data.get("ok"))
    except _TelegramRateLimited:
        logger.warning("Telegram %s rate-limited (429); stopping.", method)
        return False


def sync_telegram_bot_profile(token: str | None = None) -> dict[str, bool]:
    resolved = (token or resolve_telegram_token()).strip()
    if not resolved:
        raise RuntimeError("Telegram token is required.")
    base_url = f"https://api.telegram.org/bot{resolved}"
    results: dict[str, bool] = {}

    try:
        for scope in COMMAND_SCOPES:
            for lang in COMMAND_LANGS:
                key = f"cmd_{scope['type']}_{lang or 'def'}"
                lang_part = {"language_code": lang} if lang else {}
                cur = _call_tg(base_url, "getMyCommands", {"scope": scope, **lang_part}, token=resolved)
                cur_cmds = cur.get("result") if (cur and cur.get("ok")) else None
                normalized = (
                    [{"command": c.get("command"), "description": c.get("description")} for c in cur_cmds if isinstance(c, dict)]
                    if isinstance(cur_cmds, list)
                    else None
                )
                if normalized == COMMANDS:
                    results[key] = True
                else:
                    res = _call_tg(
                        base_url, "setMyCommands", {"commands": COMMANDS, "scope": scope, **lang_part}, token=resolved
                    )
                    results[key] = bool(res and res.get("ok"))

        cur_mb = _call_tg(base_url, "getChatMenuButton", {}, token=resolved)
        mb_res = cur_mb.get("result") if (cur_mb and cur_mb.get("ok")) else None
        if isinstance(mb_res, dict) and mb_res.get("type") == "commands":
            results["menu_button"] = True
        else:
            res_mb = _call_tg(base_url, "setChatMenuButton", {"menu_button": {"type": "commands"}}, token=resolved)
            results["menu_button"] = bool(res_mb and res_mb.get("ok"))

        for lang in ("", "en", "ar"):
            lang_payload = {"language_code": lang} if lang else {}
            cur_name = _call_tg(base_url, "getMyName", lang_payload, token=resolved)
            name_res = cur_name.get("result") if (cur_name and cur_name.get("ok")) else None
            if not (isinstance(name_res, dict) and name_res.get("name") == BOT_NAME):
                _call_tg(base_url, "setMyName", {"name": BOT_NAME, **lang_payload}, token=resolved)

            cur_sd = _call_tg(base_url, "getMyShortDescription", lang_payload, token=resolved)
            sd_res = cur_sd.get("result") if (cur_sd and cur_sd.get("ok")) else None
            if not (isinstance(sd_res, dict) and sd_res.get("short_description") == SHORT_DESCRIPTION):
                _call_tg(
                    base_url, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION, **lang_payload}, token=resolved
                )

            cur_desc = _call_tg(base_url, "getMyDescription", lang_payload, token=resolved)
            desc_res = cur_desc.get("result") if (cur_desc and cur_desc.get("ok")) else None
            if isinstance(desc_res, dict) and desc_res.get("description") == FULL_DESCRIPTION:
                results[f"desc_{lang or 'default'}"] = True
            else:
                res_d = _call_tg(
                    base_url, "setMyDescription", {"description": FULL_DESCRIPTION, **lang_payload}, token=resolved
                )
                results[f"desc_{lang or 'default'}"] = bool(res_d and res_d.get("ok"))
    except _TelegramRateLimited:
        logger.warning("Telegram profile sync hit 429 rate limit; stopping sync.")
    return results


async def clear_stale_chat_commands(bot, chat_id: int) -> None:
    if chat_id in _CLEARED_CHATS:
        return
    _CLEARED_CHATS.add(chat_id)
    try:
        from telegram import BotCommand, BotCommandScopeChat, MenuButtonCommands
    except ImportError:
        return
    scope = BotCommandScopeChat(chat_id=chat_id)
    for lang in COMMAND_LANGS:
        try:
            await bot.delete_my_commands(scope=scope, language_code=lang or None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Clearing chat-scope commands failed (lang=%r): %s", lang, redact_token(str(exc)))
    if hasattr(bot, "set_my_commands"):
        try:
            bot_cmds = [BotCommand(c["command"], c["description"]) for c in COMMANDS]
            for lang in ("", "en", "ar"):
                await bot.set_my_commands(bot_cmds, scope=scope, language_code=lang or None)
            if hasattr(bot, "set_chat_menu_button"):
                await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())
        except Exception:  # noqa: BLE001
            pass
