import telebot
import v4_handlers as H

def test_v4_register_handlers_first():
    bot = telebot.TeleBot("123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", threaded=False)
    H.register(bot)
    assert len(bot.message_handlers) >= 2
    assert len(bot.callback_query_handlers) >= 1
    assert bot.message_handlers[0]["function"].__module__ == "v4_handlers"
    for label in ("💬 Open Scene", "🎭 Replay", "🤝 Dilemma", "🔁 Review", "🏁 Scene Debrief", "📊 My Progress"):
        assert label in H.BUTTONS
