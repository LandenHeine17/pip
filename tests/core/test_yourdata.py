"""Settings → Your data (core/yourdata.py, PIP-NEW-034, D-128): download a copy, delete everything."""
import asyncio

from helpers import OneVoice

from pip_assistant.core.memory import Memory
from pip_assistant.core.modules import Module, Registry
from pip_assistant.core.people import People
from pip_assistant.core.yourdata import YourData


class Notes(Module):
    def __init__(self):
        self.notes = ["talked about chips"]

    def export_data(self):
        return {"notes": list(self.notes)}

    async def wipe_data(self):
        n, self.notes = len(self.notes), []
        return n


def test_export_and_delete_everything(tmp_path, monkeypatch):
    from pip_assistant.common import paths
    monkeypatch.setattr(paths, "user_settings", lambda: tmp_path / "settings.toml")
    (tmp_path / "settings.toml").write_text("[conversation]\nmax_sentences = 3\n")
    mem, people = Memory(tmp_path / "pip.db"), People(tmp_path / "pip.db", embedder=OneVoice())
    sam = people.add_person("Sam")
    asyncio.run(people.add_samples(sam["id"], [b"x" * 64000], "test"))
    mem.add("Sam likes tea", "preference", [sam["id"]], source="Sam, by voice")
    reg = Registry()
    reg.modules["notes"] = Notes()
    d = YourData(mem, people, reg)
    out = asyncio.run(d.handle({"action": "export"}))
    assert out["memories"][0] == {**out["memories"][0], "text": "Sam likes tea", "about": ["Sam"]}
    assert out["people"] == [{"name": "Sam", "role": "member"}] and out["modules"] == {"notes": {"notes": ["talked about chips"]}}
    assert "max_sentences = 3" in out["settings_toml"] and "'emb'" not in str(out)      # never the voiceprints
    assert asyncio.run(d.handle({"action": "wipe"}))["error"]                           # needs DELETE
    r = asyncio.run(d.handle({"action": "wipe", "confirm": "DELETE"}))
    assert r == {"ok": True, "memories": 1, "voices": 1, "other": 1}
    assert mem.items() == [] and people.profiles() == {} and people.all()[0]["name"] == "Sam"
