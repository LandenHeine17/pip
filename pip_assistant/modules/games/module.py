"""The games module (D-144): trivia, 20 questions, would you rather and guess the song, played with Pip by voice,
with the game up on the monitor. Plug and play: it uses only the hooks every module has, nothing in Pip names it, and
switching it off (Settings → games, or deleting the folder) leaves Pip as he was.

Only when someone asks ("Pip, let's play trivia"); Pip never starts one, but any hour is fine. Claude's `game` action
starts one (trivia.py, twenty.py, rather.py, song.py; the questions or songs are fetched first). One game at a
time: a new one replaces it.

While it's on:
  - Everything heard is offered to it (`room_heard`). An answer (a trivia letter, a song's name) counts said to Pip
    or in the room; Pip's reply is a `Done` ("Right, Sam! Question 4: …?"), so Claude isn't asked. His questions end
    in "?", so the reply after is taken as said to him (the follow-up window).
  - Said to Pip: "stop the game", "skip" / "next", "repeat the question", "play it again", "what's the score" are
    done at once (common.control). Anything else said to him goes to Claude, who's told about the game (`summary`):
    20 questions' answers and would you rather's roasts are Claude's.
  - The game covers the monitor (the screen module's `api.cover`, if it's on; a video up is covered over), with its
    box drawn by static/monitor.js. It's taken off 30 s after the game ends, or after 10 minutes with nothing said.
    If Pip was busy when an answer came (talking), the monitor still shows what happened; "repeat the question"
    says it again.
Who scores: the person, if Pip is sure who's speaking, else "Guest". Each finished game's result (its winner and
scores) is kept in games_results for the all-time board on the Games page; one stopped halfway isn't, and nothing
that was said is kept."""
import asyncio
import json
import logging
import random

from pip_assistant.common.messages import new_id
from pip_assistant.core.modules import Action, Call, Done, Invalid, Module

from . import song, trivia
from .common import Game, control, plain
from .rather import Rather
from .twenty import Twenty

log = logging.getLogger(__name__)

BOX = "game"
GAMES = {"trivia": "trivia", "twenty": "twenty", "20 questions": "twenty", "twenty questions": "twenty",
         "rather": "rather", "would you rather": "rather", "song": "song", "guess the song": "song",
         "name that tune": "song", "music": "song"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS games_results (
    id      TEXT PRIMARY KEY,
    game    TEXT NOT NULL,          -- trivia / twenty / song
    at      REAL NOT NULL,
    winner  TEXT NOT NULL,          -- a name, "Guest", "Pip" (20 questions not guessed), or '' (a tie, nobody)
    scores  TEXT NOT NULL           -- JSON {name: points}
);
"""
IDLE_S, SHOW_END_S = 600.0, 30.0


class Games(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.db = ctx.db()
        self.db.executescript(SCHEMA)
        self.db.commit()
        self.game: Game | None = None
        self.last_heard = 0.0     # when something was last said in the game (ctx.clock)
        self.ended = 0.0          # when it ended (its final scores stay up a little)
        self.rng = random.Random()
        self.drawn: dict | None = None   # the game's box as the monitor last had it
        self.actions = {"game": Action(self.check_game, self.do_game)}

    # ---------------------------------------------------------------- starting, ending
    def setting(self, key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(self.ctx.settings.get(key, default))))
        except (TypeError, ValueError):
            return default

    def check_game(self, a: dict, call: Call) -> dict:
        op = plain(str(a.get("op") or ""))
        if op:
            if op not in ("stop", "next", "skip", "repeat", "score", "replay"):
                raise Invalid("op: stop, next, repeat, replay or score")
            out = {"op": "skip" if op == "next" else op}
            x, y = str(a.get("a") or "").strip()[:120], str(a.get("b") or "").strip()[:120]
            if out["op"] == "skip" and x and y:
                out.update(a=x, b=y)
            return out
        kind = GAMES.get(plain(str(a.get("game") or "")))
        if kind is None:
            raise Invalid("game: trivia, twenty, rather or song")
        plan = {"start": kind, "topic": str(a.get("topic") or a.get("category") or "").strip()[:40]}
        if kind == "trivia":
            plan["questions"] = trivia.checked(a.get("questions"))
            plan["category"] = trivia.category_id(str(a.get("category") or a.get("topic") or ""))
            d = plain(str(a.get("difficulty") or ""))
            plan["difficulty"] = d if d in trivia.DIFFICULTIES else None
        if kind == "rather" and a.get("a") and a.get("b"):
            plan["pair"] = (str(a["a"]).strip()[:120], str(a["b"]).strip()[:120])
        return plan

    async def do_game(self, plan: dict, call: Call) -> str:
        if "op" in plan:
            if plan.get("a") and self.game is not None and self.game.kind == "rather" and not self.ended:
                self.last_heard = self.ctx.clock()
                return self.after(self.game.made_up(plan["a"], plan["b"]))
            return self.control(plan["op"])
        if plan["start"] == "song" and self.screen() is None:
            return "Guess the song needs the monitor, and it's switched off."
        try:
            game = await self.make(plan)
        except Exception as e:
            log.warning("a game couldn't start: %s", type(e).__name__)
            return "I couldn't get the game ready. Is the internet down?"
        if game is None:
            return "I couldn't find anything for that. Want to try another topic?"
        self.end()
        self.game, self.last_heard, self.ended, self.drawn = game, self.ctx.clock(), 0.0, None
        if (screen := self.screen()) is not None:
            screen.cover(BOX)
        self.changed()
        return game.start()

    async def make(self, plan: dict) -> Game | None:
        kind = plan["start"]
        if kind == "twenty":
            return Twenty(self.setting("twenty_limit", 20, 5, 30), rng=self.rng)
        if kind == "rather":
            return Rather(rng=self.rng, first=plan.get("pair"))
        if kind == "song":
            async with self.ctx.http(timeout=8.0) as http:
                songs = await song.fetch(http, plan["topic"])
            return song.Song(songs, self.setting("song_rounds", 5, 1, 15), plan["topic"], self.rng) if songs else None
        questions = plan.get("questions") or []
        topic = plan["topic"]
        if not questions:
            async with self.ctx.http(timeout=8.0) as http:
                questions = await trivia.fetch(http, self.setting("trivia_questions", 10, 3, 20), plan.get("category"),
                                               plan.get("difficulty"))
            topic = trivia.CATEGORIES.get(plan.get("category"), topic) if plan.get("category") else ""
        return trivia.Trivia(questions, topic, self.rng) if questions else None

    def end(self) -> None:
        """The game's gone (taken down, replaced or stopped): the monitor given back. A finished game was already
        kept (`finished`); one stopped or left halfway isn't."""
        g, self.game, self.ended = self.game, None, 0.0
        if g is None:
            return
        if (screen := self.screen()) is not None:
            screen.uncover(BOX)
        self.changed()

    def finished(self) -> None:
        """It just ended by itself (the last question, a guess): its result kept, its last scores shown a little
        longer before it comes down (`_tidy`)."""
        g = self.game
        if g is not None and not self.ended:
            self.ended = self.ctx.clock()
            if g.scored and (g.scores or g.kind == "twenty"):
                self.db.execute("INSERT INTO games_results VALUES (?,?,?,?,?)",
                                (new_id(), g.kind, self.ctx.clock(), g.winner(), json.dumps(g.scores)))
                self.db.commit()

    def screen(self):
        return self.ctx.module("screen")

    def changed(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self.ctx.publish("changed"))   # the monitor and the page redraw
        except RuntimeError:   # no loop running (tests)
            pass

    # ---------------------------------------------------------------- playing
    def after(self, reply: str) -> str:
        """Whatever was just said or done: if that ended it, it's kept and comes down soon."""
        if self.game is not None and self.game.over:
            self.finished()
        self.changed()
        return reply

    def control(self, op: str) -> str:
        g = self.game
        if g is None:
            return "There's no game on."
        self.last_heard = self.ctx.clock()
        if op == "stop":
            line = "Okay, game over." if g.over or not g.scored or not g.scores else g.final()
            self.end()
            return line
        if g.over:
            return g.final()
        if op == "score":
            return g.score_line() if g.scored else "No scores in this one, just judgement."
        if op == "replay":
            return self.after(g.replay() or g.repeat())
        if op == "repeat":
            return self.after(g.repeat())
        return self.after(g.skip())

    def room_heard(self, text: str, call: Call) -> str | None:
        g = self.game
        if g is None or call.kind not in ("direct", "room"):
            return None
        to_pip = call.kind == "direct"
        op = control(text) if to_pip else None
        if self.ended:   # the final scores are up: only "stop the game" (takes them down); "let's play" is Claude's
            return Done(self.control("stop")) if op == "stop" else None
        if op:
            return Done(self.control(op))
        reply = g.heard(text, call, to_pip)
        if to_pip or reply:
            self.last_heard = self.ctx.clock()
        if reply:
            return Done(self.after(reply))
        if g.box() != self.drawn:   # a would-you-rather vote, a question counted: the monitor shows it
            self.drawn = g.box()
            self.changed()
        return None

    def summary(self, call: Call) -> str:
        g = self.game
        if g is None:
            return ""
        if call.kind == "join":
            return f"A game of {g.name} is on."
        text = g.summary()
        if g.kind == "twenty" and getattr(g, "final", False) and not g.over:
            g.over = True        # Claude says what it was now; the game ends a little after
            self.finished()
        return text

    def tasks(self) -> list:
        return [self._tidy()]

    async def _tidy(self) -> None:
        """Take a finished game down after a little while, and an abandoned one after 10 minutes."""
        while True:
            await asyncio.sleep(5.0)
            self.tidy()

    def tidy(self) -> None:
        now = self.ctx.clock()
        if self.game is not None and ((self.ended and now - self.ended >= SHOW_END_S)
                                      or (not self.ended and now - self.last_heard >= IDLE_S)):
            self.end()

    # ---------------------------------------------------------------- the monitor and the Games page
    def tv(self, shown: list[dict]) -> dict:
        if not any(s["type"] == BOX for s in shown):
            return {}
        if self.game is None:
            return {BOX: {}}
        box = self.game.box()
        if box.get("kind") == "song":
            box.update(volume=self.setting("clip_volume", 80, 0, 100), duck=self.setting("duck_volume", 15, 0, 100))
        return {BOX: box}

    def board(self) -> dict:
        rows = self.db.execute("SELECT game, winner, COUNT(*) FROM games_results WHERE winner != '' "
                               "GROUP BY game, winner").fetchall()
        wins: dict[str, dict] = {}
        for game, winner, n in rows:
            wins.setdefault(winner, {"name": winner, "total": 0})
            wins[winner][game] = n
            wins[winner]["total"] += n
        recent = [{"game": r["game"], "at": r["at"], "winner": r["winner"], "scores": json.loads(r["scores"])}
                  for r in self.db.execute("SELECT * FROM games_results ORDER BY at DESC LIMIT 10")]
        g = self.game
        return {"board": sorted(wins.values(), key=lambda w: -w["total"]), "recent": recent,
                "playing": {"name": g.name, "scores": g.scores, "over": g.over} if g else None,
                "has_screen": self.screen() is not None}

    async def dashboard(self, action: str, args: dict) -> dict:
        if action == "list":
            return self.board()
        if action == "stop":
            self.end()
            return self.board()
        if action == "reset":
            self.db.execute("DELETE FROM games_results")
            self.db.commit()
            self.changed()
            return self.board()
        return {"error": f"unknown action {action}"}

    # ---------------------------------------------------------------- Settings → Your data
    def export_data(self) -> dict | None:
        return {"results": [{"game": r["game"], "at": r["at"], "winner": r["winner"], "scores": json.loads(r["scores"])}
                            for r in self.db.execute("SELECT * FROM games_results ORDER BY at")]}

    async def wipe_data(self) -> int:
        n = self.db.execute("SELECT COUNT(*) FROM games_results").fetchone()[0]
        self.db.execute("DELETE FROM games_results")
        self.db.commit()
        return n

    async def forget_person(self, person_id: str, name: str) -> int:
        """Their wins go, and their name from every game's scores."""
        n = 0
        for r in self.db.execute("SELECT * FROM games_results").fetchall():
            scores = json.loads(r["scores"])
            if r["winner"] == name or name in scores:
                scores.pop(name, None)
                self.db.execute("UPDATE games_results SET winner = ?, scores = ? WHERE id = ?",
                                ("" if r["winner"] == name else r["winner"], json.dumps(scores), r["id"]))
                n += 1
        self.db.commit()
        return n


def setup(ctx) -> Module:
    return Games(ctx)
