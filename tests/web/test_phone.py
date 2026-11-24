"""Talking to Pip on a phone (D-149): /phone and its scripts, the websocket passed through to pip-ears unchanged, where
Pip is reaching the pages, and which page plays videos' and songs' sound."""
import fnmatch
import json
import socket
import threading

from starlette.testclient import TestClient

from pip_assistant.common import paths
from pip_assistant.common.messages import Message
from pip_assistant.common.wire import SOUND, TEXT, pack
from pip_assistant.web.main import State, build_app, listen


class FakeBus:
    def __init__(self, reply=None):
        self.handlers, self.reply = [], reply or {}

    class connected:
        @staticmethod
        def is_set():
            return True

    def subscribe(self, pattern, handler=None):
        self.handlers.append((pattern, handler))

    async def request(self, target, kind, data=None, private=None, timeout=5):
        return Message("reply", kind, data={}, private=self.reply.get(kind, {}))

    async def deliver(self, msg: Message):
        for pattern, h in self.handlers:
            if h and fnmatch.fnmatchcase(msg.type, pattern):
                await h(msg)


def test_phone_page_and_its_scripts():
    client = TestClient(build_app(None, State(), {}))
    page = client.get("/phone")
    assert page.status_code == 200 and "<canvas" in page.text and "/phone.js" in page.text
    assert "orientation: portrait" in page.text
    for path, has in (("/phone.js", "/api/phone"), ("/phone-audio.js", "registerProcessor")):
        r = client.get(path)
        assert r.status_code == 200 and "javascript" in r.headers["content-type"] and has in r.text
    assert 'href="/phone"' in client.get("/talk").text


def recv_exact(conn, n):
    out = b""
    while len(out) < n:
        chunk = conn.recv(n - len(out))
        if not chunk:
            raise ConnectionError
        out += chunk
    return out


def test_the_websocket_is_passed_to_pip_ears_and_back(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(paths.remote_socket()))
    server.listen(1)
    got = []

    def ears():   # a stand-in for pip-ears: says hello, takes one sound message, sends sound back, then lets go
        conn, _ = server.accept()
        conn.sendall(pack(TEXT, b'{"hello": {}}'))
        length = int.from_bytes(recv_exact(conn, 4), "big")
        kind = recv_exact(conn, 1)[0]
        got.append((kind, recv_exact(conn, length)))
        conn.sendall(pack(SOUND, b"\x22\x56\x00\x00" + b"\x01\x00" * 4) + pack(TEXT, b'{"bye": "idle"}'))
        conn.close()
    t = threading.Thread(target=ears, daemon=True)
    t.start()
    client = TestClient(build_app(None, State(), {}))
    with client.websocket_connect("/api/phone") as ws:
        assert json.loads(ws.receive_text()) == {"hello": {}}
        ws.send_bytes(b"\x05\x00" * 1280)
        assert ws.receive_bytes() == b"\x22\x56\x00\x00" + b"\x01\x00" * 4
        assert json.loads(ws.receive_text()) == {"bye": "idle"}
    t.join(2)
    assert got == [(SOUND, b"\x05\x00" * 1280)]
    server.close()


def test_pip_ears_not_running_is_said_plainly(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    client = TestClient(build_app(None, State(), {}))
    with client.websocket_connect("/api/phone") as ws:
        assert json.loads(ws.receive_text()) == {"bye": "unavailable"}


async def test_where_pip_is_reaches_the_dashboard_and_the_screen():
    bus, state = FakeBus(), State()
    listen(bus, state)
    import asyncio
    dash, tv = asyncio.Queue(), asyncio.Queue()
    state.listeners.add(dash)
    state.tv_listeners.add(tv)
    await bus.deliver(Message("event", "ears.route", data={"to": "phone", "why": "taken"}))
    assert state.route == "phone"
    assert dash.get_nowait() == ("route", {"route": "phone"}) and tv.get_nowait() == ("route", {"route": "phone"})


def test_only_the_monitor_plays_sound_while_pip_is_in_the_room():
    state = State()
    client = TestClient(build_app(FakeBus({"core.tv": {}}), state, {}))
    assert client.get("/api/tv", headers={"host": "127.0.0.1:8790"}).json()["sound_here"] is True
    assert client.get("/api/tv", headers={"host": "pi.example.ts.net:8791"}).json()["sound_here"] is False
    state.route = "phone"   # a phone has Pip: it plays them itself, the monitor shows them muted
    assert client.get("/api/tv", headers={"host": "127.0.0.1:8790"}).json()["sound_here"] is False
    assert client.get("/api/health").json()["route"] == "phone"
