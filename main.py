import logging, os
from telegram.ext import ApplicationBuilder
from src.bot.app import LabContainer, register_all_modules

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

def main() -> None:
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required.")
    container = LabContainer()
    app = ApplicationBuilder().token(bot_token).build()
    register_all_modules(app, container)
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
