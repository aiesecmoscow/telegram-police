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

from typing import List


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
