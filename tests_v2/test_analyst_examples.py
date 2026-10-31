"""The worked examples in the Analyst prompt must pass the same checks the code applies to a real answer."""

from __future__ import annotations

import json
import re

import pytest

from coach import prompt_loader as pl
from coach.checks import L1_PATTERN_CODES, arabic_note_problems, sanitize_analysis
from coach.models import Analysis


def worked_examples() -> list[tuple[str, str, dict]]:
    text = pl.analyst_system()
    out = []
    for chunk in text.split("### Example ")[1:]:
        title = chunk.splitlines()[0]
        learner = re.search(r'Learner: "(.+)"\s*$', chunk.split("```")[0], re.M)
        block = re.search(r"```json analysis-example\n(.*?)```", chunk, re.S)
        if learner and block:
            out.append((title, learner.group(1), json.loads(block.group(1))))
    return out


EXAMPLES = worked_examples()


def test_all_worked_examples_were_found():
    assert len(EXAMPLES) >= 7, [t for t, _, _ in EXAMPLES]


@pytest.mark.parametrize("title,learner,data", EXAMPLES, ids=[t[:30] for t, _, _ in EXAMPLES])
def test_example_survives_sanitising_untouched(title, learner, data):
    analysis = Analysis.model_validate(data)
    ids = [u.target_id for u in analysis.used_targets]
    cleaned, notes = sanitize_analysis(analysis, learner, active_target_ids=ids, recent_texts=[])
    assert notes == [], (title, notes)
    assert len(cleaned.findings) == len(analysis.findings)
    for f in analysis.findings:
        assert arabic_note_problems(f.ar_note) == [], (title, f.ar_note)
        if f.l1_transfer:
            assert f.l1_pattern in L1_PATTERN_CODES


def test_the_refusal_example_teaches_direct_first_and_a_restraint_example_exists():
    by_title = {t: (learner, d) for t, learner, d in EXAMPLES}
    refusal = next(d for t, (l, d) in by_title.items() if t.startswith("2:"))
    f = refusal["findings"][0]
    assert f["alternatives"][0]["register"] == "executive" and "I can't take that on" in f["alternatives"][0]["text"]
    assert any(a["register"] == "diplomatic" for a in f["alternatives"][1:])       # the warmer one is an option, not the only fix
    restraint = next(d for t, (l, d) in by_title.items() if t.startswith("6:"))
    assert restraint["findings"] == []


def test_overs_softening_is_a_known_pattern_and_marker_absence_is_not_a_finding():
    assert "OVER_SOFTENING" in L1_PATTERN_CODES
    taxonomy = pl.load_text("_partials/l1_taxonomy.md")
    assert "NEVER flag the absence" in taxonomy and "Almost no" not in taxonomy
    analyst = pl.analyst_system()
    assert "The ABSENCE of" in analyst and "Do not flag" in analyst
