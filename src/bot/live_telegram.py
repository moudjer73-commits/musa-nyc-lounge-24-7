"""Backwards-compatibility shim.

The old hardcoded "live" Telegram layer (group -2) duplicated every reply, bypassed LabContainer / SQLite / the
engines, lost its state on restart and claimed to wipe data it never touched. It has been removed. Everything now
runs through the engine-backed handlers in src/bot/app.py and src/bot/ui_buttons.py; voice output lives in
src/bot/voice_io.py. Only the small helpers below are kept so existing imports and tests keep working.
"""
from telegram import InlineKeyboardMarkup

from src.bot.ui_buttons import main_menu_keyboard
from src.bot.voice_io import ALEX_VOICE, MAYA_VOICE, clean_for_tts, synthesize_mp3_bytes

__all__ = [
    "ALEX_VOICE",
    "MAYA_VOICE",
    "RuntimeUserState",
    "USER_STATES",
    "_clean_for_tts",
    "get_user_state",
    "main_menu_kb",
    "register_live_telegram_handlers",
    "synthesize_speech_mp3",
]

_clean_for_tts = clean_for_tts


class RuntimeUserState:
    """Transient, in-memory view kept only for compatibility. Real state lives in SQLite via LabContainer."""

    def __init__(self) -> None:
        self.auto_voice: bool = True


USER_STATES: dict[int, RuntimeUserState] = {}


def get_user_state(uid: int) -> RuntimeUserState:
    if uid not in USER_STATES:
        USER_STATES[uid] = RuntimeUserState()
    return USER_STATES[uid]


def main_menu_kb(st: RuntimeUserState | None = None) -> InlineKeyboardMarkup:
    return main_menu_keyboard()


async def synthesize_speech_mp3(text: str, voice: str = ALEX_VOICE) -> bytes | None:
    return await synthesize_mp3_bytes(text, voice=voice)


def register_live_telegram_handlers(app, container=None) -> None:
    """Deprecated no-op: handlers are registered by register_all_modules()."""
    return None
