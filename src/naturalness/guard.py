"""Regex Guard & 10-Message Window Analyzer: cleans AI-isms in <2ms without LLM regeneration."""
from __future__ import annotations
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "naturalness"
EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class GuardResult:
    segments: list[str]
    text: str
    guard_hits: list[str] = field(default_factory=list)
    n_words: int = 0
    ends_with_question: bool = False


class NaturalnessGuard:
    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        cfg = json.loads((data_dir / "ai_isms.json").read_text(encoding="utf-8"))
        self.hard_openers: list[str] = cfg["hard_openers"]
        self.hard_phrases: list[str] = [p.lower() for p in cfg["hard_phrases"]]
        self.soft_phrases: list[str] = cfg["soft_phrases"]
        self.fallback_pool: list[str] = cfg["fallback_pool"]
        self._fallback_idx = 0

    def _clean_segment(
        self, raw_seg: str, learner_used_emoji: bool, recent_soft_hits: list[str]
    ) -> tuple[str, list[str]]:
        hits: list[str] = []
        s = raw_seg.strip()
        if not s:
            return "", hits

        if "**" in s or "__" in s or re.search(r"(?m)^\s*(?:[-*•]|\d+\.|#+)\s+", s):
            hits.append("markdown_stripped")
        s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
        s = re.sub(r"__(.*?)__", r"\1", s)
        s = re.sub(r"(?m)^\s*#+\s*", "", s)
        s = re.sub(r"(?m)^\s*[-*•]\s+", "", s)
        s = re.sub(r"(?m)^\s*\d+\.\s+", "", s)

        if "—" in s or " – " in s:
            hits.append("em_dash_replaced")
            s = re.sub(r"\s*—\s*", ", ", s)
            s = re.sub(r"\s+–\s+", ", ", s)

        sentences = [part.strip() for part in SENTENCE_SPLIT_RE.split(s) if part.strip()]
        if sentences:
            first_low = sentences[0].lower()
            for opener in self.hard_openers:
                op_low = opener.lower()
                # an AI-ism opener is "Certainly!" / "Absolutely," ... not "Absolutely not, I can't."
                if re.match(r"^" + re.escape(op_low) + r"(?:\s*[!.,:;]|\s*$)", first_low):
                    hits.append(f"hard_opener:{opener}")
                    if len(sentences) > 1:
                        sentences = sentences[1:]
                    else:
                        rest = re.sub(r"^" + re.escape(opener) + r"[!.,:\s]*", "", sentences[0], flags=re.I).strip()
                        sentences = [rest] if rest else []
                    break

        kept_sentences: list[str] = []
        for sent in sentences:
            sent_low = sent.lower()
            matched_hard = next((hp for hp in self.hard_phrases if hp in sent_low), None)
            if matched_hard:
                hits.append(f"hard_phrase:{matched_hard}")
                continue
            kept_sentences.append(sent)

        s = " ".join(kept_sentences).strip()
        if not s:
            return "", hits

        excl_count = s.count("!")
        if excl_count > 1:
            hits.append("excess_exclamation")
            first_idx = s.index("!")
            s = s[: first_idx + 1] + s[first_idx + 1 :].replace("!", ".")

        emojis = EMOJI_RE.findall(s)
        if emojis:
            if not learner_used_emoji:
                hits.append("emoji_stripped")
                s = EMOJI_RE.sub("", s).strip()
            elif len(emojis) > 1:
                hits.append("excess_emoji")
                kept_first = False
                out_chars = []
                for ch in s:
                    if EMOJI_RE.match(ch):
                        if not kept_first:
                            out_chars.append(ch)
                            kept_first = True
                    else:
                        out_chars.append(ch)
                s = "".join(out_chars).strip()

        for sp in self.soft_phrases:
            if re.search(r"\b" + re.escape(sp) + r"\b", s, flags=re.I):
                if sp.lower() in [x.lower() for x in recent_soft_hits[-8:]]:
                    hits.append(f"soft_excess:{sp}")
                recent_soft_hits.append(sp.lower())

        s = re.sub(r"\s{2,}", " ", s).strip()
        return s, hits

    def apply(
        self,
        raw_text: str,
        learner_used_emoji: bool = False,
        recent_soft_hits: list[str] | None = None,
    ) -> GuardResult:
        soft_tracker = recent_soft_hits if recent_soft_hits is not None else []
        raw_segments = [seg.strip() for seg in raw_text.split("---") if seg.strip()]
        cleaned_segments: list[str] = []
        all_hits: list[str] = []

        for seg in raw_segments:
            cleaned, hits = self._clean_segment(seg, learner_used_emoji, soft_tracker)
            all_hits.extend(hits)
            if cleaned:
                cleaned_segments.append(cleaned)

        if not cleaned_segments:
            fb = self.fallback_pool[self._fallback_idx % len(self.fallback_pool)]
            self._fallback_idx += 1
            cleaned_segments = [fb]
            all_hits.append("fallback_used")

        final_text = "\n\n".join(cleaned_segments)
        words = final_text.split()
        return GuardResult(
            segments=cleaned_segments,
            text=final_text,
            guard_hits=all_hits,
            n_words=len(words),
            ends_with_question=final_text.rstrip().endswith("?"),
        )

    @staticmethod
    def analyze_window(recent_bot_messages: list[str]) -> dict[str, Any]:
        window = [m.strip() for m in recent_bot_messages[-10:] if m.strip()]
        if len(window) < 4:
            return {"force_no_question": False, "force_short_or_detail": False, "avoid_opener": None}

        q_share = sum(1 for m in window if m.endswith("?")) / len(window)
        first_words = [re.sub(r"[^a-zA-Z']", "", m.split()[0]).lower() for m in window if m.split()]
        avoid_opener = None
        for fw in set(first_words):
            if fw and (first_words.count(fw) / len(window)) > 0.30:
                avoid_opener = fw
                break

        counts = [len(m.split()) for m in window]
        mean = sum(counts) / len(counts)
        stdev = math.sqrt(sum((c - mean) ** 2 for c in counts) / len(counts))

        return {
            "force_no_question": q_share > 0.45,
            "force_short_or_detail": stdev < 3.0,
            "avoid_opener": avoid_opener,
            "question_share": round(q_share, 3),
            "word_count_stdev": round(stdev, 2),
        }

    @staticmethod
    def prepare_for_tts(text: str) -> str:
        num_map = {
            "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
            "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten"
        }
        s = EMOJI_RE.sub("", text)
        s = s.replace("—", ", ").replace(" – ", ", ")
        s = re.sub(r"\btbh\b", "to be honest", s, flags=re.I)
        s = re.sub(r"\bidk\b", "I don't know", s, flags=re.I)
        s = re.sub(r"\brn\b", "right now", s, flags=re.I)
        s = re.sub(r"\blol\b", "", s, flags=re.I)
        for k, v in num_map.items():
            # only bare standalone digits: leave 3:30, 2.5, $5, 10%, 1,000 and 2024 alone
            s = re.sub(r"(?<![\w.:$,/-])" + k + r"(?![\w.:%,/-])", v, s)
        return re.sub(r"\s{2,}", " ", s).strip()

