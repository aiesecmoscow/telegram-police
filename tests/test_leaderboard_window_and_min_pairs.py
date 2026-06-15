"""
Tests for the leaderboard min-pairs threshold helper.

The 7-day windowing itself is now enforced at the Telegram layer via
`client.get_messages(..., offset_date=window_start)` in
`calculate_chat_avg_response_time` — no pure helper to unit-test.
Here we cover the threshold logic that stays in pure code.
"""

import pytest

from response_time import filter_by_min_pairs


def test_min_pairs_drops_under_threshold():
    """
    Chats with pairs_count < min_pairs are dropped. This is the guard
    against the bug where a chat with 1 pair and avg=0.1h outranks a
    chat with 20 pairs and avg=0.5h.
    """
    leaderboard = [
        ("chat_a", 0.1, 1),  # 1 pair — should be dropped at threshold 3
        ("chat_b", 0.5, 5),  # 5 pairs — kept
        ("chat_c", 2.0, 10), # 10 pairs — kept
    ]
    kept, excluded = filter_by_min_pairs(leaderboard, min_pairs=3)
    assert excluded == 1
    assert [name for name, _, _ in kept] == ["chat_b", "chat_c"]


def test_min_pairs_inclusive_at_boundary():
    """
    Threshold is inclusive: a chat with EXACTLY min_pairs pairs is kept.
    This is the documented behaviour (>= min_pairs).
    """
    leaderboard = [("chat_a", 0.5, 3)]
    kept, excluded = filter_by_min_pairs(leaderboard, min_pairs=3)
    assert excluded == 0
    assert kept == [("chat_a", 0.5, 3)]


def test_min_pairs_zero_keeps_everything():
    """A threshold of 0 is a no-op — every chat is kept."""
    leaderboard = [("a", 0.1, 1), ("b", 0.5, 2)]
    kept, excluded = filter_by_min_pairs(leaderboard, min_pairs=0)
    assert excluded == 0
    assert kept == leaderboard


def test_min_pairs_with_empty_leaderboard():
    """No chats at all — no exclusions, empty result."""
    kept, excluded = filter_by_min_pairs([], min_pairs=3)
    assert kept == []
    assert excluded == 0


def test_min_pairs_excludes_all_when_all_below_threshold():
    """Every chat is below the threshold — all are excluded, kept is empty."""
    leaderboard = [("a", 0.1, 1), ("b", 0.2, 2)]
    kept, excluded = filter_by_min_pairs(leaderboard, min_pairs=3)
    assert kept == []
    assert excluded == 2


def test_min_pairs_preserves_input_order():
    """
    The helper does NOT sort — that's the caller's job (sort by avg_hours
    for the actual leaderboard). Order here is whatever the caller
    supplied, which lets the caller sort first and then filter.
    """
    leaderboard = [
        ("slow",   5.0, 4),
        ("fast",   0.5, 3),
        ("medium", 2.0, 10),
    ]
    kept, _ = filter_by_min_pairs(leaderboard, min_pairs=3)
    assert [name for name, _, _ in kept] == ["slow", "fast", "medium"]


def test_empty_leaderboard_after_filtering_yields_empty_for_reporting():
    """
    When filtering produces an empty list, the report function receives
    an empty list and renders the explicit "not enough data" branch.
    This test documents the contract: kept==[] is the trigger for that branch.
    """
    raw = [("a", 0.1, 1), ("b", 0.2, 2)]  # both below threshold
    kept, excluded = filter_by_min_pairs(raw, min_pairs=3)
    assert kept == []
    assert excluded == 2
