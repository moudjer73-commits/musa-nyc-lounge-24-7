import json
from pathlib import Path
from src.config import DATA_DIR
from src.models.listening_podcast import ListeningLevel, ListeningTaskItem, ListeningTaskType

LEVEL_SPECS: dict[ListeningLevel, dict[str, str | int]] = {
    "L1": {"wpm": 150, "features": "clear spontaneous speech; contractions, flaps, glottal stops"},
    "L2": {"wpm": 185, "features": "fast and elliptical; elision, assimilation, dropped subjects"},
    "L3": {"wpm": 195, "features": "multi-speaker overlaps, backchannels, false starts, self-repair, pitch subtext"},
    "L4": {"wpm": 205, "features": "regional and social variation, professional shorthand"},
}

LEVEL_ORDER: tuple[ListeningLevel, ...] = ("L1", "L2", "L3", "L4")


class ListeningLadderEngine:
    def __init__(self, data_path: Path = DATA_DIR / "listening_and_curated.json") -> None:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
        self.tasks: list[ListeningTaskItem] = [
            ListeningTaskItem.model_validate(item) for item in payload.get("tasks", [])
        ]
        self.curated_links: list[dict] = payload.get("curated_links", [])

    def get_level_spec(self, level: ListeningLevel) -> dict[str, str | int]:
        return LEVEL_SPECS[level]

    def get_tasks_for_level(
        self, level: ListeningLevel, task_type: ListeningTaskType | None = None
    ) -> list[ListeningTaskItem]:
        items = [t for t in self.tasks if t.level == level]
        if task_type:
            items = [t for t in items if t.task_type == task_type]
        return items or self.tasks[:1]

    def evaluate_task_response(self, task: ListeningTaskItem, learner_answer: str) -> tuple[bool, float]:
        norm_expected = [w.strip(".,?!").lower() for w in task.expected_answer.split() if len(w) > 2]
        norm_learner = learner_answer.lower()
        if not norm_expected:
            return True, 1.0
        hits = sum(1 for word in norm_expected if word in norm_learner)
        ratio = round(hits / len(norm_expected), 2)
        return (ratio >= 0.6), ratio

    def compute_next_listening_level(
        self, current_level: ListeningLevel, recent_accuracies: list[float]
    ) -> ListeningLevel:
        if len(recent_accuracies) < 3:
            return current_level
        avg = sum(recent_accuracies[-3:]) / 3.0
        idx = LEVEL_ORDER.index(current_level)
        if avg >= 0.80 and idx < len(LEVEL_ORDER) - 1:
            return LEVEL_ORDER[idx + 1]
        return current_level
