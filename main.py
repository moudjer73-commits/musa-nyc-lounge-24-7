import asyncio
import logging
import os

from telegram.ext import ApplicationBuilder

from src.bot.app import LabContainer, register_all_modules
from src.bot.background import start_background_tasks, stop_background_tasks
from src.bot.telegram_setup import sync_telegram_bot_profile
from src.config import resolve_telegram_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # httpx logs every poll URL, including the bot token


def build_application(bot_token: str, container: LabContainer):
    async def post_init(app) -> None:
        # Telegram profile/commands sync used to block start-up for up to minutes; now it runs off the loop.
        async def _sync() -> None:
            try:
                await asyncio.to_thread(sync_telegram_bot_profile, bot_token)
            except Exception as exc:  # noqa: BLE001
                logging.warning("Telegram profile sync skipped: %s", exc)

        app.bot_data["bg_tasks"] = start_background_tasks(app, container) + [asyncio.create_task(_sync())]

    async def post_shutdown(app) -> None:
        await stop_background_tasks(app.bot_data.get("bg_tasks", []))
        await asyncio.to_thread(container.flush)

    app = (
        ApplicationBuilder()
        .token(bot_token)
        .concurrent_updates(True)  # one slow LLM/TTS call no longer freezes every other learner
        .connection_pool_size(16)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    register_all_modules(app, container)
    return app


def main() -> None:
    bot_token = resolve_telegram_token()
    if not bot_token:
        raise RuntimeError("Telegram token required (TELEGRAM_TOKEN / BOT_TOKEN / TELEGRAM_BOT_TOKEN).")
    container = LabContainer()
    app = build_application(bot_token, container)
    logging.info("Spoken English Lab is polling...")
    # Messages sent while the runner was restarting are kept by Telegram for 24 h: answer them instead of dropping.
    drop = os.getenv("SEL_DROP_PENDING", "0") == "1"
    app.run_polling(drop_pending_updates=drop, bootstrap_retries=5, allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
