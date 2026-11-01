# S1 hand-off to Gemini (Durability and Migrations)

## What you need to change in your files

1. **`.github/workflows/main.yml`**: add `python -m pytest tests_v3 -q` next to `pytest tests_v2 -q` and `python tests.py`. `SPOKEN_LAB_DB` must point at a temp file (`config.py` reads it at import; the tests set a default, CI should not rely on that).
2. **Deploy step**, in this order:
   * `python -m database status --check --db-path "$DB"` → exit **10** means migrations are pending, so take the pre-migrate anchor/snapshot first; exit 0 means nothing to do.
   * `python -m database migrate --db-path "$DB"` → prints one JSON line (`applied_now`, `current_version`, `pending`, `checksum_ok`).
   * Exit codes: `0` ok, `1` error (reason on stderr: checksum mismatch, verification failure, database ahead of the code, unreadable file), `10` pending (only with `status --check`).
3. **`tools/state_snapshot.py`**: a v2 database has `event_log` but no `schema_migrations`; the runner treats that as baseline version 0. `status` is strictly read-only, so it is safe to call before the snapshot.
4. **`config.py` / `app.py`**: construct `EventSourcedDatabase(path, clock=SystemClock())`. The constructor runs the migration runner, so a booting process on a stale file migrates it (idempotent, one writer wins the `BEGIN IMMEDIATE`). Pass `auto_migrate=False` only in tools that must not touch the file.
5. **Python 3.11** is the CI target and is the one thing I could not run in full (see the changelog).

## Things you can rely on

* `append_event_ex(...)` writes `schema_version='2.0'`, validated through the V2 model; `append_event(...)` is unchanged for old callers and writes `1.0`.
* `accept_scene_turn(...)` = message event + `session_revision` bump in one transaction, idempotent on `idempotency_key` (use `tg:<chat>:<message_id>`).
* `append_events_if_revision(session_id, expected_revision, [EventSpec, ...])` is all-or-nothing; `False` means the revision moved and nothing was written.
* Callback data: `build_callback` / `parse_callback` in `interaction.py`. Anything `parse_callback` returns `None` for (legacy `set*`, `tts:`, `card:`, `guide:`, `decode_reveal`, malformed, unknown) stays with the old handlers or gets `STALE_CALLBACK_TEXT`; it never raises.
* Time: inject a `Clock`. `FakeClock.advance(...)` / `.travel(days=...)` replace `time.sleep` in tests.

## Questions for you / Musa

1. `sessions.scene_revision`: S0 names it but never says what increments it. I added `bump_scene_revision` and left the policy to the orchestrator (S2/S4). Say if you want it bumped by a specific event.
2. `feedback_card_delivered` v1 → V2 defaults (`trigger='tap'`, empty `card_id`): S0 is silent, this is my choice.
3. V2 `responder_reply_delivered` has no "asked an optional question" flag, so for V2 rows the projection derives it from the reply text (`?`) and `comprehension_status`. v1 rows keep their stored flags. If you want an explicit field, it is a one-line addition to `ResponderReplyV2` in S2.
4. `idx_event_log_correlation` is kept (the v2 schema had it; the S0 index list did not mention it).
5. `CallClass` belongs to your transport; nothing in S1 references it.
