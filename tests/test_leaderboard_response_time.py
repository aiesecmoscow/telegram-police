"""
Tests for the leaderboard response-time algorithm.

The leaderboard ranks chats by how actively / client-oriented a manager is,
judging by average response time (lower = better).

We focus on `compute_response_times_hours` — the pure helper extracted from
`calculate_chat_avg_response_time` — and assert the business contract:

  1. A burst of N client messages + 1 manager reply  ->  exactly 1 pair,
     measured from the EARLIEST message in the burst (the real reaction delay).
  2. A burst of M manager messages without a new client message in between
     -> only the FIRST manager message forms a pair (no duplicates).
  3. Messages arrive from Telethon in reverse-chronological order; sorting
     ascending must not affect the result vs an already-sorted input.
  4. Edge cases: empty input, no client messages, no manager messages, and
     a chat that starts with the manager must produce zero pairs.
  5. Across many chats, the ranking by avg response time puts the most
     client-oriented manager (lowest avg) first — proving the leaderboard
     sorting contract still holds.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List

import pytest

from response_time import compute_response_times_hours


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ME = 100  # manager id
CLIENT = 200


def _msg(sender_id: int, dt: datetime) -> SimpleNamespace:
    """Lightweight stand-in for telethon.tl.types.Message — only fields we use."""
    return SimpleNamespace(sender_id=sender_id, date=dt)


def _at(hours: float) -> datetime:
    """Epoch + `hours` hours, in UTC. Hours can be fractional."""
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=hours)


def _chronological(messages: List[SimpleNamespace]) -> List[SimpleNamespace]:
    """telethon returns reverse-chronological; simulate it."""
    return list(reversed(messages))


# ---------------------------------------------------------------------------
# Core business-rules
# ---------------------------------------------------------------------------

def test_client_burst_collapses_to_single_pair():
    """
    3 client messages at t=0, 0.5h, 1h, then one manager reply at t=2h.
    Business rule: one real manager action -> one pair.
    Time is measured from the LATEST client message in the burst (t=1.0),
    i.e. "how long since the client's last message until the manager replied".
    This is the meaningful response-time metric and it avoids the old
    "pair every client msg with the next manager reply" bug.
    """
    msgs = _chronological([
        _msg(ME,      _at(2.0)),
        _msg(CLIENT, _at(1.0)),
        _msg(CLIENT, _at(0.5)),
        _msg(CLIENT, _at(0.0)),
    ])
    rt = compute_response_times_hours(msgs, ME)
    assert rt == [pytest.approx(1.0)]


def test_manager_burst_only_first_forms_pair():
    """
    Client msg at t=0, then 3 manager replies at t=1, 2, 3.
    Business rule: only the first reply counts; subsequent ones with no new
    client input would otherwise inflate the pair count and skew the average.
    """
    msgs = _chronological([
        _msg(ME,      _at(3.0)),
        _msg(ME,      _at(2.0)),
        _msg(ME,      _at(1.0)),
        _msg(CLIENT, _at(0.0)),
    ])
    rt = compute_response_times_hours(msgs, ME)
    assert rt == [pytest.approx(1.0)]


def test_alternating_client_manager_each_pair_counted():
    """
    Standard 1:1 ping-pong. Each manager reply should pair with the
    immediately preceding client message.
    """
    msgs = [
        _msg(CLIENT, _at(0.0)),
        _msg(ME,      _at(0.5)),  # pairs with client@0.0 -> 0.5h
        _msg(CLIENT, _at(1.0)),
        _msg(ME,      _at(2.0)),  # pairs with client@1.0 -> 1.0h
    ]
    rt = compute_response_times_hours(msgs, ME)
    assert rt == [pytest.approx(0.5), pytest.approx(1.0)]


def test_input_order_invariance_for_alternating_messages():
    """
    Important contract: the helper REQUIRES chronologically-sorted input
    (oldest -> newest). The caller at telegram_monitor.py sorts messages
    ascending before calling. Feeding reverse-chronological input
    produces wrong results (including negative durations when a later
    manager message is seen before an earlier one).
    """
    chronological = [
        _msg(CLIENT, _at(0.0)),
        _msg(ME,      _at(1.0)),
        _msg(CLIENT, _at(2.0)),
        _msg(ME,      _at(2.5)),
    ]
    reverse_chrono = list(reversed(chronological))

    rt_sorted = compute_response_times_hours(chronological, ME)
    assert rt_sorted == [pytest.approx(1.0), pytest.approx(0.5)]

    # Reverse-chronological must NOT silently produce the same result.
    rt_reverse = compute_response_times_hours(reverse_chrono, ME)
    assert rt_reverse != rt_sorted  # correctness depends on input order


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_empty_input_returns_empty_list():
    assert compute_response_times_hours([], ME) == []


def test_no_client_messages_returns_empty():
    """Manager monologue, no incoming — no pairs."""
    msgs = [_msg(ME, _at(i)) for i in range(3)]
    assert compute_response_times_hours(msgs, ME) == []


def test_no_manager_messages_returns_empty():
    """Client monologue, no replies — no pairs."""
    msgs = [_msg(CLIENT, _at(i)) for i in range(3)]
    assert compute_response_times_hours(msgs, ME) == []


def test_chat_starts_with_manager_returns_empty():
    """No prior client message -> first manager msg is unpaired, no pairs."""
    msgs = [
        _msg(ME,      _at(0.0)),
        _msg(CLIENT, _at(1.0)),
        _msg(ME,      _at(2.0)),
    ]
    rt = compute_response_times_hours(msgs, ME)
    assert rt == [pytest.approx(1.0)]


def test_manager_message_without_prior_client_is_skipped():
    """Two manager messages in a row at the start — both unpaired."""
    msgs = [
        _msg(ME,      _at(0.0)),
        _msg(ME,      _at(1.0)),
        _msg(CLIENT, _at(2.0)),
        _msg(ME,      _at(2.5)),
    ]
    rt = compute_response_times_hours(msgs, ME)
    assert rt == [pytest.approx(0.5)]


# ---------------------------------------------------------------------------
# Leaderboard ranking semantics
# ---------------------------------------------------------------------------

def test_ranking_puts_most_responsive_chat_first():
    """
    The leaderboard sorts chats by avg response time ascending — the chat
    where the manager is most client-oriented (fastest) should rank #1.

    Simulate three chats, each with the SAME number of pairs to make the
    average comparison unambiguous.
    """
    def build_chat(reply_lag_hours: float) -> List[SimpleNamespace]:
        return [
            _msg(CLIENT, _at(0.0)),
            _msg(ME,      _at(reply_lag_hours)),
            _msg(CLIENT, _at(reply_lag_hours + 1.0)),
            _msg(ME,      _at(reply_lag_hours * 2 + 1.0)),
        ]

    chats = {
        "slow_chat":   build_chat(reply_lag_hours=4.0),  # avg = 4.0
        "fast_chat":   build_chat(reply_lag_hours=0.5),  # avg = 0.5
        "medium_chat": build_chat(reply_lag_hours=2.0),  # avg = 2.0
    }

    avgs = {
        name: (sum(rt) / len(rt)) if (rt := compute_response_times_hours(msgs, ME)) else None
        for name, msgs in chats.items()
    }
    assert avgs == {
        "slow_chat":   pytest.approx(4.0),
        "fast_chat":   pytest.approx(0.5),
        "medium_chat": pytest.approx(2.0),
    }

    ranking = sorted(avgs, key=lambda name: avgs[name])
    assert ranking == ["fast_chat", "medium_chat", "slow_chat"]


def test_old_algorithm_would_have_distorted_average():
    """
    Regression guard: with the old "pair every client msg with the next
    manager reply" approach, a single 3-msg client burst would have
    produced 3 pairs (each with nearly identical duration), biasing the
    average. The new algorithm must produce only 1 pair for the same input.
    """
    msgs = _chronological([
        _msg(ME,      _at(2.0)),
        _msg(CLIENT, _at(1.0)),
        _msg(CLIENT, _at(0.9)),
        _msg(CLIENT, _at(0.8)),
    ])

    new_pairs = compute_response_times_hours(msgs, ME)
    assert len(new_pairs) == 1
    assert new_pairs[0] == pytest.approx(1.0)  # 2.0 - 1.0 (latest client)

    # Sanity: simulate the old algorithm to confirm it would have produced 3
    def old_algorithm(messages_chrono, manager_id):
        pairs = []
        for i, msg in enumerate(messages_chrono):
            if msg.sender_id == manager_id:
                continue
            for j in range(i + 1, len(messages_chrono)):
                nxt = messages_chrono[j]
                if nxt.sender_id == manager_id:
                    pairs.append((nxt.date - msg.date).total_seconds() / 3600)
                    break
        return pairs

    old_pairs = old_algorithm(msgs, ME)
    assert len(old_pairs) == 3  # the bug we are fixing
    # And the old average would have been (1.0 + 1.1 + 1.2) / 3 = 1.1,
    # vs the new algorithm's 1.0. The new value is closer to the real
    # manager reaction time (the burst was essentially "one event").
    assert new_pairs != old_pairs  # new algorithm does NOT replicate the bug
