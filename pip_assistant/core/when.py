"""Understanding and saying times and durations, on the Pi (no internet needed).

    duration("ten and a half minutes")          -> 630.0 seconds
    clock("at 7:30 tomorrow", now)               -> datetime for 7:30 AM tomorrow
    say_duration(630) -> "10 and a half minutes" · say_time(dt, now) -> "7:30 AM tomorrow"

Times without AM/PM mean the next one coming up ("at 6" in the afternoon is 6 PM; in the evening,
6 AM tomorrow). A time that has already passed today means tomorrow (FEAT-034).
"""
import re
from datetime import date, datetime, time as dtime, timedelta

ONES = {"zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
        "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
        "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
UNIT = r"(hours?|hrs?|minutes?|mins?|seconds?|secs?)"
SECONDS = {"h": 3600, "m": 60, "s": 1}


def numbers(text: str) -> str:
    """'twenty five minutes' -> '25 minutes', 'a minute' -> '1 minute'. Lower-cases the text."""
    t = text.lower().replace("-", " ")
    t = re.sub(r"\b(a|an|one) (couple of|couple)\b", "2", t)
    t = re.sub(r"\ba couple( of)?\b", "2", t)
    t = re.sub(r"\ba few\b", "3", t)

    def tens(m):
        return str(TENS[m.group(1)] + (ONES[m.group(2)] if m.group(2) else 0))
    t = re.sub(rf"\b({'|'.join(TENS)})(?: ({'|'.join(k for k in ONES if 0 < ONES[k] < 10)}))?\b", tens, t)
    t = re.sub(rf"\b({'|'.join(ONES)})\b", lambda m: str(ONES[m.group(1)]), t)
    t = re.sub(r"\b(\d+) hundred\b", lambda m: str(int(m.group(1)) * 100), t)
    t = re.sub(rf"\b(a|an)\s+(?={UNIT}\b)", "1 ", t)
    return t


def duration(text: str) -> float | None:
    """Total length mentioned ('1 hour and 20 minutes', 'half an hour', '90 seconds'), or None."""
    t = numbers(text)
    total, found = 0.0, False
    if re.search(r"\b(half (?:an |1 |a )?hour|1/2 hour)\b", t):
        total, found = total + 1800, True
        t = re.sub(r"\b(half (?:an |1 |a )?hour|1/2 hour)\b", " ", t)
    if re.search(r"\b(quarter (?:of )?(?:an |1 |a )?hour)\b", t):
        total, found = total + 900, True
        t = re.sub(r"\b(quarter (?:of )?(?:an |1 |a )?hour)\b", " ", t)
    for m in re.finditer(rf"(\d+(?:\.\d+)?)\s*(?:and (a half|a quarter) )?{UNIT}(?: and (a half|a quarter))?\b", t):
        n = float(m.group(1))
        frac = m.group(2) or m.group(4)
        n += 0.5 if frac == "a half" else 0.25 if frac == "a quarter" else 0
        total += n * SECONDS[m.group(3)[0]]
        found = True
    return total if found and total > 0 else None


def _day(t: str, today: date) -> date | None:
    if re.search(r"\b(day after tomorrow)\b", t):
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    if re.search(r"\b(today|tonight|this (morning|afternoon|evening))\b", t):
        return today
    m = re.search(rf"\b(next )?({'|'.join(WEEKDAYS)})\b", t)
    if m:
        ahead = (WEEKDAYS.index(m.group(2)) - today.weekday()) % 7
        if m.group(1) and ahead == 0:
            ahead = 7
        return today + timedelta(days=ahead)
    return None


def has_day(text: str) -> bool:
    return _day(numbers(text), date.today()) is not None


def day(text: str, now: datetime | None = None) -> date | None:
    """Just the day mentioned (today, tomorrow, Thursday...), or None."""
    now = now or datetime.now()
    return _day(numbers(text), now.date())


def clock(text: str, now: datetime | None = None) -> datetime | None:
    """The clock time mentioned ('at 7', '7:30 pm', 'noon', 'tomorrow at quarter past 6'), or None."""
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    t = numbers(text)
    t = re.sub(r"\b(a\.m\.|a\. m\.|a m)(?=\W|$)", "am", t)
    t = re.sub(r"\b(p\.m\.|p\. m\.|p m)(?=\W|$)", "pm", t)
    hour = minute = half = None  # half: "am" / "pm" when stated
    if re.search(r"\b(noon|midday)\b", t):
        hour, minute, half = 12, 0, "pm"
    elif re.search(r"\bmidnight\b", t):
        hour, minute, half = 0, 0, "am"
    elif m := re.search(r"\b(quarter|half|15|30) (past|after|to|till|of) (\d{1,2})\b(?:\s*(am|pm))?", t):
        h, mins = int(m.group(3)), 30 if m.group(1) in ("half", "30") else 15
        hour, minute = (h, mins) if m.group(2) in ("past", "after") else ((h - 1) % 12 or 12, 60 - mins)
        half = m.group(4)
    else:
        for m in re.finditer(r"\b(at|for|by|until|till|from)?\s*(\d{1,2})(?:[:.](\d{2})|\s(\d{2}))?"
                             r"\s*(am|pm|o'?clock)?(?=\W|$)", t):
            prep, h, mm1, mm2, suffix = m.groups()
            if re.match(r"\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?|days?|weeks?)\b", t[m.end():]):
                continue  # "for 10 minutes" is a duration, not a time
            # A bare number only counts when it's clearly a time: "at 6", "6 pm", "7:30", "6 o'clock".
            if not (prep in ("at", "by", "until", "till", "from") or mm1 or suffix or
                    (prep == "for" and re.search(r"\b(alarm|wake)", t))):
                continue
            h, mm = int(h), int(mm1 or mm2 or 0)
            if h > 23 or mm > 59:
                continue
            hour, minute, half = h, mm, suffix if suffix in ("am", "pm") else None
            break
    if hour is None:
        return None
    if half is None and hour < 12:
        if re.search(r"\b(tonight|evening|afternoon|at night)\b", t):
            half = "pm"
        elif re.search(r"\bmorning\b", t):
            half = "am"
    if half == "pm" and hour < 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    cands = [hour] if half or hour == 0 or hour > 12 else [hour % 12, hour % 12 + 12]
    d = _day(t, now.date())
    if d is not None and d != now.date():
        return _prefer_day_hours([datetime.combine(d, dtime(h, minute)) for h in cands])
    opts = []
    for h in cands:  # today, or tomorrow if it has already passed: whichever comes next
        o = datetime.combine(now.date(), dtime(h, minute))
        opts.append(o if o > now else o + timedelta(days=1))
    return min(opts)


def _prefer_day_hours(opts: list[datetime]) -> datetime:
    """'Tuesday at 3' means 3 PM, 'Tuesday at 8' means 8 AM: pick the one between 7 AM and 9 PM."""
    good = [o for o in opts if 7 <= o.hour <= 21]
    return min(good or opts)


def repeat(text: str) -> str:
    """'' (once), 'daily', 'weekdays', 'weekends', 'weekly' or 'weekly:<0-6>' (0 = Monday)."""
    t = text.lower()
    if re.search(r"\b(every ?day|daily|each day|every morning|every night|every evening)\b", t):
        return "daily"
    if re.search(r"\b(every weekday|weekdays|on weekdays)\b", t):
        return "weekdays"
    if re.search(r"\b(every weekend|weekends)\b", t):
        return "weekends"
    m = re.search(rf"\b(every|each) ({'|'.join(WEEKDAYS)})\b|\b({'|'.join(WEEKDAYS)})s\b", t)
    if m:
        return f"weekly:{WEEKDAYS.index(m.group(2) or m.group(3))}"
    if re.search(r"\b(every week|weekly)\b", t):
        return "weekly"
    return ""


def next_repeat(rule: str, last: datetime, now: datetime | None = None) -> datetime | None:
    """The next time for a repeating alert, keeping its clock time (so daylight saving doesn't shift it)."""
    if not rule:
        return None
    now = now or datetime.now()
    d = last
    for _ in range(400):
        d = datetime.combine(d.date() + timedelta(days=1), last.time())
        wd = d.weekday()
        ok = (rule == "daily" or (rule == "weekdays" and wd < 5) or (rule == "weekends" and wd >= 5) or
              (rule == "weekly" and wd == last.weekday()) or
              (rule.startswith("weekly:") and wd == int(rule.split(":")[1])))
        if ok and d > now:
            return d
    return None


def first_repeat(rule: str, at: datetime) -> datetime:
    """For 'every Monday at 8': move the first time onto a day that matches the rule."""
    if not rule or rule in ("daily", "weekly"):
        return at
    probe = at - timedelta(days=1)
    nxt = next_repeat(rule, probe, now=at - timedelta(seconds=1))
    return nxt or at


# ------------------------------------------------------------------ saying things
def say_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h == 0 and m == 30 and sec == 0:
        return "half an hour"
    if h and not m and not sec:
        return "an hour" if h == 1 else f"{h} hours"
    if h and m == 30 and not sec:
        return "an hour and a half" if h == 1 else f"{h} and a half hours"
    if not h and m and sec == 30 and m < 10:
        return f"{m} and a half minutes"
    parts = []
    if h:
        parts.append("1 hour" if h == 1 else f"{h} hours")
    if m:
        parts.append("1 minute" if m == 1 else f"{m} minutes")
    if sec and not h:
        parts.append("1 second" if sec == 1 else f"{sec} seconds")
    return " and ".join(parts) if parts else "0 seconds"


def say_left(seconds: float) -> str:
    """Remaining time, rounded the way a person would say it."""
    s = max(0, int(seconds))
    if s >= 120 and s % 60:
        s = s // 60 * 60 + (60 if s % 60 >= 30 else 0)
    return say_duration(s)


def say_clock(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    if dt.hour == 12 and dt.minute == 0:
        return "noon"
    if dt.hour == 0 and dt.minute == 0:
        return "midnight"
    return f"{h}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}" if dt.minute else f"{h} {'AM' if dt.hour < 12 else 'PM'}"


def say_day(d: date, today: date | None = None) -> str:
    today = today or date.today()
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    if 0 < (d - today).days < 7:
        return WEEKDAYS[d.weekday()].title()
    return f"{WEEKDAYS[d.weekday()].title()} {d.strftime('%B')} {d.day}"


def say_time(dt: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now()
    day_ = say_day(dt.date(), now.date())
    return say_clock(dt) if day_ == "today" else f"{say_clock(dt)} {day_}" if day_ == "tomorrow" else f"{day_} at {say_clock(dt)}"


def say_repeat(rule: str) -> str:
    if rule.startswith("weekly:"):
        return f"every {WEEKDAYS[int(rule.split(':')[1])].title()}"
    return {"daily": "every day", "weekdays": "every weekday", "weekends": "every weekend", "weekly": "every week"}.get(rule, "")
