"""flows.py (V3.0, owner Claude): the learner-facing flows that sit on top of ``interaction.py``.

Everything here is Telegram-agnostic: a flow takes a learner id, some text or a parsed callback, writes the
events and interaction rows it needs, and returns plain ``View`` / ``CallbackResult`` objects (HTML text plus
button specs). ``app.py`` only renders them. That keeps every state change of S0 5.4 testable without a bot.

Flows: the 💡 card (``ug``), reconstruction (``rc``, ``rs``, ``bk``), Review & Retrieval (``rvn``, ``rvh``,
``rvs``), the merged scene debrief, the ``/progress`` 5D profile, and the listen text (``ls``).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

import feedback_queue
import interaction
import podcast
import prosody
import replay
import sparring
import retrieval
from database import EventSourcedDatabase
from interaction import ActiveInteraction, ParsedCallback, TextRoute
from schemas import (
    FeedbackCardDeliveredV2,
    InteractionObject,
    InteractionState,
    PromptKind,
    ReconstructionAttemptedV2,
    RetrievalOrigin,
    RetrievalOutcome,
)

NO_ARABIC = "english_only"
REVIEW_PAYLOAD_ITEMS = 5


# ------------------------------------------------------------------------------ view models


@dataclass(frozen=True)
class Button:
    label: str
    data: str


@dataclass(frozen=True)
class View:
    text: str  # Telegram HTML
    buttons: tuple[tuple[Button, ...], ...] = ()


@dataclass(frozen=True)
class TurnSubmission:
    """Text the app must now run as a scene turn (``rs``: the reconstruction goes to Alex)."""

    text: str
    origin: str
    interaction_id: Optional[int] = None


@dataclass
class CallbackResult:
    toast: Optional[str] = None
    edit: Optional[View] = None  # replaces the message the button sits on
    send: list[View] = field(default_factory=list)  # new messages
    submit_turn: Optional[TurnSubmission] = None
    tts_text: Optional[str] = None  # ``ls``: speak this


@dataclass
class TextResult:
    scene_turn: bool = False
    views: list[View] = field(default_factory=list)


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def script_hash(text: str) -> str:
    """Key of ``audio_cache``: the same spoken line always maps to the same Telegram file."""
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().lower().encode("utf-8")).hexdigest()[:32]


# ------------------------------------------------------------------------------ the card


def render_card_html(reply_text: str, diag: dict[str, Any], *, with_arabic: bool = True) -> str:
    lines = [
        f"💬 <b>Alex:</b> {_e(reply_text)}",
        "━━━━━━━━━━━━━━━━━━━━",
        f"💡 <b>Quick Upgrade</b> (<i>{_e(diag.get('kind', 'usage'))}</i>)",
        f"• <b>You said:</b> “<s>{_e(diag.get('original_span', ''))}</s>”",
    ]
    if diag.get("alternative"):
        lines.append(f"• <b>More natural:</b> “<b>{_e(diag['alternative'])}</b>”")
    for alt in diag.get("secondary_alternatives") or []:
        lines.append(
            f"• <b>Also natural ({_e(alt.get('register_profile', 'casual'))}):</b> "
            f"“<b>{_e(alt.get('phrase', ''))}</b>” — <i>{_e(alt.get('pragmatic_effect', ''))}</i>"
        )
    if diag.get("listener_effect"):
        lines.append(f"• <b>How it sounds:</b> <i>{_e(diag['listener_effect'])}</i>")
    lines.append(f"• <b>Why:</b> {_e(diag.get('explanation_en', ''))}")
    if with_arabic and (diag.get("l1_source_ar") or diag.get("l1_pattern")):
        src_ar = f"«{_e(diag['l1_source_ar'])}» " if diag.get("l1_source_ar") else ""
        pat_code = f"(<code>{_e(diag['l1_pattern'])}</code>)" if diag.get("l1_pattern") else ""
        lines.append(f"• <b>L1 Transfer:</b> {src_ar}{pat_code}")
    if with_arabic and diag.get("explanation_ar"):
        lines.append(f"• <b>بالعربية:</b> <tg-spoiler>{_e(diag['explanation_ar'])}</tg-spoiler>")
    return "\n".join(lines)


def plain_card_text(diag: dict[str, Any]) -> str:
    """What the learner was shown, as text: the echo guard compares an attempt against this."""
    parts = [diag.get("alternative"), diag.get("explanation_en"), diag.get("recast_candidate")]
    parts += [a.get("phrase") for a in diag.get("secondary_alternatives") or []]
    return " ".join(str(p) for p in parts if p)


_OUTCOME_LINES = {
    RetrievalOutcome.SUCCESS.value: "✅ <b>Nice, that's it.</b>",
    RetrievalOutcome.HINTED_SUCCESS.value: "✅ <b>Got it, with a little help.</b>",
    RetrievalOutcome.PARTIAL.value: "🟡 <b>Close.</b>",
    RetrievalOutcome.FAILURE.value: "🔁 <b>Not this time.</b>",
}


class V3Flows:
    def __init__(
        self,
        db: EventSourcedDatabase,
        ai: Any = None,
        *,
        judge: Optional[retrieval.Judge] = None,
        now_fn: Optional[Callable[[], datetime]] = None,
    ):
        self.db = db
        self.ai = ai
        self._judge = judge
        self._now_fn = now_fn

    # ------------------------------------------------------------------ plumbing

    def now(self) -> datetime:
        return self._now_fn() if self._now_fn else self.db.clock.now()

    @property
    def judge(self) -> Optional[retrieval.Judge]:
        if self._judge is None and self.ai is not None:
            self._judge = self.ai.build_judge()
        return self._judge

    def _session(self, learner_id: int) -> dict[str, Any]:
        return self.db.get_or_create_active_session(learner_id)

    def _with_arabic(self, learner_id: int) -> bool:
        return str(self.db.get_learner_settings(learner_id).get("arabic_support", "bilingual")) != NO_ARABIC

    def close_active(self, learner_id: int) -> bool:
        """Any keyboard mode button, /start or the Session button closes the open row first (S0 5.4)."""
        replay.close_replay_session(learner_id)
        sparring.close_sparring(learner_id)
        return interaction.close_learner_interaction(self.db, learner_id)


    def prosody_hub_view(self, learner_id: int, drill_idx: int = 0) -> View:
        self.close_active(learner_id)
        html_out = prosody.render_prosody_hub_html(drill_idx, with_arabic=self._with_arabic(learner_id))
        d = prosody.PROSODY_SHADOW_DRILLS[drill_idx % len(prosody.PROSODY_SHADOW_DRILLS)]
        buttons = (
            (
                Button("🎧 Hear Native Model", f"pros:listen:{drill_idx % len(prosody.PROSODY_SHADOW_DRILLS)}"),
                Button("➡️ Next Rhythm Drill", f"pros:drill:{(drill_idx + 1) % len(prosody.PROSODY_SHADOW_DRILLS)}"),
            ),
        )
        return View(html_out, buttons, tts_text=d["sentence"])

    def podcast_hub_view(self, learner_id: int) -> View:
        self.close_active(learner_id)
        text = (
            "🎙️ <b>Spoken English Lab — Personal Podcast Studio (V4.0)</b>\n"
            "Turn your real upgrades, L1-Arabic fixes, and due review phrases into a natural 2-host NYC dialogue (<b>Alex &amp; Maya</b>):\n\n"
            "• <b>🔄 Session Recap:</b> Unpack the top shifts from your recent conversation.\n"
            "• <b>☕ Daily Briefing:</b> Weave your due review items into a brisk morning exchange.\n"
            "• <b>🎬 Context Transfer:</b> Hear your target phrases inside a fresh high-stakes scenario."
        )
        buttons = (
            (
                Button("🔄 Session Recap", "pod:mode:recap"),
                Button("☕ Daily Briefing", "pod:mode:briefing"),
            ),
            (
                Button("🎬 Context Transfer", "pod:mode:narrative"),
            ),
        )
        return View(text, buttons)

    def replay_hub_view(self, learner_id: int) -> View:
        self.close_active(learner_id)
        moment = replay.extract_latest_replay_moment(self.db, learner_id)
        buttons = (
            (
                Button("🕊️ Diplomatic Pushback", "rep:angle:diplomatic"),
                Button("🎯 Crisp Executive", "rep:angle:executive"),
            ),
            (
                Button("⚡ Warm & Witty", "rep:angle:witty"),
            ),
        )
        return View(replay.render_replay_hub_html(moment), buttons)

    def replay_attempt_view(self, learner_id: int, text: str, moment: replay.ActiveReplaySession) -> View:
        html_out, ready = replay.evaluate_replay_attempt(
            moment, text, judge=self.judge, with_arabic=self._with_arabic(learner_id)
        )
        rows: list[tuple[Button, ...]] = []
        if ready and moment.last_attempt_text:
            rows.append((Button("📨 Send to Alex", "rep:send"),))
        rows.append(
            (
                Button("🕊️ Diplomatic", "rep:angle:diplomatic"),
                Button("🎯 Executive", "rep:angle:executive"),
                Button("⚡ Witty", "rep:angle:witty"),
            )
        )
        return View(html_out, tuple(rows))


    def sparring_hub_view(self, learner_id: int, topic_key: str = "ai_decisions") -> View:
        self.close_active(learner_id)
        sess = sparring.start_sparring(learner_id, topic_key=topic_key)
        buttons = (
            (
                Button("🏛️ AI vs. Discretion", "spar:topic:ai_decisions"),
                Button("⚡ Autonomy vs. Sync", "spar:topic:remote_autonomy"),
            ),
            (
                Button("🔥 Radical Candor", "spar:topic:candor_culture"),
                Button("🚪 Exit to 1-on-1 Alex", "spar:exit"),
            ),
        )
        return View(sparring.render_sparring_opening_html(sess), buttons)

    def sparring_turn_view(self, learner_id: int, text: str, sess: sparring.SparringSession) -> View:
        exch = sparring.run_sparring_turn(self.ai, sess, text)
        html_out = sparring.render_sparring_exchange_html(exch, with_arabic=self._with_arabic(learner_id))
        buttons = (
            (
                Button("🔄 Switch Topic", "spar:topic:remote_autonomy"),
                Button("🚪 Exit Sparring", "spar:exit"),
            ),
        )
        return View(html_out, buttons)

    def handle_v4_callback(self, learner_id: int, data: str) -> CallbackResult:
        if data.startswith("pros:drill:"):
            idx = int(data.split(":", 2)[2]) if data.split(":", 2)[2].isdigit() else 0
            return CallbackResult(toast=f"Drill #{idx + 1}", edit=self.prosody_hub_view(learner_id, idx))

        if data.startswith("pros:listen:"):
            idx = int(data.split(":", 2)[2]) if data.split(":", 2)[2].isdigit() else 0
            d = prosody.PROSODY_SHADOW_DRILLS[idx % len(prosody.PROSODY_SHADOW_DRILLS)]
            return CallbackResult(toast="Playing native model...", tts_text=d["sentence"])

        if data.startswith("spar:topic:"):
            t_key = data.split(":", 2)[2]
            sess = sparring.start_sparring(learner_id, topic_key=t_key)
            buttons = (
                (
                    Button("🏛️ AI vs. Discretion", "spar:topic:ai_decisions"),
                    Button("⚡ Autonomy vs. Sync", "spar:topic:remote_autonomy"),
                ),
                (
                    Button("🔥 Radical Candor", "spar:topic:candor_culture"),
                    Button("🚪 Exit to 1-on-1 Alex", "spar:exit"),
                ),
            )
            return CallbackResult(toast="Topic switched!", edit=View(sparring.render_sparring_opening_html(sess), buttons))

        if data == "spar:exit":
            sparring.close_sparring(learner_id)
            return CallbackResult(
                toast="Back to 1-on-1 Scene",
                edit=View("💬 <b>Back to 1-on-1 Open Scene with Alex.</b> Send any message or voice note to continue."),
            )

        if data.startswith("rep:angle:"):
            angle_raw = data.split(":", 2)[2]
            angle: replay.ReplayAngle = (
                angle_raw if angle_raw in ("diplomatic", "executive", "witty") else "diplomatic"
            )
            moment = replay.get_replay_session(learner_id) or replay.extract_latest_replay_moment(self.db, learner_id)
            moment.selected_angle = angle
            opt = replay.build_replay_angle_option(self.ai, moment, angle)
            html_out = replay.render_replay_angle_html(moment, opt, with_arabic=self._with_arabic(learner_id))
            buttons = (
                (
                    Button("🕊️ Diplomatic", "rep:angle:diplomatic"),
                    Button("🎯 Executive", "rep:angle:executive"),
                    Button("⚡ Witty", "rep:angle:witty"),
                ),
            )
            return CallbackResult(toast=f"Angle: {angle.title()}", edit=View(html_out, buttons))

        if data == "rep:send":
            moment = replay.get_replay_session(learner_id)
            if moment is None or not moment.last_attempt_text:
                return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
            attempt = moment.last_attempt_text
            replay.close_replay_session(learner_id)
            return CallbackResult(
                toast="Sent to Alex!",
                submit_turn=TurnSubmission(text=attempt, origin="scene", interaction_id=None),
            )

        return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)

    # ------------------------------------------------------------------ text routing

    def handle_text(self, learner_id: int, text: str) -> TextResult:
        route, row = interaction.route_text(self.db, learner_id, self.now())
        if route == TextRoute.RECONSTRUCTION_ATTEMPT and row is not None:
            return TextResult(views=[self.reconstruction_attempt(learner_id, text, row)])
        if route == TextRoute.REVIEW_ATTEMPT and row is not None:
            return TextResult(views=[self.review_attempt(learner_id, text, row)])
        spar_sess = sparring.get_sparring(learner_id)
        if spar_sess is not None:
            return TextResult(views=[self.sparring_turn_view(learner_id, text, spar_sess)])
        rep_sess = replay.get_replay_session(learner_id)
        if rep_sess is not None and rep_sess.selected_angle is not None:
            return TextResult(views=[self.replay_attempt_view(learner_id, text, rep_sess)])
        # SCENE_TURN, and DECODE_ANSWER / WORKOUT_ATTEMPT (their sessions still live in the legacy registries, so the app handles them first)
        return TextResult(scene_turn=True)

    # ------------------------------------------------------------------ callbacks

    def handle_callback(self, learner_id: int, data: str) -> CallbackResult:
        """Every V3 button. A stale, malformed or unknown one answers 'That option expired.' and changes nothing."""
        parsed = interaction.parse_callback(data)
        if parsed is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        handlers = {
            "ug": self.open_upgrade,
            "rc": self.start_reconstruction,
            "rs": self.send_reconstruction,
            "bk": self.back,
            "rvn": self.review_next,
            "rvh": self.review_hint,
            "rvs": self.review_skip,
            "ls": self.listen,
        }
        try:
            return handlers[parsed.op](learner_id, parsed)
        except (LookupError, ValueError, KeyError):
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)

    def _live(self, learner_id: int, parsed: ParsedCallback, *states: InteractionState) -> Optional[ActiveInteraction]:
        row = interaction.get_active_interaction(self.db, learner_id, self.now())
        if row is None or not interaction.callback_matches(row, parsed):
            return None
        if states and row.state not in states:
            return None
        return row

    # ------------------------------------------------------------------ 💡 card (ug)

    def _card_for_reply(self, reply_event_seq: int) -> Optional[dict[str, Any]]:
        with self.db.read() as conn:
            reply = conn.execute(
                "SELECT learner_id, session_id, payload_json FROM event_log "
                "WHERE event_seq = ? AND event_type = 'responder_reply_delivered'",
                (reply_event_seq,),
            ).fetchone()
            if reply is None:
                return None
            payload = json.loads(reply["payload_json"])
            turn_id = payload.get("turn_event_id")
            if not turn_id:
                return None
            policy = conn.execute(
                "SELECT payload_json FROM event_log WHERE event_type = 'policy_decision_recorded' "
                "AND json_extract(payload_json, '$.turn_event_id') = ? ORDER BY event_seq DESC LIMIT 1",
                (turn_id,),
            ).fetchone()
            analysis = conn.execute(
                "SELECT payload_json FROM event_log WHERE event_type = 'analyst_diagnosis_recorded' "
                "AND json_extract(payload_json, '$.turn_event_id') = ? ORDER BY event_seq DESC LIMIT 1",
                (turn_id,),
            ).fetchone()
        if policy is None or analysis is None:
            return None
        selected = json.loads(policy["payload_json"]).get("selected_diagnostic_id")
        if not selected:
            return None
        diag = next((d for d in json.loads(analysis["payload_json"]).get("diagnostics", []) if d.get("diagnostic_id") == selected), None)
        if diag is None:
            return None
        return {
            "learner_id": int(reply["learner_id"]),
            "session_id": str(reply["session_id"]),
            "reply_text": str(payload.get("reply_text", "")),
            "turn_event_id": str(turn_id),
            "diag": diag,
        }

    def open_upgrade(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        now = self.now()
        session = self._session(learner_id)
        session_id = str(session["session_id"])
        if not interaction.ug_is_current(self.db, learner_id=learner_id, session_id=session_id, parsed=parsed, now=now):
            return CallbackResult(toast=interaction.SAVED_TO_REVIEW_TEXT)
        card = self._card_for_reply(parsed.obj_id)
        if card is None or card["learner_id"] != learner_id or card["session_id"] != session_id:
            return CallbackResult(toast=interaction.SAVED_TO_REVIEW_TEXT)
        diag = dict(card["diag"])
        diag_id = str(diag["diagnostic_id"])
        row = interaction.open_interaction(
            self.db,
            learner_id=learner_id,
            session_id=session_id,
            object_type=InteractionObject.UPGRADE,
            object_ref=diag_id,
            state=InteractionState.UPGRADE_VIEW,
            payload={
                "diagnostic_id": diag_id,
                "reply_event_seq": parsed.obj_id,
                "reply_text": card["reply_text"],
                "alternative": diag.get("alternative") or "",
                "original_span": diag.get("original_span") or "",
                "card_text": plain_card_text(diag),
                "intention": str(session.get("scene_mode") or ""),
            },
            scene_revision=self.db.get_scene_revision(session_id),
            now=now,
            ttl_s=interaction.default_ttl_s(InteractionState.UPGRADE_VIEW),
        )
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="feedback_card_delivered",
            payload=FeedbackCardDeliveredV2(diagnostic_id=diag_id, card_id=diag_id, trigger="tap"),
            correlation_id=f"tap_{diag_id}",
            causation_id=card["turn_event_id"],
            idempotency_key=f"tap:{diag_id}",
        )
        self.register_card_exposure(learner_id, session_id, diag, session)
        view = View(
            render_card_html(card["reply_text"], diag, with_arabic=self._with_arabic(learner_id)),
            (
                (
                    Button("✍️ Say it again", interaction.build_callback("rc", row.interaction_id, row.revision)),
                    Button("↩️ Back", interaction.build_callback("bk", row.interaction_id, row.revision)),
                ),
                (Button("🎧 Listen", interaction.build_callback("ls", parsed.obj_id, 0)),),
            ),
        )
        return CallbackResult(toast="Upgrade opened", edit=view)

    def mark_card_offered(self, learner_id: int, session_id: str, diagnostic_id: str, turn_event_id: str, telegram_message_id: Optional[int] = None) -> None:
        """The 💡 button is now visible on the reply: that is the end-of-turn card for pacing (one visible card)."""
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="feedback_card_delivered",
            payload=FeedbackCardDeliveredV2(
                diagnostic_id=diagnostic_id, card_id=diagnostic_id, trigger="end_of_turn", telegram_message_id=telegram_message_id
            ),
            correlation_id=f"eot_{diagnostic_id}",
            causation_id=turn_event_id,
            idempotency_key=f"eot:{diagnostic_id}",
        )

    def register_card_exposure(self, learner_id: int, session_id: str, diag: dict[str, Any], session: dict[str, Any]) -> Optional[str]:
        """A phrase-sized alternative the learner has just been shown becomes an organic catalog phrase plus an exposure.

        Called when a card is delivered (end of turn) or opened (``ug`` tap). Python decides: only a high-confidence
        error or upgrade with a short local repair qualifies; a whole-utterance rewrite or a mere alternative never does."""
        alt = str(diag.get("alternative") or "").strip()
        if not alt or diag.get("confidence_band") != "high":
            return None
        if str(diag.get("classification")) not in ("error", "upgrade"):
            return None
        if str(diag.get("repair_scope")) == "utterance_rewrite" or len(alt.split()) > 8:
            return None
        phrase_id = self.db.register_organic_phrase_from_diagnostic(
            normalized_pattern_id=str(diag.get("normalized_pattern_id", "")),
            canonical_form=alt,
            communicative_intention=str(diag.get("intended_meaning") or diag.get("normalized_pattern_id") or ""),
            register_profile=str(session.get("register_profile") or "casual"),
        )
        self.db.append_event(
            learner_id=learner_id,
            session_id=session_id,
            event_type="phrase_exposure_recorded",
            payload={"phrase_id": phrase_id, "revealed_to_learner": True, "directly_cued": False},
            correlation_id=f"card_{diag['diagnostic_id']}",
            idempotency_key=f"card:{diag['diagnostic_id']}:exposure:{phrase_id}",
        )
        return phrase_id

    # ------------------------------------------------------------------ reconstruction (rc, rs, bk)

    def start_reconstruction(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        upgrade = self._live(learner_id, parsed, InteractionState.UPGRADE_VIEW)
        if upgrade is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        payload = dict(upgrade.payload)
        row = interaction.open_interaction(
            self.db,
            learner_id=learner_id,
            session_id=upgrade.session_id,
            object_type=InteractionObject.RECONSTRUCTION,
            object_ref=upgrade.object_ref,
            state=InteractionState.RECONSTRUCTION_ACTIVE,
            payload=payload,
            scene_revision=upgrade.scene_revision_at_open,
            now=self.now(),
            ttl_s=interaction.default_ttl_s(InteractionState.RECONSTRUCTION_ACTIVE),
        )
        text = (
            "✍️ <b>Say it again</b>\n"
            f"You said: “<s>{_e(payload.get('original_span'))}</s>”\n"
            "Send the sentence again with the more natural wording, in your own words. Alex won't see it unless you choose."
        )
        back = Button("↩️ Back", interaction.build_callback("bk", row.interaction_id, row.revision))
        return CallbackResult(toast="Your turn", edit=View(text, ((back,),)))

    def reconstruction_attempt(self, learner_id: int, text: str, row: ActiveInteraction) -> View:
        payload = dict(row.payload)
        alternative = str(payload.get("alternative") or "")
        result = retrieval.grade_reconstruction(
            text,
            alternative,
            shown_texts=[str(payload.get("card_text") or ""), str(payload.get("original_span") or "")],
            judge=self.judge,
            communicative_intention=str(payload.get("intention") or ""),
        )
        back = Button("↩️ Back", interaction.build_callback("bk", row.interaction_id, row.revision))
        if result.shed:
            return View(retrieval.SHED_MESSAGE, ((back,),))
        if result.echo:
            return View("That is the card's own wording. Try it once more in your own sentence.", ((back,),))
        outcome = result.outcome.value if result.outcome else "failure"
        if outcome not in ("success", "partial", "failure"):
            outcome = "failure"
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=row.session_id,
            event_type="reconstruction_attempted",
            payload=ReconstructionAttemptedV2(
                interaction_id=row.interaction_id,
                diagnostic_id=str(payload.get("diagnostic_id")),
                attempt_text=text,
                linguistic_level=result.linguistic_level or 0,
                functional_level=result.functional_level or 0,
                outcome=outcome,
                functional_equivalent_credited=result.functional_equivalent_credited,
                matched_by=result.matched_by,
            ),
            correlation_id=f"rc_{row.interaction_id}",
            idempotency_key=f"rc:{row.interaction_id}:{row.revision}",
        )
        moved = interaction.transition(
            self.db,
            interaction_id=row.interaction_id,
            expected_revision=row.revision,
            new_state=InteractionState.RECONSTRUCTION_FEEDBACK,
            payload_patch={"attempt_text": text, "outcome": outcome},
            now=self.now(),
            ttl_s=interaction.default_ttl_s(InteractionState.RECONSTRUCTION_FEEDBACK),
        )
        if moved is None:
            return View(interaction.STALE_CALLBACK_TEXT)
        verdict = {
            "success": "✅ <b>That works.</b>",
            "partial": "🟡 <b>Close.</b>",
            "failure": "🔁 <b>Not quite yet.</b>",
        }[outcome]
        lines = [verdict, f"More natural: “<b>{_e(alternative)}</b>”"]
        if result.functional_equivalent_credited:
            lines.insert(1, "Your own wording does the same job.")
        buttons = (
            Button("📨 Send to Alex", interaction.build_callback("rs", moved.interaction_id, moved.revision)),
            Button("↩️ Back", interaction.build_callback("bk", moved.interaction_id, moved.revision)),
        )
        return View("\n".join(lines), (buttons,))

    def send_reconstruction(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        row = self._live(learner_id, parsed, InteractionState.RECONSTRUCTION_FEEDBACK)
        if row is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        attempt = str(row.payload.get("attempt_text") or "").strip()
        if not attempt:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        interaction.close_interaction(self.db, interaction_id=row.interaction_id, expected_revision=row.revision)
        return CallbackResult(
            toast="Sent to Alex",
            submit_turn=TurnSubmission(text=attempt, origin="reconstruction", interaction_id=row.interaction_id),
        )

    def back(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        row = self._live(learner_id, parsed)
        if row is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        interaction.close_interaction(self.db, interaction_id=row.interaction_id, expected_revision=row.revision)
        payload = row.payload
        if payload.get("reply_text") and payload.get("reply_event_seq"):
            listen = Button("🎧 Listen", interaction.build_callback("ls", int(payload["reply_event_seq"]), 0))
            return CallbackResult(toast="Back to the scene", edit=View(f"💬 <b>Alex:</b> {_e(payload['reply_text'])}", ((listen,),)))
        return CallbackResult(toast="Back to the scene")

    # ------------------------------------------------------------------ listen (ls)

    def listen(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        """Stateless: speak the reply the button belongs to."""
        with self.db.read() as conn:
            row = conn.execute(
                "SELECT learner_id, payload_json FROM event_log WHERE event_seq = ? AND event_type = 'responder_reply_delivered'",
                (parsed.obj_id,),
            ).fetchone()
        if row is None or int(row["learner_id"]) != learner_id:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        payload = json.loads(row["payload_json"])
        spoken = " ".join(p for p in (payload.get("reply_text"), payload.get("second_bubble_text")) if p)
        return CallbackResult(toast="🎧 Alex is warming up…", tts_text=spoken)

    # ------------------------------------------------------------------ Review & Retrieval

    def start_review(self, learner_id: int) -> list[View]:
        now = self.now()
        self.close_active(learner_id)
        session = self._session(learner_id)
        session_id = str(session["session_id"])
        views: list[View] = []
        queue = self.db.get_review_queue(learner_id, now)
        if queue:
            views.append(self._queue_view(learner_id, session_id, queue))
        plans = self.db.get_due_phrases(learner_id, now)[:REVIEW_PAYLOAD_ITEMS]
        if not plans:
            views.append(View("🔁 <b>Review &amp; Retrieval</b>\nNothing is due for phrase review right now. New items appear as you use them in scenes."))
            return views
        items = [
            {"phrase_id": p.phrase_id, "step": p.step, "family": p.family, "prompt_kind": PromptKind(p.prompt_kind).value}
            for p in plans
        ]
        row = interaction.open_interaction(
            self.db,
            learner_id=learner_id,
            session_id=session_id,
            object_type=InteractionObject.REVIEW_ITEM,
            object_ref=items[0]["phrase_id"],
            state=InteractionState.REVIEW_ACTIVE,
            payload={"items": items, "index": 0, "hint_level": 0, "hints": [], "answered": False},
            scene_revision=self.db.get_scene_revision(session_id),
            now=now,
            ttl_s=interaction.default_ttl_s(InteractionState.REVIEW_ACTIVE),
        )
        views.append(self._review_view(row))
        return views

    def _queue_view(self, learner_id: int, session_id: str, entries: list[feedback_queue.QueueEntry]) -> View:
        lines = ["📌 <b>Worth another look</b>"]
        for number, entry in enumerate(entries, start=1):
            item = entry.representative
            line = f"{number}. <i>{_e(item.original_span)}</i> → <b>{_e(item.alternative)}</b>"
            if entry.seen_count > 1:
                line += f" (seen {entry.seen_count} times)"
            lines.append(line)
            self._deliver_queue_item(learner_id, session_id, item.diagnostic_id, "review")
        return View("\n".join(lines))

    def _deliver_queue_item(self, learner_id: int, session_id: str, diagnostic_id: str, trigger: str) -> None:
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=session_id,
            event_type="feedback_card_delivered",
            payload=FeedbackCardDeliveredV2(diagnostic_id=diagnostic_id, card_id=diagnostic_id, trigger=trigger),
            correlation_id=f"{trigger}_{diagnostic_id}",
            idempotency_key=f"{trigger}:{diagnostic_id}:{session_id}",
        )

    def _current_item(self, row: ActiveInteraction) -> dict[str, Any]:
        items = row.payload["items"]
        return items[int(row.payload["index"])]

    def _review_prompt(self, item: dict[str, Any]) -> str:
        entry = self.db.get_phrase_entry(item["phrase_id"])
        if entry is None:
            raise LookupError(item["phrase_id"])
        return retrieval.build_prompt(entry, item["prompt_kind"], item.get("family", "general"))

    def _review_view(self, row: ActiveInteraction) -> View:
        payload = row.payload
        item = self._current_item(row)
        number, total = int(payload["index"]) + 1, len(payload["items"])
        lines = [f"🔁 <b>Review {number}/{total}</b>", _e(self._review_prompt(item))]
        for hint in payload.get("hints") or []:
            lines.append(f"💡 <i>{_e(hint)}</i>")
        buttons: list[Button] = []
        if int(payload.get("hint_level", 0)) < 2:
            buttons.append(Button("💡 Hint", interaction.build_callback("rvh", row.interaction_id, row.revision)))
        buttons.append(Button("⏭ Skip", interaction.build_callback("rvs", row.interaction_id, row.revision)))
        return View("\n".join(lines), (tuple(buttons),))

    def review_attempt(self, learner_id: int, text: str, row: ActiveInteraction) -> View:
        payload = dict(row.payload)
        if payload.get("answered"):
            nxt = Button("➡️ Next", interaction.build_callback("rvn", row.interaction_id, row.revision))
            return View("Tap Next for the following item.", ((nxt,),))
        item = self._current_item(row)
        entry = self.db.get_phrase_entry(item["phrase_id"])
        if entry is None:
            return View(interaction.STALE_CALLBACK_TEXT)
        hint_level = int(payload.get("hint_level", 0))
        result = retrieval.grade_attempt(
            text,
            entry.target(),
            shown_texts=[self._review_prompt(item), *[str(h) for h in payload.get("hints") or []]],
            hint_level=hint_level,
            judge=self.judge,
            prompt_kind=item["prompt_kind"],
            context_family=item.get("family", "general"),
            communicative_intention=entry.communicative_intention,
        )
        if result.shed:
            return View(retrieval.SHED_MESSAGE)
        if result.echo:
            return View("That repeats what was on screen. Say it in your own words.")
        event = retrieval.to_attempt_event(
            result,
            phrase_id=entry.phrase_id,
            origin=RetrievalOrigin.REVIEW,
            prompt_kind=PromptKind(item["prompt_kind"]),
            context_family=item.get("family", "general"),
            due=True,
            schedule_step_before=int(item.get("step", 0)),
            interaction_id=row.interaction_id,
        )
        self.db.append_event_ex(
            learner_id=learner_id,
            session_id=row.session_id,
            event_type="retrieval_attempted",
            payload=event,
            correlation_id=f"rv_{row.interaction_id}",
            idempotency_key=f"rv:{row.interaction_id}:{payload['index']}",
        )
        moved = interaction.transition(
            self.db,
            interaction_id=row.interaction_id,
            expected_revision=row.revision,
            new_state=InteractionState.REVIEW_ACTIVE,
            payload_patch={"answered": True, "outcome": result.outcome.value if result.outcome else None},
            now=self.now(),
            ttl_s=interaction.default_ttl_s(InteractionState.REVIEW_ACTIVE),
        )
        if moved is None:
            return View(interaction.STALE_CALLBACK_TEXT)
        lines = [_OUTCOME_LINES.get(result.outcome.value if result.outcome else "failure", "")]
        if result.functional_equivalent_credited:
            lines.append("Your wording does the same job.")
        lines.append(f"Target: <b>{_e(entry.canonical_form)}</b>")
        if entry.arabic_gloss:
            lines.append(f"<tg-spoiler>{_e(entry.arabic_gloss)}</tg-spoiler>")
        last = int(payload["index"]) + 1 >= len(payload["items"])
        nxt = Button("🏁 Finish" if last else "➡️ Next", interaction.build_callback("rvn", moved.interaction_id, moved.revision))
        return View("\n".join(lines), ((nxt,),))

    def _advance(self, learner_id: int, row: ActiveInteraction) -> CallbackResult:
        index = int(row.payload["index"]) + 1
        items = row.payload["items"]
        if index >= len(items):
            interaction.close_interaction(self.db, interaction_id=row.interaction_id, expected_revision=row.revision)
            return CallbackResult(toast="Review finished", edit=View(f"🏁 <b>Review done</b>\n{len(items)} items. Due items come back on their schedule."))
        moved = interaction.transition(
            self.db,
            interaction_id=row.interaction_id,
            expected_revision=row.revision,
            new_state=InteractionState.REVIEW_ACTIVE,
            payload_patch={"index": index, "hint_level": 0, "hints": [], "answered": False},
            now=self.now(),
            ttl_s=interaction.default_ttl_s(InteractionState.REVIEW_ACTIVE),
        )
        if moved is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        return CallbackResult(edit=self._review_view(moved))

    def review_next(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        row = self._live(learner_id, parsed, InteractionState.REVIEW_ACTIVE)
        if row is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        return self._advance(learner_id, row)

    def review_skip(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        """A skip writes no evidence: absence is not failure."""
        return self.review_next(learner_id, parsed)

    def review_hint(self, learner_id: int, parsed: ParsedCallback) -> CallbackResult:
        row = self._live(learner_id, parsed, InteractionState.REVIEW_ACTIVE)
        if row is None or row.payload.get("answered") or int(row.payload.get("hint_level", 0)) >= 2:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        level = int(row.payload.get("hint_level", 0)) + 1
        entry = self.db.get_phrase_entry(self._current_item(row)["phrase_id"])
        hint = retrieval.build_hint(entry, level) if entry else None
        if not hint:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        moved = interaction.transition(
            self.db,
            interaction_id=row.interaction_id,
            expected_revision=row.revision,
            new_state=InteractionState.REVIEW_ACTIVE,
            payload_patch={"hint_level": level, "hints": [*(row.payload.get("hints") or []), hint]},
            now=self.now(),
            ttl_s=interaction.default_ttl_s(InteractionState.REVIEW_ACTIVE),
        )
        if moved is None:
            return CallbackResult(toast=interaction.STALE_CALLBACK_TEXT)
        return CallbackResult(toast="Hint", edit=self._review_view(moved))

    # ------------------------------------------------------------------ debrief and progress

    def debrief_view(self, learner_id: int) -> View:
        """S0 5.9: templated, zero LLM calls. The top 3 queue entries are delivered with trigger='debrief'."""
        session = self._session(learner_id)
        session_id = str(session["session_id"])
        entries = self.db.get_review_queue(learner_id, self.now())
        counts = self.db.get_debrief_counts(session_id)
        for entry in entries[: feedback_queue.DEBRIEF_TOP]:
            self._deliver_queue_item(learner_id, session_id, entry.representative.diagnostic_id, "debrief")
        return View(feedback_queue.render_debrief(entries, counts))

    def progress_view(self, learner_id: int) -> View:
        now = self.now()
        states = self.db.get_phrase_states(learner_id, now)
        due = len(self.db.get_due_phrases(learner_id, now, limit=1000))
        stage_counts: dict[str, int] = {}
        for s in states:
            stage_counts[str(s["effective_stage"])] = stage_counts.get(str(s["effective_stage"]), 0) + 1
        lines = ["📊 <b>Your progress</b>", "━━━━━━━━━━━━━━━━━━━━"]
        if not states:
            lines.append("• No tracked phrases yet. Use a few in a scene and they appear here.")
        else:
            lines.append("<b>Stages:</b> " + " · ".join(f"{_e(k.replace('_', ' '))} {v}" for k, v in sorted(stage_counts.items())))
            lines.append(f"<b>Due for review:</b> {due}")
            lines.append("")
            lines.append("<b>Five dimensions</b> (R recognition · P production · I independence · T transfer · A appropriateness)")
            ranked = sorted(states, key=lambda s: (s.get("mastery_index") is None, -(s.get("mastery_index") or 0.0), str(s["phrase_id"])))
            for s in ranked[:8]:
                entry = self.db.get_phrase_entry(str(s["phrase_id"]))
                name = entry.canonical_form if entry else str(s["phrase_id"])
                dims = " ".join(f"{label} {_dim(s.get('dim_' + key))}" for label, key in (("R", "r"), ("P", "p"), ("I", "i"), ("T", "t"), ("A", "a")))
                lines.append(f"• <b>{_e(name)}</b> — {_e(str(s['effective_stage']).replace('_', ' '))}\n  <code>{dims}</code>")
        with self.db.read() as conn:
            patterns = conn.execute(
                "SELECT normalized_pattern_id, issue_kind, distinct_eligible_occurrences, times_feedback_shown "
                "FROM error_pattern_projection WHERE learner_id = ? ORDER BY distinct_eligible_occurrences DESC LIMIT 5",
                (learner_id,),
            ).fetchall()
        if patterns:
            lines.append("\n<b>Recurring patterns:</b>")
            for r in patterns:
                lines.append(
                    f"• <code>{_e(r['normalized_pattern_id'])}</code> ({_e(r['issue_kind'])}): "
                    f"seen {r['distinct_eligible_occurrences']}×, cards shown {r['times_feedback_shown']}"
                )
        return View("\n".join(lines))


def _dim(value: Any) -> str:
    return "–" if value is None else f"{float(value):.0f}"
