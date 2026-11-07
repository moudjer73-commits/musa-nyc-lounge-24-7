import os, requests
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
    {"command": "delete_my_data", "description": "Permanently clear your history and profile"},
]

def sync_telegram_bot_profile(token: str | None = None) -> dict[str, bool]:
    resolved = (token or resolve_telegram_token()).strip()
    if not resolved:
        raise RuntimeError("Telegram token is required.")
    base_url = f"https://api.telegram.org/bot{resolved}"
    results = {}

    for lang in ("", "en", "ar"):
        lang_payload = {"language_code": lang} if lang else {}
        requests.post(f"{base_url}/setMyName", json={"name": BOT_NAME, **lang_payload}, timeout=10)
        requests.post(f"{base_url}/setMyShortDescription", json={"short_description": SHORT_DESCRIPTION, **lang_payload}, timeout=10)
        r = requests.post(f"{base_url}/setMyDescription", json={"description": FULL_DESCRIPTION, **lang_payload}, timeout=10)
        results[f"desc_{lang or 'default'}"] = bool(r.json().get("ok"))

    scopes = [
        {"type": "default"},
        {"type": "all_private_chats"},
        {"type": "all_group_chats"},
    ]
    for scope in scopes:
        for lang in ("", "en", "ar"):
            del_payload = {"scope": scope, **({"language_code": lang} if lang else {})}
            requests.post(f"{base_url}/deleteMyCommands", json=del_payload, timeout=10)
            set_payload = {"commands": COMMANDS, "scope": scope, **({"language_code": lang} if lang else {})}
            rc = requests.post(f"{base_url}/setMyCommands", json=set_payload, timeout=10)
            results[f"cmd_{scope['type']}_{lang or 'def'}"] = bool(rc.json().get("ok"))

    rm = requests.post(f"{base_url}/setChatMenuButton", json={"menu_button": {"type": "commands"}}, timeout=10)
    results["menu_button"] = bool(rm.json().get("ok"))
    return results
