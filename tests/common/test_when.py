"""Understanding and saying times (common/when.py): durations, clock times, repeats, and "tomorrow" meaning
after you next sleep (D-114)."""
from datetime import date, datetime

import pytest
from helpers import clock_at

from pip_assistant.common import when

NOW = datetime(2026, 11, 4, 19, 30)  # a Wednesday evening
Clock = clock_at(NOW)



@pytest.mark.parametrize("text,secs", [
    ("ten minutes", 600), ("an hour and a half", 5400), ("half an hour", 1800), ("twenty five minutes", 1500),
    ("1 hour and 20 minutes", 4800), ("90 seconds", 90), ("a minute", 60), ("two and a half minutes", 150),
    ("a couple of minutes", 120), ("what's the weather", None)])
def test_durations(text, secs):
    assert when.duration(text) == secs


@pytest.mark.parametrize("text,expect", [
    ("at 7:30", datetime(2026, 11, 5, 7, 30)),    # already past today -> tomorrow
    ("at 8", datetime(2026, 11, 4, 20, 0)),       # no AM/PM: whichever comes next
    ("at 6", datetime(2026, 11, 5, 6, 0)),
    ("tomorrow at 3", datetime(2026, 11, 5, 15, 0)),   # another day: daytime hours preferred
    ("tuesday at 8", datetime(2026, 11, 10, 8, 0)),
    ("at noon", datetime(2026, 11, 5, 12, 0)), ("7 pm tomorrow", datetime(2026, 11, 5, 19, 0)),
    ("quarter to 8", datetime(2026, 11, 4, 19, 45)), ("at 10 tonight", datetime(2026, 11, 4, 22, 0)),
    ("for 10 minutes", None), ("set a timer for 10 minutes", None)])
def test_clock_times(text, expect):
    assert when.clock(text, NOW) == expect


def test_repeats_keep_their_clock_time():
    assert when.next_repeat("weekdays", datetime(2026, 11, 6, 8, 0), datetime(2026, 11, 6, 8, 1)) == datetime(2026, 11, 9, 8, 0)
    assert when.next_repeat("weekly:0", datetime(2026, 11, 9, 9, 0), datetime(2026, 11, 9, 9, 1)) == datetime(2026, 11, 16, 9, 0)
    # Across the daylight-saving change (Nov 1 2026 in the US) a daily 7 AM alarm stays at 7 AM
    assert when.next_repeat("daily", datetime(2026, 10, 31, 7, 0), datetime(2026, 10, 31, 7, 1)).hour == 7


def test_saying_durations():
    assert when.say_duration(600) == "10 minutes"
    assert when.say_duration(5400) == "an hour and a half"
    assert when.say_duration(3660) == "1 hour and 1 minute"
    assert when.say_left(4 * 60 + 40) == "5 minutes"


def test_tomorrow_means_after_you_next_sleep():
    """Owner, D-114: just after midnight, "tomorrow" is the coming day, like people mean it."""
    late = datetime(2026, 11, 12, 0, 30)                       # Thursday 00:30, not slept yet
    assert when.clock("tomorrow at 7", late, alarm=True) == datetime(2026, 11, 12, 7, 0)
    assert when.day("tomorrow", late) == date(2026, 11, 12)
    assert when.day("the day after tomorrow", late) == date(2026, 11, 13)
    assert when.say_time(datetime(2026, 11, 12, 7, 0), late) == "7 AM tomorrow"
    assert when.say_time(datetime(2026, 11, 12, 1, 30), late) == "1:30 AM"          # later the same night
    assert when.say_time(datetime(2026, 11, 12, 1, 0), datetime(2026, 11, 11, 23, 0)) == "1 AM"
    morning = datetime(2026, 11, 12, 8, 0)                     # after sleeping: the usual meaning
    assert when.day("tomorrow", morning) == date(2026, 11, 13)
    assert when.say_time(datetime(2026, 11, 13, 7, 0), morning) == "7 AM tomorrow"
    from pip_assistant.thinker.main import Thinker
    assert '"tomorrow" means Thursday' in Thinker.time_now(late) and "after midnight" not in Thinker.time_now(morning)
