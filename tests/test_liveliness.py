"""
Tests for the liveliness leaderboard helpers.

The liveliness leaderboard is the second of the two leaderboards produced
when `liveliness` is in `REPORT_TYPES`. It ranks chats by total message
count in the same time window as the speed leaderboard.

Pure helpers covered here:

  - `count_total_messages` — total message count in a chronologically-
    sorted list. This is the entire "liveliness metric": a chat is more
    "alive" if more messages flowed in the window.
  - `filter_by_min_pairs` works on 4-tuples (regression guard). The
    speed leaderboard returns `(name, avg, pairs, total)`; the
    `liveliness` filter reads `pairs` from index 2 just like the
    speed filter. We lock this in so a refactor of tuple shape doesn't
    silently break the threshold for liveliness.
"""

import pytest

from response_time import count_total_messages, filter_by_min_pairs
from telegram_monitor import format_duration


# ---------------------------------------------------------------------------
# count_total_messages
# ---------------------------------------------------------------------------

def test_count_total_messages_basic():
    msgs = [object(), object(), object()]
    assert count_total_messages(msgs) == 3


def test_count_total_messages_empty():
    assert count_total_messages([]) == 0


def test_count_total_messages_is_len():
    """
    Lock the contract: liveliness = len(messages_in_window). If we ever
    swap this for a weighted formula or per-direction count, the test
    must be updated and the leaderboard semantics re-justified.
    """
    msgs = list(range(17))
    assert count_total_messages(msgs) == 17 == len(msgs)


# ---------------------------------------------------------------------------
# filter_by_min_pairs on 4-tuples (regression for the liveliness filter)
# ---------------------------------------------------------------------------

def test_filter_by_min_pairs_works_on_4tuple():
    """
    The liveliness leaderboard feeds the same `filter_by_min_pairs` but
    with 4-tuple rows `(name, avg, pairs, total)`. The helper must read
    `pairs_count` from index 2 and ignore index 3 (total_messages).

    If someone refactors the helper to "use the last element", the
    liveliness threshold would silently break (it would filter by
    total_messages, not pairs). This test guards that.
    """
    rows = [
        ("slow", 5.0, 2, 100),  # 2 pairs — should be dropped
        ("fast", 0.5, 5, 30),   # 5 pairs — kept
        ("loud", 3.0, 1, 500),  # 1 pair but huge total — should be dropped
    ]
    kept, excluded = filter_by_min_pairs(rows, min_pairs=3)
    assert excluded == 2
    assert [name for name, _, _, _ in kept] == ["fast"]


def test_filter_by_min_pairs_4tuple_keeps_chat_with_low_total_but_enough_pairs():
    """
    Boundary case: a chat with few total messages but enough pairs
    passes the threshold. This is the case where the "loud" example
    above is the failure mode: big total + few pairs must NOT pass.
    Here we assert the inverse: small total + enough pairs DOES pass.
    """
    rows = [("quiet", 1.0, 3, 4)]  # exactly 3 pairs, only 4 messages
    kept, excluded = filter_by_min_pairs(rows, min_pairs=3)
    assert excluded == 0
    assert kept == [("quiet", 1.0, 3, 4)]


# ---------------------------------------------------------------------------
# Sort + min-pairs together — the exact flow used by the liveliness report
# ---------------------------------------------------------------------------

def test_liveliness_top_orders_by_total_descending():
    """
    Reproduce the exact slicing logic the report uses:
    1. Filter by min_pairs (drops noise)
    2. Sort by total_messages descending
    3. Take top N
    """
    raw = [
        ("alpha", 0.5, 4, 10),  # 4 pairs, 10 total
        ("beta",  1.0, 3, 25),  # 3 pairs, 25 total — most active
        ("gamma", 2.0, 2, 50),  # dropped by min_pairs=3
        ("delta", 0.3, 5, 15),  # 5 pairs, 15 total
    ]
    kept, _ = filter_by_min_pairs(raw, min_pairs=3)
    kept_sorted = sorted(kept, key=lambda x: x[3], reverse=True)
    top = kept_sorted[:10]
    assert [name for name, _, _, _ in top] == ["beta", "delta", "alpha"]


def test_liveliness_top_drops_mono_manager_chat_even_with_huge_total():
    """
    The original concern: a chat with a 500-message manager monologue
    and only 1 client→manager pair would otherwise outrank a healthy
    5-pair / 20-msg chat. With min_pairs=3 applied to BOTH leaderboards
    the monologue is excluded. This test is the documentation of that
    contract for the liveliness report specifically.
    """
    raw = [
        ("healthy",  0.5, 5, 20),
        ("monologue", 0.1, 1, 500),  # manager flood, almost no real dialog
    ]
    kept, excluded = filter_by_min_pairs(raw, min_pairs=3)
    kept_sorted = sorted(kept, key=lambda x: x[3], reverse=True)
    top = kept_sorted[:10]
    assert excluded == 1
    assert [name for name, _, _, _ in top] == ["healthy"]


# ---------------------------------------------------------------------------
# Empty / reportable edge cases
# ---------------------------------------------------------------------------

def test_liveliness_empty_after_filtering_yields_empty_for_reporting():
    """
    After min_pairs filter, if no chats pass, `top` is empty and the
    report function renders the explicit 'not enough data' branch.
    Document the contract: kept==[] is the trigger.
    """
    raw = [("a", 0.1, 1, 5), ("b", 0.2, 2, 8)]
    kept, excluded = filter_by_min_pairs(raw, min_pairs=3)
    assert kept == []
    assert excluded == 2
    # `top = kept_sorted[:N]` where kept is [] is itself [].
    top = sorted(kept, key=lambda x: x[3], reverse=True)[:10]
    assert top == []


# ---------------------------------------------------------------------------
# Threshold applies to 0-pair rows (regression for the "0-pair drop" bug)
# ---------------------------------------------------------------------------

def test_min_pairs_threshold_filters_zero_pair_rows():
    """
    The reviewer caught that chats with zero client→manager pairs used
    to be dropped from `leaderboard_raw` BEFORE `min_pairs` was ever
    evaluated — meaning the threshold was a lie for those rows. After
    the fix, calculate_chat_avg_response_time emits a row with
    pairs_count=0 and avg=inf, and the explicit filter below handles it.

    This test locks in the new contract: 0-pair rows go through the
    threshold (and are dropped at the default min_pairs=3, but pass
    through at min_pairs=0).
    """
    raw = [
        ("silent", float('inf'), 0, 50),  # manager never replied, but 50 msgs
        ("chatty", 0.5, 5, 30),           # 5 pairs, healthy
    ]
    # Default min_pairs=3: 0-pair row is dropped
    kept, excluded = filter_by_min_pairs(raw, min_pairs=3)
    assert excluded == 1
    assert [name for name, _, _, _ in kept] == ["chatty"]

    # min_pairs=0: 0-pair row PASSES the filter (it's now a real knob)
    kept_zero, _ = filter_by_min_pairs(raw, min_pairs=0)
    assert [name for name, _, _, _ in kept_zero] == ["silent", "chatty"]


def test_liveliness_top_with_min_pairs_zero_includes_silent_chats():
    """
    With min_pairs=0, a chat where the manager never replied at all
    should still appear in the liveliness top if it had high message
    volume. This is the use case the review surfaced: a customer
    flooding the chat with no reply is the most urgent liveliness
    signal.
    """
    raw = [
        ("silent",   float('inf'), 0, 80),  # 80 messages, 0 pairs
        ("healthy",  0.5, 5, 30),
        ("dying",    1.0, 2, 5),  # also passes at min_pairs=0
    ]
    kept, _ = filter_by_min_pairs(raw, min_pairs=0)
    kept_sorted = sorted(kept, key=lambda x: x[3], reverse=True)
    top = kept_sorted[:10]
    # All three pass; sorted by total descending.
    assert [name for name, _, _, _ in top] == ["silent", "healthy", "dying"]
    # And critically, "silent" (the 0-pair customer-flood chat) is first.
    assert top[0][0] == "silent"
    assert top[0][2] == 0  # pairs_count = 0


# ---------------------------------------------------------------------------
# format_duration with inf (defensive: speed report at min_pairs=0)
# ---------------------------------------------------------------------------

def test_format_duration_handles_infinity():
    """
    With `min_pairs=0`, the speed report can receive a row with
    `avg_hours = float('inf')` (the manager never replied). `format_duration`
    must not crash; it should render a stable diagnostic.
    """
    assert format_duration(float('inf')) == "∞"
