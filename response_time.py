"""
Pure logic for the leaderboard response-time computation.

Lives in its own module so it can be unit-tested without pulling in
the rest of telegram_monitor.py (which requires live Telegram settings).

Contract:
  - Input: messages already sorted in CHRONOLOGICAL order (oldest -> newest),
    each exposing .sender_id and .date (datetime, tz-aware).
  - Output: list of response times in hours, one entry per manager reply
    that had a prior client message to pair with.

Business rules:
  - A burst of N client messages followed by 1 manager reply produces
    exactly 1 pair; the time is measured from the LATEST client message
    in the burst (i.e. "how long since the client's last message until
    the manager replied").
  - A burst of M manager messages without an intervening client message
    produces only 1 pair (the first manager message); later ones are
    dropped to avoid skewing the average.
"""

from typing import List, Tuple


def count_total_messages(messages_sorted: list) -> int:
    """
    Pure helper: total message count in a chronologically-sorted list.

    Used by the liveliness leaderboard to rank chats by activity volume.
    "Живость диалога" = "сколько сообщений в окне" — captured here as a
    single named function so the contract is explicit in the code, not
    buried in a lambda at the call site.
    """
    return len(messages_sorted)


def filter_by_min_pairs(
    leaderboard: List[Tuple[str, float, int, int]], min_pairs: int,
) -> Tuple[List[Tuple[str, float, int, int]], int]:
    """
    Pure helper: drop chats with `pairs_count < min_pairs` from the leaderboard.

    Reads `pairs_count` from index 2 of each row. Works for the 4-tuple
    `(name, avg_hours, pairs_count, total_messages)` returned by
    `calculate_chat_avg_response_time`.

    Returns (filtered_list, excluded_count). Exposed for unit-testing the
    threshold logic without going through the Telegram client.
    """
    kept: List[Tuple[str, float, int, int]] = []
    excluded = 0
    for entry in leaderboard:
        if entry[2] >= min_pairs:
            kept.append(entry)
        else:
            excluded += 1
    return kept, excluded


def take_anti_leaderboard(
    leaderboard: List[Tuple[str, float, int, int]], n: int,
) -> List[Tuple[str, float, int, int]]:
    """
    Pure helper: slice the anti-leaderboard (slowest responders) from the
    SAME data the fastest leaderboard uses.

    The fastest leaderboard ranks by `avg_hours` ASCENDING (lowest = best).
    The anti-leaderboard is the mirror view: `avg_hours` DESCENDING
    (highest = worst) so the bottom-N chats with the longest average
    response time bubble to the top of the report.

    Important contract details:

    - The input is the *already filtered* list (post `filter_by_min_pairs`),
      so the threshold is shared by both views.
    - `float('inf')` rows (a manager who never replied) sort last, which
      matches the slowest-of-slowest intuition; if `min_pairs=0` lets them
      pass, they appear as the most unresponsive chats. The report renders
      them as `∞` via `format_duration`.
    - For ties on `avg_hours`, secondary sort is `pairs_count` DESCENDING
      so a "slow but actively slow" chat outranks a "slow with 3 pairs"
      chat (more pairs = stronger signal of a real slow pattern, not noise).
    - Tertiary sort is `chat_name` ASCENDING for deterministic output.
    - Returns at most `n` rows; never raises on empty input.

    Exposed for unit-testing without going through the Telegram client.
    """
    if n <= 0 or not leaderboard:
        return []
    return sorted(
        leaderboard,
        key=lambda row: (-row[1], -row[2], row[0]),
    )[:n]


def compute_response_times_hours(messages_sorted: list, manager_id: int) -> List[float]:
    response_times: List[float] = []
    last_client_msg_date = None
    for msg in messages_sorted:
        if msg.sender_id != manager_id:
            last_client_msg_date = msg.date
            continue
        if last_client_msg_date is None:
            continue
        hours = (msg.date - last_client_msg_date).total_seconds() / 3600
        response_times.append(hours)
        last_client_msg_date = None
    return response_times
