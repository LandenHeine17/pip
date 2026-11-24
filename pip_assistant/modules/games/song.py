"""Guess the song (D-144): a 30-second clip plays on the monitor, and the first to name the song or its singer
scores.

The clips are Deezer's free previews (api.deezer.com: no key, no account): today's chart, or a playlist found by what
they asked for ("guess the song, 2000s"). Sound only: the monitor shows the round and an equaliser, never the title
(a YouTube video would show it, and YouTube's rules don't allow hiding its player). The monitor's browser plays the
clip (static/monitor.js), quieter while Pip listens or talks. A guess counts said to Pip or in the room."""
import random
import re

from .common import Game, bare, mentions, who

API = "https://api.deezer.com"


def short(title: str) -> str:
    """"Dracula (with JENNIE)" → "Dracula"; "Song - Remastered 2011" → "Song"."""
    t = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", title or "")
    t = re.sub(r"\s+-\s+.*$", "", t)
    return t.strip() or (title or "").strip()


def tracks(data: list[dict]) -> list[dict]:
    """Deezer's tracks → {"title", "artist", "clip"}: only ones with a clip, one per title."""
    out, seen = [], set()
    for t in data or []:
        title, artist, clip = short(t.get("title_short") or t.get("title") or ""), (t.get("artist") or {}).get("name", ""), t.get("preview")
        if title and artist and clip and clip.startswith("https://") and title.lower() not in seen:
            seen.add(title.lower())
            out.append({"title": title, "artist": artist, "clip": clip})
    return out


async def fetch(http, topic: str = "") -> list[dict]:
    """Songs for the game: a playlist matching `topic` (the biggest of the first few found), else today's chart."""
    if topic:
        r = await http.get(f"{API}/search/playlist", params={"q": topic, "limit": 5})
        r.raise_for_status()
        found = [p for p in (r.json().get("data") or []) if (p.get("nb_tracks") or 0) >= 15]
        if found:
            best = max(found[:3], key=lambda p: p.get("nb_tracks") or 0)
            r = await http.get(f"{API}/playlist/{int(best['id'])}/tracks", params={"limit": 100})
            r.raise_for_status()
            got = tracks(r.json().get("data") or [])
            if len(got) >= 5:
                return got
    r = await http.get(f"{API}/chart/0/tracks", params={"limit": 100})
    r.raise_for_status()
    return tracks(r.json().get("data") or [])


class Song(Game):
    kind, name = "song", "guess the song"

    def __init__(self, songs: list[dict], rounds: int = 5, topic: str = "", rng: random.Random | None = None):
        super().__init__()
        rng = rng or random.Random()
        self.songs = rng.sample(songs, min(len(songs), max(1, int(rounds))))
        self.topic = topic
        self.i = 0
        self.plays = 1      # the monitor plays the clip again whenever this goes up

    @property
    def now(self) -> dict:
        return self.songs[self.i]

    def ask(self) -> str:
        return f"Round {self.i + 1} of {len(self.songs)}: what's this song?"

    def start(self) -> str:
        about = f" ({self.topic})" if self.topic else ""
        return f"Guess the song{about}! Name the song or who sings it. " + self.ask()

    def heard(self, text: str, call, said_to_pip: bool) -> str | None:
        if self.over:
            return None
        t, s = bare(text), self.now
        if not (mentions(t, s["title"], close=0.82) or mentions(t, s["artist"], close=0.82)):
            return None
        name = who(call)
        self.point(name)
        self.last = {"who": name, "title": s["title"], "artist": s["artist"]}
        got = f"Yes, {name}! " if name != "Guest" else "Yes! "
        return got + f"It's {s['title']} by {s['artist']}. " + self.advance()

    def advance(self) -> str:
        self.i += 1
        if self.i >= len(self.songs):
            self.i = len(self.songs) - 1
            self.over = True
            return self.final()
        self.plays += 1
        return self.ask()

    def skip(self) -> str:
        s = self.now
        self.last = {"who": "", "title": s["title"], "artist": s["artist"]}
        return f"It was {s['title']} by {s['artist']}. " + self.advance()

    def replay(self) -> str:
        self.plays += 1
        return "Here it is again."

    def repeat(self) -> str:
        return self.replay()

    def box(self) -> dict:
        return {"kind": self.kind, "round": self.i + 1, "of": len(self.songs), "topic": self.topic,
                "clip": "" if self.over else self.now["clip"], "plays": self.plays, "scores": self.scores,
                "last": self.last, "over": self.over}

    def summary(self) -> str:
        s = self.now
        return (f"Guess the song is on: round {self.i + 1} of {len(self.songs)}; the clip playing is \"{s['title']}\" by "
                f"{s['artist']}. {self.score_line()} Pip judges the guesses himself: a guess reaching you is wrong, "
                f"so say it isn't that, without ever saying the song or the singer; a small hint only if they ask.")
