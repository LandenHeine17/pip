"""YouTube videos on the monitor (D-141): "Pip, pull up a YouTube video explaining matrix multiplication".

How a video goes up:

    Claude:   ACTION {"do": "play_video", "query": "matrix multiplication explained"}
    here:     search() asks YouTube's Data API for videos that may play outside YouTube (with their lengths),
              show() puts the first up: the screen module covers the monitor with the "video" box (its api,
              needs = ["screen"]) and Pip says which video it is
    monitor:  static/monitor.js plays it with YouTube's own player, from what tv() below sends

How it's controlled: "Pip, pause the video", "fast forward a minute", "go to 4:30", "louder", "a different one",
"take down the video". Said to Pip by name, room_heard() recognises these (the whole sentence must be one of them:
COMMANDS) and does them at once, without asking Claude; anything worded otherwise goes to Claude, which has the same
controls ({"do": "video", "op": …}). Each control is added to a short numbered list (`commands`), which the monitor
plays through in order: "pause", "play", "seek_by" (seconds forwards or back) and "seek_to". The module also keeps
an estimate of where the video is (`position()`), so the monitor can carry on from about there if its page reloads.

The volume: the monitor plays it at `volume`, and at `duck_volume` while Pip is listening, thinking or talking.

Nothing is kept: no tables, and neither what was asked for nor the videos' titles are logged. What's up is only in
memory, so Pip restarting takes the video down (the screen's cover isn't kept either). The key is the module's own
section of ~/pip-data/secrets.toml ([youtube] api_key); it may reach only www.googleapis.com (ctx.http). The video
itself is played by the monitor's browser, from YouTube.
"""
import asyncio
import html
import logging
import re

import httpx

from pip_assistant.common import when
from pip_assistant.core.commands import clean
from pip_assistant.core.modules import Action, Call, Invalid, Module

log = logging.getLogger(__name__)

SEARCH = "https://www.googleapis.com/youtube/v3/search"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
BOX = "video"            # its kind of box on the monitor (module.toml [monitor.video])
KEEP_COMMANDS = 20       # the monitor plays through the last few controls, in order
VOLUME_STEP = 15
OPS = ("pause", "play", "forward", "back", "go_to", "restart", "louder", "quieter", "next", "close")

# ---------------------------------------------------------------- understanding times
UNITS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60,
         "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600}
SMALLER = {3600: 60, 60: 1, 1: 1}
CLOCK = re.compile(r"\b(?:(\d+):)?(\d+):(\d\d)\b")   # "4:30", "1:02:03"


def seconds_of(t: str) -> float | None:
    """How long, or what time into a video, in words already cleaned ("30 seconds", "2 and a half minutes",
    "1 minute 30", "4:30", "minute 12", "half a minute"). A bare number is seconds. None if there's no time."""
    if m := CLOCK.search(t):
        return int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3])
    if m := re.search(r"(?<!\d )\bminute (\d+)\b", t):   # "minute 12", but not "1 minute 5"
        return int(m[1]) * 60
    t = re.sub(r"\bhalf (?:an?|1) ", "0.5 ", t)               # "half a minute" (when.numbers made the "a" a 1)
    total, number, half, last, found = 0.0, None, False, None, False
    for w in re.findall(r"\d+(?:\.\d+)?|[a-z]+", t):
        if w[0].isdigit():
            number = float(w)
        elif w == "half":
            half = True
        elif w in UNITS:
            if number is not None or half:
                total += ((number or 0) + (0.5 if half else 0)) * UNITS[w]
                found = True
            number, half, last = None, False, UNITS[w]
    if number is not None:   # "1 minute 30": the rest is the next unit down; "fast forward 30": seconds
        total += number * (SMALLER[last] if last else 1)
        found = True
    if half and last:        # "a minute and a half"
        total += 0.5 * last
        found = True
    return total if found else None


def say_time(s: float) -> str:
    """A time into a video, for a sentence: "4:30", "1:02:03"."""
    s = int(max(0, s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def say_length(s: float) -> str:
    """How long, for a sentence: "45 seconds", "12 minutes", "1 hour 5 minutes"."""
    s = int(round(s))
    if s < 60:
        return f"{s} seconds"
    h, m = divmod(round(s / 60), 60)
    mins = f"{m} minute{'s' if m != 1 else ''}"
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {mins}" if m else "") if h else mins


def iso_seconds(d: str) -> int:
    """YouTube's lengths: "PT1H2M3S" → 3723. A live stream is "P0D" → 0."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    d_, h, mi, s = (int(x or 0) for x in m.groups())
    return d_ * 86400 + h * 3600 + mi * 60 + s


def short_title(title: str) -> str:
    """A title to say aloud: entities decoded, without the "| Channel" or "#tags" tails, at most 90 letters."""
    t = html.unescape(title or "")
    t = re.split(r"\s+[|]\s+|\s+#\w", t)[0].strip()
    return t if len(t) <= 90 else t[:87].rsplit(" ", 1)[0] + "…"


# ---------------------------------------------------------------- the controls said to Pip
# The whole sentence (after commands.clean() and when.numbers()) must be one of these: anything more ("pause it when
# he gets to the proof") goes to Claude. Only while a video is up, and only said to Pip by name. when.numbers() has
# made "a different one" "a different 1".
_VIDEO = r"(?:the |this |that )?(?:youtube )?(?:video|vid|youtube)"
_IT = rf"(?: {_VIDEO}| it| that| this)?"
_TIME = r"(?: by)?(?: (?:\d|half|a half).*)?"
COMMANDS = [
    ("close", re.compile(rf"^(?:(?:take|put|bring|pull|shut) (?:down|off|away) {_VIDEO}|(?:take|put|pull) (?:{_VIDEO}|it|that) "
                         rf"(?:down|off|away)|(?:close|hide|remove|exit|end|quit|kill|turn off|switch off|get rid of|"
                         rf"clear|stop showing) {_VIDEO}|no more (?:videos?|youtube))$")),
    ("restart", re.compile(rf"^(?:restart{_IT}|start{_IT} (?:over|again|from the (?:start|beginning))|"
                           r"(?:go|skip|jump|rewind|take it) (?:back )?to the (?:start|beginning)|from the (?:start|beginning))$")),
    ("go_to", re.compile(rf"^(?:go|skip|jump|seek|fast forward|rewind|move|take it|play(?: it)? from|start(?: it)? from)"
                         rf"(?: it| {_VIDEO})? (?:to|from|at) (?:minute \d+|\d+:\d\d(?::\d\d)?|\d.*?)"
                         r"(?: (?:in|into) (?:the video|it))?$")),
    ("forward", re.compile(rf"^(?:fast ?forward|skip(?: ahead| forward)?|go forward|forward|jump ahead|move ahead){_IT}{_TIME}$")),
    ("back", re.compile(rf"^(?:rewind|go back|skip back(?:wards?)?|jump back|back up|go backwards?){_IT}{_TIME}$")),
    ("pause", re.compile(rf"^(?:pause{_IT}(?: for a (?:sec|second|moment|minute))?|stop {_VIDEO})$")),
    ("play", re.compile(rf"^(?:play|resume|unpause|continue|keep playing|keep going|carry on|start){_IT}(?: again)?$")),
    ("louder", re.compile(rf"^(?:louder|(?:make|turn){_IT} (?:up|louder)|turn up{_IT}|volume up|turn the volume up|"
                          r"turn up the volume|(?:raise|increase) the volume|more volume)$")),
    ("quieter", re.compile(rf"^(?:quieter|softer|(?:make|turn){_IT} (?:down|quieter|softer)|turn down{_IT}|volume down|"
                           r"turn the volume down|turn down the volume|(?:lower|decrease) the volume|less volume)$")),
    ("next", re.compile(rf"^(?:(?:play |show |put on |try )?(?:the )?next (?:(?:one|1)|video)|(?:play |show |put on |try |find |"
                        rf"get )?(?:me )?(?:a different|another|a new|different) (?:(?:one|1)|video)|skip (?:this|that) (?:(?:one|1)|video)|"
                        rf"not (?:this|that) (?:one|1)|next)$")),
]


def control_in(text: str) -> tuple[str, float | None] | None:
    """("forward", 60.0) for "Pip, fast forward a minute"; None if the sentence isn't just a control."""
    t = when.numbers(clean(text)).strip(" .?!,")
    t = re.sub(r"-", " ", t)
    for op, pattern in COMMANDS:
        if pattern.match(t):
            return op, seconds_of(t) if op in ("forward", "back", "go_to") else None
    return None


class YouTube(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.found: list[dict] = []   # the last search's videos: {"id", "title", "channel", "seconds"}
        self.at = 0                   # which of them is up
        self.video: dict | None = None
        self.playing = False
        self.offset, self.since = 0.0, ctx.clock()   # where it was, and when (position())
        self.volume = self._setting("volume", 70)
        self.commands: list[dict] = []   # the last few controls for the monitor: {"n", "op", "s"}
        self.n = 0
        self.actions = {"play_video": Action(self.check_play, self.do_play),
                        "video": Action(self.check_control, self.do_control)}

    def _setting(self, key: str, default: int, low: int = 0, high: int = 100) -> int:
        try:
            return max(low, min(high, int(self.ctx.settings.get(key, default))))
        except (TypeError, ValueError):
            return default

    def _changed(self) -> None:
        """Tell pip-web: the monitor (if the video's box is up) and the Video page reload what they show."""
        try:
            asyncio.get_running_loop().create_task(self.ctx.publish("changed"))
        except RuntimeError:   # no loop running (tests)
            pass

    def _screen(self):
        return self.ctx.module("screen")   # the screen module's api (None if it's switched off)

    # ---------------------------------------------------------------- finding videos
    async def search(self, query: str) -> list[dict]:
        """Videos for a query: only ones allowed to play outside YouTube, never live streams, with their lengths."""
        key = self.ctx.secrets.get("api_key")
        safe = str(self.ctx.settings.get("safe_search", "moderate")).lower()
        params = {"part": "snippet", "type": "video", "videoEmbeddable": "true", "videoSyndicated": "true",
                  "maxResults": self._setting("results", 5, 1, 10), "q": query, "key": key,
                  "safeSearch": safe if safe in ("none", "moderate", "strict") else "moderate"}
        async with self.ctx.http(timeout=6.0) as http:
            r = await http.get(SEARCH, params=params)
            r.raise_for_status()
            items = [x for x in r.json().get("items") or []
                     if (x.get("id") or {}).get("videoId") and (x.get("snippet") or {}).get("liveBroadcastContent", "none") == "none"]
            if not items:
                return []
            ids = [x["id"]["videoId"] for x in items]
            r = await http.get(VIDEOS, params={"part": "contentDetails", "id": ",".join(ids), "key": key})
            r.raise_for_status()
            lengths = {v["id"]: iso_seconds((v.get("contentDetails") or {}).get("duration", "")) for v in r.json().get("items") or []}
        return [{"id": x["id"]["videoId"], "title": short_title(x["snippet"].get("title", "")),
                 "channel": html.unescape(x["snippet"].get("channelTitle", "")), "seconds": lengths.get(x["id"]["videoId"], 0)}
                for x in items if lengths.get(x["id"]["videoId"], 0) > 0]

    # ---------------------------------------------------------------- what's up, and controlling it
    def position(self) -> float:
        """About where the video is now, in seconds (from the controls; the monitor knows exactly)."""
        p = self.offset + (self.ctx.clock() - self.since if self.playing else 0)
        length = (self.video or {}).get("seconds") or 0
        return max(0.0, min(p, length) if length else p)

    def _command(self, op: str, s: float | None = None) -> None:
        self.n += 1
        self.commands = [*self.commands, {"n": self.n, "op": op, **({"s": round(s, 1)} if s is not None else {})}][-KEEP_COMMANDS:]

    def show(self, video: dict) -> str:
        """Put a video up and start it: the monitor is covered with its box."""
        self.video, self.playing, self.offset, self.since = video, True, 0.0, self.ctx.clock()
        self.commands = []
        if (screen := self._screen()) is not None:
            screen.cover(BOX)
        self._changed()
        return f"Here's “{video['title']}” from {video['channel']}, {say_length(video['seconds'])}."

    def close(self) -> None:
        self.video, self.playing, self.commands = None, False, []
        if (screen := self._screen()) is not None:
            screen.uncover(BOX)
        self._changed()

    def control(self, op: str, seconds: float | None = None) -> str:
        """Do one control (OPS) on the video that's up; returns what Pip says."""
        if op == "close":
            if not self.video:
                return "There's no video up."
            self.close()
            return "Taken down."
        if not self.video:
            return "There's no video up. Ask me for one."
        pos, length = self.position(), self.video["seconds"]
        if op == "pause":
            if self.playing:
                self.offset, self.since, self.playing = pos, self.ctx.clock(), False
                self._command("pause")
            said = "Paused."
        elif op == "play":
            if not self.playing:
                self.offset, self.since, self.playing = pos, self.ctx.clock(), True
                self._command("play")
            said = "Playing."
        elif op in ("forward", "back"):
            step = seconds if seconds else self._setting("skip_seconds", 10, 1, 3600)
            step = step if op == "forward" else -step
            self.offset, self.since = max(0.0, min(pos + step, length)), self.ctx.clock()
            self._command("seek_by", step)
            said = (f"Skipped ahead {say_length(abs(step))}." if step > 0 else f"Back {say_length(abs(step))}.")
        elif op in ("go_to", "restart"):
            to = 0.0 if op == "restart" else seconds
            if to is None:
                return "To what time? Say something like four minutes thirty."
            if length and to >= length:
                return f"It's only {say_length(length)} long."
            self.offset, self.since = to, self.ctx.clock()
            self._command("seek_to", to)
            if not self.playing:
                self.playing = True
                self._command("play")
            said = "From the start." if op == "restart" else f"Going to {say_time(to)}."
        elif op in ("louder", "quieter"):
            self.volume = max(0, min(100, self.volume + (VOLUME_STEP if op == "louder" else -VOLUME_STEP)))
            said = "That's as loud as it goes." if self.volume == 100 and op == "louder" else \
                "Muted." if self.volume == 0 else ("Louder." if op == "louder" else "Quieter.")
        elif op == "next":
            if self.at + 1 >= len(self.found):
                return "That was the last one I found. Ask me for something else."
            self.at += 1
            return self.show(self.found[self.at])
        else:
            raise Invalid(f"unknown video control {op}")
        self._changed()
        return said

    # ---------------------------------------------------------------- Claude
    def check_play(self, a: dict, call: Call) -> str:
        query = re.sub(r"\s+", " ", str(a.get("query") or "")).strip()
        if not 1 <= len(query) <= 200:
            raise Invalid("a video search needs a short query")
        if not self.ctx.secrets.get("api_key"):
            raise Invalid("no YouTube API key in secrets.toml [youtube]")
        return query

    async def do_play(self, query: str, call: Call) -> str:
        try:
            found = await self.search(query)
        except httpx.HTTPStatusError as e:
            log.warning("a YouTube search failed: HTTP %s", e.response.status_code)   # never the query
            if e.response.status_code == 403:
                return "YouTube won't let me search right now: I may have used up today's searches."
            return "YouTube didn't answer properly. Try again in a bit."
        except httpx.HTTPError as e:
            log.warning("a YouTube search failed: %s", type(e).__name__)
            return "I couldn't reach YouTube."
        if not found:
            return "I couldn't find a video for that."
        self.found, self.at = found, 0
        return self.show(found[0])

    def check_control(self, a: dict, call: Call) -> tuple[str, float | None]:
        op = str(a.get("op") or "").strip().lower().replace(" ", "_")
        op = {"stop": "pause", "resume": "play", "unpause": "play", "fast_forward": "forward", "rewind": "back",
              "seek": "go_to", "goto": "go_to", "take_down": "close", "hide": "close", "different": "next"}.get(op, op)
        if op not in OPS:
            raise Invalid("which video control")
        seconds = a.get("seconds")
        if seconds is not None:
            try:
                seconds = float(seconds)
            except (TypeError, ValueError):
                seconds = seconds_of(when.numbers(str(seconds).lower()))
            if seconds is None or not 0 <= seconds <= 24 * 3600:
                raise Invalid("how many seconds")
        return op, seconds

    def do_control(self, plan: tuple[str, float | None], call: Call) -> str:
        return self.control(*plan)

    def summary(self, call: Call) -> str:
        if not self.video or call.kind == "join":   # join-in checks: nothing (they run every ~40 s)
            return ""
        v = self.video
        how = "playing" if self.playing else "paused"
        return (f"Video on the monitor: “{v['title']}” from {v['channel']}, {how} at {say_time(self.position())} "
                f"of {say_time(v['seconds'])}, volume {self.volume}.")

    # ---------------------------------------------------------------- said to Pip by name
    def room_heard(self, text: str, call: Call) -> str | None:
        """"Pip, pause the video", "fast forward a minute", "take it down": done here at once, while a video is up."""
        if call.kind != "direct" or not self.video:
            return None
        found = control_in(text)
        return self.control(*found) if found else None

    # ---------------------------------------------------------------- the monitor and the Video page
    def tv(self, shown: list[dict]) -> dict:
        if not any(s["type"] == BOX for s in shown):
            return {}
        if not self.video:
            return {BOX: {}}
        v = self.video
        return {BOX: {"id": v["id"], "title": v["title"], "channel": v["channel"], "playing": self.playing,
                      "start": round(self.position(), 1), "volume": self.volume,
                      "duck": self._setting("duck_volume", 15), "commands": self.commands}}

    def page(self) -> dict:
        v = self.video
        return {"video": v and {**v, "playing": self.playing, "position": round(self.position()), "volume": self.volume},
                "more": max(0, len(self.found) - self.at - 1) if v else 0,
                "has_key": bool(self.ctx.secrets.get("api_key")), "skip": self._setting("skip_seconds", 10, 1, 3600)}

    async def dashboard(self, action: str, args: dict) -> dict:
        if action == "status":
            return self.page()
        if action == "play":
            said = await self.do_play(self.check_play({"query": args.get("query")}, Call()), Call())
        elif action == "control":
            said = self.control(*self.check_control(args, Call()))
        else:
            return {"error": f"unknown action {action}"}
        return {**self.page(), "said": said}


def setup(ctx) -> Module:
    return YouTube(ctx)
