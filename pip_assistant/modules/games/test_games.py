"""The games module (D-144), loaded the way pip-core loads modules, with a pretend Open Trivia DB and Deezer."""
import random
from types import SimpleNamespace
from urllib.parse import quote

import httpx
import pytest

from pip_assistant.common.modules import ROOT, discover, read
from pip_assistant.core.modules import Call, Done, Registry
from pip_assistant.modules.games import trivia
from pip_assistant.modules.games.common import bare, control, mentions
from pip_assistant.modules.games.twenty import Twenty

Q = [("What is the capital of Australia?", "Canberra", ["Sydney", "Melbourne", "Perth"]),
     ("How many legs does a spider have?", "8", ["6", "10", "12"]),
     ("Which planet is known as the Red Planet?", "Mars", ["Venus", "Jupiter", "Saturn"])]
CHART = {"data": [{"title_short": "Blinding Lights", "artist": {"name": "The Weeknd"}, "preview": "https://cdnt-preview.dzcdn.net/a.mp3"},
                  {"title_short": "Dracula (with JENNIE)", "artist": {"name": "Tame Impala"}, "preview": "https://cdnt-preview.dzcdn.net/b.mp3"},
                  {"title_short": "No clip", "artist": {"name": "X"}, "preview": ""}]}


class Internet:
    def __init__(self):
        self.asked = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url)
        if request.url.host == "opentdb.com":
            return httpx.Response(200, json={"response_code": 0, "results": [
                {"question": quote(q), "correct_answer": quote(r), "incorrect_answers": [quote(w) for w in ws]}
                for q, r, ws in Q]})
        if request.url.path.startswith("/chart"):
            return httpx.Response(200, json=CHART)
        if request.url.path.startswith("/search/playlist"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def person(name):
    return Call(speaker=SimpleNamespace(person_id="p-" + name, name=name, level="sure", guest=False), kind="direct")


def room(name=None):
    c = person(name) if name else Call()
    c.kind = "room"
    return c


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    net, clock = Internet(), Clock()
    reg = Registry.load({}, infos=[read(ROOT / "screen"), read(ROOT / "games")], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(net), clock=clock)
    games = reg.modules["games"]
    games.rng = random.Random(4)
    return reg, games, reg.modules["screen"], net, clock


async def play(reg, call=None, **a):
    act = reg.action("game")
    call = call or Call()
    return await act.do(act.check({"do": "game", **a}, call), call)


def covered(screen):
    return screen.api.covered()


# ---------------------------------------------------------------- hearing answers
def test_answers_are_heard_loosely():
    assert bare("Pip, I think it's B!") == "b" and bare("is it Canberra, final answer") == "canberra"
    assert control("Pip, stop the game") == "stop" and control("skip") == "skip" and control("next question") == "skip"
    assert control("what's the score?") == "score" and control("Pip, play it again") == "replay"
    assert control("repeat the question") == "repeat" and control("I think it's Canberra") is None
    assert mentions("it's the weekend", "The Weeknd") and mentions("blinding lights!", "Blinding Lights")
    assert not mentions("I love lights", "Blinding Lights") and not mentions("bus", "U2")


def test_trivia_categories_and_claudes_questions():
    assert trivia.category_id("Science & Nature") == 17 and trivia.category_id("science") == 17
    assert trivia.category_id("movies") == 11 and trivia.category_id("Minecraft") is None
    good = {"q": "What block do you mine for diamonds?", "right": "Diamond ore", "wrong": ["Coal", "Dirt", "Sand"]}
    assert trivia.checked([good, {"q": "x"}, {"q": "y", "right": "a", "wrong": ["a", "b", "c"]}, "nonsense"]) == [good]


# ---------------------------------------------------------------- trivia
async def test_trivia_from_open_trivia_db_first_answer_counts(env):
    reg, games, screen, net, clock = env
    said = await play(reg, game="trivia", category="Geography")
    assert said.startswith("Trivia about Geography! ") and said.endswith("?") and "Question 1 of 3" in said
    assert net.asked[0].params["category"] == "22" and covered(screen) == "game"
    g = games.game
    right = "ABCD"[g.now["right"]]
    reply = reg.room_heard(f"Pip, I think it's {right}", person("Sam"))
    assert isinstance(reply, Done) and reply.startswith("Right, Sam! Question 2 of 3.")
    wrong = next(c for c in g.now["choices"] if c != g.now["choices"][g.now["right"]])
    reply = reg.room_heard(wrong, room("Alex"))                       # out loud in the room counts too
    assert reply.startswith("Nope, it was ")
    assert reg.room_heard("so anyway, about dinner", room("Alex")) is None   # room talk that isn't an answer
    assert reg.room_heard("Pip, give me a hint", person("Sam")) is None      # for Claude, who knows the answer
    assert "the answer is" in reg.summary(Call()) and "never say the answer" in reg.summary(Call())
    reply = reg.room_heard("Pip, what's the score?", person("Sam"))
    assert reply == "The score: Sam 1 and Alex 0."
    reply = reg.room_heard("Venus", room("Alex"))
    assert reply == "Nope, it was " + "ABCD"[g.now["right"]] + ", Mars. That's the game! Sam wins with 1, then Alex 0."
    board = (await games.dashboard("list", {}))["board"]
    assert board and board[0]["total"] == 1


async def test_trivia_on_a_topic_claude_wrote(env):
    reg, games, screen, net, clock = env
    qs = [{"q": "What do creepers do?", "right": "Explode", "wrong": ["Fly", "Swim", "Sing"]}] * 3
    said = await play(reg, game="trivia", topic="Minecraft", questions=qs)
    assert said.startswith("Trivia about Minecraft!") and net.asked == []
    reply = reg.room_heard("explode", room())
    assert reply.startswith("Right! ")   # someone Pip doesn't know: no name


async def test_skip_repeat_stop(env):
    reg, games, screen, net, clock = env
    await play(reg, game="trivia")
    assert reg.room_heard("Pip, repeat the question", person("Sam")).startswith("Question 1 of 3.")
    assert reg.room_heard("Pip, skip", person("Sam")).startswith("It was ")
    assert games.game.i == 1
    assert reg.room_heard("Pip, stop the game", person("Sam")) == "Okay, game over."
    assert games.game is None and covered(screen) is None
    assert (await games.dashboard("list", {}))["recent"] == []   # stopped halfway: not kept


async def test_a_finished_game_comes_down_a_little_later_and_an_idle_one_after_10_minutes(env):
    reg, games, screen, net, clock = env
    await play(reg, game="trivia")
    for _ in range(3):
        reg.room_heard("Pip, skip", person("Sam"))
    assert games.game.over and covered(screen) == "game"
    assert reg.room_heard("A", room()) is None                    # over: nothing more counts
    clock.t += 31
    games.tidy()
    assert games.game is None and covered(screen) is None
    await play(reg, game="rather")
    clock.t += 599
    games.tidy()
    assert games.game is not None
    clock.t += 2
    games.tidy()
    assert games.game is None


# ---------------------------------------------------------------- 20 questions
async def test_twenty_questions(env):
    reg, games, screen, net, clock = env
    said = await play(reg, game="twenty questions")
    assert "I'm thinking of" in said and said.endswith("What's your first question?")
    games.game = Twenty(limit=5, pick=("an animal", ["penguin", "penguins"]))
    assert reg.room_heard("Pip, is it bigger than a cat?", person("Sam")) is None   # Claude answers it
    assert len(games.game.asked) == 1 and '"penguin"' in reg.summary(Call())
    reg.room_heard("Pip, does it live in the ocean", person("Sam"))
    assert len(games.game.asked) == 2
    reply = reg.room_heard("Pip, is it a penguin?", person("Alex"))
    assert reply == "Yes! It's a penguin! Alex got it in 3 questions."
    assert (await games.dashboard("list", {}))["recent"][0]["winner"] == "Alex"


async def test_twenty_questions_run_out_and_pip_wins(env):
    reg, games, screen, net, clock = env
    await play(reg, game="twenty")
    games.game = Twenty(limit=5, pick=("something you eat", ["pizza"]))
    for _ in range(5):
        reg.room_heard("Pip, is it sweet?", person("Sam"))
    assert "last question" in reg.summary(Call()) and "it was pizza" in reg.summary(Call())
    assert games.game.over
    assert (await games.dashboard("list", {}))["recent"][0]["winner"] == "Pip"


# ---------------------------------------------------------------- would you rather
async def test_would_you_rather(env):
    reg, games, screen, net, clock = env
    said = await play(reg, game="rather")
    assert said.startswith("Would you rather! Would you rather ") and said.endswith("?")
    reg.room_heard("Pip, the first one, obviously", person("Sam"))
    assert reg.room_heard("Pip, I'd rather the second one", person("Alex")) is None   # Claude roasts it
    assert games.game.votes == {"Sam": 0}   # "I'd rather the second one" isn't plainly a choice
    assert "Sam chose" in reg.summary(Call()) and "roast" in reg.summary(Call())
    nxt = reg.room_heard("Pip, next", person("Sam"))
    assert nxt.startswith("Would you rather ") and games.game.votes == {}
    made = await play(reg, op="next", a="eat a lemon", b="eat a chilli")
    assert made == "Would you rather eat a lemon, or eat a chilli?"
    assert reg.room_heard("Pip, what's the score", person("Sam")) == "No scores in this one, just judgement."


# ---------------------------------------------------------------- guess the song
async def test_guess_the_song(env):
    reg, games, screen, net, clock = env
    games.ctx.settings["song_rounds"] = 2
    said = await play(reg, game="song")
    assert said == "Guess the song! Name the song or who sings it. Round 1 of 2: what's this song?"
    box = reg.tv()["boxes"]["game"]
    assert box["clip"].startswith("https://cdnt-preview.dzcdn.net/") and box["volume"] == 80 and box["duck"] == 15
    assert "title" not in str(box).lower() or "Blinding" not in str(box)   # never the answer on the monitor
    g = games.game
    first = g.now
    plays = g.plays
    assert reg.room_heard("Pip, play it again", person("Sam")) == "Here it is again." and g.plays == plays + 1
    guess = "it's the weekend!" if first["artist"] == "The Weeknd" else "Tame Impala"
    reply = reg.room_heard(guess, room("Sam"))
    assert reply.startswith(f"Yes, Sam! It's {first['title']} by {first['artist']}. Round 2 of 2")
    reply = reg.room_heard("Pip, skip", person("Sam"))
    assert reply.startswith("It was ") and reply.endswith("That's the game! Sam wins with 1.")


async def test_guess_the_song_needs_the_monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    reg = Registry.load({}, infos=[read(ROOT / "games")], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(Internet()))
    assert await play(reg, game="song") == "Guess the song needs the monitor, and it's switched off."
    assert (await play(reg, game="rather")).startswith("Would you rather")   # the rest work without it


# ---------------------------------------------------------------- plug and play, and people's data
def test_switched_off_its_gone():
    info = next(i for i in discover({"modules": {"games": {"enabled": False}}}) if i.name == "games")
    assert not info.enabled


async def test_nothing_on_until_asked(env):
    reg, games, screen, net, clock = env
    assert reg.room_heard("let's play trivia", room()) is None and games.summary(Call()) == ""
    assert reg.tv()["boxes"] == {} or reg.tv()["boxes"].get("game") in ({}, None)


async def test_forgetting_someone_and_wiping(env):
    reg, games, screen, net, clock = env
    await play(reg, game="twenty")
    games.game = Twenty(limit=5, pick=("an animal", ["penguin"]))
    reg.room_heard("Pip, is it a penguin", person("Sam"))
    assert await games.forget_person("p-Sam", "Sam") == 1
    assert (await games.dashboard("list", {}))["board"] == []
    assert await games.wipe_data() == 1
