import math
import os
from typing import Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont

GUIDE_CHAPTERS_HTML = {
    "overview": (
        "📖 <b>Spoken English Lab — Complete User Guide</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your interactive training system for mastering <b>authentic Spoken American English</b> across every real-life context and register.\n\n"
        "<b>Core Design Philosophy:</b>\n"
        "• <b>Interlocutor First, Coach Second:</b> Alex responds naturally to what you mean before anything else—no robotic 3-question interviews and no fabricated workplace crises.\n"
        "• <b>Zero Interruption Flow:</b> Corrections never clutter your chat stream unless you tap <code>💡 Quick Upgrade</code>.\n"
        "• <b>Evidence-Based Acquisition:</b> Expressions are tracked from first exposure all the way to unprompted spontaneous production.\n\n"
        "👇 <b>Tap any chapter below to explore how the lab works:</b>\n"
        "<tg-spoiler>مرحباً بك في معمل الإنجليزية المنطوقة! اضغط على أي فصل بالأسفل لشرح طريقة عمل الطبقات الثلاث، وأنماط التدريب الخمسة، وإعدادات السجلات، وسلّم الإتقان.</tg-spoiler>"
    ),
    "ch1_layers": (
        "🏛️ <b>Chapter 1: The 3-Layer Coaching System</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>Layer 1 — Natural Conversation & Implicit Recasts:</b>\n"
        "When you send a message, Alex replies in 1–2 natural spoken sentences. If your phrasing is understandable but slightly unidiomatic, Alex models the natural phrasing invisibly inside the reply.\n\n"
        "<b>Layer 2 — On-Demand Upgrade Card (<code>💡 Quick Upgrade</code>):</b>\n"
        "When the silent analyst detects a high-value grammar, collocation, or pragmatic gap (within your turn budget), two compact buttons appear under Alex's reply:\n"
        "• <code>💡 Quick Upgrade</code>: Expands the card showing your original span, the primary American alternative, register-specific alternatives, a concise English reason, and a neutral Arabic note hidden inside a spoiler.\n"
        "• <code>🎧 Listen</code>: Generates natural spoken audio so you hear the exact rhythm and intonation.\n\n"
        "<b>Layer 3 — Recurring Pattern Detection:</b>\n"
        "If the exact same error pattern appears <b>3 times</b> across separate messages, the bot notifies you and unlocks a focused 2-minute practice.\n\n"
        "<b>بالعربية:</b>\n"
        "<tg-spoiler>الطبقة 1 تحفظ تدفق الحوار وتصحح ضمنياً داخل رد Alex. الطبقة 2 تضع زر 💡 تحت الرسالة عند الحاجة فقط لفتح بطاقة التصحيح والشرح العربي دون تشويش الدردشة. الطبقة 3 ترصد الأخطاء التي تتكرر 3 مرات لتدريبك عليها.</tg-spoiler>"
    ),
    "ch2_modes": (
        "🎯 <b>Chapter 2: The 5 Interactive Training Modes</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1️⃣ <b>💬 Open Scene (<code>/scene</code>):</b>\n"
        "Unscripted everyday conversation on any topic you choose (~70% of practice time).\n\n"
        "2️⃣ <b>🎛️ Register Shift (<code>/shift</code>):</b>\n"
        "Send any thought or intention, and see how Americans package it across 3 relationships:\n"
        "• <i>Casual (Close Friend)</i>\n"
        "• <i>Diplomatic (Peer Colleague)</i>\n"
        "• <i>Executive (Senior Manager)</i>\n\n"
        "3️⃣ <b>🤝 Pragmatic Dilemma (<code>/dilemma</code>):</b>\n"
        "Generates a fresh, unscripted real-life social or workplace dilemma where you must protect a boundary or priority while keeping rapport intact.\n\n"
        "4️⃣ <b>🧠 Think-Aloud (<code>/think</code>):</b>\n"
        "Analytical sparring to practice qualifying arguments, hedging claims, and using spoken discourse markers (<i>'The way I see it…', 'That said…', 'It cuts both ways'</i>).\n\n"
        "5️⃣ <b>🏋️ Phrase Workout (<code>/workout</code>):</b>\n"
        "Creates a brand-new situation that calls for a target communicative move without showing you the phrase first—testing true unaided retrieval.\n\n"
        "<b>بالعربية:</b>\n"
        "<tg-spoiler>خمسة أنماط متكاملة: (1) محادثة حرة، (2) تحويل الجملة الواحدة بين الصديق والزميل والمدير، (3) مواقف تفاوضية وإنسانية حية متجددة، (4) نقاش فكري وتحليلي، (5) تمرين استرجاع العبارات في مواقف جديدة دون تلقين.</tg-spoiler>"
    ),
    "ch3_registers": (
        "⚙️ <b>Chapter 3: Registers & Coaching Intensity (<code>/settings</code>)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Tap <b>⚙️ Register & Tone</b> anytime to customize two independent dimensions:\n\n"
        "<b>A. The 6 Spoken Registers:</b>\n"
        "• <b>Casual:</b> Everyday contractions, phrasal verbs, relaxed flow.\n"
        "• <b>Warm:</b> Supportive, empathetic, face-saving softeners.\n"
        "• <b>Witty:</b> Dry understatement and light situational humor.\n"
        "• <b>Diplomatic:</b> Tactful pushback, acknowledging views before qualifying.\n"
        "• <b>Executive:</b> Crisp, bottom-line first, decisive leadership tone.\n"
        "• <b>Analytical:</b> Nuanced distinctions, concessions, and structured reasoning.\n\n"
        "<b>B. Coaching Intensity (Intervention Budget):</b>\n"
        "• <b>🔇 Silent:</b> Pure conversation; zero cards shown (all diagnostics deferred to your profile).\n"
        "• <b>💡 Light (Default):</b> At most 1 visible upgrade card every 4 turns.\n"
        "• <b>🔬 Active:</b> High-density coaching (cards allowed every 2 turns + optional stylistic upgrades).\n\n"
        "<b>بالعربية:</b>\n"
        "<tg-spoiler>من زر الإعدادات ⚙️ يمكنك تغيير نبرة الحوار بين 6 سجلات (كاجوال، دافئ، ساخر، دبلوماسي، تنفيذي، تحليلي) والتحكم في كثافة التصحيح بين الصامت والخفيف والمكثف.</tg-spoiler>"
    ),
    "ch4_mastery": (
        "📊 <b>Chapter 4: Honest Acquisition & The 7-Stage Ladder (<code>/progress</code>)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "We never award fake percentages for merely reading a phrase. Every expression in your catalog (including phrases organically discovered from your own chats) moves through 7 verifiable stages:\n\n"
        "1. <code>Unseen</code> — Not encountered yet.\n"
        "2. <code>Seen</code> — Encountered in dialogue or an upgrade card.\n"
        "3. <code>Recognized</code> — Understood accurately in context.\n"
        "4. <code>Produced-with-prompt</code> — Used after a direct hint.\n"
        "5. <code>Produced-independently</code> — Retrieved unaided in a workout scenario.\n"
        "6. <code>Produced-spontaneously</code> — Used naturally on your own during open conversation.\n"
        "7. <code>Stable</code> — Produced spontaneously across <b>≥ 3 separate sessions</b>, <b>≥ 2 different registers</b>, spanning <b>≥ 7 days</b> in novel contexts.\n\n"
        "<b>بالعربية:</b>\n"
        "<tg-spoiler>لا يُحتسب الإتقان بمجرد قراءة العبارة؛ بل يتدرج عبر 7 مراحل موثقة في قاعدة البيانات، ولا يصل إلى مرحلة الإتقان المستقر (Stable) إلا إذا استخدمتَ العبارة تلقائياً في 3 جلسات مختلفة وسجلّين مختلفين وعلى مدى 7 أيام فأكثر.</tg-spoiler>"
    ),
}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def generate_bot_branding_images(output_dir: str) -> Tuple[str, str]:
    """
    Generates two studio-grade images with 2x supersampling (LANCZOS downscaling):
    1. `bot_profile_800x800.png` (800x800 square avatar for @BotFather Edit Botpic)
    2. `bot_description_640x360.png` (Exact 640x360 16:9 banner required by @BotFather Edit Description Picture)
    """
    os.makedirs(output_dir, exist_ok=True)
    profile_path = os.path.join(output_dir, "bot_profile_800x800.png")
    desc_path = os.path.join(output_dir, "bot_description_640x360.png")

    # ==========================================================================
    # IMAGE 1: PROFILE AVATAR (Rendered at 1600x1600 -> Downsampled to 800x800)
    # ==========================================================================
    S = 1600
    img_p = Image.new("RGBA", (S, S), (10, 15, 28, 255))
    draw_p = ImageDraw.Draw(img_p)

    # Radial dark-navy to indigo background glow
    for r in range(780, 0, -10):
        ratio = 1.0 - (r / 780.0)
        red = int(10 + 18 * ratio)
        green = int(15 + 38 * ratio)
        blue = int(30 + 68 * ratio)
        draw_p.ellipse([S // 2 - r, S // 2 - r, S // 2 + r, S // 2 + r], fill=(red, green, blue, 255))

    # Outer double metallic gold & cyan studio ring
    draw_p.ellipse([90, 90, S - 90, S - 90], outline=(245, 190, 75, 220), width=14)
    draw_p.ellipse([126, 126, S - 126, S - 126], outline=(56, 189, 248, 150), width=6)

    # Acoustic equalizer bars across the center
    bar_x_start = 310
    bar_width = 24
    bar_gap = 20
    num_bars = 23
    center_y = 660
    for i in range(num_bars):
        dist = abs(i - (num_bars // 2)) / (num_bars // 2)
        wave = (math.cos(dist * math.pi) + 1.0) / 2.0
        h = int(70 + 270 * (wave ** 1.3) + (45 if i % 2 == 0 else -20))
        x0 = bar_x_start + i * (bar_width + bar_gap)
        y0 = center_y - h // 2
        y1 = center_y + h // 2
        color = (245, 190, 75, 245) if dist < 0.38 else (56, 189, 248, 225)
        draw_p.rounded_rectangle([x0, y0, x0 + bar_width, y1], radius=12, fill=color)

    # Speech bubble frame around the acoustic wave
    draw_p.rounded_rectangle([240, 420, S - 240, 900], radius=90, outline=(245, 190, 75, 255), width=14)
    # Speech bubble tail
    tail_pts = [(480, 900), (410, 1015), (610, 900)]
    draw_p.polygon(tail_pts, fill=(18, 36, 66, 255), outline=(245, 190, 75, 255))
    draw_p.line([(484, 900), (606, 900)], fill=(18, 36, 66, 255), width=16)

    # Typography
    f_title = _load_font(124, bold=True)
    f_sub = _load_font(62, bold=True)
    f_tag = _load_font(44, bold=False)

    t1 = "SPOKEN"
    b1 = draw_p.textbbox((0, 0), t1, font=f_title)
    draw_p.text(((S - (b1[2] - b1[0])) // 2, 1045), t1, font=f_title, fill=(255, 255, 255, 255))

    t2 = "ENGLISH LAB"
    b2 = draw_p.textbbox((0, 0), t2, font=f_sub)
    draw_p.text(((S - (b2[2] - b2[0])) // 2, 1195), t2, font=f_sub, fill=(245, 190, 75, 255))

    t3 = "REAL AMERICAN CONVERSATION"
    b3 = draw_p.textbbox((0, 0), t3, font=f_tag)
    draw_p.text(((S - (b3[2] - b3[0])) // 2, 1295), t3, font=f_tag, fill=(148, 163, 184, 255))

    img_p_final = img_p.resize((800, 800), resample=Image.Resampling.LANCZOS).convert("RGB")
    img_p_final.save(profile_path, format="PNG", optimize=True)

    # ==========================================================================
    # IMAGE 2: DESCRIPTION BANNER (Rendered at 1280x720 -> Exact 640x360)
    # ==========================================================================
    W, H = 1280, 720
    img_d = Image.new("RGBA", (W, H), (11, 17, 32, 255))
    draw_d = ImageDraw.Draw(img_d)

    # Subtle background gradient & accent card
    for y in range(H):
        r = int(11 + (y / H) * 10)
        g = int(17 + (y / H) * 22)
        b = int(32 + (y / H) * 40)
        draw_d.line([(0, y), (W, y)], fill=(r, g, b, 255))

    # Outer border frame
    draw_d.rounded_rectangle([28, 28, W - 28, H - 28], radius=32, outline=(56, 189, 248, 120), width=4)

    # Left Badge & Header
    f_badge = _load_font(28, bold=True)
    f_h1 = _load_font(66, bold=True)
    f_h2 = _load_font(34, bold=False)
    f_pill_title = _load_font(30, bold=True)
    f_pill_sub = _load_font(23, bold=False)

    draw_d.rounded_rectangle([72, 64, 540, 118], radius=27, fill=(30, 58, 95, 220), outline=(56, 189, 248, 200), width=3)
    draw_d.text((98, 76), "AI CONVERSATION & PRAGMATICS LAB", font=f_badge, fill=(56, 189, 248, 255))

    draw_d.text((72, 140), "Spoken English Lab", font=f_h1, fill=(255, 255, 255, 255))
    draw_d.text((72, 225), "Master Natural American English Across Every Register", font=f_h2, fill=(245, 190, 75, 255))

    # 4 Feature Cards in a 2x2 Grid
    cards = [
        ("1. Unscripted Flow", "Real conversation first, silent recasts & zero interrogation", (72, 310, 615, 470), (56, 189, 248, 255)),
        ("2. 6 Live Registers", "Casual • Warm • Witty • Diplomatic • Executive • Analytical", (655, 310, 1208, 470), (245, 190, 75, 255)),
        ("3. L1-Arabic Calibrated", "Surgical collocation upgrades & neutral bilingual notes", (72, 495, 615, 655), (52, 211, 153, 255)),
        ("4. Pragmatic Dilemmas", "Live negotiation, register shifting & unaided retrieval", (655, 495, 1208, 655), (192, 132, 252, 255)),
    ]

    for title, subtitle, (x0, y0, x1, y1), accent_col in cards:
        draw_d.rounded_rectangle([x0, y0, x1, y1], radius=22, fill=(19, 30, 53, 235), outline=(71, 85, 105, 160), width=3)
        draw_d.rounded_rectangle([x0, y0 + 24, x0 + 10, y1 - 24], radius=5, fill=accent_col)
        draw_d.text((x0 + 30, y0 + 26), title, font=f_pill_title, fill=accent_col)
        # Wrap subtitle into 2 lines if needed
        words = subtitle.split()
        line1, line2 = [], []
        for w in words:
            if len(" ".join(line1 + [w])) <= 34:
                line1.append(w)
            else:
                line2.append(w)
        draw_d.text((x0 + 30, y0 + 74), " ".join(line1), font=f_pill_sub, fill=(226, 232, 240, 255))
        if line2:
            draw_d.text((x0 + 30, y0 + 106), " ".join(line2), font=f_pill_sub, fill=(148, 163, 184, 255))

    # Top-right decorative acoustic wave emblem
    wave_cx, wave_cy = 1065, 155
    draw_d.rounded_rectangle([920, 68, 1208, 242], radius=28, fill=(19, 30, 53, 220), outline=(245, 190, 75, 180), width=3)
    for idx in range(11):
        d_norm = abs(idx - 5) / 5.0
        bh = int(30 + 95 * math.cos(d_norm * math.pi / 2.0))
        bx = 958 + idx * 20
        col = (245, 190, 75, 255) if d_norm < 0.4 else (56, 189, 248, 230)
        draw_d.rounded_rectangle([bx, wave_cy - bh // 2, bx + 11, wave_cy + bh // 2], radius=5, fill=col)

    img_d_final = img_d.resize((640, 360), resample=Image.Resampling.LANCZOS).convert("RGB")
    img_d_final.save(desc_path, format="PNG", optimize=True)

    return profile_path, desc_path
