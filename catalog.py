"""catalog.py (S0 section 4.4, owner Claude): the catalog seed model and an idempotent seeder.

``data/catalog_v1.0.0.json`` is validated by ``CatalogSeed`` and upserted into ``phrase_catalog`` and
``competency_subskills``. Rules:

* ``phrase_id`` is a stable slug (``[a-z0-9_]+``), never reused or renamed; the seeder never deletes.
* A row is written only when its stored ``catalog_version`` is LOWER than the seed's (v2 rows carry
  ``'legacy'``, which is lower than any version), so seeding twice changes nothing and a later catalog
  version can refine a row without a manual step.
* ``meta('catalog_version')`` records the highest seeded version.
* A prompt must never contain its own expression, and no seed expression may appear in a prompt file or
  in ``eval/`` (Zero-Bias): ``zero_bias_violations`` is the grep the tests run.
* Every phrase_id already in the live database must exist in the seed: ``missing_live_phrases``.

This module does not ship the seed content. The file is data; it is loaded with ``load_seed``.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas import ContextFamily, LexicalStructureType, RegisterProfile

PHRASE_ID_RE = re.compile(r"^[a-z0-9_]+$")
SUBSKILL_ID_RE = re.compile(r"^P([1-9])\.([0-9]+)$")
COMPETENCY_ID_RE = re.compile(r"^P[1-9]$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){0,2}$")
LEGACY_VERSION = "legacy"
RELEASE_COUNTS_V1 = {"competencies": 7, "subskills": 42, "expressions": 56}  # S0 4.4, enforced by the release test
_SLOT_RE = re.compile(r"\[[^\]]*\]")


def version_key(version: str) -> tuple[int, int, int]:
    """Comparable key; ``'legacy'`` (or anything that is not a version) sorts below every real version."""
    if not VERSION_RE.match(version or ""):
        return (-1, -1, -1)
    parts = [int(p) for p in version.split(".")]
    parts += [0] * (3 - len(parts))
    return (parts[0], parts[1], parts[2])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Competency(_Strict):
    competency_id: str
    name: str = Field(min_length=1)

    @field_validator("competency_id")
    @classmethod
    def _id(cls, value: str) -> str:
        if not COMPETENCY_ID_RE.match(value):
            raise ValueError(f"competency id must look like P1, got {value!r}")
        return value


class Subskill(_Strict):
    subskill_id: str
    competency_id: str
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)

    @model_validator(mode="after")
    def _ids(self) -> "Subskill":
        m = SUBSKILL_ID_RE.match(self.subskill_id)
        if not m:
            raise ValueError(f"subskill id must look like P1.2, got {self.subskill_id!r}")
        if f"P{m.group(1)}" != self.competency_id:
            raise ValueError(f"subskill {self.subskill_id} does not belong to {self.competency_id}")
        return self


class Expression(_Strict):
    phrase_id: str
    canonical_form: str = Field(min_length=1)
    communicative_intention: str = Field(min_length=1)
    construction_pattern: str = Field(min_length=1)
    lexical_structure_type: LexicalStructureType
    subskill_id: str
    register_profiles: list[RegisterProfile] = Field(min_length=1)
    context_families: list[ContextFamily] = Field(min_length=1)
    arabic_gloss: Optional[str] = None
    acceptable_forms: list[str] = Field(default_factory=list)
    meaning_prompt: Optional[str] = None
    situation_prompt: Optional[str] = None
    function_prompt: Optional[str] = None
    applicability: str = "core"
    variety_tags: list[str] = Field(default_factory=lambda: ["general_american"])

    @field_validator("phrase_id")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not PHRASE_ID_RE.match(value):
            raise ValueError(f"phrase_id must match [a-z0-9_]+, got {value!r}")
        return value

    @field_validator("applicability")
    @classmethod
    def _applicability(cls, value: str) -> str:
        if value not in ("core", "situational", "context_limited"):
            raise ValueError(f"applicability must be core, situational or context_limited, got {value!r}")
        return value

    @model_validator(mode="after")
    def _prompts_do_not_leak(self) -> "Expression":
        fixed = [f.strip().lower() for f in _SLOT_RE.split(self.canonical_form) if len(f.split()) >= 2]
        for name in ("meaning_prompt", "situation_prompt", "function_prompt"):
            prompt = (getattr(self, name) or "").lower()
            if any(fragment in prompt for fragment in fixed):
                raise ValueError(f"{name} of {self.phrase_id} contains the expression it is meant to elicit")
        return self


class CatalogSeed(_Strict):
    catalog_version: str
    competencies: list[Competency]
    subskills: list[Subskill]
    expressions: list[Expression]

    @field_validator("catalog_version")
    @classmethod
    def _version(cls, value: str) -> str:
        if not VERSION_RE.match(value):
            raise ValueError(f"catalog_version must look like 1.0.0, got {value!r}")
        return value

    @model_validator(mode="after")
    def _references(self) -> "CatalogSeed":
        competency_ids = [c.competency_id for c in self.competencies]
        subskill_ids = [s.subskill_id for s in self.subskills]
        phrase_ids = [e.phrase_id for e in self.expressions]
        for label, ids in (("competency", competency_ids), ("subskill", subskill_ids), ("phrase", phrase_ids)):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                raise ValueError(f"duplicate {label} ids: {dupes}")
        known = set(competency_ids)
        for s in self.subskills:
            if s.competency_id not in known:
                raise ValueError(f"subskill {s.subskill_id} names unknown competency {s.competency_id}")
        known_subskills = set(subskill_ids)
        for e in self.expressions:
            if e.subskill_id not in known_subskills:
                raise ValueError(f"expression {e.phrase_id} names unknown subskill {e.subskill_id}")
        return self


def load_seed(path: str | Path) -> CatalogSeed:
    return CatalogSeed.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def check_release_counts(seed: CatalogSeed, expected: Mapping[str, int] = RELEASE_COUNTS_V1) -> list[str]:
    """Differences from the S0 4.4 release shape (7 competencies, 42 subskills, 56 expressions)."""
    actual = {"competencies": len(seed.competencies), "subskills": len(seed.subskills), "expressions": len(seed.expressions)}
    return [f"{k}: expected {v}, found {actual[k]}" for k, v in expected.items() if actual[k] != v]


# ------------------------------------------------------------------------------ database


@dataclass(frozen=True)
class SeedReport:
    catalog_version: str
    expressions_written: int
    expressions_skipped: int
    subskills_written: int
    meta_version: str


def seed_catalog(conn: sqlite3.Connection, seed: CatalogSeed) -> SeedReport:
    """Upsert inside the caller's transaction. Idempotent: rows whose stored version is not lower are left alone."""
    target = version_key(seed.catalog_version)
    stored = {
        str(r[0]): str(r[1]) for r in conn.execute("SELECT phrase_id, catalog_version FROM phrase_catalog")
    }
    written = skipped = 0
    for e in seed.expressions:
        if e.phrase_id in stored and not version_key(stored[e.phrase_id]) < target:
            skipped += 1
            continue
        conn.execute(
            """
            INSERT INTO phrase_catalog (
                phrase_id, canonical_form, communicative_intention, construction_pattern,
                register_profiles_json, variety_tags_json, applicability, catalog_version,
                lexical_structure_type, context_families_json, subskill_id, arabic_gloss,
                acceptable_forms_json, meaning_prompt, situation_prompt, function_prompt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(phrase_id) DO UPDATE SET
                canonical_form = excluded.canonical_form,
                communicative_intention = excluded.communicative_intention,
                construction_pattern = excluded.construction_pattern,
                register_profiles_json = excluded.register_profiles_json,
                variety_tags_json = excluded.variety_tags_json,
                applicability = excluded.applicability,
                catalog_version = excluded.catalog_version,
                lexical_structure_type = excluded.lexical_structure_type,
                context_families_json = excluded.context_families_json,
                subskill_id = excluded.subskill_id,
                arabic_gloss = excluded.arabic_gloss,
                acceptable_forms_json = excluded.acceptable_forms_json,
                meaning_prompt = excluded.meaning_prompt,
                situation_prompt = excluded.situation_prompt,
                function_prompt = excluded.function_prompt
            """,
            (
                e.phrase_id, e.canonical_form, e.communicative_intention, e.construction_pattern,
                json.dumps([p.value for p in e.register_profiles]), json.dumps(e.variety_tags), e.applicability,
                seed.catalog_version, e.lexical_structure_type.value, json.dumps([f.value for f in e.context_families]),
                e.subskill_id, e.arabic_gloss, json.dumps(e.acceptable_forms), e.meaning_prompt, e.situation_prompt,
                e.function_prompt,
            ),
        )
        written += 1

    stored_sub = {str(r[0]): str(r[1]) for r in conn.execute("SELECT subskill_id, catalog_version FROM competency_subskills")}
    sub_written = 0
    for s in seed.subskills:
        if s.subskill_id in stored_sub and not version_key(stored_sub[s.subskill_id]) < target:
            continue
        conn.execute(
            """
            INSERT INTO competency_subskills (subskill_id, competency_id, name, description, catalog_version)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(subskill_id) DO UPDATE SET
                competency_id = excluded.competency_id, name = excluded.name,
                description = excluded.description, catalog_version = excluded.catalog_version
            """,
            (s.subskill_id, s.competency_id, s.name, s.description, seed.catalog_version),
        )
        sub_written += 1

    row = conn.execute("SELECT value FROM meta WHERE key = 'catalog_version'").fetchone()
    current = str(row[0]) if row else LEGACY_VERSION
    meta_version = seed.catalog_version if version_key(current) < target else current
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('catalog_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (meta_version,),
    )
    return SeedReport(seed.catalog_version, written, skipped, sub_written, meta_version)


def missing_live_phrases(seed: CatalogSeed, live_phrase_ids: Iterable[str]) -> list[str]:
    """Live phrase ids the seed does not contain (they would be orphaned from the catalog's structure)."""
    have = {e.phrase_id for e in seed.expressions}
    return sorted(set(live_phrase_ids) - have)


# ------------------------------------------------------------------------------ Zero-Bias


def _fragments(canonical_form: str) -> list[str]:
    """The fixed parts of an expression (slots removed), lower-cased; one-word fragments are not checked."""
    out = []
    for part in _SLOT_RE.split(canonical_form):
        words = re.sub(r"[^a-z0-9' ]+", " ", part.lower().replace("’", "'")).split()
        if len(words) >= 2:
            out.append(" ".join(words))
    return out


def zero_bias_violations(canonical_forms: Iterable[str], texts: Mapping[str, str]) -> list[tuple[str, str]]:
    """(path, expression) pairs where a seed expression appears in a prompt or eval file."""
    hits: list[tuple[str, str]] = []
    cleaned = {
        path: " ".join(re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("’", "'")).split())
        for path, text in texts.items()
    }
    for form in canonical_forms:
        for fragment in _fragments(form):
            for path, text in cleaned.items():
                if fragment in text:
                    hits.append((path, form))
    return sorted(set(hits))


def read_zero_bias_files(root: str | Path) -> dict[str, str]:
    """Every prompt file and eval file under ``root`` (prompts/ and eval/)."""
    base = Path(root)
    found: dict[str, str] = {}
    for folder, suffixes in (("prompts", {".md", ".txt", ".json"}), ("eval", {".json", ".md", ".txt", ".jsonl"})):
        for path in sorted((base / folder).rglob("*")) if (base / folder).exists() else []:
            if path.is_file() and path.suffix in suffixes:
                found[str(path.relative_to(base))] = path.read_text(encoding="utf-8", errors="replace")
    return found
