"""The Hall of Fame (D-147): funny moments from talking with Pip, kept word for word, apart from his memories.

Why apart: a memory is a fact Pip uses when he answers, so it goes to Claude with every request. A funny moment
isn't needed to answer anything, so here it costs nothing until someone asks for it (D-146).

  - **Saving**: only when someone asks ("Pip, save that to the hall of fame"). Claude, who has the conversation
    in front of it, replies ACTION {"do": "add", "kind": "moment", "title", "lines"} with the lines copied word
    for word (Pip's own included). Checked here: a few lines, no passwords or numbers, not one that's already
    in. Pip never saves one on his own, and this module doesn't listen to the room at all (no `room_heard`).
  - **The owner's exception** to "no quotes are kept" (D-128): a moment is the exact words, of anyone in the
    room ("Someone" for a voice Pip doesn't know). So: anyone can have one removed by voice (`remove_moment`),
    they're in Settings → Your data, removing a person removes the moments they're in, and the words are never
    logged or traced (events carry numbers only).
  - **Looking up**: ACTION {"do": "look", "kind": "moments"} (a query, or the latest), and Claude is asked again.
  - **Bringing one up**: with each request Claude gets a few titles (`summary`, at most 200 letters: starred
    ones first, the rest changing daily), so he can refer to one; the words he looks up.
  - **The monitor**: `show_moment` covers the monitor with one for a minute (the screen module's `api.cover`).
  - **Memories that are really moments** can be moved here from the dashboard's Memory page (`take_memory`).
  - Its page (Hall of Fame): read, star, edit, add, show on the monitor, delete.
"""
import asyncio
import difflib
import json
import random
import re
import time

from pip_assistant.common.messages import new_id
from pip_assistant.core.memory import CREDENTIAL
from pip_assistant.core.modules import Action, Call, Invalid, Module

BOX = "moment"
SCHEMA = """
CREATE TABLE IF NOT EXISTS hall_of_fame_moments (
    id      TEXT PRIMARY KEY,
    at      REAL NOT NULL,              -- when it happened (saved)
    title   TEXT NOT NULL,
    lines   TEXT NOT NULL,              -- JSON list: "Sam: …", "Pip: …", word for word
    people  TEXT NOT NULL DEFAULT '',   -- who's in it, comma separated (never Pip or "Someone")
    starred INTEGER NOT NULL DEFAULT 0,
    source  TEXT NOT NULL DEFAULT ''    -- "Sam, by voice", "added on the dashboard", "from a memory"
);
"""
TITLE_CHARS, LINE_CHARS = 80, 300
SUMMARY_CHARS, LOOK_CHARS = 200, 1200
STAMP = re.compile(r"^\[\d{1,2}:\d{2}\]\s*")           # "[18:14] Sam: …": the transcript's time, not kept
SPEAKER = re.compile(r"^([^:]{1,30}):\s")
# Words that say nothing about which moment is meant ("the one about the goose")
COMMON = {"the", "and", "that", "this", "one", "about", "when", "with", "time", "what", "was", "for", "you", "from",
          "moment", "hall", "fame", "funny", "said", "tell", "remember"}


def asker(call: Call) -> str:
    sp = call.speaker
    return sp.name if sp is not None and getattr(sp, "name", "") and sp.level == "sure" else "someone"


def clean_lines(value, most: int) -> list[str]:
    """The moment's lines: text only, the transcript's times taken off, each cut to a sensible length."""
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise Invalid("lines: a list of the lines said")
    out = [re.sub(r"\s+", " ", STAMP.sub("", str(x).strip())).strip()[:LINE_CHARS] for x in value]
    out = [x for x in out if x]
    if not out:
        raise Invalid("no lines")
    if len(out) > most:
        raise Invalid(f"at most {most} lines")
    return out


# A memory that was really a moment often began with a label saying so ("Pip's quote list (lines that got big
# laughs): …", "Hall of Fame quote from Pip, said when …: …"). In the Hall of Fame the label says nothing: it's taken
# off, and what's left is the quote with who said it (`untagged`).
TAG = re.compile(r"^(?:(?P<who>\w+)'s quote list\s*(?:\([^)]*\))?"
                 r"|hall of fame(?: quotes?)?(?: from (?P<from>\w+))?(?:,\s*(?P<note>[^:]*))?)\s*:\s*", re.I)
QUOTED = re.compile(r"^(?:then\s+)?(?:(?P<who>\w+):\s*)?(?P<q>['\"])")


def untagged(text: str) -> tuple[str, list[str]]:
    """A memory's text as a moment: (title, lines). A label in front is taken off; a quote that follows becomes
    "Pip: the words" (several, if it goes "…' then '…'" or back and forth), and anything said around it (when it
    was, who asked) a line in brackets. Text with no label and no quote is kept as it is."""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    m = TAG.match(text)
    if not m:
        return text[:TITLE_CHARS], [text[:LINE_CHARS]]
    who = (m.group("who") or m.group("from") or "").strip()
    note = re.sub(r"^said\s+", "", (m.group("note") or "").strip())
    rest, lines = text[m.end():].strip(), []
    while (q := QUOTED.match(rest)) and (who or q.group("who")):
        mark, start = q.group("q"), q.end()
        # the closing mark: the first one not inside a word (an apostrophe in "I'll" or "Landen's" is)
        end = next((i for i in range(start, len(rest)) if rest[i] == mark and (i + 1 == len(rest) or not rest[i + 1].isalnum())), -1)
        if end < 0:
            break
        who = q.group("who") or who
        lines.append(f"{who}: {rest[start:end].strip()}"[:LINE_CHARS])
        rest = rest[end + 1:].strip()
    if not lines:
        return rest[:TITLE_CHARS], [rest[:LINE_CHARS]]
    around = [x for x in (note, re.sub(r"^\((.*)\)$", r"\1", rest).strip()) if x]
    if around:
        lines.append(f"({'; '.join(around)})"[:LINE_CHARS])
    first = lines[0].split(": ", 1)[1]
    return (first if len(first) <= TITLE_CHARS else first[:TITLE_CHARS - 1].rstrip() + "…"), lines


def people_in(lines: list[str]) -> str:
    """Who said the lines ("Sam: …"), without Pip, unknown voices, or the "?" of someone he's only fairly sure of."""
    names = []
    for x in lines:
        m = SPEAKER.match(x)
        name = m.group(1).strip().rstrip("?") if m else ""
        if name and name.lower() not in ("pip", "someone") and name not in names:
            names.append(name)
    return ", ".join(names)


class HallOfFame(Module):
    takes_memories = "Hall of Fame"     # the Memory page's "Move to Hall of Fame" (core/modules.py take_memory)

    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db()
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.showing: str | None = None    # the moment on the monitor now (show_moment), and until when
        self.show_until = 0.0
        self.rng = random.Random()
        self.actions = {"add:moment": Action(self.check_add, self.do_add),
                        "remove_moment": Action(self.check_remove, self.do_remove),
                        "show_moment": Action(self.check_show, self.do_show),
                        "look:moments": Action(self.check_look, self.do_look)}

    # ---------------------------------------------------------------- the moments
    def setting(self, key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(self.ctx.settings.get(key, default))))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def short(mid: str) -> str:
        return "#h" + mid[:6]

    def moments(self) -> list[dict]:
        """All of them, newest first."""
        return [{"id": r["id"], "ref": self.short(r["id"]), "at": r["at"], "title": r["title"],
                 "lines": json.loads(r["lines"]), "people": r["people"], "starred": bool(r["starred"]),
                 "source": r["source"]}
                for r in self.db.execute("SELECT * FROM hall_of_fame_moments ORDER BY at DESC, id")]

    def get(self, ref) -> dict | None:
        """One moment by its id or its short id (#h1a2b3)."""
        ref = str(ref or "").strip().lstrip("#").lower()
        ref = ref[1:] if ref.startswith("h") and len(ref) == 7 else ref
        hits = [m for m in self.moments() if len(ref) >= 4 and m["id"].lower().startswith(ref)]
        return hits[0] if len(hits) == 1 else None

    def find(self, ref: str) -> str | None:
        return "moment" if str(ref or "").lstrip("#").lower().startswith("h") and self.get(ref) else None

    def same_as(self, lines: list[str]) -> dict | None:
        """A moment with (nearly) these very lines: saving it twice keeps one."""
        text = " ".join(lines).lower()
        for m in self.moments():
            if difflib.SequenceMatcher(None, text, " ".join(m["lines"]).lower()).ratio() >= 0.9:
                return m
        return None

    def checked(self, title, lines) -> dict:
        """A title and lines fit to keep. Refused: nothing in it, too long, or a password, code or number."""
        lines = clean_lines(lines, self.setting("max_lines", 8, 1, 20))
        title = re.sub(r"\s+", " ", str(title or "")).strip()[:TITLE_CHARS] or lines[0][:TITLE_CHARS]
        if CREDENTIAL.search(title) or any(CREDENTIAL.search(x) for x in lines):
            raise Invalid("credential")
        return {"title": title, "lines": lines}

    def keep(self, title: str, lines: list[str], source: str, at: float | None = None, people: str | None = None) -> dict:
        mid = new_id()
        self.db.execute("INSERT INTO hall_of_fame_moments (id, at, title, lines, people, source) VALUES (?,?,?,?,?,?)",
                        (mid, at if at is not None else self.ctx.clock(), title, json.dumps(lines),
                         people_in(lines) if people is None else people, source))
        self.db.commit()
        self.changed("saved", lines=len(lines))
        return self.get(mid)

    def remove(self, mid: str) -> None:
        self.db.execute("DELETE FROM hall_of_fame_moments WHERE id = ?", (mid,))
        self.db.commit()
        if self.showing == mid:
            self.take_down()
        self.changed()

    def changed(self, kind: str = "changed", **numbers) -> None:
        """Tell the page and the monitor to redraw. Numbers only: never a title or a line (they'd be traced)."""
        try:
            asyncio.get_running_loop().create_task(self.ctx.publish(kind, numbers))
        except RuntimeError:   # no loop running (tests)
            pass

    # ---------------------------------------------------------------- Claude's actions
    def check_add(self, a: dict, call: Call) -> dict:
        return self.checked(a.get("title") or a.get("label"), a.get("lines"))

    def do_add(self, plan: dict, call: Call) -> str:
        if self.same_as(plan["lines"]):
            return "That one's already in the Hall of Fame."
        m = self.keep(plan["title"], plan["lines"], f"{asker(call)}, by voice")
        return f"Saved to the Hall of Fame: {m['title']}."

    def check_remove(self, a: dict, call: Call) -> dict:
        m = self.get(a.get("id"))
        if m is None:
            raise Invalid("no such moment")
        return m

    def do_remove(self, m: dict, call: Call) -> str:
        self.remove(m["id"])
        return f"Taken out of the Hall of Fame: {m['title']}."

    def check_show(self, a: dict, call: Call) -> dict:
        if a.get("off"):
            return {"off": True}
        if a.get("id"):
            m = self.get(a["id"])
            if m is None:
                raise Invalid("no such moment")
            return {"id": m["id"]}
        return {}

    def do_show(self, plan: dict, call: Call) -> str:
        if plan.get("off"):
            self.take_down()
            return "Taken down."
        if self.screen() is None:
            return "The monitor's switched off."
        every = self.moments()
        if not every:
            return "The Hall of Fame is empty. Say \"save that to the hall of fame\" when something's worth it."
        m = self.get(plan["id"]) if plan.get("id") else self.rng.choice(every)
        self.put_up(m["id"])
        return f"Here's one: {m['title']}."

    def check_look(self, a: dict, call: Call) -> dict:
        return {"query": str(a.get("query") or "").strip()[:100]}

    def do_look(self, plan: dict, call: Call) -> str:
        every = self.moments()
        if not every:
            return "The Hall of Fame is empty: nobody has saved a moment yet."
        words = [w for w in re.findall(r"[a-z0-9']+", plan["query"].lower()) if len(w) > 2 and w not in COMMON]
        found = every
        if words:
            scored = []
            for m in every:
                hay = " ".join([m["title"], m["people"], *m["lines"]]).lower()
                if score := sum(1 for w in words if w in hay):
                    scored.append((score, m["at"], m))
            found = [m for *_, m in sorted(scored, key=lambda x: (-x[0], -x[1]))]
            if not found:
                return f"Nothing in the Hall of Fame for \"{plan['query']}\" ({len(every)} moments in it)."
        out = f"The Hall of Fame has {len(every)} moment{'' if len(every) == 1 else 's'}. " + \
              ("Matching: " if words else "The latest: ")
        for m in found[:3]:
            line = f"{m['ref']} \"{m['title']}\" ({time.strftime('%b %-d %Y', time.localtime(m['at']))}): " + " / ".join(m["lines"]) + " "
            if len(out) + len(line) > LOOK_CHARS:
                out += line[:max(0, LOOK_CHARS - len(out))]
                break
            out += line
        return out.strip()

    def summary(self, call: Call) -> str:
        """A few titles, so he can bring one up (at most 200 letters; it goes with every request). Starred ones
        first, then others that change with the day. Titles only: the words are a look-up away."""
        if not self.ctx.settings.get("bring_up", True):
            return ""
        every = self.moments()
        if not every:
            return ""
        rest = [m for m in every if not m["starred"]]
        random.Random(int(self.ctx.clock() // 86400)).shuffle(rest)
        out = "Hall of Fame moments you can bring up (look one up for the words):"
        some = False
        for m in [m for m in every if m["starred"]] + rest:
            piece = f" {m['ref']} {m['title']};"
            if len(out) + len(piece) > SUMMARY_CHARS:
                break
            out, some = out + piece, True
        return out.rstrip(";") + "." if some else ""

    # ---------------------------------------------------------------- the monitor
    def screen(self):
        return self.ctx.module("screen")

    def put_up(self, mid: str) -> None:
        self.showing, self.show_until = mid, self.ctx.clock() + self.setting("show_seconds", 60, 10, 600)
        if (screen := self.screen()) is not None:
            screen.cover(BOX)
        self.changed()

    def take_down(self) -> None:
        was, self.showing, self.show_until = self.showing, None, 0.0
        if was is not None and (screen := self.screen()) is not None:
            screen.uncover(BOX)
        self.changed()

    def tasks(self) -> list:
        return [self._tidy()]

    async def _tidy(self) -> None:
        """Take a shown moment down again when its time is up."""
        while True:
            await asyncio.sleep(5.0)
            self.tidy()

    def tidy(self) -> None:
        if self.showing is not None and self.ctx.clock() >= self.show_until:
            self.take_down()

    def tv(self, shown: list[dict]) -> dict:
        """The moment box: the one being shown, else (the box was added to a screen) one that changes daily."""
        if not any(s["type"] == BOX for s in shown):
            return {}
        every = self.moments()
        m = next((x for x in every if x["id"] == self.showing), None)
        if m is None and every:
            m = random.Random(int(self.ctx.clock() // 86400)).choice(every)
        return {BOX: {"title": m["title"], "lines": m["lines"], "at": m["at"], "count": len(every)} if m else {}}

    # ---------------------------------------------------------------- memories moved here
    async def take_memory(self, memory: dict) -> bool:
        """A memory that's really a funny moment, moved here from the Memory page (the owner's click). There are
        no exact words for it: the memory's text is the moment, marked as from a memory, with its own date and
        the people it was about (`about_names`), so removing one of them removes it too."""
        title, lines = untagged(memory.get("text"))
        if not lines[0]:
            return False
        if not self.same_as(lines):
            self.keep(title, lines, "from a memory", at=memory.get("created"),
                      people=", ".join(n for n in memory.get("about_names") or [] if n and n != "?"))
        return True

    # ---------------------------------------------------------------- its page
    async def dashboard(self, action: str, args: dict) -> dict:
        def listing():
            return {"moments": self.moments(), "showing": self.showing, "has_screen": self.screen() is not None,
                    "max_lines": self.setting("max_lines", 8, 1, 20)}
        if action == "list":
            return listing()
        try:
            if action == "add":
                plan = self.checked(args.get("title"), args.get("lines"))
                self.keep(plan["title"], plan["lines"], "added on the dashboard")
                return listing()
            m = self.get(args.get("id"))
            if m is None:
                return {"error": "That moment is gone."}
            if action == "edit":
                plan = self.checked(args.get("title"), args.get("lines"))
                self.db.execute("UPDATE hall_of_fame_moments SET title = ?, lines = ?, people = ? WHERE id = ?",
                                (plan["title"], json.dumps(plan["lines"]), people_in(plan["lines"]), m["id"]))
                self.db.commit()
                self.changed()
            elif action == "star":
                self.db.execute("UPDATE hall_of_fame_moments SET starred = ? WHERE id = ?", (int(bool(args.get("on"))), m["id"]))
                self.db.commit()
                self.changed()
            elif action == "delete":
                self.remove(m["id"])
            elif action == "show":
                if self.screen() is None:
                    return {"error": "The monitor's switched off (the screen module)."}
                self.put_up(m["id"])
            elif action == "hide":
                self.take_down()
            else:
                return {"error": f"unknown action {action}"}
        except Invalid as e:
            return {"error": "Passwords, codes, card and phone numbers can't be kept." if str(e) == "credential"
                    else f"That can't be saved: {e}."}
        return listing()

    # ---------------------------------------------------------------- Settings → Your data
    def export_data(self) -> dict | None:
        return {"moments": [{"when": time.strftime("%Y-%m-%d %H:%M", time.localtime(m["at"])), "title": m["title"],
                             "lines": m["lines"], "people": m["people"], "starred": m["starred"], "source": m["source"]}
                            for m in self.moments()]}

    async def wipe_data(self) -> int:
        n = len(self.moments())
        self.db.execute("DELETE FROM hall_of_fame_moments")
        self.db.commit()
        self.take_down()
        return n

    async def forget_person(self, person_id: str, name: str) -> int:
        """They're removed from the household: every moment they're in goes (their words are in it)."""
        if not name:
            return 0
        gone = [m for m in self.moments() if name in [p.strip() for p in m["people"].split(",")]]
        for m in gone:
            self.remove(m["id"])
        return len(gone)


def setup(ctx) -> Module:
    return HallOfFame(ctx)
