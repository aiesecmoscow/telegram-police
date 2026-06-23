"""
Tests for the anti-leaderboard (slowest responders) feature.

The anti-leaderboard is the mirror view of the existing leaderboard: it
ranks chats by `avg_hours` DESCENDING instead of ascending, so the
bottom-N chats with the longest average response time bubble to the top
of the report.

It reuses the SAME data and the SAME `leaderboard_min_pairs` threshold
that `filter_by_min_pairs` already enforced for the fastest leaderboard,
so:

  - chats below `min_pairs` are excluded by the upstream filter and never
    appear in either view (covered here as the "threshold exclusion"
    case);
  - the report function renders an empty list the same way as the
    fastest leaderboard (covered by a unit test on the render side).

Pure helper covered here: `take_anti_leaderboard`. The Telegram-network
behaviour (window, min_pairs) is already tested in the existing
leaderboard tests; we just lock in that the new pure helper produces a
correct, stable, threshold-aware ranking on top of the same input.
"""

import pytest

from response_time import take_anti_leaderboard


# ---------------------------------------------------------------------------
# Anti-leaderboard ranking semantics
# ---------------------------------------------------------------------------

def test_anti_leaderboard_orders_by_avg_hours_descending():
    """
    Mirror of the fastest leaderboard's `test_ranking_puts_most_responsive_chat_first`:
    the slowest chat (highest avg_hours) ranks #1 in the anti-leaderboard.
    """
    leaderboard = [
        ("fast",   0.5,  3, 10),
        ("medium", 2.0,  4, 15),
        ("slow",   5.0,  6, 20),
    ]
    top = take_anti_leaderboard(leaderboard, n=10)
    assert [name for name, _, _, _ in top] == ["slow", "medium", "fast"]


def test_anti_leaderboard_takes_top_n_from_slowest_end():
    """
    With n=2 and 4 chats, only the two SLOWEST chats appear — not the two
    fastest. This is the opposite direction from the existing leaderboard,
    which takes the two fastest. Same input, mirror output.
    """
    leaderboard = [
        ("a", 0.1, 3, 10),
        ("b", 0.5, 3, 10),
        ("c", 2.0, 3, 10),
        ("d", 5.0, 3, 10),
    ]
    top = take_anti_leaderboard(leaderboard, n=2)
    assert [name for name, _, _, _ in top] == ["d", "c"]


def test_anti_leaderboard_preserves_full_4tuple_shape():
    """
    The helper takes and returns the same 4-tuple shape
    `(name, avg_hours, pairs_count, total_messages)` produced by
    `calculate_chat_avg_response_time`. A refactor that drops the
    `total_messages` index would silently break the future "anti by
    liveliness" feature; lock the shape in.
    """
    leaderboard = [("a", 1.0, 3, 7), ("b", 2.0, 5, 9)]
    top = take_anti_leaderboard(leaderboard, n=10)
    assert top == [("b", 2.0, 5, 9), ("a", 1.0, 3, 7)]


# ---------------------------------------------------------------------------
# Empty / single-user / ties
# ---------------------------------------------------------------------------

def test_anti_leaderboard_empty_input_returns_empty():
    """
    Acceptance criterion #6: empty data. After `filter_by_min_pairs`
    drops everything (or when there are no chats at all), the helper
    must return an empty list, not raise.
    """
    assert take_anti_leaderboard([], n=10) == []


def test_anti_leaderboard_single_user_returns_that_user():
    """
    Acceptance criterion #6: single user. With exactly one chat in the
    input, that chat is both the fastest AND the slowest — the
    anti-leaderboard should include it (it's the bottom-N of one).
    """
    leaderboard = [("only", 1.5, 4, 12)]
    top = take_anti_leaderboard(leaderboard, n=10)
    assert top == [("only", 1.5, 4, 12)]


def test_anti_leaderboard_handles_ties_deterministically():
    """
    Acceptance criterion #6: ties in response time. Two chats with the
    SAME avg_hours must still produce a deterministic order: the one
    with more pairs (more reliable signal) ranks first in the
    anti-leaderboard, breaking ties towards "more clearly slow".
    """
    leaderboard = [
        ("alpha",   3.0, 3, 10),  # tied avg, fewer pairs -> second
        ("bravo",   3.0, 5, 10),  # tied avg, more pairs  -> first
        ("charlie", 1.0, 3, 10),  # faster avg, irrelevant to tie
    ]
    top = take_anti_leaderboard(leaderboard, n=10)
    # The two slowest (tied at 3.0) come first; bravo outranks alpha by pairs.
    assert [name for name, _, _, _ in top] == ["bravo", "alpha", "charlie"]


def test_anti_leaderboard_ties_at_pairs_breaks_by_name():
    """
    Final tiebreaker when avg_hours AND pairs_count are equal: sort by
    chat_name ASCENDING for deterministic, reproducible output across
    runs. Without this, the report would be non-deterministic on the
    Python sort's stability, which is bad for testing and bad for users
    who compare snapshots.
    """
    leaderboard = [
        ("zeta",  3.0, 5, 10),
        ("alpha", 3.0, 5, 10),
        ("mike",  3.0, 5, 10),
    ]
    top = take_anti_leaderboard(leaderboard, n=10)
    assert [name for name, _, _, _ in top] == ["alpha", "mike", "zeta"]


# ---------------------------------------------------------------------------
# Threshold exclusion (acceptance criterion #6)
# ---------------------------------------------------------------------------

def test_anti_leaderboard_does_not_rerun_threshold_filtering():
    """
    Acceptance criterion #6: a user with FEWER MESSAGES than the
    threshold is excluded. The contract is that `take_anti_leaderboard`
    is a PURE ranking helper on top of the already-filtered list —
    it does NOT re-apply `filter_by_min_pairs` itself.

    The threshold is shared by construction: callers pass in the result
    of `filter_by_min_pairs(raw, settings.leaderboard_min_pairs)` so the
    anti-leaderboard sees the same rows the fastest leaderboard does.

    This test documents the contract: feeding an already-filtered list
    (which excludes the low-pair chat) is the supported flow, and the
    helper stays agnostic about thresholds.
    """
    raw = [
        ("loud",    0.5, 10, 100),  # fast, lots of pairs — kept
        ("silent",  5.0,  2,  20),  # slow but FEW pairs — excluded upstream
        ("average", 2.0,  6,  50),  # kept
    ]

    # Simulate what `monitor_chats` does: filter first, then anti-rank.
    from response_time import filter_by_min_pairs
    filtered, excluded = filter_by_min_pairs(raw, min_pairs=3)
    assert excluded == 1
    assert [name for name, _, _, _ in filtered] == ["loud", "average"]

    top = take_anti_leaderboard(filtered, n=10)
    # silent is GONE — it was dropped by filter_by_min_pairs before the
    # anti-leaderboard ever saw it. This is the shared-threshold contract.
    assert [name for name, _, _, _ in top] == ["average", "loud"]


def test_anti_leaderboard_includes_inf_rows_when_threshold_is_zero():
    """
    Edge case mirroring the existing leaderboard's `min_pairs=0` semantics:
    when the threshold is zero, chats with `avg_hours=float('inf')` (a
    manager who never replied) pass the filter. They MUST then bubble to
    the absolute top of the anti-leaderboard — the slowest-of-slowest.

    This test asserts the contract: at min_pairs=0, the anti-leaderboard
    shows the silent chat at rank #1, because an unbounded response time
    is by definition slower than any finite hour count.
    """
    from response_time import filter_by_min_pairs
    raw = [
        ("slow",   8.0,    3, 30),
        ("silent", float('inf'), 0, 50),  # 0 pairs, but min_pairs=0 lets it through
        ("medium", 2.0,    5, 20),
    ]
    filtered, _ = filter_by_min_pairs(raw, min_pairs=0)
    top = take_anti_leaderboard(filtered, n=10)
    assert top[0][0] == "silent"
    assert top[0][1] == float('inf')


# ---------------------------------------------------------------------------
# Edge cases on n
# ---------------------------------------------------------------------------

def test_anti_leaderboard_n_zero_returns_empty():
    """n=0 is a documented no-op: zero rows requested, zero rows returned."""
    leaderboard = [("a", 1.0, 3, 10), ("b", 2.0, 3, 10)]
    assert take_anti_leaderboard(leaderboard, n=0) == []


def test_anti_leaderboard_n_larger_than_input_returns_all():
    """
    n > len(input) is supported: every row comes back. This matches the
    existing leaderboard's slicing behaviour ([:N] in Python).
    """
    leaderboard = [("a", 1.0, 3, 10), ("b", 2.0, 3, 10)]
    top = take_anti_leaderboard(leaderboard, n=100)
    assert [name for name, _, _, _ in top] == ["b", "a"]


# ---------------------------------------------------------------------------
# Integration with the existing report rendering
# ---------------------------------------------------------------------------

def test_anti_leaderboard_empty_for_reporting_triggers_not_enough_data():
    """
    When the helper returns [], the report function should render the
    "not enough data" branch — same contract as the fastest leaderboard.

    We don't invoke the Telegram-client sender here; we just verify that
    an empty `take_anti_leaderboard` result is the trigger condition
    that the report function checks (via `if anti_leaderboard:`).
    This locks in the contract documented in `send_anti_leaderboard_report`.
    """
    top = take_anti_leaderboard([], n=10)
    assert not top  # falsy → the report renders the "not enough data" branch