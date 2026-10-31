# Spoken-English coach for L1 Arabic learners: prompts and core engine

Foundation for a Telegram bot that teaches authentic spoken English to native Arabic speakers (B1+/B2 to C1/C2).
There is no Telegram wiring yet: this is the part that has to be right first, the prompts and the deterministic engine.

**Design rule: the LLM proposes, the code decides.** Whether, when and how a learner is corrected is never a model's mood.
It comes from `policy.decide`, the correction budget, the dialogue-move sampler and an event-sourced mastery ladder.

```
python -m pytest tests -q          # 235 tests, no network, no API key
python tools/render_prompts.py     # writes RENDERED_PROMPTS.md: every prompt exactly as the model sees it
python tools/eval_analyst.py --model <name>   # needs an API key: scores the Analyst on eval/analyst_cases.json
```

See `CHANGELOG.md` for what changed after the first external review (v2) and which tests changed with it.

## Layout

| Path | What it is |
|---|---|
| `prompts/` | All system prompts as markdown. `_partials/` holds shared blocks (`registers`, `l1_taxonomy`, `finding_schema`, `arabic_note_rules`) included with `{{> name}}`. |
| `prompts/responder_system.md` | The character: Persona Card, Scene Bible, six registers, director brief, silent recast, stealth targets, output format, worked examples. |
| `prompts/analyst_system.md` | Silent diagnosis: L1 Arabic transfer protocol, Finding schema, alternatives and their pragmatic differences, the neutral Arabic note. |
| `prompts/card_writer_system.md` | The 💡 upgrade card (Telegram HTML). Runs lazily, only when the learner taps the button. |
| `prompts/debrief_writer_system.md`, `scene_generator_system.md` | End-of-scene note; Persona Card + Scene Bible generator. |
| `prompts/mode_*.md` | Register Shift, Pragmatic Dilemma, Think-Aloud, Phrase Workout. Several prompts per file, split by `## PROMPT X: NAME`. |
| `coach/models.py` | Pydantic schemas for every JSON contract (Finding, Analysis, DirectorBrief, ResponderReply, PersonaScene, mode models). |
| `coach/move_sampler.py` | Dialogue-Move Sampler. |
| `coach/policy.py` | `decide(analysis, budget, learner_state)` and `CorrectionBudget`. |
| `coach/mastery.py` | `Seen → Recognized → Produced-with-prompt → Produced-spontaneously → Stable`, computed from events. |
| `coach/registers.py` | What a register label means for code: formality buckets and whether an expression fits a scene. |
| `coach/db.py` | SQLite schema (event-sourced) and queries. |
| `coach/director.py` | Turns a decision into the brief the Responder performs (move, length, recast, stealth target, beat). |
| `coach/checks.py` | Everything that does not trust the model: verbatim-span check, echo guard, Arabic-note tone validator, reply rule checks, Telegram HTML validation. |
| `coach/pipeline.py` | One full turn, plus cards, scene creation and debrief. |
| `coach/llm.py` | `LLMClient` protocol, `GeminiClient` adapter, `ScriptedLLM` test double. |
| `coach/evalset.py`, `eval/analyst_cases.json` | Analyst evaluation: labelled natural sentences that must be left alone, and real errors that should be found. |
| `tests/` | Prompt/schema drift tests, unit tests per module, end-to-end turns with a scripted LLM. |

## One turn

```
learner text → Analyst (JSON findings) → sanitize_analysis → policy.decide
             → persist findings → director.build_brief → Responder → check_reply (one silent retry)
             → verify the recast really landed → persist → bubbles (+ optional 💡)
```

The Analyst runs **before** the Responder, not in parallel. A recast only works if it appears in the very next message,
so the Responder must know about it. The cost is the Analyst's latency (8 s timeout by default). If it fails,
the turn degrades gracefully: no findings, no recast, the conversation carries on.

## Behaviour worth knowing before you tune it

* **Correction budget:** at most one visible card per 6 / 4 / 2 learner turns (light / normal / coach), +2 under strain,
  nothing in the first 2 turns, max 6 cards per scene. Severity-3 pragmatic, register or comprehension failures use a shorter gap (2 turns) and skip the warm-up rule, unless the learner is mid-story, emotional or frustrated (then the item waits for the recap).
* **Priority:** `severity × category weight × (1 + 0.5·min(prior occurrences, 4)) × teachable − (0.5 + strain)`.
  Thresholds: card 2.0, silent recast 0.9, adjusted by CEFR (B1 +0.4 … C2 −0.4). The third occurrence of the same `pattern_key` queues a drill.
* **Dialogue moves:** REACT, SHARE, BUILD, TEASE, ASK, PUSHBACK, CLARIFY, BACKCHANNEL, PIVOT, WRAP. Questions only on ASK/CLARIFY,
  question ratio capped at 30% of recent turns, no repeated move, TEASE/PUSHBACK blocked when the learner is emotional or frustrated.
* **Recast and stealth targets never share a turn.** A recast excludes REACT/BACKCHANNEL; a stealth target needs a normal
  discourse state, a 2-turn cooldown and room in the scene.
* **Honest tracking:** only a free scene can write a `spontaneous` event, and only when the Analyst saw use that is *not* an echo
  of the bot's last lines or a just-shown card (the echo guard is code, not model judgement). Drills and modes write `prompted` only.
  `Stable` needs good spontaneous use in ≥3 sessions, in ≥2 *formality buckets* (informal / neutral / formal; casual, warm and witty count as one) unless the expression is only natural in one, ≥7 days apart; it decays after 90 days; a miss drops one rung.
  No score or level is ever stored: every figure is derived from events.
* **Three active targets at most** (partial unique index), freed when one reaches Stable.
* **An expression is only used where it is natural.** Its `register_tags` are the range of that exact form; drills and stealth targets stay inside it (`modes.workout_setup_problems`, `director.pick_targets`).
* **The debrief cannot invent how the scene went.** Code computes `session_facts`; the writer must declare every number it quotes, and the code checks each declaration against the facts.
* **Think-Aloud borrows a persona's voice, not its errand** (`prompt_loader.voice_only_persona`, applied by `ModeRunner`). Pass `scene=` when the persona came from a scene.

## Wiring it up

```python
import time
from coach.db import Database
from coach.llm import GeminiClient
from coach.pipeline import CoachPipeline

db = Database("coach.sqlite")
pipe = CoachPipeline(db, GeminiClient(model="<your model>"))
db.get_or_create_user(telegram_user_id, now=time.time())

persona_scene = await pipe.create_scene(telegram_user_id, seed="a coworker asks you to take on extra work")
sid = pipe.start_scene(telegram_user_id, persona_scene.persona, persona_scene.scene)
opening = await pipe.open_scene(sid)                     # TurnResult.bubbles
result = await pipe.handle_turn(sid, "No, I can't. I have many works.")
html = await pipe.build_card(result.card_finding_id)     # when the learner taps 💡
recap = await pipe.end_scene(sid)
```

`TurnResult` carries `bubbles`, `card_finding_id` (show a 💡 button when set), `scene_over` and a `debug` dict for logging.
Mode helpers live in `coach/modes.py` (`ModeRunner`, `record_workout_result`).

## Honest caveats

* **`GeminiClient` has never been run against the live API.** It is a thin adapter around `google-genai`; try it with your own key and model name first.
  Nothing else depends on it: tests use `ScriptedLLM`.
* **The Analyst has not been scored on a live model.** `tools/eval_analyst.py` does it; the 35 seed cases are hand-written, small, and only good for finding problems. Replace them with real learner utterances before trusting a number. The figure that matters is the share of natural sentences that would have drawn a visible reaction.
* **The prompts have been validated structurally, not against a live model.** Tests prove that every example JSON in a prompt parses into its schema,
  every schema field is documented in its prompt, and slots fill safely. They cannot prove how a model behaves. Expect to adjust wording after the first real conversations.
* **Thresholds and weights are starting values,** not calibrated numbers (`policy.py`, `move_sampler.py`, `state.py`, `hesitation_from_voice`). Tune on real sessions.
* **The Arabic-note validator is a safety net, not a linguist.** It catches stigmatising or absolute phrasing and non-Arabic notes; the quality of the explanation is the prompt's job.
* **Known residual limit of the debrief guard:** a writer that declares a true field but attaches the number to the wrong noun is not caught by code.
* The Telegram layer (handlers, keyboards, voice transcription) is not included.
