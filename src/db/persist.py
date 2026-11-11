import os, subprocess, sys
from pathlib import Path
from src.config import DB_PATH, SNAPSHOT_PASSPHRASE, SNAPSHOT_PATH
from src.db.engine import export_encrypted_snapshot, restore_encrypted_snapshot, restore_failed

ROOT = Path(__file__).resolve().parent.parent.parent


def _git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def publish_snapshot() -> str:
    try:
        if os.getenv("GITHUB_ACTIONS") != "true" and os.getenv("SEL_GIT_PERSIST") != "1":
            return "not-ci"
        if not SNAPSHOT_PATH.exists() or restore_failed(SNAPSHOT_PATH):
            return "skipped"
        rel = os.path.relpath(SNAPSHOT_PATH, ROOT)
        _git("config", "user.name", "github-actions[bot]")
        _git("config", "user.email", "github-actions[bot]@users.noreply.github.com")
        _git("add", rel)
        if _git("diff", "--staged", "--quiet").returncode == 0:
            return "unchanged"
        if _git("commit", "-m", "chore(state): update encrypted snapshot [skip ci]").returncode != 0:
            return "commit-failed"
        if _git("pull", "--rebase", "--autostash", "-X", "theirs", "origin", "main", timeout=120).returncode != 0:
            _git("rebase", "--abort")
            return "pull-failed"
        return "pushed" if _git("push", "origin", "HEAD:main", timeout=120).returncode == 0 else "push-failed"
    except Exception as exc:
        return f"error: {exc}"


def main(argv: list[str]) -> int:
    act = argv[0] if argv else ""
    if act == "restore":
        if not SNAPSHOT_PATH.exists():
            return 0
        if not SNAPSHOT_PASSPHRASE:
            SNAPSHOT_PATH.with_name(".restore_failed").write_text("no_passphrase\n")
            return 0
        restore_encrypted_snapshot(SNAPSHOT_PATH, DB_PATH, SNAPSHOT_PASSPHRASE)
        return 0
    if act == "save":
        export_encrypted_snapshot(DB_PATH, SNAPSHOT_PATH, SNAPSHOT_PASSPHRASE, force=True)
        return 0
    if act == "publish":
        print("publish:", publish_snapshot())
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
