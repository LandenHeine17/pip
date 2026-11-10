"""The memory and people tests' shared starting point: an empty memory and household at 9 AM on 2026-11-05, and
Alex (admin) and Sam with a Commands to act through."""
from datetime import datetime

import pytest
from helpers import TaggedVoices as FakeEmbedder
from helpers import clock_at

from pip_assistant.core.commands import Commands
from pip_assistant.core.memory import Memory
from pip_assistant.core.people import Match, People
from pip_assistant.core.schedule import Schedule

NOW = datetime(2026, 11, 5, 9, 0)
Clock = clock_at(NOW)



@pytest.fixture
def mem(tmp_path):
    return Memory(tmp_path / "pip.db", clock=Clock())


@pytest.fixture
def people(tmp_path):
    return People(tmp_path / "pip.db", clock=Clock(), embedder=FakeEmbedder())


@pytest.fixture
def ctx(tmp_path, mem, people):
    clock = Clock()
    alex = people.add_person("Alex", "admin")
    sam = people.add_person("Sam")
    cmds = Commands(Schedule(tmp_path / "pip.db", clock=clock), clock=clock)
    return cmds, {"memory": mem, "people": people, "speaker": Match(alex["id"], "Alex", "sure", 0.7)}, alex, sam
