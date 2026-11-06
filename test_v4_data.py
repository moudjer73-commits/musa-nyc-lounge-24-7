import v4_data as D

def test_v4_counts_and_rubrics():
    assert D.ROOMS == ("casual", "diplomatic", "executive", "analytical")
    assert sum(len(v) for v in D.DILEMMAS.values()) == 18
    assert sum(len(v) for v in D.SCENE_REGISTRY.values()) == 12
    assert sum(len(v) for v in D.PIVOTS.values()) == 32
    assert len(D.DRIFT_EXAMPLES) == 8
    assert len(D.REPLAY_INTENTIONS) == 6
    for room, weights in D.SCORING_WEIGHTS.items():
        assert sum(weights.values()) == 100, room
