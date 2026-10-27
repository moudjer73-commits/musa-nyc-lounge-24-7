import traceback, base64
import config, database, ai_engine, validation_layer

if __name__ == "__main__":
    try:
        db = database.LoungeDatabase(user_db_path="/tmp/musa_live_u.db", corpus_db_path="/tmp/musa_live_c.db")
        ai = ai_engine.NYCLoungeAI(api_key=config.GEMINI_API_KEY, db=db)
        user = {"user_id": 777, "preferred_register": "articulate", "current_mode": "banter"}
        msg = "Hey guys, I've been trying to take a decision on whether to switch up my morning routine lately. How do you usually deal with it when your schedule starts feeling repetitive?"

        d_res = ai.generate_dialogue(user=user, user_text=msg, mode="banter")
        c_res = ai.coach_turn(user=user, user_text=msg, turns=d_res.turns)

        rendered = (
            "=== MUSA LIVE MESSAGE TEST (Vibe: 🎙️ Articulate) ===\n"
            f"👤 Musa: {msg}\n\n"
            "[Message 1 — Call A: Free Dialogue @ T=0.8]\n"
            + validation_layer.format_v2_dialogue_html(d_res)
            + "\n\n[Message 2 — Call B: Precision Coach @ T=0.15]\n"
            + validation_layer.format_v2_coach_html(c_res)
        )
    except Exception:
        rendered = "❌ ERROR:\n" + traceback.format_exc()

    b64 = base64.b64encode(rendered.encode("utf-8")).decode("ascii")
    print("B64_EVAL_START:" + b64 + ":B64_EVAL_END", flush=True)
