import logging
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
    "• Everyday Service & Phone Calls (Marcus)\n\n"
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

COMMAND_LANGS = ("", "en", "ar", "fr", "es", "de", "tr")
COMMAND_SCOPES = ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"})
_CLEARED_CHATS: set[int] = set()


def _post(base_url: str, method: str, payload: dict, timeout: int = 20) -> bool:
    for attempt in (1, 2):
        try:
            r = requests.post(f"{base_url}/{method}", json=payload, timeout=timeout)
            return bool(r.json().get("ok"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram %s failed (attempt %d): %s", method, attempt, exc)
    return False


def sync_telegram_bot_profile(token: str | None = None) -> dict[str, bool]:
    resolved = (token or resolve_telegram_token()).strip()
    if not resolved:
        raise RuntimeError("Telegram token is required.")
    base_url = f"https://api.telegram.org/bot{resolved}"
    results: dict[str, bool] = {}

    for scope in COMMAND_SCOPES:
        for lang in COMMAND_LANGS:
            payload = {"commands": COMMANDS, "scope": scope, **({"language_code": lang} if lang else {})}
            results[f"cmd_{scope['type']}_{lang or 'def'}"] = _post(base_url, "setMyCommands", payload)
    results["menu_button"] = _post(base_url, "setChatMenuButton", {"menu_button": {"type": "commands"}})

    for lang in ("", "en", "ar"):
        lang_payload = {"language_code": lang} if lang else {}
        _post(base_url, "setMyName", {"name": BOT_NAME, **lang_payload})
        _post(base_url, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION, **lang_payload})
        results[f"desc_{lang or 'default'}"] = _post(
            base_url, "setMyDescription", {"description": FULL_DESCRIPTION, **lang_payload}
        )
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
            logger.warning("Clearing chat-scope commands failed (lang=%r): %s", lang, exc)
    if hasattr(bot, "set_my_commands"):
        try:
            bot_cmds = [BotCommand(c["command"], c["description"]) for c in COMMANDS]
            for lang in ("", "en", "ar"):
                await bot.set_my_commands(bot_cmds, scope=scope, language_code=lang or None)
            if hasattr(bot, "set_chat_menu_button"):
                await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())
        except Exception:  # noqa: BLE001
            pass
