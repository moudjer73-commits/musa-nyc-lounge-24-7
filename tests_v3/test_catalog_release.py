"""S4: the shipped ``data/catalog_v1.0.0.json`` (S0 4.4): release shape, live phrases, prompts, Zero-Bias, seeding."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

import database
import validation
from catalog import (
    CatalogSeed,
    check_release_counts,
    load_seed,
    missing_live_phrases,
    read_zero_bias_files,
    zero_bias_violations,
)
from retrieval import build_hint, build_prompt

ROOT = Path(__file__).resolve().parents[1]
CATALOG_FILE = ROOT / "data" / "catalog_v1.0.0.json"
LIVE = {row[0]: row for row in database.DEFAULT_CURATED_PHRASES}
_SLOT = re.compile(r"\[[^\]]*\]")


@pytest.fixture(scope="module")
def seed() -> CatalogSeed:
    return load_seed(CATALOG_FILE)


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'").lower()).strip()


def all_forms(entry) -> list[str]:
    return [entry.canonical_form, *entry.acceptable_forms]


# ------------------------------------------------------------------------------ shape


def test_the_release_has_the_s0_shape_and_version(seed):
    assert check_release_counts(seed) == []
    assert seed.catalog_version == "1.0.0"
    assert (len(seed.competencies), len(seed.subskills), len(seed.expressions)) == (7, 42, 56)


def test_every_competency_has_six_subskills_and_eight_expressions_and_every_subskill_is_used(seed):
    per_competency = Counter(s.competency_id for s in seed.subskills)
    assert set(per_competency.values()) == {6} and len(per_competency) == 7
    competency_of = {s.subskill_id: s.competency_id for s in seed.subskills}
    assert set(Counter(competency_of[e.subskill_id] for e in seed.expressions).values()) == {8}
    used = {e.subskill_id for e in seed.expressions}
    assert used == set(competency_of), f"subskills without an expression: {sorted(set(competency_of) - used)}"


def test_names_and_descriptions_are_filled(seed):
    assert all(c.name.strip() for c in seed.competencies)
    assert all(s.name.strip() and s.description.strip() for s in seed.subskills)


def test_the_file_is_utf8_json_with_readable_arabic():
    raw = CATALOG_FILE.read_text(encoding="utf-8")
    assert json.loads(raw)["catalog_version"] == "1.0.0"
    assert "\\u06" not in raw  # Arabic is written as characters, not escapes, so a human can read the diff


# ------------------------------------------------------------------------------ live phrases


def test_all_eight_live_phrase_ids_are_present(seed):
    assert len(LIVE) == 8
    assert missing_live_phrases(seed, LIVE) == []


def test_live_phrases_keep_their_v2_form_register_profiles_and_applicability(seed):
    by_id = {e.phrase_id: e for e in seed.expressions}
    for phrase_id, (_, form, intention, _pattern, registers, applicability) in LIVE.items():
        e = by_id[phrase_id]
        assert e.canonical_form == form, phrase_id
        assert [r.value for r in e.register_profiles] == registers, phrase_id
        assert e.applicability == applicability, phrase_id


# ------------------------------------------------------------------------------ content quality


def test_no_two_expressions_share_a_form(seed):
    seen: dict[str, str] = {}
    for e in seed.expressions:
        for form in all_forms(e):
            key = norm(form)
            assert key not in seen, f"{form!r} is used by {seen[key]} and {e.phrase_id}"
            seen[key] = e.phrase_id


def test_every_expression_has_two_non_general_families_so_the_stable_gate_can_be_reached(seed):
    for e in seed.expressions:
        non_general = {f.value for f in e.context_families if f.value != "general"}
        assert len(non_general) >= 2, e.phrase_id
        assert len(e.register_profiles) >= 1


def test_every_expression_has_an_arabic_gloss_with_arabic_letters_and_no_false_frame(seed):
    for e in seed.expressions:
        assert e.arabic_gloss and re.search("[ء-ي]", e.arabic_gloss), e.phrase_id
        assert not validation.has_false_arabic_frame(e.arabic_gloss), e.phrase_id
        assert validation.arabic_note_problems(e.arabic_gloss) == [], (e.phrase_id, validation.arabic_note_problems(e.arabic_gloss))


def test_every_expression_has_three_distinct_prompts_and_a_gloss_hint(seed):
    for e in seed.expressions:
        prompts = [e.meaning_prompt, e.situation_prompt, e.function_prompt]
        assert all(p and p.strip() for p in prompts), e.phrase_id
        assert len({norm(p) for p in prompts}) == 3, e.phrase_id
        assert all("[" not in p and "{" not in p for p in prompts), e.phrase_id
        assert all(p.rstrip().endswith(("?", ".")) for p in prompts), e.phrase_id


def test_no_prompt_contains_any_form_of_its_own_expression(seed):
    for e in seed.expressions:
        for kind, prompt in (("meaning", e.meaning_prompt), ("situation", e.situation_prompt), ("function", e.function_prompt)):
            assert zero_bias_violations(all_forms(e), {f"{e.phrase_id}.{kind}": prompt}) == [], (e.phrase_id, kind)


def test_no_prompt_gives_away_another_expression(seed):
    """A prompt for one phrase must not hand the learner a different catalog phrase either."""
    for e in seed.expressions:
        others = [f for o in seed.expressions if o.phrase_id != e.phrase_id for f in all_forms(o)]
        texts = {f"{e.phrase_id}.{k}": v for k, v in (("meaning", e.meaning_prompt), ("situation", e.situation_prompt), ("function", e.function_prompt))}
        assert zero_bias_violations(others, texts) == [], e.phrase_id


def test_built_prompts_and_hints_come_from_the_catalog_text(seed):
    e = seed.expressions[0]
    from retrieval import PhraseEntry

    entry = PhraseEntry(e.phrase_id, e.canonical_form, e.communicative_intention, e.arabic_gloss, tuple(e.acceptable_forms),
                        e.meaning_prompt, e.situation_prompt, e.function_prompt, tuple(f.value for f in e.context_families))
    assert build_prompt(entry, "meaning") == e.meaning_prompt.strip()
    assert build_hint(entry, 2) == e.arabic_gloss.strip()  # S5 ladder: 1 = structure, 2 = Arabic gloss
    assert build_hint(entry, 1) != e.arabic_gloss.strip() and "_" in build_hint(entry, 1)


# ------------------------------------------------------------------------------ Zero-Bias


def test_zero_bias_is_clean_for_every_canonical_and_acceptable_form(seed):
    files = read_zero_bias_files(ROOT)
    assert files, "prompts/ and eval/ were not found"
    forms = [f for e in seed.expressions for f in all_forms(e)]
    assert zero_bias_violations(forms, files) == []


def test_zero_bias_reads_the_v3_prompts_too():
    files = read_zero_bias_files(ROOT)
    assert "prompts/analyst_v3_system.md" in files and "prompts/retrieval_judge_system.md" in files


# ------------------------------------------------------------------------------ seeding the real file


def test_seeding_the_release_is_idempotent_and_keeps_live_ids(db, seed):
    first = db.seed_catalog(seed)
    assert (first.expressions_written, first.subskills_written, first.meta_version) == (56, 42, "1.0.0")
    with db.read() as conn:
        rows = {r["phrase_id"]: r for r in conn.execute("SELECT * FROM phrase_catalog")}
        before = [tuple(r) for r in conn.execute("SELECT * FROM phrase_catalog ORDER BY phrase_id")]
        assert conn.execute("SELECT COUNT(*) FROM competency_subskills").fetchone()[0] == 42
    assert set(LIVE) <= set(rows) and len(rows) == 56
    assert all(rows[i]["catalog_version"] == "1.0.0" for i in LIVE)
    second = db.seed_catalog(seed)
    assert (second.expressions_written, second.expressions_skipped, second.subskills_written) == (0, 56, 0)
    with db.read() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM phrase_catalog ORDER BY phrase_id")] == before


def test_retrieval_can_read_every_seeded_entry(db, seed):
    db.seed_catalog(seed)
    for e in seed.expressions:
        entry = db.get_phrase_entry(e.phrase_id)
        assert entry is not None and entry.canonical_form == e.canonical_form
        assert entry.meaning_prompt and entry.situation_prompt and entry.function_prompt and entry.arabic_gloss
        assert len([f for f in entry.context_families if f != "general"]) >= 2


def test_the_seed_catalog_command_accepts_the_shipped_file(tmp_path, capsys):
    db_file = str(tmp_path / "lab.db")
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", str(CATALOG_FILE), "--check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"catalog_version": "1.0.0", "problems": []}
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", str(CATALOG_FILE)]) == 0
    assert json.loads(capsys.readouterr().out)["expressions_written"] == 56
