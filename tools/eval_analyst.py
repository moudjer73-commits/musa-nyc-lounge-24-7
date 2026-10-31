"""Run the Analyst against eval/analyst_cases.json with a real model and print the numbers.

    python tools/eval_analyst.py --model <model name> [--cases path] [--out results.json]

Needs `pip install google-genai` and GEMINI_API_KEY (or GOOGLE_API_KEY). The GeminiClient has not been
run live by the author, so treat a first run as a test of the adapter as well. The seed set is small
and hand-written: use it to find problems, not to certify quality.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coach.evalset import DEFAULT_CASES, load_cases, run_cases, summarize  # noqa: E402
from coach.llm import GeminiClient  # noqa: E402
from coach.models import PersonaScene  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--cases", default=str(DEFAULT_CASES))
    ap.add_argument("--out", default=None, help="write per-case results as JSON")
    ap.add_argument("--max-visible-false-alarm", type=float, default=0.1,
                    help="exit with status 1 when more than this share of natural sentences would have drawn a visible reaction")
    args = ap.parse_args()

    example = PersonaScene.model_validate(json.loads((ROOT / "examples" / "persona_scene_example.json").read_text(encoding="utf-8")))
    results = asyncio.run(run_cases(GeminiClient(model=args.model), load_cases(args.cases), example))
    summary = summarize(results)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for r in results:
        if not r.passed:
            got = [f"{f.category}:{f.span!r}" for f in r.findings] or "nothing"
            print(f"\nFAIL {r.case.id} [{r.case.kind}] action={r.action} got={got}\n  {r.case.learner_message}\n  expected: {r.case.why}"
                  + (f"\n  error: {r.error}" if r.error else ""))
    if args.out:
        Path(args.out).write_text(json.dumps(
            [{"id": r.case.id, "kind": r.case.kind, "passed": r.passed, "action": r.action, "error": r.error,
              "findings": [f.model_dump() for f in r.findings], "notes": r.notes} for r in results],
            ensure_ascii=False, indent=2), encoding="utf-8")
    vfa = summary["visible_false_alarm_rate"]
    return 1 if (vfa is not None and vfa > args.max_visible_false_alarm) or summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
