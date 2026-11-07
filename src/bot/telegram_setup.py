import os, requests

BOT_NAME = "Spoken English Lab"
SHORT_DESCRIPTION = (
    "Practice natural spoken American English with Alex. "
    "Real conversations, everyday situations, and smart feedback."
)
FULL_DESCRIPTION = (
    "Welcome to Spoken English Lab\n\n"
    "Practice speaking natural American English with Alex, your conversation partner. "
    "No stiff textbook lessons or stressful tests—just real conversations adapted to "
    "everyday life, work, study, and social situations.\n\n"
    "• Speak by voice or text in realistic scenarios\n"
    "• Build natural expressions that fit the moment\n"
    "• Get helpful feedback without interrupting your flow\n"
    "• Listen to short dialogues and train your ear\n\n"
    "Tap Start below to begin talking with Alex."
)
COMMANDS = [
    {"command": "start", "description": "Start or resume talking with Alex"},
    {"command": "session", "description": "Start today's 10-minute practice session"},
    {"command": "room", "description": "Choose the setting (casual, social, work, study)"},
    {"command": "switch", "description": "Practice adapting your tone to a new situation"},
    {"command": "review", "description": "Practice expressions you learned recently"},
    {"command": "podcast", "description": "Listen to a short two-host dialogue"},
    {"command": "curated", "description": "Explore recommended real-world American podcasts"},
    {"command": "prosody", "description": "5-minute rhythm, stress, and pronunciation practice"},
    {"command": "pressure", "description": "Try a 90-second spontaneous speaking challenge"},
    {"command": "hint", "description": "Get a clue when a word is on the tip of your tongue"},
    {"command": "summary", "description": "See your top speaking takeaways from today"},
    {"command": "progress", "description": "View your personal progress and growth"},
    {"command": "profile", "description": "View your learning goals and preferences"},
    {"command": "reentry", "description": "Quick warm-up session after taking a break"},
    {"command": "remind", "description": "Set a daily practice reminder"},
    {"command": "delete_my_data", "description": "Permanently clear your history and profile"},
]

def sync_telegram_bot_profile(token: str | None = None) -> dict[str, bool]:
    resolved = (token or os.getenv("TELEGRAM_BOT_TOKEN", "")).strip()
    if not resolved:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required.")
    base_url = f"https://api.telegram.org/bot{resolved}"
    endpoints = [
        ("setMyName", {"name": BOT_NAME}),
        ("setMyShortDescription", {"short_description": SHORT_DESCRIPTION}),
        ("setMyDescription", {"description": FULL_DESCRIPTION}),
        ("setMyCommands", {"commands": COMMANDS}),
    ]
    results = {}
    for method, payload in endpoints:
        resp = requests.post(f"{base_url}/{method}", json=payload, timeout=15)
        results[method] = bool(resp.json().get("ok"))
    return results

if __name__ == "__main__":
    print(sync_telegram_bot_profile())
