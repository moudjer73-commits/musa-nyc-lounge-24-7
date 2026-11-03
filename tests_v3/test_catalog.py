"""S3: catalog.py (S0 4.4): the seed model, the idempotent seeder, Zero-Bias, and the seed-catalog command."""

from __future__ import annotations

import json
import sqlite3

import pytest
from pydantic import ValidationError

import database
from catalog import (
    CatalogSeed,
    Competency,
    Subskill,
    check_release_counts,
    load_seed,
    missing_live_phrases,
    read_zero_bias_files,
    version_key,
    zero_bias_violations,
)
from database import EventSourcedDatabase
from helpers_s3 import expression, tiny_seed


def full_seed(version: str = "1.0.0", prefix: str = "ex") -> CatalogSeed:
    """The release shape of S0 4.4: 7 competencies, 42 subskills, 56 expressions (synthetic content)."""
    competencies = [Competency(competency_id=f"P{c}", name=f"Competency {c}") for c in range(1, 8)]
    subskills = [
        Subskill(subskill_id=f"P{c}.{n}", competency_id=f"P{c}", name=f"Subskill {c}.{n}", description="d")
        for c in range(1, 8)
        for n in range(1, 7)
    ]
    expressions = [
        expression(f"{prefix}_{i:02d}", f"zorp {i:02d} blent", subskill_id=f"P{i % 7 + 1}.{i % 6 + 1}") for i in range(56)
    ]
    return CatalogSeed(catalog_version=version, competencies=competencies, subskills=subskills, expressions=expressions)


def catalog_rows(db: EventSourcedDatabase) -> dict[str, sqlite3.Row]:
    with db.read() as conn:
        return {str(r["phrase_id"]): r for r in conn.execute("SELECT * FROM phrase_catalog")}


# ------------------------------------------------------------------------------ versions


def test_version_key_orders_legacy_below_every_real_version():
    keys = [version_key(v) for v in ("legacy", "0.9", "1", "1.0.1", "1.1", "2.0.0")]
    assert keys == sorted(keys) and len(set(keys)) == len(keys)
    assert version_key("1.0") == version_key("1.0.0") == version_key("1")
    assert version_key("") == version_key("garbage") == version_key("legacy")


# ------------------------------------------------------------------------------ the model


def test_a_valid_seed_loads_from_a_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(tiny_seed().model_dump_json(), encoding="utf-8")
    seed = load_seed(path)
    assert seed.catalog_version == "1.0.0" and [e.phrase_id for e in seed.expressions] == ["come_to_think_of_it"]


def test_the_seed_rejects_unknown_fields_and_bad_versions():
    data = json.loads(tiny_seed().model_dump_json())
    with pytest.raises(ValidationError):
        CatalogSeed.model_validate({**data, "surprise": 1})
    with pytest.raises(ValidationError):
        CatalogSeed.model_validate({**data, "catalog_version": "v1"})


@pytest.mark.parametrize("bad_id", ["Come To", "has-dash", "UPPER", "", "two words"])
def test_phrase_ids_are_lowercase_slugs(bad_id):
    with pytest.raises(ValidationError):
        expression(bad_id)


def test_duplicate_ids_and_dangling_references_are_rejected():
    ok = expression("alpha_one", "alpha one two")
    with pytest.raises(ValidationError, match="duplicate phrase"):
        tiny_seed(ok, ok)
    with pytest.raises(ValidationError, match="unknown subskill"):
        tiny_seed(expression("beta_one", "beta one two", subskill_id="P1.9"))
    with pytest.raises(ValidationError, match="unknown competency"):
        CatalogSeed(
            catalog_version="1.0.0",
            competencies=[Competency(competency_id="P1", name="n")],
            subskills=[Subskill(subskill_id="P2.1", competency_id="P2", name="n", description="d")],
            expressions=[],
        )
    with pytest.raises(ValidationError):
        Subskill(subskill_id="P2.1", competency_id="P1", name="n", description="d")  # belongs to P2, not P1


def test_a_prompt_may_not_contain_the_expression_it_elicits():
    with pytest.raises(ValidationError, match="contains the expression"):
        expression("leaky", "come to think of it", meaning_prompt="Start with come to think of it, then add your point.")
    # the fixed part of a frame counts (two or more words); a slot never does
    with pytest.raises(ValidationError, match="contains the expression"):
        expression("leaky_frame", "I'm not entirely sold on [that]", function_prompt="Say that I'm not entirely sold on the plan.")
    expression("fine_frame", "I'm not entirely sold on [that]", function_prompt="Politely show that you have doubts about a plan.")
    expression("single_words", "run [something] by [someone]", function_prompt="Ask a colleague to run it by you first.")  # one-word fragments are not checked


def test_applicability_is_checked():
    with pytest.raises(ValidationError):
        expression("odd", "odd form here", applicability="sometimes")


# ------------------------------------------------------------------------------ release counts


def test_release_counts_are_7_42_56():
    assert check_release_counts(full_seed()) == []
    problems = check_release_counts(tiny_seed())
    assert len(problems) == 3 and "competencies: expected 7, found 1" in problems[0]


# ------------------------------------------------------------------------------ the seeder


def test_seeding_writes_the_v3_columns_and_the_meta_version(db):
    seed = tiny_seed(expression("come_to_think_of_it", acceptable_forms=["now that I think about it"]))
    report = db.seed_catalog(seed)
    assert (report.expressions_written, report.expressions_skipped, report.subskills_written) == (1, 0, 1)
    assert report.meta_version == "1.0.0"
    row = catalog_rows(db)["come_to_think_of_it"]
    assert row["catalog_version"] == "1.0.0" and row["subskill_id"] == "P1.1" and row["lexical_structure_type"] == "fixed_chunk"
    assert json.loads(row["context_families_json"]) == ["workplace", "friendships", "general"]
    assert json.loads(row["acceptable_forms_json"]) == ["now that I think about it"]
    assert row["arabic_gloss"] and row["meaning_prompt"] and row["situation_prompt"] and row["function_prompt"]
    with db.read() as conn:
        assert conn.execute("SELECT value FROM meta WHERE key = 'catalog_version'").fetchone()[0] == "1.0.0"
        assert conn.execute("SELECT COUNT(*) FROM competency_subskills").fetchone()[0] == 1


def test_seeding_twice_changes_nothing(db):
    seed = tiny_seed()
    db.seed_catalog(seed)
    with db.read() as conn:
        before = [tuple(r) for r in conn.execute("SELECT * FROM phrase_catalog ORDER BY phrase_id")]
    second = db.seed_catalog(seed)
    assert (second.expressions_written, second.expressions_skipped, second.subskills_written) == (0, 1, 0)
    with db.read() as conn:
        assert [tuple(r) for r in conn.execute("SELECT * FROM phrase_catalog ORDER BY phrase_id")] == before


def test_a_v2_legacy_row_is_upgraded_in_place_and_keeps_its_id(db):
    assert catalog_rows(db)["plate_is_full"]["catalog_version"] == "legacy"
    db.seed_catalog(tiny_seed(expression("plate_is_full", "my plate's pretty full", intention="decline_workload")))
    row = catalog_rows(db)["plate_is_full"]
    assert row["catalog_version"] == "1.0.0" and row["subskill_id"] == "P1.1"


def test_a_later_catalog_version_refines_a_row_and_an_older_one_never_downgrades(db):
    db.seed_catalog(tiny_seed(expression("alpha_one", "alpha one two", arabic_gloss="old"), version="1.0.0"))
    db.seed_catalog(tiny_seed(expression("alpha_one", "alpha one two", arabic_gloss="new"), version="1.1.0"))
    assert catalog_rows(db)["alpha_one"]["arabic_gloss"] == "new"
    report = db.seed_catalog(tiny_seed(expression("alpha_one", "alpha one two", arabic_gloss="older"), version="1.0.5"))
    assert report.expressions_written == 0 and report.meta_version == "1.1.0"
    assert catalog_rows(db)["alpha_one"]["arabic_gloss"] == "new"


def test_the_seeder_never_deletes_a_phrase_that_is_not_in_the_seed(db):
    db.seed_catalog(tiny_seed(expression("alpha_one", "alpha one two")))
    db.seed_catalog(tiny_seed(expression("beta_one", "beta one two"), version="1.1.0"))
    assert {"alpha_one", "beta_one", "plate_is_full"} <= set(catalog_rows(db))


def test_seeding_rolls_back_whole_on_a_bad_row(db):
    seed = tiny_seed(expression("alpha_one", "alpha one two"))
    bad = seed.model_copy(update={"expressions": [*seed.expressions, seed.expressions[0].model_copy(update={"subskill_id": "P1.1", "lexical_structure_type": None})]})
    with pytest.raises(Exception):
        db.seed_catalog(bad)
    assert "alpha_one" not in catalog_rows(db)


def test_register_phrase_does_not_wipe_the_catalog_columns_after_seeding(db):
    db.seed_catalog(tiny_seed(expression("alpha_one", "alpha one two")))
    db.register_phrase("alpha_one", "alpha one two", "add_an_afterthought", "discourse_marker", ["casual"], "core")
    row = catalog_rows(db)["alpha_one"]
    assert row["catalog_version"] == "1.0.0" and row["arabic_gloss"]


def test_get_phrase_entry_reads_the_catalog_for_retrieval(db):
    db.seed_catalog(tiny_seed(expression("alpha_one", "alpha one two", acceptable_forms=["one alpha two"])))
    entry = db.get_phrase_entry("alpha_one")
    assert entry.canonical_form == "alpha one two" and entry.acceptable_forms == ("one alpha two",)
    assert entry.context_families == ("workplace", "friendships", "general")
    assert entry.meaning_prompt and entry.arabic_gloss
    legacy = db.get_phrase_entry("plate_is_full")
    assert legacy.acceptable_forms == () and legacy.context_families == ("general",) and legacy.meaning_prompt is None
    assert db.get_phrase_entry("nope") is None


# ------------------------------------------------------------------------------ live phrases and Zero-Bias


def test_missing_live_phrases_lists_ids_the_seed_does_not_cover():
    seed = tiny_seed(expression("alpha_one", "alpha one two"))
    assert missing_live_phrases(seed, ["alpha_one", "plate_is_full", "cuts_both_ways"]) == ["cuts_both_ways", "plate_is_full"]
    assert missing_live_phrases(seed, ["alpha_one"]) == []


def test_zero_bias_finds_an_expression_in_a_prompt_or_eval_file():
    forms = ["come to think of it", "run [something] by [someone]", "gonna"]
    texts = {
        "prompts/a.md": "Say things like: Come to think of it, ...",
        "prompts/b.md": "Ask them to run it by you? no, 'run' and 'by' alone are fine",
        "eval/c.json": '{"x": "nothing here"}',
    }
    hits = zero_bias_violations(forms, texts)
    assert hits == [("prompts/a.md", "come to think of it")]


def test_zero_bias_ignores_apostrophe_and_case_differences_and_one_word_fragments():
    texts = {"prompts/a.md": "I SEE WHERE YOU’RE COMING FROM", "prompts/b.md": "gonna"}
    assert zero_bias_violations(["I see where you're coming from", "gonna"], texts) == [("prompts/a.md", "I see where you're coming from")]


def test_read_zero_bias_files_reads_prompts_and_eval_only(tmp_path):
    (tmp_path / "prompts" / "_partials").mkdir(parents=True)
    (tmp_path / "eval").mkdir()
    (tmp_path / "other").mkdir()
    (tmp_path / "prompts" / "a.md").write_text("A", encoding="utf-8")
    (tmp_path / "prompts" / "_partials" / "b.txt").write_text("B", encoding="utf-8")
    (tmp_path / "prompts" / "skip.bin").write_bytes(b"x")
    (tmp_path / "eval" / "c.jsonl").write_text("C", encoding="utf-8")
    (tmp_path / "other" / "d.md").write_text("D", encoding="utf-8")
    assert read_zero_bias_files(tmp_path) == {"prompts/a.md": "A", "prompts/_partials/b.txt": "B", "eval/c.jsonl": "C"}
    assert read_zero_bias_files(tmp_path / "nowhere") == {}


# ------------------------------------------------------------------------------ the command


def write_seed(tmp_path, seed):
    path = tmp_path / "catalog_v1.0.0.json"
    path.write_text(seed.model_dump_json(), encoding="utf-8")
    return str(path)


def test_seed_catalog_check_validates_without_writing(tmp_path, capsys):
    db_file = str(tmp_path / "lab.db")
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", write_seed(tmp_path, full_seed()), "--check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"catalog_version": "1.0.0", "problems": []}
    assert not (tmp_path / "lab.db").exists()


def test_seed_catalog_check_reports_wrong_counts(tmp_path, capsys):
    assert database.main(["seed-catalog", "--db-path", str(tmp_path / "x.db"), "--file", write_seed(tmp_path, tiny_seed()), "--check"]) == 1
    assert len(json.loads(capsys.readouterr().out)["problems"]) == 3


def test_seed_catalog_writes_the_release_and_a_second_run_is_a_no_op(tmp_path, capsys):
    db_file = str(tmp_path / "lab.db")
    path = write_seed(tmp_path, full_seed())
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", path]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["expressions_written"] == 56 and first["subskills_written"] == 42 and first["meta_version"] == "1.0.0"
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", path]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["expressions_written"] == 0 and second["expressions_skipped"] == 56


def test_seed_catalog_refuses_a_file_with_the_wrong_counts_and_a_missing_file(tmp_path, capsys):
    db_file = str(tmp_path / "lab.db")
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", write_seed(tmp_path, tiny_seed())]) == 1
    assert "release counts differ" in capsys.readouterr().err
    assert database.main(["seed-catalog", "--db-path", db_file, "--file", str(tmp_path / "missing.json")]) == 1
    assert "seed-catalog failed" in capsys.readouterr().err
