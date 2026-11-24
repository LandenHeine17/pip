"""What a routine's "read" steps say (D-143): the weather, today's events, tomorrow's first event, the alarm, the
to-dos, what rang while you were out, and something fun. Each returns one or two short sentences, or "" when
there's nothing worth saying (or its module is off).

They read from what's already at hand, so a routine said by voice is answered at once: the weather Pip fetched
in the background (`warm`, every half hour), the calendar and the to-dos (their modules' `api`), the alarms
(ctx.alerts). The fun line is fetched once a day: something that happened on this day from Wikipedia (only the
cheerful kind: a first, a discovery or a premiere, nothing with deaths, wars or disasters), else one of the jokes in jokes.txt.

Calendar and to-dos go through their own privacy rule with `call`: who's asking. The morning briefing after the
wake-up alarm has nobody in particular asking, so it only reads what everyone in the room may hear."""
import asyncio
import logging
import random
import re
from datetime import date, datetime, timedelta
from pathlib import Path

from pip_assistant.common import when
from pip_assistant.common.weather import Weather

log = logging.getLogger(__name__)

READS = {   # what each read-out is called on the Routines page
    "weather": "The weather today",
    "today": "Today's events",
    "tomorrow": "Tomorrow's first event",
    "alarm": "When the next alarm is",
    "todos": "The open to-dos",
    "missed": "What rang while you were out",
    "fun": "Something fun (on this day, or a joke)",
}
JOKES = Path(__file__).parent / "jokes.txt"
# Wikipedia's "on this day" is mostly wars and disasters: only the cheerful ones make the morning briefing.
GRIM = re.compile(r"\b(kill|dead|death|die[ds]?|dying|murder|massacre|war|wars|battle|attack|bomb|shoot|shot|crash|"
                  r"sank|sink|sunk|hijack|earthquake|tsunami|hurricane|flood|fire|disaster|executed?|execution|hanged|"
                  r"assassinat|terror|genocide|slave|revolt|riot|invad|invasion|coup|siege|razed|prison|arrest|"
                  r"plague|pandemic|famine|explosion|explod|wound|injur|victim|casualt|fatal|nazi|holocaust)", re.I)
# …and of those, only the fun kind: a first, a discovery, a premiere (not politics).
CHEERFUL = re.compile(r"\b(first|premiere[ds]?|released?|debut(ed|s)?|discover(ed|y)|invent(ed|ion)|launch(ed)?|"
                      r"opened|record|broadcast|landed|unveiled|introduced|patent(ed)?|published|founded|"
                      r"completed|won|wins)\b", re.I)
WEATHER_EVERY_S = 1800


def join(items: list[str]) -> str:
    """["a", "b", "c"] → "a, b and c"."""
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


class Readouts:
    def __init__(self, ctx):
        self.ctx = ctx
        self.weather = Weather(ctx.location) if ctx.location else None
        self.fun_day: date | None = None     # the day `fun_lines` were fetched for
        self.fun_lines: list[str] = []       # that day's cheerful "on this day" lines
        self.jokes = [j.strip() for j in JOKES.read_text().splitlines() if j.strip() and not j.startswith("#")]

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.ctx.clock())

    def say(self, what: str, call, missed: list[dict] | None = None) -> str:
        try:
            return getattr(self, "read_" + what)(call, missed or [])
        except Exception:
            log.exception("a routine's %s read-out failed", what)
            return ""

    # ---------------------------------------------------------------- keeping things at hand
    async def warm(self, fun: bool = False) -> None:
        """Fetch the weather (if it's due) and, if asked, today's fun line, waiting at most a few seconds."""
        jobs = []
        if self.weather is not None:
            jobs.append(self.weather.screen_within(6.0))
        if fun:
            jobs.append(asyncio.wait_for(self.fetch_fun(), 6.0))
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, Exception) and not isinstance(r, asyncio.TimeoutError):
                log.warning("a routine's read-out couldn't be fetched: %s", type(r).__name__)

    async def keep_warm(self) -> None:
        """Background job: the weather fresh enough to read out at once."""
        while True:
            await self.warm()
            await asyncio.sleep(WEATHER_EVERY_S)

    # ---------------------------------------------------------------- each read-out
    def read_weather(self, call, missed) -> str:
        w = self.weather.cached() if self.weather is not None else {}
        if not w or not w.get("days"):
            return ""
        d = w["days"][0]
        line = f"It's {w['temp']} degrees{' and ' + w['sky'] if w.get('sky') else ''} now, with a high of {d['high']}"
        line += f" and a low of {d['low']}"
        if (d.get("rain") or 0) >= 20:
            line += f", and a {d['rain']} percent chance of rain or snow"
        return line + "."

    def _events(self, day: date, call) -> list[dict] | None:
        cal = self.ctx.module("calendar")
        if cal is None:
            return None
        return sorted(cal.events_on(day, call), key=lambda e: (not e.get("all_day"), e.get("at") or 0))

    @staticmethod
    def _event(ev: dict) -> str:
        if ev.get("all_day"):
            return ev["title"]
        return f"{ev['title']} at {when.say_clock(datetime.fromtimestamp(ev['at']))}"

    def read_today(self, call, missed) -> str:
        now = self.now()
        events = self._events(when.waking_day(now), call)
        if events is None:
            return ""
        events = [e for e in events if e.get("all_day") or e["at"] >= now.timestamp() - 600]   # not the ones over
        if not events:
            return "Nothing on the calendar today."
        names = [self._event(e) for e in events[:4]]
        more = f", and {len(events) - 4} more" if len(events) > 4 else ""
        return f"Today: {join(names)}{more}."

    def read_tomorrow(self, call, missed) -> str:
        # "Tomorrow" is after you next sleep (D-114): at 1 AM it's still the day that's starting
        events = self._events(when.waking_day(self.now()) + timedelta(days=1), call)
        if events is None:
            return ""
        if not events:
            return "Nothing on the calendar tomorrow."
        timed = [e for e in events if not e.get("all_day")]
        first = timed[0] if timed else events[0]
        return f"First thing tomorrow: {self._event(first)}."

    def read_alarm(self, call, missed) -> str:
        if self.ctx.alerts is None:
            return ""
        soon = [a for a in self.ctx.alerts.upcoming("alarm") if a["due"] - self.ctx.clock() <= 24 * 3600]
        if not soon:
            return "There's no alarm set."
        a = soon[0]
        at = when.say_clock(datetime.fromtimestamp(a["due"]))
        return f"Your alarm's set for {at}."

    def read_todos(self, call, missed) -> str:
        todos = self.ctx.module("todos")
        if todos is None:
            return ""
        items = todos.items(call)
        if not items:
            return "Your to-do list is clear."
        names = [i["title"] for i in items[:3]]
        if len(items) <= 3:
            return f"On your to-do list: {join(names)}."
        return f"You've got {len(items)} to-dos, like {join(names)}."

    def read_missed(self, call, missed) -> str:
        if not missed:
            return ""
        things = []
        for a in missed[:5]:
            label = (a.get("label") or "").strip()
            if a.get("kind") == "timer":
                things.append("a timer" if label in ("", "timer") else f"your {label} timer")
            elif a.get("kind") == "alarm":
                things.append("an alarm" if label in ("", "alarm") else f"your {label} alarm")
            elif a.get("kind") == "reminder":
                things.append(f"a reminder to {label}" if label else "a reminder")
            else:
                things.append(f"a heads-up for {label}" if label else "a heads-up")
        more = f", and {len(missed) - 5} more" if len(missed) > 5 else ""
        return f"While you were out, nobody answered {join(things)}{more}."

    def read_fun(self, call, missed) -> str:
        if not self.ctx.settings.get("fun", True):
            return ""
        if self.fun_day == self.now().date() and self.fun_lines:
            return random.choice(self.fun_lines)
        return random.choice(self.jokes) if self.jokes else ""

    async def fetch_fun(self) -> None:
        """Today's cheerful "on this day" lines from Wikipedia (once a day)."""
        today = self.now().date()
        if self.fun_day == today:
            return
        self.fun_day, self.fun_lines = today, []
        url = f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/selected/{today:%m}/{today:%d}"
        async with self.ctx.http(timeout=6.0, headers={"User-Agent": "pip-assistant (a home voice assistant)"}) as http:
            r = await http.get(url)
            r.raise_for_status()
            got = r.json().get("selected") or []
        for ev in got:
            text = re.sub(r"\s*\((pictured|depicted|flag depicted|shown)\)", "", str(ev.get("text") or "")).strip()
            if text and ev.get("year") and len(text) <= 200 and CHEERFUL.search(text) \
                    and not GRIM.search(text):
                text = re.sub(r"^(The|A|An) ", lambda m: m.group(1).lower() + " ", text)   # not names ("Edwin Hubble")
                self.fun_lines.append(f"On this day in {ev['year']}, {text}")
        self.fun_lines = [line if line.endswith(".") else line + "." for line in self.fun_lines]

