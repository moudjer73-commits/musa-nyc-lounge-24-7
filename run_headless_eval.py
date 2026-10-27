import os, sys, json, base64, traceback
import config, database, ai_engine, app

if __name__ == "__main__":
    try:
        db = database.LoungeDatabase(user_db_path="/tmp/eval_v2_u.db", corpus_db_path="/tmp/eval_v2_c.db")
        ai = ai_engine.NYCLoungeAI(api_key=config.GEMINI_API_KEY, db=db)
        user = {"user_id": 777, "preferred_register": "witty"}

        sections = []

        # TEST 1: Natural learner sentence -> Mirror MUST stay silent
        msg1 = "What's your favorite English expression guys, and why?"
        d1 = ai.generate_dialogue(user=user, user_text=msg1, mode="banter")
        c1 = ai.coach_turn(user=user, user_text=msg1, turns=d1.turns)
        sections.append(
            "=== GOLDEN TEST 1: BANTER WITH NATURAL SENTENCE (MIRROR MUST BE SILENT) ===\n"
            f"👤 Learner: {msg1}\n\n"
            "[Message 1 — Call A: Free Dialogue @ T=0.8]\n" + app.format_v2_dialogue_html(d1) + "\n\n"
            "[Message 2 — Call B: Coach @ T=0.15 (Mirror Verdict=" + (c1.mirror.verdict if c1 else "none") + ")]\n" + app.format_v2_coach_html(c1)
        )

        # TEST 2: Learner sentence with 'for him or her' -> Mirror MUST do surgical span -> replacement
        msg2 = "Ok I hear you, now each one of you tell me a proverb that makes sense for him or her"
        d2 = ai.generate_dialogue(user=user, user_text=msg2, mode="banter", history_lines=[f"{t.speaker}: {t.text}" for t in d1.turns])
        c2 = ai.coach_turn(user=user, user_text=msg2, turns=d2.turns)
        sections.append(
            "=== GOLDEN TEST 2: BANTER WITH PRONOUN SHIFT (SURGICAL MIRROR + NO IDIOM STACKING) ===\n"
            f"👤 Learner: {msg2}\n\n"
            "[Message 1 — Call A: Free Dialogue @ T=0.8]\n" + app.format_v2_dialogue_html(d2) + "\n\n"
            "[Message 2 — Call B: Coach @ T=0.15 (Mirror Verdict=" + (c2.mirror.verdict if c2 else "none") + ")]\n" + app.format_v2_coach_html(c2)
        )

        # TEST 3: Read the Room + Turn-2 Stateful Practice Reply
        rtr_input = "That’s one way to do it."
        rtr_res = ai.read_the_room_v2(user=user, user_text=rtr_input)
        pending = db.get_pending_practice(777)
        learner_attempt = "I would like to know what exactly you are implying by saying that."
        pe_res = ai.practice_reply_v2(user=user, learner_reply=learner_attempt, pending_state=pending)

        sections.append(
            "=== GOLDEN TEST 3A: READ THE ROOM V2 (STREAMLINED + HIDDEN READING) ===\n"
            + app.format_v2_read_the_room_html(rtr_res, rtr_input)
            + "\n\n=== GOLDEN TEST 3B: TURN-2 STATEFUL FEEDBACK LOOP (`PracticeEval`) ===\n"
            + f"👤 Learner Reply to Your Turn: \"{learner_attempt}\"\n"
            + f"🔒 Counterpart's Hidden Reading Index: {rtr_res.your_turn.hidden_reading}\n\n"
            + app.format_v2_practice_eval_html(pe_res, learner_attempt)
        )

        rendered = "\n\n" + ("=" * 72 + "\n\n").join(sections)
    except Exception:
        rendered = "❌ EVAL_ERROR:\n" + traceback.format_exc()

    b64 = base64.b64encode(rendered.encode("utf-8")).decode("ascii")
    print("B64_EVAL_START:" + b64 + ":B64_EVAL_END", flush=True)
