import math
import os
from typing import Tuple
from PIL import Image, ImageDraw, ImageFilter, ImageFont

GUIDE_CHAPTERS_HTML = {
    "overview": (
        "📖 <b>Spoken English Lab 🎙️ — Interactive User Guide (v2_complete)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Welcome to your personal studio for mastering natural, unscripted <b>Spoken American English (B2–C2)</b>.\n\n"
        "<b>What's New in v2_complete:</b>\n"
        "• ⚡ <b>Sub-Second Replies:</b> Alex replies immediately while diagnostics attach silently as a <b>💡 Quick Upgrade</b> button.\n"
        "• 🎧 <b>2-Phase Listen &amp; Decode:</b> Decode reduced American speech &amp; hidden subtext first, then get instant evaluation + IPA breakdown.\n"
        "• 🏋️ <b>Task-Aware Phrase Workout:</b> Spontaneous retrieval with in-character reinforcement if you drift off-task.\n"
        "• 🎛️ <b>Spoken-First Register Shift:</b> Calibrate speech for live meetings &amp; calls—never stiff written emails.\n\n"
        "💡 <b>بالعربية:</b> استخدم الأزرار أدناه لتصفح فصول الدليل الخمسة، أو اضغط على زر القائمة <b>( / )</b> للوصول السريع لجميع الأوامر."
    ),
    "ch1_layers": (
        "🏛️ <b>1. Dual-Engine &amp; 3-Layer Coaching System</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>Layer 1 — Pure Spoken Flow:</b> Alex responds naturally in character without turning every message into a classroom lesson.\n"
        "• <b>Layer 2 — On-Demand Surgical Upgrade:</b> When the analyst detects an L1-Arabic transfer trap, collocation slip, or pragmatic mismatch, a <b>💡 Quick Upgrade</b> button attaches below Alex's reply.\n"
        "• <b>Layer 3 — Scene Debrief (🏁):</b> Summarizes your spontaneous wins and top shifts with unified Arabic-English formatting.\n\n"
        "💡 <b>بالعربية:</b> لا يقاطع البوت تسلسل أفكارك؛ بل يمنحك رداً حوارياً طبيعياً أولاً، ويضع التصحيح الدقيق داخل زر <b>💡 Quick Upgrade</b> عند الحاجة فقط."
    ),
    "ch2_modes": (
        "🎯 <b>2. Interactive Training Modes</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "• <b>💬 Open Scene (/scene):</b> Free-flowing American conversation.\n"
        "• <b>🏋️ Phrase Workout (/workout):</b> Tests unaided phrase retrieval in a fresh situation without leaking the target phrase. If you miss the communicative goal, Alex naturally re-opens the opportunity in-scene.\n"
        "• <b>🎧 Listen &amp; Decode (/decode):</b> Two-phase training! Listen to fast reduced speech (<i>Whaddaya, Mighta, Didja</i>) and reply with the speaker's real meaning first—then receive evaluation, unpacked form, and IPA (or tap <b>🔍 Reveal Breakdown</b>).\n"
        "• <b>🎛️ Register Shift (/shift or /register):</b> Transforms your thought across <b>Casual</b>, <b>Diplomatic</b>, and crisp <b>Executive</b> spoken American English.\n"
        "• <b>🤝 Pragmatic Dilemma (/dilemma) &amp; 🧠 Think-Aloud (/think):</b> High-stakes pushback and analytical sparring."
    ),
    "ch3_registers": (
        "⚙️ <b>3. Live Registers, Directness &amp; Coaching Intensity</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Use <b>⚙️ Register &amp; Tone (/settings)</b> anytime to switch:\n"
        "• <b>6 Spoken Registers:</b>\n"
        "  – <i>Informal Band:</i> <b>Casual</b>, <b>Warm</b>, <b>Witty</b>\n"
        "  – <i>Neutral Band:</i> <b>Diplomatic</b>, <b>Analytical</b>\n"
        "  – <i>Formal Band:</i> <b>Executive</b> (crisp, decisive meeting speech)\n"
        "• <b>3 Directness Levels:</b> 🕊️ <b>Soft</b> | ⚖️ <b>Balanced</b> | 🎯 <b>Direct</b> (crisp directness is never flagged as rude in executive/direct contexts).\n"
        "• <b>Coaching Intensity:</b> 🔇 <b>Silent</b> | 💡 <b>Light</b> (with cooldown) | 🔬 <b>Active</b> (every eligible turn)."
    ),
    "ch4_mastery": (
        "📊 <b>4. Evidence-Based Mastery Ladder (/progress)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Knowing a phrase is not mastery. Every expression moves through a strict lifecycle:\n"
        "<code>UNKNOWN → EXPOSED → ATTEMPTED → CONSOLIDATING → STABLE</code>\n\n"
        "• <b>How to reach STABLE:</b>\n"
        "  1. At least <b>3 unaided, accurate uses</b> in real turns.\n"
        "  2. Used across at least <b>2 distinct formality bands</b> (Informal / Neutral / Formal).\n"
        "  3. At least <b>24 hours</b> between first and latest successful uses.\n"
        "  4. Immediate echoing of Alex's words never counts as independent retrieval."
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
