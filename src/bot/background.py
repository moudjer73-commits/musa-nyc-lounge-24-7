"""Background loops started from main.post_init: snapshot flushing, publishing, and practice reminders."""
import asyncio
import logging

from src.config import SNAPSHOT_PUSH_MINUTES
from src.db.engine import export_encrypted_snapshot, flush_pending_snapshots
from src.db.persist import publish_snapshot

logger = logging.getLogger(__name__)

REMINDER_TEXT = "👋 Quick check-in: got five minutes to chat today? Just send me anything, text or a voice note."


async def flush_loop(interval_s: float = 30.0) -> None:
    """Flush coalesced dirty DB snapshots off the event loop every 30s."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(flush_pending_snapshots)
        except Exception:  # noqa: BLE001
            logger.exception("Periodic snapshot flush failed")


async def snapshot_loop(container, interval_s: float) -> None:
    """Every interval: encrypt the live DB and publish it, so a killed runner loses minutes, not hours."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await asyncio.to_thread(
                export_encrypted_snapshot, container.db_path, container.snapshot_path, container.passphrase, True
            )
            status = await asyncio.to_thread(publish_snapshot)
            logger.info("Periodic snapshot publish: %s", status)
        except Exception:  # noqa: BLE001
            logger.exception("Periodic snapshot publish failed")


async def reminder_loop(bot, container, interval_s: float = 300.0, first_delay_s: float = 60.0) -> None:
    """Send the daily practice reminder that /remind promises (private chat id == user id)."""
    await asyncio.sleep(first_delay_s)
    while True:
        try:
            for uid in container.chunk_repo.get_due_reminder_users():
                try:
                    await bot.send_message(chat_id=uid, text=REMINDER_TEXT)
                except Exception as exc:  # noqa: BLE001
                    logger.info("Reminder to %s not delivered: %s", uid, exc)
                container.chunk_repo.mark_reminder_sent(uid)
        except Exception:  # noqa: BLE001
            logger.exception("Reminder loop iteration failed")
        await asyncio.sleep(interval_s)


def start_background_tasks(app, container) -> list[asyncio.Task]:
    tasks: list[asyncio.Task] = []
    if container.passphrase:
        tasks.append(asyncio.create_task(flush_loop(30.0)))
    if SNAPSHOT_PUSH_MINUTES > 0 and container.passphrase:
        tasks.append(asyncio.create_task(snapshot_loop(container, SNAPSHOT_PUSH_MINUTES * 60.0)))
    tasks.append(asyncio.create_task(reminder_loop(app.bot, container)))
    return tasks


async def stop_background_tasks(tasks: list[asyncio.Task]) -> None:
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
