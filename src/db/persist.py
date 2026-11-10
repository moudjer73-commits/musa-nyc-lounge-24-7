"""State persistence helpers shared by the GitHub Actions workflow and the in-run publisher.

    python -m src.db.persist restore   # before the bot starts
    python -m src.db.persist save      # after the bot stops (safe: never overwrites a snapshot that failed to restore)
    python -m src.db.persist publish   # git commit + push of the encrypted snapshot (CI only)
"""
import logging
import os
import subprocess
import sys
from pathlib import Path

from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, restore_encrypted_snapshot, restore_failed

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def publish_snapshot() -> str:
    """Commit and push data/state_snapshot.enc. Returns a short status string (never raises)."""
    try:
        if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("SEL_GIT_PERSIST") != "1":
            return "not-ci"
        if not SNAPSHOT_PATH.exists():
            return "no-snapshot"
        if restore_failed(SNAPSHOT_PATH):
            return "skipped-restore-failed"
        rel = os.path.relpath(SNAPSHOT_PATH, ROOT)
        _git("config", "user.name", "github-actions[bot]")
        _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
        _git("add", rel)
        if _git("diff", "--staged", "--quiet").returncode == 0:
            return "unchanged"
        commit = _git("commit", "-m", "chore(state): update encrypted snapshot [skip ci]")
        if commit.returncode != 0:
            return f"commit-failed: {commit.stderr[-200:]}"
        pull = _git("pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main", timeout=120)
        if pull.returncode != 0:
            _git("rebase", "--abort")
            return f"pull-failed: {pull.stderr[-200:]}"
        push = _git("push", "origin", "HEAD:main", timeout=120)
        return "pushed" if push.returncode == 0 else f"push-failed: {push.stderr[-200:]}"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def main(argv: list[str]) -> int:
    action = argv[0] if argv else ""
    if action == "restore":
        if not SNAPSHOT_PATH.exists():
            print("restore: no snapshot yet, starting fresh")
            return 0
        if not SNAPSHOT_PASSPHRASE:
            SNAPSHOT_PATH.with_name(".restore_failed").write_text("no passphrase\n")
            print("::warning::SNAPSHOT_PASSPHRASE is empty: snapshot kept untouched, bot starts with an empty DB")
            return 0
        ok = restore_encrypted_snapshot(SNAPSHOT_PATH, DB_PATH, SNAPSHOT_PASSPHRASE)
        print("restore:", "ok" if ok else "FAILED (snapshot protected from being overwritten)")
        if not ok:
            print("::warning::State snapshot could not be restored; it will NOT be overwritten in this run.")
        return 0
    if action == "save":
        out = export_encrypted_snapshot(DB_PATH, SNAPSHOT_PATH, SNAPSHOT_PASSPHRASE)
        print("save:", out or "skipped")
        return 0
    if action == "publish":
        print("publish:", publish_snapshot())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
