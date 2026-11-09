"""The google module: connecting several people's accounts, against a pretend Google."""
import tomllib
from urllib.parse import parse_qs, urlparse

import pytest

from pip_assistant.common.modules import ROOT, read
from pip_assistant.core.modules import Registry
from pip_assistant.core.people import People
from pip_assistant.modules.google.testing import FakeGoogle


class NoVoices:
    available = False


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("PIP_DATA", str(tmp_path))
    (tmp_path / "secrets.toml").write_text('# my keys\ngroq_api_key = "gsk-test"\n')
    fake = FakeGoogle()
    people = People(tmp_path / "pip.db", embedder=NoVoices())
    reg = Registry.load({}, infos=[read(ROOT / "google")], people=people, db_path=tmp_path / "pip.db",
                        transport=fake.transport())
    return reg.modules["google"], reg.get("google"), fake, people, tmp_path


async def connect(mod, g, fake, person_id, email):
    url = (await mod.dashboard("connect", {"person_id": person_id}))["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    pasted = f"http://127.0.0.1:8765/?state={state}&code={fake.sign_in_code(email)}&scope=x"
    return await mod.dashboard("finish", {"pasted": pasted})


async def test_two_people_connect_their_own_accounts(setup):
    mod, g, fake, people, tmp = setup
    with pytest.raises(ValueError):
        await mod.dashboard("connect", {"person_id": "x"})        # no client set up yet
    await mod.dashboard("save_client", {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret-1"})
    landen, sam = people.add_person("Landen", "admin"), people.add_person("Sam")
    a = await connect(mod, g, fake, landen["id"], "landen@example.com")
    b = await connect(mod, g, fake, sam["id"], "sam@example.com")
    assert a["email"] == "landen@example.com" and b["email"] == "sam@example.com"
    assert g.for_person(sam["id"])["email"] == "sam@example.com"
    secrets = tomllib.loads((tmp / "secrets.toml").read_text())
    assert secrets["groq_api_key"] == "gsk-test" and "# my keys" in (tmp / "secrets.toml").read_text()   # rest kept
    assert len(secrets["google"]["accounts"]) == 2
    snap = await mod.dashboard("status", {})
    assert "refresh" not in str(snap) and "GOCSPX" not in str(snap)   # secrets never go to the dashboard
    assert [x["person"] for x in snap["accounts"]] == ["Landen", "Sam"]
    # Using an account: a fresh access token when needed; refused → "needs reconnecting"
    g._tokens.clear()
    r = await g.request(g.for_person(sam["id"])["id"], "GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList")
    assert r.status_code == 200
    fake.refuse_refresh = True
    g._tokens.clear()
    from pip_assistant.modules.google.module import NeedsReconnect
    with pytest.raises(NeedsReconnect):
        await g.request(g.accounts()[0]["id"], "GET",
                        "https://www.googleapis.com/calendar/v3/users/me/calendarList")
    assert g.accounts()[0]["status"] == "reconnect"
    # Disconnect revokes at Google and forgets the token
    sam_acc = g.accounts(sam["id"])[0]["id"]
    await mod.dashboard("disconnect", {"id": sam_acc})
    assert "refresh-sam@example.com" in fake.revoked
    assert sam_acc not in tomllib.loads((tmp / "secrets.toml").read_text())["google"]["accounts"]


async def test_a_pasted_address_from_another_sign_in_is_refused(setup):
    mod, g, fake, people, tmp = setup
    await mod.dashboard("save_client", {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret-1"})
    p = people.add_person("Landen", "admin")
    await mod.dashboard("connect", {"person_id": p["id"]})
    with pytest.raises(ValueError):
        await mod.dashboard("finish", {"pasted": f"http://127.0.0.1:8765/?state=wrong&code={fake.sign_in_code('x@y.z')}"})


async def test_unticked_permissions_are_shown_not_hidden(setup):
    """Google's "what Pip can access" boxes start unticked: Pip says so instead of looking connected."""
    mod, g, fake, people, tmp = setup
    await mod.dashboard("save_client", {"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret-1"})
    p = people.add_person("Landen", "admin")
    fake.only_email = True
    a = await connect(mod, g, fake, p["id"], "landen@example.com")
    acc = g.accounts()[0]
    assert acc["status"] == "missing" and "Select all" in acc["error"]
    assert g.household_accounts() == []                 # the calendar and to-dos leave it alone
    # An account connected before this check: the first refused request marks it the same way
    g.set(acc["id"], status="ok", error="")
    from pip_assistant.modules.google.module import NeedsReconnect
    with pytest.raises(NeedsReconnect):
        await g.request(acc["id"], "GET", "https://www.googleapis.com/calendar/v3/users/me/calendarList")
    assert g.accounts()[0]["status"] == "missing"
    # Connecting again with the boxes ticked fixes it
    fake.only_email = False
    await connect(mod, g, fake, p["id"], "landen@example.com")
    assert g.accounts()[0]["status"] == "ok" and len(g.accounts()) == 1


async def test_it_may_only_reach_google(setup):
    mod, g, fake, people, tmp = setup
    from pip_assistant.core.modules import HostNotAllowed
    with pytest.raises(HostNotAllowed):
        async with g.ctx.http() as http:
            await http.get("https://example.com/")
