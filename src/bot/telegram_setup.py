import logging
import requests
from src.config import resolve_telegram_token

BOT_NAME = "Spoken English Lab"
SHORT_DESCRIPTION = (
    "Practice natural spoken American English with Alex. "
    "Real conversations, everyday situations, and smart feedback."
)
FULL_DESCRIPTION = (
    "Welcome to Spoken English Lab 🎙️\n\n"
    "Practice speaking natural American English with Alex, your conversation partner. "
    "No stiff textbook lessons or stressful tests—just real conversations adapted to "
    "everyday life, work, study, and social situations.\n\n"
    "• Speak by voice or text in realistic scenarios\n"
    "• Choose your Room, Directness, and Tone\n"
    "• Build natural expressions that fit the moment\n"
    "• Get helpful feedback without interrupting your flow\n\n"
    "Tap Start below to begin talking with Alex."
)
COMMANDS = [
    {"command": "start", "description": "Open main menu and talk with Alex"},
    {"command": "session", "description": "Start today's 10-minute practice session"},
    {"command": "room", "description": "Set Room, Directness, and Tone"},
    {"command": "profile", "description": "Adjust your goals and feedback preferences"},
    {"command": "review", "description": "Practice expressions due for review"},
    {"command": "podcast", "description": "Listen to a short two-host dialogue"},
    {"command": "prosody", "description": "5-minute rhythm, stress, and pronunciation lab"},
    {"command": "pressure", "description": "90-second spontaneous speaking challenge"},
    {"command": "progress", "description": "View your personal progress"},
    {"command": "summary", "description": "See your top takeaways from today"},
    {"command": "hint", "description": "Get a clue when looking for a word"},
    {"command": "reentry", "description": "Warm-up session after taking a break"},
    {"command": "voice", "description": "Turn Alex's voice replies on or off"},
    {"command": "privacy", "description": "What is stored, and how to erase it"},
    {"command": "delete_everything", "description": "Permanently clear your history and profile"},
]

logger = logging.getLogger(__name__)

# Languages whose command list must be overwritten too (Telegram shows the list of the user's app language first).
COMMAND_LANGS = ("", "en", "ar", "fr", "es", "de", "tr")
COMMAND_SCOPES = ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"})
_CLEARED_CHATS: set[int] = set()


def _post(base_url: str, method: str, payload: dict, timeout: int = 20) -> bool:
    """One isolated Bot API call with a single retry; never raises."""
    for attempt in (1, 2):
        try:
            r = requests.post(f"{base_url}/{method}", json=payload, timeout=timeout)
            return bool(r.json().get("ok"))
        except Exception as exc:  # noqa: BLE001 - timeouts must not abort the rest of the sync
            logger.warning("Telegram %s failed (attempt %d): %s", method, attempt, exc)
    return False


def sync_telegram_bot_profile(token: str | None = None) -> dict[str, bool]:
    resolved = (token or resolve_telegram_token()).strip()
    if not resolved:
        raise RuntimeError("Telegram token is required.")
    base_url = f"https://api.telegram.org/bot{resolved}"
    results: dict[str, bool] = {}

    # 1) Commands first: this is what users see in the Menu button, so it must not be blocked by slower calls.
    for scope in COMMAND_SCOPES:
        for lang in COMMAND_LANGS:
            payload = {"commands": COMMANDS, "scope": scope, **({"language_code": lang} if lang else {})}
            results[f"cmd_{scope['type']}_{lang or 'def'}"] = _post(base_url, "setMyCommands", payload)
    results["menu_button"] = _post(base_url, "setChatMenuButton", {"menu_button": {"type": "commands"}})

    # 2) Cosmetic profile texts last, each call isolated.
    for lang in ("", "en", "ar"):
        lang_payload = {"language_code": lang} if lang else {}
        _post(base_url, "setMyName", {"name": BOT_NAME, **lang_payload})
        _post(base_url, "setMyShortDescription", {"short_description": SHORT_DESCRIPTION, **lang_payload})
        results[f"desc_{lang or 'default'}"] = _post(base_url, "setMyDescription", {"description": FULL_DESCRIPTION, **lang_payload})
    return results


async def clear_stale_chat_commands(bot, chat_id: int) -> None:
    """Remove old per-chat command lists (BotCommandScopeChat) so the current default list shows in this chat.

    Runs at most once per chat per process; every call is isolated and failures are only logged.
    """
    if chat_id in _CLEARED_CHATS:
        return
    _CLEARED_CHATS.add(chat_id)
    try:
        from telegram import BotCommandScopeChat
    except ImportError:
        return
    for lang in COMMAND_LANGS:
        try:
            await bot.delete_my_commands(scope=BotCommandScopeChat(chat_id=chat_id), language_code=lang or None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Clearing chat-scope commands failed (lang=%r): %s", lang, exc)
