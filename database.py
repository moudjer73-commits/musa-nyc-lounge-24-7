import json
import re
import sqlite3
import uuid
from typing import Any, Dict, List, Optional
from config import ADMIN_ID, DB_PATH


def canonicalize_target_key(raw_key: str) -> str:
    k = (raw_key or "").strip().lower()
    k = re.sub(r"\[[^\]]+\]", "", k)
    k = re.sub(r"[^a-z0-9]+", "_", k).strip("_")
    return f"lexical:{k or 'general'}"


class LoungeDatabase:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _conn(self):
        return sqlite3.connect(self.db_path, timeout=15)

    def _init_db(self):
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    whitelisted INTEGER DEFAULT 1,
                    current_mode TEXT DEFAULT 'spontaneous_chat',
                    active_scene_id TEXT,
                    negotiation_state_json TEXT DEFAULT '{}',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS v3_evidence_ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    scene_id TEXT NOT NULL,
                    parent_event_id INTEGER NULL,
                    event_type TEXT NOT NULL,
                    functional_category TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    canonical_target_key TEXT NOT NULL,
                    provenance TEXT NOT NULL,
                    attempt_number INTEGER DEFAULT 1,
                    outcome TEXT NOT NULL,
                    evidence_stage TEXT NOT NULL,
                    learner_text TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, scene_id, event_type, canonical_target_key, attempt_number),
                    FOREIGN KEY(parent_event_id) REFERENCES v3_evidence_ledger(id)
                )
                """
            )
            conn.commit()

    def get_user(self, user_id: int, username: str = "", first_name: str = "") -> Dict[str, Any]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT user_id, username, first_name, whitelisted, current_mode, active_scene_id, negotiation_state_json FROM users WHERE user_id = ?",
                (int(user_id),),
            ).fetchone()
            if not row:
                scene_id = f"scene_{uuid.uuid4().hex[:8]}"
                is_wl = 1
                conn.execute(
                    "INSERT INTO users (user_id, username, first_name, whitelisted, current_mode, active_scene_id, negotiation_state_json) VALUES (?, ?, ?, ?, 'spontaneous_chat', ?, '{}')",
                    (int(user_id), username or "", first_name or "Member", is_wl, scene_id),
                )
                conn.commit()
                return {
                    "user_id": int(user_id),
                    "username": username or "",
                    "first_name": first_name or "Member",
                    "whitelisted": is_wl,
                    "current_mode": "spontaneous_chat",
                    "active_scene_id": scene_id,
                    "negotiation_state": {},
                }
            scene_id = row[5] or f"scene_{uuid.uuid4().hex[:8]}"
            if not row[5]:
                conn.execute("UPDATE users SET active_scene_id = ? WHERE user_id = ?", (scene_id, int(user_id)))
                conn.commit()
            try:
                neg_state = json.loads(row[6] or "{}")
            except Exception:
                neg_state = {}
            return {
                "user_id": row[0],
                "username": row[1] or username,
                "first_name": row[2] or first_name or "Member",
                "whitelisted": row[3],
                "current_mode": row[4] or "spontaneous_chat",
                "active_scene_id": scene_id,
                "negotiation_state": neg_state,
            }

    def start_new_scene(self, user_id: int, mode: Optional[str] = None) -> str:
        new_scene = f"scene_{uuid.uuid4().hex[:8]}"
        with self._conn() as conn:
            if mode:
                conn.execute(
                    "UPDATE users SET active_scene_id = ?, current_mode = ?, negotiation_state_json = '{}', updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (new_scene, mode, int(user_id)),
                )
            else:
                conn.execute(
                    "UPDATE users SET active_scene_id = ?, negotiation_state_json = '{}', updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                    (new_scene, int(user_id)),
                )
            conn.commit()
        return new_scene

    def update_user(self, user_id: int, **kwargs):
        if not kwargs:
            return
        with self._conn() as conn:
            for k, v in kwargs.items():
                if k == "negotiation_state":
                    conn.execute(
                        "UPDATE users SET negotiation_state_json = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                        (json.dumps(v, ensure_ascii=False), int(user_id)),
                    )
                elif k in ("current_mode", "active_scene_id", "whitelisted", "username", "first_name"):
                    conn.execute(f"UPDATE users SET {k} = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ?", (v, int(user_id)))
            conn.commit()

    def is_whitelisted(self, user_id: int) -> bool:
        if int(user_id) == ADMIN_ID or ADMIN_ID == 0:
            return True
        u = self.get_user(user_id)
        return bool(u.get("whitelisted", 1))

    def append_turn(self, user_id: int, scene_id: str, role: str, speaker: str, content: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO chat_history (user_id, scene_id, role, speaker, content) VALUES (?, ?, ?, ?, ?)",
                (int(user_id), str(scene_id), role, speaker, content),
            )
            conn.commit()

    def get_scene_history(self, user_id: int, scene_id: str, limit: int = 6) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT speaker, content FROM chat_history WHERE user_id = ? AND scene_id = ? ORDER BY id DESC LIMIT ?",
                (int(user_id), str(scene_id), int(limit)),
            ).fetchall()
        rows.reverse()
        return [f"{r[0]}: {r[1]}" for r in rows]

    def record_evidence(
        self,
        user_id: int,
        scene_id: str,
        event_type: str,
        functional_category: str,
        target_key: str,
        provenance: str,
        attempt_number: int,
        outcome: str,
        evidence_stage: str,
        learner_text: str,
        parent_event_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        canon_key = canonicalize_target_key(target_key)
        with self._conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM v3_evidence_ledger
                WHERE user_id = ? AND scene_id = ? AND event_type = ?
                  AND canonical_target_key = ? AND attempt_number = ?
                LIMIT 1
                """,
                (int(user_id), str(scene_id), str(event_type), canon_key, int(attempt_number)),
            ).fetchone()
            if existing:
                return {"inserted": False, "row_id": int(existing[0]), "canonical_key": canon_key}

            cur = conn.execute(
                """
                INSERT INTO v3_evidence_ledger (
                    user_id, scene_id, parent_event_id, event_type, functional_category,
                    target_key, canonical_target_key, provenance, attempt_number,
                    outcome, evidence_stage, learner_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(user_id),
                    str(scene_id),
                    int(parent_event_id) if parent_event_id is not None else None,
                    str(event_type),
                    str(functional_category),
                    str(target_key),
                    canon_key,
                    str(provenance),
                    int(attempt_number),
                    str(outcome),
                    str(evidence_stage),
                    str(learner_text),
                ),
            )
            conn.commit()
            return {"inserted": True, "row_id": int(cur.lastrowid), "canonical_key": canon_key}

    def detect_uptake_from_exposed_targets(self, user_id: int, scene_id: str, learner_text: str) -> Optional[Dict[str, Any]]:
        tl = (learner_text or "").strip().lower()
        if not tl:
            return None
        with self._conn() as conn:
            exposed_rows = conn.execute(
                """
                SELECT id, target_key, functional_category
                FROM v3_evidence_ledger
                WHERE user_id = ? AND event_type = 'exposure'
                ORDER BY id DESC LIMIT 40
                """,
                (int(user_id),),
            ).fetchall()

        for exp_id, t_key, func_cat in exposed_rows:
            stem = re.sub(r"\[[^\]]+\]", "", t_key).strip().lower()
            stem = re.sub(r"\s+", " ", stem)
            if len(stem) >= 5 and stem in tl:
                prov = f"Prior Coaching Exposure Verified in SQLite (exposure_event_id=#{exp_id})"
                stage = "Contextual Production (verified retrieval of exposed target; Stabilization requires delayed transfer)"
                res_db = self.record_evidence(
                    user_id=user_id,
                    scene_id=scene_id,
                    event_type="lexical_production",
                    functional_category=func_cat or "Spoken American Move",
                    target_key=t_key,
                    provenance=prov,
                    attempt_number=1,
                    outcome="success",
                    evidence_stage=stage,
                    learner_text=learner_text,
                    parent_event_id=int(exp_id),
                )
                return {
                    "function": func_cat or "Spoken American Move",
                    "target_move": t_key,
                    "provenance": prov,
                    "stage": stage,
                    "db_audit": (
                        f"SQLite Persisted (row_id=#{res_db['row_id']}, parent_exposure_id=#{exp_id})"
                        if res_db["inserted"]
                        else f"SQLite Deduplicated (existing row_id=#{res_db['row_id']})"
                    ),
                }
        return None

    def get_recent_ledger_rows(self, user_id: int, limit: int = 8) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT id, scene_id, parent_event_id, event_type, target_key, provenance, attempt_number, outcome
                FROM v3_evidence_ledger
                WHERE user_id = ?
                ORDER BY id DESC LIMIT ?
                """,
                (int(user_id), int(limit)),
            ).fetchall()
        return [
            {
                "id": r[0],
                "scene_id": r[1],
                "parent_id": r[2],
                "type": r[3],
                "target": r[4],
                "provenance": r[5],
                "attempt": r[6],
                "outcome": r[7],
            }
            for r in rows
        ]
