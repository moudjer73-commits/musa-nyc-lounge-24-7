import logging
from telegram.ext import ApplicationBuilder
from src.bot.app import LabContainer, register_all_modules
from src.bot.telegram_setup import sync_telegram_bot_profile
from src.config import resolve_telegram_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def main() -> None:
    bot_token = resolve_telegram_token()
    if not bot_token:
        raise RuntimeError("Telegram token required (TELEGRAM_TOKEN / BOT_TOKEN / TELEGRAM_BOT_TOKEN).")
    try:
        sync_telegram_bot_profile(bot_token)
    except Exception as exc:
        logging.warning("Telegram profile sync skipped: %s", exc)
    container = LabContainer()
    app = ApplicationBuilder().token(bot_token).build()
    register_all_modules(app, container)
    logging.info("Spoken English Lab v1.0 is live and polling...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
