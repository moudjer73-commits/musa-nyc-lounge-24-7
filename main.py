import asyncio, inspect, logging, time
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
        logging.info("Echo Twin v3.0 polling active and ready for updates.")

    async def post_shutdown(app) -> None:
        for t in app.bot_data.get("bg_tasks", []):
            t.cancel()
        await asyncio.to_thread(container.flush)

    builder = ApplicationBuilder().token(token).concurrent_updates(True).post_init(post_init).post_shutdown(post_shutdown)
    app = builder.build()
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
    for attempt in range(1, 6):
        try:
            app = build_application(token, container)
            poll_kwargs = {"allowed_updates": Update.ALL_TYPES, "drop_pending_updates": (attempt == 1)}
            if "bootstrap_retries" in inspect.signature(app.run_polling).parameters:
                poll_kwargs["bootstrap_retries"] = -1
            app.run_polling(**poll_kwargs)
            break
        except KeyboardInterrupt:
            break
        except Exception as exc:
            logging.error("Polling interrupted (attempt %d/5): %s", attempt, redact_token(str(exc), token))
            if attempt == 5:
                raise
            time.sleep(6 * attempt)
    container.flush()


if __name__ == "__main__":
    main()
