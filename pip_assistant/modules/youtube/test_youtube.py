"""YouTube videos on the monitor (D-141): finding one, the controls said to Pip, the monitor's box and the screen."""
import httpx
import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Call, Invalid, Registry
from pip_assistant.modules.youtube.module import control_in, iso_seconds, say_length, say_time, seconds_of, short_title

SEARCH = {"items": [
    {"id": {"videoId": "aaa"}, "snippet": {"title": "Matrix multiplication &amp; you | Math Channel", "channelTitle": "Math Channel",
                                         "liveBroadcastContent": "none"}},
    {"id": {"videoId": "live"}, "snippet": {"title": "Live now", "channelTitle": "X", "liveBroadcastContent": "live"}},
    {"id": {"videoId": "bbb"}, "snippet": {"title": "Linear algebra, chapter 4", "channelTitle": "Other", "liveBroadcastContent": "none"}},
]}
LENGTHS = {"items": [{"id": "aaa", "contentDetails": {"duration": "PT10M5S"}},
                     {"id": "bbb", "contentDetails": {"duration": "PT1H2M"}}]}


class FakeYouTube:
    def __init__(self, status=200):
        self.asked, self.status = [], status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request.url)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {}})
        return httpx.Response(200, json=SEARCH if request.url.path.endswith("/search") else LENGTHS)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text('[youtube]\napi_key = "KEY"\n')
    now = [1000.0]
    yt = FakeYouTube()
    reg = Registry.load({}, infos=[read(ROOT / "screen"), read(ROOT / "youtube")], db_path=tmp_path / "pip.db",
                        transport=httpx.MockTransport(yt), clock=lambda: now[0])
    return reg, reg.modules["youtube"], reg.modules["screen"], now, yt


async def ask(reg, verb, **a):
    act, call = reg.action(verb), Call()
    out = act.do(act.check({"do": verb, **a}, call), call)
    return await out if hasattr(out, "__await__") else out


def shown(screen) -> list[str]:
    return [s["type"] for s in screen.tv_layout()["view"]["sections"]]


# ---------------------------------------------------------------- understanding what's said
@pytest.mark.parametrize("said, op, seconds", [
    ("Hey Pip, pause the video.", "pause", None),
    ("Pip, stop the video", "pause", None),
    ("Pip, play", "play", None),
    ("Pip, could you resume the video?", "play", None),
    ("Hey Pip, fast forward.", "forward", None),
    ("Pip, fast-forward thirty seconds", "forward", 30),
    ("Pip, skip ahead a minute and a half", "forward", 90),
    ("Pip, skip 2 minutes", "forward", 120),
    ("Pip, go back ten seconds", "back", 10),
    ("Pip, rewind half a minute", "back", 30),
    ("Hey Pip, go to four minutes and thirty seconds", "go_to", 270),
    ("Pip, skip to 4:30", "go_to", 270),
    ("Pip, jump to minute twelve", "go_to", 720),
    ("Pip, go to 1 minute 5", "go_to", 65),
    ("Pip, start it over", "restart", None),
    ("Pip, go back to the beginning", "restart", None),
    ("Pip, turn it up", "louder", None),
    ("Pip, quieter please", "quieter", None),
    ("Pip, a different one", "next", None),
    ("Pip, play the next video", "next", None),
    ("Hey Pip, take down the video.", "close", None),
    ("Pip, take it down", "close", None),
    ("Pip, close the video", "close", None),
])
def test_controls_said_to_pip(said, op, seconds):
    assert control_in(said) == (op, seconds)


@pytest.mark.parametrize("said", [
    "Pip, pull up a video about linear algebra",   # a new video: Claude makes the search
    "Pip, pause the video when he gets to the proof",
    "Pip, stop",                                    # stops Pip talking (his own command)
    "Pip, hold on",                                 # Pip waits (his own)
    "Pip, turn off the lights",                     # home control
    "Pip, set a timer for 4 minutes",
    "Pip, what's this video about?",
])
def test_anything_else_isnt_a_control(said):
    assert control_in(said) is None


def test_times():
    assert seconds_of("2 and a half minutes") == 150 and seconds_of("1 hour 5") == 3900 and seconds_of("30") == 30
    assert seconds_of("1:02:03") == 3723 and seconds_of("fast forward") is None
    assert say_time(270) == "4:30" and say_time(3723) == "1:02:03"
    assert say_length(45) == "45 seconds" and say_length(605) == "10 minutes" and say_length(3720) == "1 hour 2 minutes"
    assert iso_seconds("PT1H2M3S") == 3723 and iso_seconds("P0D") == 0
    assert short_title("Matrix multiplication &amp; you | Math Channel") == "Matrix multiplication & you"


# ---------------------------------------------------------------- finding a video and putting it up
async def test_a_video_is_found_and_covers_the_monitor(env):
    reg, yt, screen, now, fake = env
    before = screen.tv_layout()["view"]
    said = await ask(reg, "play_video", query="matrix multiplication explained")
    assert said == "Here's “Matrix multiplication & you” from Math Channel, 10 minutes."
    q = fake.asked[0].params
    assert q["q"] == "matrix multiplication explained" and q["videoEmbeddable"] == "true" and q["key"] == "KEY"
    assert shown(screen) == ["video"]                                   # the video covers the screen, the face beside it
    face = screen.tv_layout()["view"]["face_shown"]
    assert face["w"] == 3 and screen.tv_layout()["view"]["sections"][0] == {"type": "video", "x": 3, "y": 0, "w": 9, "h": 8}
    box = reg.tv()["boxes"]["video"]
    assert box["id"] == "aaa" and box["playing"] and box["start"] == 0 and box["commands"] == []
    assert [v["id"] for v in yt.found] == ["aaa", "bbb"]                 # the live stream is left out
    assert "playing at 0:00 of 10:05" in yt.summary(Call())

    assert await ask(reg, "video", op="next") == "Here's “Linear algebra, chapter 4” from Other, 1 hour 2 minutes."
    assert await ask(reg, "video", op="next") == "That was the last one I found. Ask me for something else."

    assert await ask(reg, "video", op="close") == "Taken down."
    assert screen.tv_layout()["view"] == before and reg.tv()["boxes"] == {}   # the screen as it was
    assert yt.summary(Call()) == ""


async def test_changing_the_screen_under_a_video_shows_after(env):
    reg, yt, screen, now, _ = env
    await ask(reg, "play_video", query="x")
    await ask(reg, "add_box", type="clock")
    assert shown(screen) == ["video"] and "covers the monitor" in screen.summary(Call())
    await ask(reg, "video", op="close")
    assert "clock" in shown(screen)


async def test_no_key_and_youtube_failing(env, tmp_path):
    reg, yt, screen, now, fake = env
    fake.status = 403
    assert "used up today's searches" in await ask(reg, "play_video", query="x")
    assert yt.video is None and shown(screen) != ["video"]
    (tmp_path / "secrets.toml").write_text("")
    with pytest.raises(Invalid):
        reg.action("play_video").check({"do": "play_video", "query": "x"}, Call())


# ---------------------------------------------------------------- controlling it
async def test_controls_go_to_the_monitor_in_order(env):
    reg, yt, screen, now, _ = env
    assert yt.room_heard("Pip, pause the video", Call(kind="direct")) is None   # nothing up: Claude says so
    await ask(reg, "play_video", query="x")
    now[0] += 60
    assert yt.room_heard("pause the video", Call(kind="room")) is None          # only said to Pip
    assert yt.room_heard("Pip, pause the video", Call(kind="direct")) == "Paused."
    now[0] += 100                                                                # paused: it stays at 1:00
    assert yt.room_heard("Pip, fast forward thirty seconds", Call(kind="direct")) == "Skipped ahead 30 seconds."
    assert yt.room_heard("Pip, go back", Call(kind="direct")) == "Back 10 seconds."
    assert yt.room_heard("Pip, go to 4:30", Call(kind="direct")) == "Going to 4:30."
    assert yt.room_heard("Pip, go to 20 minutes", Call(kind="direct")) == "It's only 10 minutes long."
    assert yt.room_heard("Pip, turn it up", Call(kind="direct")) == "Louder."
    box = reg.tv()["boxes"]["video"]
    assert [(c["op"], c.get("s")) for c in box["commands"]] == \
        [("pause", None), ("seek_by", 30), ("seek_by", -10), ("seek_to", 270), ("play", None)]
    assert [c["n"] for c in box["commands"]] == [1, 2, 3, 4, 5]
    assert box["start"] == 270 and box["playing"] and box["volume"] == 85
    now[0] += 30
    assert reg.tv()["boxes"]["video"]["start"] == 300   # where a reloaded monitor carries on from
    assert yt.room_heard("Pip, take down the video", Call(kind="direct")) == "Taken down."
    assert yt.room_heard("Pip, pause the video", Call(kind="direct")) is None


async def test_the_video_page(env):
    reg, yt, screen, now, _ = env
    assert (await yt.dashboard("status", {}))["video"] is None
    r = await yt.dashboard("play", {"query": "x"})
    assert r["video"]["id"] == "aaa" and r["more"] == 1 and r["said"].startswith("Here's")
    r = await yt.dashboard("control", {"op": "pause"})
    assert r["said"] == "Paused." and not r["video"]["playing"]
    with pytest.raises(Invalid):
        await yt.dashboard("control", {"op": "explode"})


def test_the_box_is_declared_and_drawn():
    info = read(ROOT / "youtube")
    assert info.boxes["video"]["label"] == "Video" and (info.path / "static" / "monitor.js").exists()
    assert info.needs == ["screen"] and "www.googleapis.com" in info.hosts
