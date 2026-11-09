"""Prompt Compiler: assembles persona + cast + situation + moves + anchors + dice + last 12 turns (~2k tokens)."""
from __future__ import annotations
from pathlib import Path
from src.naturalness.router import RouteBundle

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


class PromptCompiler:
    def __init__(self, prompts_dir: Path = PROMPTS_DIR) -> None:
        how_path = prompts_dir / "how_you_sound.md"
        self.how_you_sound = how_path.read_text(encoding="utf-8").strip() if how_path.exists() else ""

    def compile_system_prompt(
        self,
        bundle: RouteBundle,
        learner_notes: list[str] | None = None,
    ) -> str:
        p = bundle.profile
        c = bundle.cast
        words_typ = p.get("msg_words", {}).get("typical", [2, 16])
        words_max = p.get("msg_words", {}).get("hard_max", 45)

        sections: list[str] = [
            f"YOU ARE {bundle.cast_name} ({c.get('age')}, {c.get('from')}, {c.get('role')}).",
            f"VOICE: {c.get('style')} Occasional habits (at most once every 6 messages): {', '.join(c.get('habits', []))}.",
            f"NEVER SAY: {', '.join(c.get('never_says', []))}.",
            "",
            self.how_you_sound,
            "",
            f"SITUATION PROFILE ({bundle.profile_id} | mode={p.get('mode')} | tenor={p.get('tenor')}):",
            f"- Target length: {words_typ[0]}-{words_typ[1]} words (hard max {words_max} words).",
            f"- Contractions: {p.get('contractions', 'always')}. Fragments: {p.get('fragments', 'often')}.",
            f"- Disagree pattern: {p.get('disagree', 'pause word + softener + reason')}.",
        ]

        if bundle.moves:
            m_lines = ["SPEECH MOVE GUIDANCE:"]
            for m in bundle.moves:
                v_key = f"{p.get('tenor')}/{p.get('mode')}"
                variants = m.get("variants", {}).get(v_key) or next(iter(m.get("variants", {}).values()), [])
                use_items = m.get("use", {}).get("items", [])
                avoid_items = m.get("avoid", [])
                m_lines.append(
                    f"- [{m['id']}] shape: {m.get('shape')} | examples: {'; '.join(variants[:2])} "
                    f"| natural chunks: {', '.join(use_items[:4])} | avoid: {', '.join(avoid_items[:2])}"
                )
            sections.append("\n".join(m_lines))

        if bundle.anchors:
            a_lines = ["STYLE ANCHORS (match this rhythm, do not copy verbatim):"]
            for a in bundle.anchors:
                rep = " / ".join(a.get("reply", []))
                prev_txt = a.get("prev", "")
                a_lines.append(f'- Learner: "{prev_txt}" -> You: "{rep}"')
            sections.append("\n".join(a_lines))

        if learner_notes:
            sections.append("LEARNER NOTES:\n- " + "\n- ".join(learner_notes[:5]))

        dice_lines = [f"THIS TURN'S DICE DIRECTIVE ({bundle.dice['id']}): {bundle.dice['text']}"]
        for d in bundle.window_directives:
            dice_lines.append(f"- {d}")
        sections.append("\n".join(dice_lines))

        return "\n\n".join(s for s in sections if s)

    @staticmethod
    def format_history(turns: list[dict[str, str]], max_turns: int = 12) -> str:
        recent = turns[-max_turns:]
        lines = []
        for t in recent:
            role = "Learner" if t.get("role") in ("user", "learner") else "You"
            lines.append(f"{role}: {t.get('text', '').strip()}")
        return "\n".join(lines)
