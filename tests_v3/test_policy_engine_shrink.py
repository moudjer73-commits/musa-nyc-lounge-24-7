"""S4/S5: policy_engine.py after the shrink. The superseded v2 policy is gone; nothing may still reference it."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import dialogue
import policy_engine

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = [p for p in ROOT.glob("*.py") if p.name != "policy_engine.py"]
REMOVED = (
    "decide",
    "validate_analyst_output_against_turn",
    "DialogueController",
    "should_schedule_micro_workout",
    "_is_pattern_relevant_to_goals",
    "set_auto_voice_pref",
    "get_auto_voice_pref",
    "get_updated_user_guide_html",
)


def test_the_superseded_v2_policy_is_gone():
    for name in REMOVED:
        assert not hasattr(policy_engine, name), name


def test_nothing_in_the_source_still_references_the_removed_names():
    gone = {"validate_analyst_output_against_turn", "get_auto_voice_pref", "set_auto_voice_pref", "get_updated_user_guide_html"}
    for path in SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        used |= {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
        assert not (gone & used), (path.name, gone & used)


def test_everything_the_call_sites_import_is_still_there():
    for path in SOURCE_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "policy_engine":
                for alias in node.names:
                    assert hasattr(policy_engine, alias.name), f"{path.name} imports missing policy_engine.{alias.name}"


def test_the_dialogue_controller_moved_unchanged_to_dialogue_py():
    for name in ("strip_quoted_speech", "count_genuine_questions", "select_dialogue_decision"):
        assert hasattr(dialogue.DialogueController, name), name
    assert dialogue.DialogueController.count_genuine_questions("I don't think that'll work. Why?") == 1


def test_what_the_orchestrator_still_uses_is_defined():
    tree = ast.parse(Path(policy_engine.__file__).read_text(encoding="utf-8"))
    defined = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    for name in ("compute_learner_strain", "extract_recent_partner_openers", "validate_responder_output",
                 "extract_alex_spoken_text", "register_workout_task", "get_active_workout_task", "start_decode_session"):
        assert name in defined, name


def test_the_module_says_what_v3_replaced():
    doc = policy_engine.__doc__ or ""
    for token in ("feedback_policy.resolve_interaction_policy", "validation.validate_analysis", "dialogue.py", "learner_settings"):
        assert token in doc
    assert re.search(r"Do not add new policy logic", doc)
