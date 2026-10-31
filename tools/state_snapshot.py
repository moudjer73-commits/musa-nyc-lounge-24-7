"""Encrypted Rolling Release State Snapshot Tool (S0 Contracts, Section 1 A1-A6 & Section 6)."""
from __future__ import annotations

import argparse
import gzip
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

RELEASE_TAG = "state"
ROLLING_RE = re.compile(r"^state-(\d{12})\.db\.gpg$")
DAILY_RE = re.compile(r"^state-daily-(\d{8})-(\d{12})\.db\.gpg$")
ANCHOR_RE = re.compile(r"^state-premigrate-v(\d+)-(\d{12})\.db\.gpg$")


def _send_admin_alert(text: str) -> None:
    token = (os.getenv("TELEGRAM_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
    admin_id = (os.getenv("ADMIN_ID") or "").strip()
    if not token or not admin_id:
        print(f"[ALERT-NO-TELEGRAM] {text}", file=sys.stderr)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": int(admin_id), "text": f"🚨 Spoken English Lab Alert:\n{text}"},
            timeout=10,
        )
    except Exception as exc:
        print(f"[ALERT-ERROR] {exc}", file=sys.stderr)


def _require_passphrase() -> str:
    # A1: SNAPSHOT_PASSPHRASE is mandatory; never derive from bot token.
    pw = (os.getenv("SNAPSHOT_PASSPHRASE") or "").strip()
    if not pw:
        msg = "SNAPSHOT_PASSPHRASE is missing. Refusing to run snapshot operation."
        _send_admin_alert(msg)
        raise SystemExit(1)
    return pw


def _gh_headers() -> dict[str, str]:
    tok = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    headers = {"Accept": "application/vnd.github+json"}
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def _api_base() -> str:
    repo = (os.getenv("GITHUB_REPOSITORY") or "moudjer73-commits/musa-nyc-lounge-24-7").strip()
    return f"https://api.github.com/repos/{repo}"


def _request_with_retry(method: str, url: str, *, allow_404: bool = False, **kwargs: Any) -> requests.Response:
    # A6: HTTP errors other than 404 retry 3 times with backoff, then exit 1.
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            if allow_404 and resp.status_code == 404:
                return resp
            if 200 <= resp.status_code < 300:
                return resp
        except Exception as exc:
            last_exc = exc
        if attempt < 2:
            time.sleep(1.5 * (2 ** attempt))
    _send_admin_alert(f"GitHub API failure on {method} {url} after 3 attempts ({last_exc}).")
    raise SystemExit(1)


def _marker_paths(db_path: Path) -> tuple[Path, Path, Path]:
    parent = db_path.parent
    return (
        parent / f".{db_path.name}.restored_seq",
        parent / f".{db_path.name}.last_backed_up_seq",
        parent / f".{db_path.name}.last_snapshot_ts",
    )


def _extract_restore_seq(name: str) -> int | None:
    # A4: Restore considers rolling and daily only (ignores premigrate anchors).
    m = ROLLING_RE.match(name)
    if m:
        return int(m.group(1))
    m_d = DAILY_RE.match(name)
    if m_d:
        return int(m_d.group(2))
    return None


def _gpg_encrypt(raw_gz: bytes, passphrase: str) -> bytes:
    # A2: Add --pinentry-mode loopback
    proc = subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--symmetric",
            "--cipher-algo",
            "AES256",
            "--compress-algo",
            "none",
        ],
        input=passphrase.encode("utf-8") + b"\n" + raw_gz,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gpg encrypt failed: {proc.stderr.decode('utf-8', 'ignore')}")
    return proc.stdout


def _gpg_decrypt(cipher_bytes: bytes, passphrase: str) -> bytes:
    # A2: Add --pinentry-mode loopback
    proc = subprocess.run(
        [
            "gpg",
            "--batch",
            "--yes",
            "--pinentry-mode",
            "loopback",
            "--passphrase-fd",
            "0",
            "--decrypt",
        ],
        input=passphrase.encode("utf-8") + b"\n" + cipher_bytes,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gpg decrypt failed: {proc.stderr.decode('utf-8', 'ignore')}")
    return proc.stdout


def _verify_sqlite_file(path: Path) -> int:
    conn = sqlite3.connect(str(path))
    try:
        chk = conn.execute("PRAGMA integrity_check").fetchone()
        if not chk or str(chk[0]).lower() != "ok":
            raise RuntimeError(f"integrity_check failed: {chk}")
        tbl = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_log'"
        ).fetchone()
        if not tbl:
            return 0
        row = conn.execute("SELECT COALESCE(MAX(event_seq), 0) FROM event_log").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def cmd_restore(db_path: Path) -> int:
    pw = _require_passphrase()
    restored_marker, backed_marker, ts_marker = _marker_paths(db_path)
    resp = _request_with_retry("GET", f"{_api_base()}/releases/tags/{RELEASE_TAG}", allow_404=True, headers=_gh_headers())

    assets: list[tuple[int, dict[str, Any]]] = []
    if resp.status_code != 404:
        for a in resp.json().get("assets", []):
            seq = _extract_restore_seq(str(a.get("name") or ""))
            if seq is not None:
                assets.append((seq, a))

    if not assets:
        # A6: ALLOW_EMPTY_DB defaults to 0
        allow_empty = (os.getenv("ALLOW_EMPTY_DB") or "0").strip() == "1"
        if allow_empty:
            restored_marker.write_text("0", encoding="utf-8")
            backed_marker.write_text("0", encoding="utf-8")
            ts_marker.write_text(str(time.time()), encoding="utf-8")
            return 0
        _send_admin_alert("Restore failed: no snapshot asset found and ALLOW_EMPTY_DB != 1.")
        return 1

    assets.sort(key=lambda item: item[0], reverse=True)
    for expected_seq, asset in assets[:2]:
        try:
            dl_headers = _gh_headers()
            dl_headers["Accept"] = "application/octet-stream"
            dl = _request_with_retry("GET", asset["url"], headers=dl_headers)
            plain_gz = _gpg_decrypt(dl.content, pw)
            raw_db = gzip.decompress(plain_gz)
            tmp_path = db_path.with_suffix(".restore_tmp")
            tmp_path.write_bytes(raw_db)
            actual_seq = _verify_sqlite_file(tmp_path)
            if actual_seq != expected_seq:
                tmp_path.unlink(missing_ok=True)
                continue
            for wal_ext in ("-wal", "-shm"):
                Path(str(db_path) + wal_ext).unlink(missing_ok=True)
            tmp_path.replace(db_path)
            now_ts = time.time()
            restored_marker.write_text(str(actual_seq), encoding="utf-8")
            backed_marker.write_text(str(actual_seq), encoding="utf-8")
            ts_marker.write_text(str(now_ts), encoding="utf-8")
            return 0
        except Exception as exc:
            print(f"Candidate asset {asset.get('name')} failed verification: {exc}", file=sys.stderr)

    _send_admin_alert("Restore failed: top candidate snapshots failed decryption/integrity verification.")
    return 1


def _is_db_dirty(db_path: Path, ts_marker: Path) -> bool:
    # A5: dirty = DB file or wal mtime newer than the start of the last snapshot.
    if not ts_marker.exists():
        return True
    try:
        last_ts = float(ts_marker.read_text(encoding="utf-8").strip())
    except ValueError:
        return True
    candidates = [db_path, Path(str(db_path) + "-wal")]
    return any(p.exists() and p.stat().st_mtime > last_ts for p in candidates)


def _ensure_release() -> dict[str, Any]:
    resp = _request_with_retry("GET", f"{_api_base()}/releases/tags/{RELEASE_TAG}", allow_404=True, headers=_gh_headers())
    if resp.status_code != 404:
        return resp.json()
    create = _request_with_retry(
        "POST",
        f"{_api_base()}/releases",
        headers=_gh_headers(),
        json={"tag_name": RELEASE_TAG, "name": "Encrypted Bot State", "prerelease": True},
    )
    return create.json()


def cmd_backup(
    db_path: Path,
    *,
    daily_anchor: bool = False,
    force: bool = False,
    anchor_version: int | None = None,
) -> int:
    pw = _require_passphrase()
    if not db_path.exists():
        return 0

    restored_marker, backed_marker, ts_marker = _marker_paths(db_path)
    if not force and anchor_version is None and not _is_db_dirty(db_path, ts_marker):
        return 0

    snapshot_start_ts = time.time()
    copy_path = db_path.with_suffix(".snap_copy_tmp")
    copy_path.unlink(missing_ok=True)

    # A3: Use backup(pages=-1), set journal_mode=DELETE on the copy, then integrity_check,
    # and read max(event_seq) from the copy!
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(copy_path))
    try:
        src_conn.backup(dst_conn, pages=-1)
    finally:
        dst_conn.close()
        src_conn.close()

    chk_conn = sqlite3.connect(str(copy_path))
    try:
        chk_conn.execute("PRAGMA journal_mode=DELETE")
        chk_conn.commit()
    finally:
        chk_conn.close()

    copy_seq = _verify_sqlite_file(copy_path)
    restored_seq = int(restored_marker.read_text(encoding="utf-8").strip()) if restored_marker.exists() else 0

    if copy_seq < restored_seq:
        copy_path.unlink(missing_ok=True)
        _send_admin_alert(f"Stale local seq ({copy_seq} < restored {restored_seq}). Aborting backup.")
        return 2

    release = _ensure_release()
    remote_seqs = [
        s for a in release.get("assets", []) if (s := _extract_restore_seq(str(a.get("name") or ""))) is not None
    ]
    remote_max = max(remote_seqs) if remote_seqs else -1

    # A5: current < remote_max = exit 3 plus admin alert (equal is allowed).
    if copy_seq < remote_max:
        copy_path.unlink(missing_ok=True)
        _send_admin_alert(f"Remote state is newer ({remote_max} > local {copy_seq}). Aborting backup.")
        return 3

    raw_bytes = copy_path.read_bytes()
    copy_path.unlink(missing_ok=True)
    gz_bytes = gzip.compress(raw_bytes, compresslevel=9)
    gpg_bytes = _gpg_encrypt(gz_bytes, pw)

    upload_url_base = release["upload_url"].split("{")[0]
    names_to_upload: list[str] = []
    if anchor_version is not None:
        names_to_upload.append(f"state-premigrate-v{anchor_version}-{copy_seq:012d}.db.gpg")
    else:
        names_to_upload.append(f"state-{copy_seq:012d}.db.gpg")
        today_str = datetime.now(timezone.utc).strftime("%Y%m%d")
        has_today = any(
            str(a.get("name") or "").startswith(f"state-daily-{today_str}-")
            for a in release.get("assets", [])
        )
        if daily_anchor or not has_today:
            names_to_upload.append(f"state-daily-{today_str}-{copy_seq:012d}.db.gpg")

    existing_by_name = {str(a.get("name")): a["id"] for a in release.get("assets", [])}
    for asset_name in names_to_upload:
        if asset_name in existing_by_name:
            _request_with_retry(
                "DELETE",
                f"{_api_base()}/releases/assets/{existing_by_name[asset_name]}",
                headers=_gh_headers(),
            )
        up_headers = _gh_headers()
        up_headers["Content-Type"] = "application/octet-stream"
        _request_with_retry(
            "POST",
            f"{upload_url_base}?name={asset_name}",
            headers=up_headers,
            data=gpg_bytes,
        )

    # A4: Prune old rolling (keep 6) and daily (keep 7); NEVER delete premigrate anchors.
    refreshed = _request_with_retry(
        "GET", f"{_api_base()}/releases/tags/{RELEASE_TAG}", headers=_gh_headers()
    ).json()
    rolling_assets = []
    daily_assets = []
    for a in refreshed.get("assets", []):
        nm = str(a.get("name") or "")
        if ANCHOR_RE.match(nm):
            continue
        if m_r := ROLLING_RE.match(nm):
            rolling_assets.append((int(m_r.group(1)), a["id"]))
        elif m_d := DAILY_RE.match(nm):
            daily_assets.append(((m_d.group(1), int(m_d.group(2))), a["id"]))

    rolling_assets.sort(key=lambda x: x[0], reverse=True)
    for _, aid in rolling_assets[6:]:
        _request_with_retry("DELETE", f"{_api_base()}/releases/assets/{aid}", headers=_gh_headers())

    daily_assets.sort(key=lambda x: x[0], reverse=True)
    for _, aid in daily_assets[7:]:
        _request_with_retry("DELETE", f"{_api_base()}/releases/assets/{aid}", headers=_gh_headers())

    backed_marker.write_text(str(copy_seq), encoding="utf-8")
    ts_marker.write_text(str(snapshot_start_ts), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.state_snapshot")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rest = sub.add_parser("restore")
    p_rest.add_argument("--db-path", required=True)

    p_back = sub.add_parser("backup")
    p_back.add_argument("--db-path", required=True)
    p_back.add_argument("--daily-anchor", action="store_true")
    p_back.add_argument("--force", action="store_true")

    p_seed = sub.add_parser("seed")
    p_seed.add_argument("--db-path", required=True)

    p_anc = sub.add_parser("anchor-name")
    p_anc.add_argument("version", type=int)
    p_anc.add_argument("--db-path", default="spoken_english_lab.db")

    p_alert = sub.add_parser("alert-failure")
    p_alert.add_argument("--reason", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "restore":
        return cmd_restore(Path(args.db_path))
    if args.cmd == "backup":
        return cmd_backup(Path(args.db_path), daily_anchor=args.daily_anchor, force=args.force)
    if args.cmd == "seed":
        return cmd_backup(Path(args.db_path), daily_anchor=True, force=True)
    if args.cmd == "anchor-name":
        return cmd_backup(Path(args.db_path), force=True, anchor_version=args.version)
    if args.cmd == "alert-failure":
        _send_admin_alert(args.reason)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
