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
    "Practice speaking natural American English with your conversation partners (Sam, Maya, Dana, Prof. Ruiz). "
    "No stiff textbook lessons or stressful tests—just real conversations adapted to "
    "everyday life, work, study, and social situations.\n\n"
    "• Speak by voice or text in realistic scenarios\n"
    "• Switch Scenes, Partners, and Register on the fly\n"
    "• Flip any reply across registers or build a 3-step Register Ladder\n"
    "• Get helpful feedback without interrupting your flow\n\n"
    "Tap Start below to open your Lab Dashboard."
)

COMMANDS = [
    {"command": "start", "description": "🏠 Lab Dashboard & Active Scene"},
    {"command": "room", "description": "🚪 Switch Scene & Partner (Sam, Maya, Prof. Ruiz)"},
    {"command": "flip", "description": "🔄 Flip Last Reply to Another Register"},
    {"command": "ladder", "description": "🪜 3-Step Register Ladder (Casual → Work → Formal)"},
    {"command": "heard", "description": "👂 Save a Real-Life Phrase to Notebook"},
    {"command": "session", "description": "🎙️ Start Today's 10-Min Practice Session"},
    {"command": "podcast", "description": "🎧 Listen to a Two-Host Dialogue Podcast"},
    {"command": "prosody", "description": "🗣️ 5-Min Rhythm, Stress & Connected Speech Lab"},
    {"command": "pressure", "description": "⏱️ 90-Second Spontaneous Speaking Challenge"},
    {"command": "review", "description": "📚 Practice Expressions Due for Review"},
    {"command": "voice", "description": "🔊 Toggle Voice Replies On or Off"},
    {"command": "stats_naturalness", "description": "📈 View Conversation Naturalness Metrics"},
    {"command": "progress", "description": "📊 View Your Fluency Progress"},
    {"command": "profile", "description": "👤 Learner Profile & Calibration"},
    {"command": "privacy", "description": "🔒 Privacy & Data Controls"},
    {"command": "delete_everything", "description": "🗑️ Permanently Clear Your History & Profile"},
]

logger = logging.getLogger(__name__)

COMMAND_LANGS = ("", "en", "ar", "fr", "es", "de", "tr")
COMMAND_SCOPES = ({"type": "default"}, {"type": "all_private_chats"}, {"type": "all_group_chats"})
_CLEARED_CHATS: set[int] = set()


def _post(base_url: str, method: str, payload: dict, timeout: int = 20) -> bool:
    """One isolated Bot API call with a single retry; never raises."""
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
    """Remove old per-chat command lists (BotCommandScopeChat) and push fresh commands to this chat."""
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
    # Push updated commands directly to this chat scope on real Telegram Bot instances
    if hasattr(bot, "set_my_commands"):
        try:
            bot_cmds = [BotCommand(c["command"], c["description"]) for c in COMMANDS]
            await bot.set_my_commands(bot_cmds, scope=scope)
            if hasattr(bot, "set_chat_menu_button"):
                await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())
        except Exception:  # noqa: BLE001
            pass
