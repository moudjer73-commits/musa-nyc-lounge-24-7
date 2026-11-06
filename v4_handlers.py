"""V4.0 modular handlers and pure rendering functions (never imports app)."""
from __future__ import annotations

import html
import logging
from typing import Any

import telebot
from telebot import types

import prompt_assembly
import room_engine
import v4_data as D

log = logging.getLogger("v4")


def _e(val: Any) -> str:
    return html.escape(str(val or ""))


def pick_dilemma(mode: str, index: int = 0) -> dict[str, Any]:
    key = mode if mode in D.DILEMMAS else "casual"
    items = D.DILEMMAS[key]
    return dict(items[index % len(items)])


def pick_scene(room: str, scene_id: str | None = None) -> dict[str, Any]:
    r_clean = room if room in D.SCENE_REGISTRY else "casual"
    if scene_id:
        for r_items in D.SCENE_REGISTRY.values():
            for sc in r_items:
                if sc["id"] == scene_id:
                    return dict(sc)
    return dict(D.SCENE_REGISTRY[r_clean][0])


def score_debrief(room: str, ratings: dict[str, float] | None = None) -> tuple[int, dict[str, int]]:
    r_clean = room if room in D.SCORING_WEIGHTS else "casual"
    weights = D.SCORING_WEIGHTS[r_clean]
    if not ratings:
        return 100, dict(weights)
    earned: dict[str, int] = {}
    for dim, max_pts in weights.items():
        ratio = max(0.0, min(1.0, float(ratings.get(dim, 1.0))))
        earned[dim] = round(max_pts * ratio)
    return sum(earned.values()), earned


def render_scene(scene: dict[str, Any], directness: str = "balanced", flavor: str = "neutral") -> str:
    room = str(scene.get("id_room") or scene.get("room") or "casual")
    for r_key, r_list in D.SCENE_REGISTRY.items():
        if any(s["id"] == scene.get("id") for s in r_list):
            room = r_key
            break
    sp = room_engine.format_alex_speaker_label(room, flavor)
    pivots = " · ".join(f"“{_e(p)}”" for p in scene.get("pivots", ())[:3])
    return (
        f"🎬 <b>{_e(scene['title'])}</b>\n"
        f"• <b>Stance:</b> Room: <b>{_e(room.upper())}</b> | "
        f"Flavor: <b>{_e(flavor.upper())}</b> | Directness: <b>{_e(directness.upper())}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌎 <b>WORLD:</b> {_e(scene['world'])}\n"
        f"🗣️ <b>ALEX:</b> {_e(scene['alex'])}\n"
        f"🎯 <b>YOU'RE JUDGED ON:</b> {_e(scene['judged_on'])}\n"
        f"🔑 <b>Spoken Pivots:</b> <i>{pivots}</i>\n\n"
        f"💬 <b>{_e(sp)}:</b> “{_e(scene['opener'])}”"
    )


def render_dilemma(dilemma: dict[str, Any], directness: str = "balanced") -> str:
    room = str(dilemma.get("room", "casual"))
    flavor = str(dilemma.get("flavor", "neutral"))
    sp = room_engine.format_alex_speaker_label(room, flavor)
    wins = " / ".join(f"“{_e(w)}”" for w in dilemma.get("winning", ()))
    return (
        f"🤝 <b>WORKOUT DILEMMA — {_e(dilemma['title'])}</b>\n"
        f"• <b>Room:</b> <b>{_e(room.upper())}</b> | <b>Flavor:</b> <b>{_e(flavor.upper())}</b> | "
        f"<b>Directness:</b> <b>{_e(directness.upper())}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🎬 <b>SETUP:</b> {_e(dilemma['setup'])}\n"
        f"❌ <b>FAILS:</b> <i>{_e(dilemma['fails'])}</i>\n"
        f"✅ <b>WINNING MOVES:</b> <tg-spoiler>{wins}</tg-spoiler>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 <b>{_e(sp)}:</b> “{_e(dilemma['alex'])}”"
    )


def render_replay(idea: str, versions: dict[str, str]) -> str:
    return (
        "🔁 <b>REPLAY — Same Idea, Different Music</b>\n"
        f"Your idea: <i>“{_e(idea)}”</i>\n\n"
        f"🎧 <b>CASUAL:</b> “{_e(versions.get('casual', ''))}”\n"
        "<i>Music: Short, direct, friend-to-friend.</i>\n\n"
        f"🕊️ <b>DIPLOMATIC:</b> “{_e(versions.get('diplomatic', ''))}”\n"
        "<i>Music: Soft, warm, leaves the door open.</i>\n\n"
        f"💼 <b>EXECUTIVE:</b> “{_e(versions.get('executive', ''))}”\n"
        "<i>Music: Brief, decision-first, no over-explaining.</i>\n\n"
        f"🧠 <b>ANALYTICAL:</b> “{_e(versions.get('analytical', ''))}”\n"
        "<i>Music: Gives the causal reason or trade-off, not just the decision.</i>\n\n"
        "👉 <b>Your turn:</b> Tap a Room below to practice it, or pick another idea:"
    )
# end part 1


def _deps():
    from ai_engine import DualLLMEngine
    from database import EventSourcedDatabase
    from flows import V3Flows
    db_i = EventSourcedDatabase()
    ai_i = DualLLMEngine()
    return db_i, ai_i, V3Flows(db_i, ai_i)


def _cfg(lid: int) -> tuple[str, str, str, str]:
    db_i, _, _ = _deps()
    db_i.upsert_learner(lid)
    sess = db_i.get_or_create_active_session(lid)
    prefs = db_i.get_learner_settings(lid)
    raw_reg = sess.get("register_profile") or prefs.get("default_register") or "casual"
    raw_dir = sess.get("directness") or prefs.get("default_directness") or "balanced"
    room, directness, flavor = prompt_assembly.normalize_3d_config(raw_reg, raw_dir, None)
    return str(sess["session_id"]), room, flavor, directness


def _remember_opener(lid: int, opener_text: str, room: str | None = None) -> None:
    db_i, _, _ = _deps()
    sid, cur_room, _, _ = _cfg(lid)
    target_room = room or cur_room
    try:
        db_i.update_session_settings(
            sid, lid,
            training_mode="open_scene",
            scene_mode="real_conversation",
            register_profile=target_room,
        )
    except Exception:
        log.debug("update_session_settings skipped", exc_info=True)


def _last_learner_line(lid: int) -> str:
    db_i, _, _ = _deps()
    sid, _, _, _ = _cfg(lid)
    try:
        history = db_i.get_recent_dialogue_history(sid, limit=6)
        for item in reversed(history or []):
            txt = str(item.get("user") or item.get("learner_text") or item.get("user_text") or "").strip()
            if txt and not txt.startswith("/"):
                return txt
    except Exception:
        log.debug("get_recent_dialogue_history failed", exc_info=True)
    return ""


def render_pivots(room: str) -> str:
    r_clean = room if room in D.PIVOTS else "casual"
    rows = [f"🔑 <b>ROOM PHYSICS PIVOTS — {_e(r_clean.upper())} (8 Moves)</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for i, item in enumerate(D.PIVOTS[r_clean], 1):
        rows.append(f"<b>{i}. “{_e(item['phrase'])}”</b>\n   <i>{_e(item['move'])}</i>")
    return "\n\n".join(rows)


def render_drifts() -> str:
    rows = ["🔀 <b>CROSS-REGISTER DRIFT GUIDE (8 Patterns)</b>", "━━━━━━━━━━━━━━━━━━━━"]
    for i, d in enumerate(D.DRIFT_EXAMPLES, 1):
        rows.append(
            f"<b>{i}. {_e(d['from_room'].title())} → {_e(d['to_room'].title())}</b>\n"
            f"   • Said: “<s>{_e(d['line'])}</s>”\n"
            f"   • Why: <i>{_e(d['why'])}</i>\n"
            f"   • Upgrade: “<b>{_e(d['upgrade'])}</b>”"
        )
    return "\n\n".join(rows)


def _scene_kb(room: str) -> types.InlineKeyboardMarkup:
    r_clean = room if room in D.SCENE_REGISTRY else "casual"
    mk = types.InlineKeyboardMarkup(row_width=2)
    for sc in D.SCENE_REGISTRY[r_clean]:
        mk.add(types.InlineKeyboardButton(str(sc["title"]), callback_data=f"v4:sc:{sc['id']}"))
    mk.row(
        types.InlineKeyboardButton("🏠 Casual", callback_data="v4:scr:casual"),
        types.InlineKeyboardButton("🤝 Diplomatic", callback_data="v4:scr:diplomatic"),
        types.InlineKeyboardButton("💼 Executive", callback_data="v4:scr:executive"),
        types.InlineKeyboardButton("🧠 Analytical", callback_data="v4:scr:analytical"),
    )
    mk.row(
        types.InlineKeyboardButton(f"🔑 {r_clean.title()} Pivots", callback_data=f"v4:pv:{r_clean}"),
        types.InlineKeyboardButton("🔀 Drift Guide", callback_data="v4:dr:all"),
    )
    return mk


def _dilemma_kb(mode: str, next_idx: int) -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=3)
    mk.row(types.InlineKeyboardButton("🔄 Next Dilemma in Mode", callback_data=f"v4:dl:{mode}:{next_idx}"))
    mk.row(
        types.InlineKeyboardButton("🏠 Casual", callback_data="v4:dl:casual:0"),
        types.InlineKeyboardButton("💛 Warm", callback_data="v4:dl:warm:0"),
        types.InlineKeyboardButton("😏 Witty", callback_data="v4:dl:witty:0"),
    )
    mk.row(
        types.InlineKeyboardButton("🤝 Diplomatic", callback_data="v4:dl:diplomatic:0"),
        types.InlineKeyboardButton("💼 Executive", callback_data="v4:dl:executive:0"),
        types.InlineKeyboardButton("🧠 Analytical", callback_data="v4:dl:analytical:0"),
    )
    return mk


def _replay_kb() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.row(
        types.InlineKeyboardButton("🏠 Try Casual", callback_data="v4:try:casual"),
        types.InlineKeyboardButton("🤝 Try Diplomatic", callback_data="v4:try:diplomatic"),
    )
    mk.row(
        types.InlineKeyboardButton("💼 Try Executive", callback_data="v4:try:executive"),
        types.InlineKeyboardButton("🧠 Try Analytical", callback_data="v4:try:analytical"),
    )
    for item in D.REPLAY_INTENTIONS:
        mk.add(types.InlineKeyboardButton(item["label"], callback_data=f"v4:rp:{item['id']}"))
    return mk
# end part 2

def _pivots_kb() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.row(
        types.InlineKeyboardButton("🏠 Casual (8)", callback_data="v4:pv:casual"),
        types.InlineKeyboardButton("🤝 Diplomatic (8)", callback_data="v4:pv:diplomatic"),
    )
    mk.row(
        types.InlineKeyboardButton("💼 Executive (8)", callback_data="v4:pv:executive"),
        types.InlineKeyboardButton("🧠 Analytical (8)", callback_data="v4:pv:analytical"),
    )
    mk.row(types.InlineKeyboardButton("🔀 8 Drift Examples", callback_data="v4:dr:all"))
    return mk

def on_open_scene(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    sc = pick_scene(room)
    _remember_opener(m.from_user.id, str(sc["opener"]), room=room)
    bot.send_message(m.chat.id, render_scene(sc, directness, flavor), parse_mode="HTML", reply_markup=_scene_kb(room))

def on_dilemma(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    mode = flavor if (room == "casual" and flavor in ("warm", "witty")) else room
    dl = pick_dilemma(mode, 0)
    _remember_opener(m.from_user.id, str(dl["alex"]), room=str(dl["room"]))
    bot.send_message(m.chat.id, render_dilemma(dl, directness), parse_mode="HTML", reply_markup=_dilemma_kb(mode, 1))

def on_replay(bot: telebot.TeleBot, m: types.Message) -> None:
    item = D.REPLAY_INTENTIONS[0]
    idea = _last_learner_line(m.from_user.id) or item["idea"]
    bot.send_message(m.chat.id, render_replay(idea, item), parse_mode="HTML", reply_markup=_replay_kb())

def on_review(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, _, _ = _cfg(m.from_user.id)
    bot.send_message(m.chat.id, render_pivots(room), parse_mode="HTML", reply_markup=_pivots_kb())

def on_debrief(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    total, breakdown = score_debrief(room)
    w_lines = "\n".join(f"• <b>{_e(k)}:</b> {v} pts" for k, v in breakdown.items())
    _, _, flows_i = _deps()
    base_view = flows_i.debrief_view(m.from_user.id)
    html_out = (
        f"🏁 <b>SCENE DEBRIEF — {_e(room.upper())} ({_e(flavor.upper())} · {_e(directness.upper())})</b>\n"
        f"⚖️ <b>100-Point Room Rubric ({total} pts total):</b>\n{w_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n{base_view.html}"
    )
    bot.send_message(m.chat.id, html_out, parse_mode="HTML", reply_markup=_pivots_kb())

def on_progress(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    _, breakdown = score_debrief(room)
    w_str = " · ".join(f"{k}: {v}" for k, v in breakdown.items())
    _, _, flows_i = _deps()
    base_view = flows_i.progress_view(m.from_user.id)
    html_out = (
        f"📊 <b>3D MASTERY DASHBOARD — {_e(room.upper())} / {_e(flavor.upper())} / {_e(directness.upper())}</b>\n"
        f"⚖️ <b>Active Rubric:</b> <i>{_e(w_str)}</i>\n"
        f"🎯 <b>C/D Gate Axes:</b> <code>too_written · too_casual · over_hedged · under_committed · corporate_filler · missing_move</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n{base_view.html}"
    )
    bot.send_message(m.chat.id, html_out, parse_mode="HTML", reply_markup=_pivots_kb())

def route_callback(bot: telebot.TeleBot, c: types.CallbackQuery) -> None:
    lid, chat_id, parts = c.from_user.id, c.message.chat.id, (c.data or "").split(":")
    _, room, flavor, directness = _cfg(lid)
    kind, arg = (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")
    if kind == "sc":
        sc = pick_scene(room, scene_id=arg)
        _remember_opener(lid, str(sc["opener"]), room=room)
        bot.send_message(chat_id, render_scene(sc, directness, flavor), parse_mode="HTML", reply_markup=_scene_kb(room))
    elif kind == "scr":
        sc = pick_scene(arg)
        _remember_opener(lid, str(sc["opener"]), room=arg)
        bot.send_message(chat_id, render_scene(sc, directness, flavor), parse_mode="HTML", reply_markup=_scene_kb(arg))
    elif kind == "pv":
        bot.send_message(chat_id, render_pivots(arg), parse_mode="HTML", reply_markup=_pivots_kb())
    elif kind == "dr":
        bot.send_message(chat_id, render_drifts(), parse_mode="HTML", reply_markup=_pivots_kb())
    elif kind == "dl":
        idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        dl = pick_dilemma(arg, idx)
        _remember_opener(lid, str(dl["alex"]), room=str(dl["room"]))
        bot.send_message(chat_id, render_dilemma(dl, directness), parse_mode="HTML", reply_markup=_dilemma_kb(arg, idx + 1))
    elif kind == "rp":
        item = next((x for x in D.REPLAY_INTENTIONS if x["id"] == arg), D.REPLAY_INTENTIONS[0])
        bot.send_message(chat_id, render_replay(item["idea"], item), parse_mode="HTML", reply_markup=_replay_kb())
    elif kind == "try":
        _remember_opener(lid, f"Switched to {arg}", room=arg)
        sp = room_engine.format_alex_speaker_label(arg, flavor)
        bot.send_message(chat_id, f"🎯 Switched to <b>{_e(arg.upper())}</b> Room! Send your version now to <b>{_e(sp)}</b>.", parse_mode="HTML")
    bot.answer_callback_query(c.id)

BUTTONS = {
    "💬 Open Scene": on_open_scene, "🎭 Replay": on_replay, "🤝 Dilemma": on_dilemma,
    "🔁 Review": on_review, "🏁 Scene Debrief": on_debrief, "📊 My Progress": on_progress,
}
CMD_TO_BUTTON = {
    "scene": "💬 Open Scene", "replay": "🎭 Replay", "dilemma": "🤝 Dilemma",
    "review": "🔁 Review", "debrief": "🏁 Scene Debrief", "progress": "📊 My Progress",
}

def register(bot: telebot.TeleBot | None) -> None:
    if bot is None:
        return
    def _on_btn(m: types.Message) -> None:
        BUTTONS[m.text](bot, m)
    def _on_cmd(m: types.Message) -> None:
        BUTTONS[CMD_TO_BUTTON[m.text.split()[0].lstrip("/").split("@")[0]]](bot, m)
    def _on_cb(c: types.CallbackQuery) -> None:
        route_callback(bot, c)
    bot.message_handler(func=lambda m: getattr(m, "text", None) in BUTTONS)(_on_btn)
    bot.message_handler(commands=list(CMD_TO_BUTTON))(_on_cmd)
    bot.callback_query_handler(func=lambda c: bool(getattr(c, "data", None)) and c.data.startswith("v4:"))(_on_cb)
    try:
        bot.set_my_commands([types.BotCommand(cmd, desc) for cmd, desc in D.BOT_COMMANDS])
    except Exception:
        log.exception("set_my_commands failed")
# end part 3
# END v4_handlers

def _pivots_kb() -> types.InlineKeyboardMarkup:
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.row(
        types.InlineKeyboardButton("🏠 Casual (8)", callback_data="v4:pv:casual"),
        types.InlineKeyboardButton("🤝 Diplomatic (8)", callback_data="v4:pv:diplomatic"),
    )
    mk.row(
        types.InlineKeyboardButton("💼 Executive (8)", callback_data="v4:pv:executive"),
        types.InlineKeyboardButton("🧠 Analytical (8)", callback_data="v4:pv:analytical"),
    )
    mk.row(types.InlineKeyboardButton("🔀 8 Drift Examples", callback_data="v4:dr:all"))
    return mk

def on_open_scene(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    sc = pick_scene(room)
    _remember_opener(m.from_user.id, str(sc["opener"]), room=room)
    bot.send_message(m.chat.id, render_scene(sc, directness, flavor), parse_mode="HTML", reply_markup=_scene_kb(room))

def on_dilemma(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    mode = flavor if (room == "casual" and flavor in ("warm", "witty")) else room
    dl = pick_dilemma(mode, 0)
    _remember_opener(m.from_user.id, str(dl["alex"]), room=str(dl["room"]))
    bot.send_message(m.chat.id, render_dilemma(dl, directness), parse_mode="HTML", reply_markup=_dilemma_kb(mode, 1))

def on_replay(bot: telebot.TeleBot, m: types.Message) -> None:
    item = D.REPLAY_INTENTIONS[0]
    idea = _last_learner_line(m.from_user.id) or item["idea"]
    bot.send_message(m.chat.id, render_replay(idea, item), parse_mode="HTML", reply_markup=_replay_kb())

def on_review(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, _, _ = _cfg(m.from_user.id)
    bot.send_message(m.chat.id, render_pivots(room), parse_mode="HTML", reply_markup=_pivots_kb())

def on_debrief(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    total, breakdown = score_debrief(room)
    w_lines = "\n".join(f"• <b>{_e(k)}:</b> {v} pts" for k, v in breakdown.items())
    _, _, flows_i = _deps()
    base_view = flows_i.debrief_view(m.from_user.id)
    html_out = (
        f"🏁 <b>SCENE DEBRIEF — {_e(room.upper())} ({_e(flavor.upper())} · {_e(directness.upper())})</b>\n"
        f"⚖️ <b>100-Point Room Rubric ({total} pts total):</b>\n{w_lines}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n{base_view.html}"
    )
    bot.send_message(m.chat.id, html_out, parse_mode="HTML", reply_markup=_pivots_kb())

def on_progress(bot: telebot.TeleBot, m: types.Message) -> None:
    _, room, flavor, directness = _cfg(m.from_user.id)
    _, breakdown = score_debrief(room)
    w_str = " · ".join(f"{k}: {v}" for k, v in breakdown.items())
    _, _, flows_i = _deps()
    base_view = flows_i.progress_view(m.from_user.id)
    html_out = (
        f"📊 <b>3D MASTERY DASHBOARD — {_e(room.upper())} / {_e(flavor.upper())} / {_e(directness.upper())}</b>\n"
        f"⚖️ <b>Active Rubric:</b> <i>{_e(w_str)}</i>\n"
        f"🎯 <b>C/D Gate Axes:</b> <code>too_written · too_casual · over_hedged · under_committed · corporate_filler · missing_move</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n{base_view.html}"
    )
    bot.send_message(m.chat.id, html_out, parse_mode="HTML", reply_markup=_pivots_kb())

def route_callback(bot: telebot.TeleBot, c: types.CallbackQuery) -> None:
    lid, chat_id, parts = c.from_user.id, c.message.chat.id, (c.data or "").split(":")
    _, room, flavor, directness = _cfg(lid)
    kind, arg = (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")
    if kind == "sc":
        sc = pick_scene(room, scene_id=arg)
        _remember_opener(lid, str(sc["opener"]), room=room)
        bot.send_message(chat_id, render_scene(sc, directness, flavor), parse_mode="HTML", reply_markup=_scene_kb(room))
    elif kind == "scr":
        sc = pick_scene(arg)
        _remember_opener(lid, str(sc["opener"]), room=arg)
        bot.send_message(chat_id, render_scene(sc, directness, flavor), parse_mode="HTML", reply_markup=_scene_kb(arg))
    elif kind == "pv":
        bot.send_message(chat_id, render_pivots(arg), parse_mode="HTML", reply_markup=_pivots_kb())
    elif kind == "dr":
        bot.send_message(chat_id, render_drifts(), parse_mode="HTML", reply_markup=_pivots_kb())
    elif kind == "dl":
        idx = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        dl = pick_dilemma(arg, idx)
        _remember_opener(lid, str(dl["alex"]), room=str(dl["room"]))
        bot.send_message(chat_id, render_dilemma(dl, directness), parse_mode="HTML", reply_markup=_dilemma_kb(arg, idx + 1))
    elif kind == "rp":
        item = next((x for x in D.REPLAY_INTENTIONS if x["id"] == arg), D.REPLAY_INTENTIONS[0])
        bot.send_message(chat_id, render_replay(item["idea"], item), parse_mode="HTML", reply_markup=_replay_kb())
    elif kind == "try":
        _remember_opener(lid, f"Switched to {arg}", room=arg)
        sp = room_engine.format_alex_speaker_label(arg, flavor)
        bot.send_message(chat_id, f"🎯 Switched to <b>{_e(arg.upper())}</b> Room! Send your version now to <b>{_e(sp)}</b>.", parse_mode="HTML")
    bot.answer_callback_query(c.id)

BUTTONS = {
    "💬 Open Scene": on_open_scene, "🎭 Replay": on_replay, "🤝 Dilemma": on_dilemma,
    "🔁 Review": on_review, "🏁 Scene Debrief": on_debrief, "📊 My Progress": on_progress,
}
CMD_TO_BUTTON = {
    "scene": "💬 Open Scene", "replay": "🎭 Replay", "dilemma": "🤝 Dilemma",
    "review": "🔁 Review", "debrief": "🏁 Scene Debrief", "progress": "📊 My Progress",
}

def register(bot: telebot.TeleBot | None) -> None:
    if bot is None:
        return
    def _on_btn(m: types.Message) -> None:
        BUTTONS[m.text](bot, m)
    def _on_cmd(m: types.Message) -> None:
        BUTTONS[CMD_TO_BUTTON[m.text.split()[0].lstrip("/").split("@")[0]]](bot, m)
    def _on_cb(c: types.CallbackQuery) -> None:
        route_callback(bot, c)
    bot.message_handler(func=lambda m: getattr(m, "text", None) in BUTTONS)(_on_btn)
    bot.message_handler(commands=list(CMD_TO_BUTTON))(_on_cmd)
    bot.callback_query_handler(func=lambda c: bool(getattr(c, "data", None)) and c.data.startswith("v4:"))(_on_cb)
    try:
        bot.set_my_commands([types.BotCommand(cmd, desc) for cmd, desc in D.BOT_COMMANDS])
    except Exception:
        log.exception("set_my_commands failed")
# end part 3
# END v4_handlers
