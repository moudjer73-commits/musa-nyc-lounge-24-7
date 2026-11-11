import asyncio, logging
from telegram import Update
from telegram.ext import ApplicationBuilder

from src.bot.handlers import TwinContainer, register_handlers
from src.bot.setup_tg import redact_token, sync_telegram_bot_profile
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH, SNAPSHOT_PUSH_MINUTES, resolve_telegram_token
from src.db.engine import export_encrypted_snapshot, flush_pending_snapshots, restore_encrypted_snapshot, set_snapshot_min_interval
from src.db.persist import publish_snapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def _flush_loop() -> None:
    while True:
        await asyncio.sleep(30.0)
        await asyncio.to_thread(flush_pending_snapshots)


async def _publish_loop(interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        await asyncio.to_thread(export_encrypted_snapshot, DB_PATH, SNAPSHOT_PATH, SNAPSHOT_PASSPHRASE, True)
        await asyncio.to_thread(publish_snapshot)


def build_application(token: str, container: TwinContainer):
    async def post_init(app) -> None:
        tasks = []
        if container.passphrase:
            tasks.append(asyncio.create_task(_flush_loop()))
            if SNAPSHOT_PUSH_MINUTES > 0:
                tasks.append(asyncio.create_task(_publish_loop(SNAPSHOT_PUSH_MINUTES * 60.0)))
        tasks.append(asyncio.create_task(asyncio.to_thread(sync_telegram_bot_profile, token)))
        app.bot_data["bg_tasks"] = tasks

    async def post_shutdown(app) -> None:
        for t in app.bot_data.get("bg_tasks", []):
            t.cancel()
        await asyncio.to_thread(container.flush)

    app = ApplicationBuilder().token(token).post_init(post_init).post_shutdown(post_shutdown).build()
    register_handlers(app, container)
    return app


def main() -> None:
    token = resolve_telegram_token()
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if SNAPSHOT_PASSPHRASE and SNAPSHOT_PATH.exists() and not DB_PATH.exists():
        restore_encrypted_snapshot(SNAPSHOT_PATH, DB_PATH, SNAPSHOT_PASSPHRASE)
    if SNAPSHOT_PASSPHRASE:
        set_snapshot_min_interval(45.0)
    container = TwinContainer()
    try:
        app = build_application(token, container)
        app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    except Exception as exc:
        logging.error("Fatal exit: %s", redact_token(str(exc), token))
        raise
    finally:
        container.flush()


if __name__ == "__main__":
    main()
