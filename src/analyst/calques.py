import re
from src.models.analyst import CalqueEntry

# Blueprint 4.3: Seed calque examples (flagged verified_by_native=False until human review;
# "depend from" explicitly tagged cause_status="uncertain").
SEED_CALQUES: list[CalqueEntry] = [
    CalqueEntry(
        pattern_id="calque_close_light",
        non_native_phrase="close the light",
        natural_alternative="turn off the light",
    ),
    CalqueEntry(
        pattern_id="calque_open_light",
        non_native_phrase="open the light",
        natural_alternative="turn on the light",
    ),
    CalqueEntry(
        pattern_id="calque_complete_idea",
        non_native_phrase="complete my idea",
        natural_alternative="finish my thought",
    ),
    CalqueEntry(
        pattern_id="calque_afraid_from",
        non_native_phrase="afraid from",
        natural_alternative="afraid of",
    ),
    CalqueEntry(
        pattern_id="calque_arrive_to",
        non_native_phrase="arrive to",
        natural_alternative="get to / arrive at",
    ),
    CalqueEntry(
        pattern_id="calque_in_same_time",
        non_native_phrase="in the same time",
        natural_alternative="at the same time",
    ),
    CalqueEntry(
        pattern_id="calque_make_discussion",
        non_native_phrase="make a discussion",
        natural_alternative="have a discussion / talk it through",
    ),
    CalqueEntry(
        pattern_id="calque_another_opinion",
        non_native_phrase="i have another opinion",
        natural_alternative="I see it a bit differently / I have a different take",
        error_type="register",
    ),
    CalqueEntry(
        pattern_id="calque_depend_from",
        non_native_phrase="depend from",
        natural_alternative="depend on",
        cause_status="uncertain",
    ),
]


def detect_seed_calques(learner_text: str) -> list[CalqueEntry]:
    lowered = learner_text.lower()
    matches: list[CalqueEntry] = []
    for entry in SEED_CALQUES:
        pattern = r"\b" + re.escape(entry.non_native_phrase.lower()) + r"\b"
        if re.search(pattern, lowered):
            matches.append(entry)
    return matches
