import logging
import threading
from telegram.ext import ApplicationBuilder
from src.bot.app import LabContainer, register_all_modules
from src.bot.telegram_setup import sync_telegram_bot_profile
from src.config import resolve_telegram_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def _bg_sync(token: str) -> None:
    try:
        sync_telegram_bot_profile(token)
    except Exception as exc:
        logging.warning("Telegram profile sync skipped: %s", exc)


def main() -> None:
    bot_token = resolve_telegram_token()
    if not bot_token:
        raise RuntimeError("Telegram token required (TELEGRAM_TOKEN / BOT_TOKEN / TELEGRAM_BOT_TOKEN).")
    threading.Thread(target=_bg_sync, args=(bot_token,), daemon=True).start()
    container = LabContainer()
    app = ApplicationBuilder().token(bot_token).build()
    register_all_modules(app, container)
    logging.info("Spoken English Lab v1.1 (engine-backed chat + Alex voice + podcast audio) is polling...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
