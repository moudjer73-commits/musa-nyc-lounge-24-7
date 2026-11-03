"""S4: policy_engine.py after the shrink. Only provably dead code left; v2 call sites keep working."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import policy_engine

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = [p for p in ROOT.glob("*.py") if p.name != "policy_engine.py"]


def test_the_dead_v2_guide_function_is_gone_and_nothing_references_it():
    assert not hasattr(policy_engine, "get_updated_user_guide_html")
    for path in SOURCE_FILES:
        assert "get_updated_user_guide_html" not in path.read_text(encoding="utf-8"), path.name


def test_everything_the_v2_call_sites_import_is_still_there():
    for path in SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "policy_engine":
                for alias in node.names:
                    assert hasattr(policy_engine, alias.name), f"{path.name} imports missing policy_engine.{alias.name}"


def test_decide_still_has_the_helpers_it_calls():
    tree = ast.parse(Path(policy_engine.__file__).read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name in ("decide", "validate_analyst_output_against_turn", "compute_learner_strain",
                 "should_schedule_micro_workout", "_is_pattern_relevant_to_goals"):
        assert name in defined, name


def test_the_module_says_what_v3_replaced():
    doc = policy_engine.__doc__ or ""
    for token in ("feedback_policy.resolve_interaction_policy", "validation.validate_analysis", "orchestrator"):
        assert token in doc
    assert re.search(r"Do not add new policy logic", doc)
