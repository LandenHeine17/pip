"""tools/propose_moments.py (D-147): Claude names the memories that are really funny moments; they're only flagged
for the owner's review on the Memory card, never moved or deleted by the tool."""
import importlib.util
from pathlib import Path

from pip_assistant.core.memory import Memory

spec = importlib.util.spec_from_file_location("propose_moments", Path(__file__).resolve().parents[2] / "tools" / "propose_moments.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def test_only_memories_claude_was_shown_can_be_picked(tmp_path):
    mem = Memory(tmp_path / "pip.db")
    fact, _ = mem.add("The bins go out on Tuesdays", "household")
    joke, _ = mem.add("Sam once argued with the toaster for ten minutes", "household")
    secret, _ = mem.add("The spare key is under the blue pot", "household", owner="s", private=True)
    rows = tool.candidates(mem)
    assert {r["id"] for r in rows} == {fact["id"], joke["id"]}                  # never a private one
    reply = f"{mem.short(joke['id'])}\n{mem.short(joke['id'])} again\n{mem.short(secret['id'])}\n#mzzzzzz\nand some chatter"
    assert [r["id"] for r in tool.picked(reply, rows, mem)] == [joke["id"]]     # once; nothing it wasn't shown
    assert tool.picked("NONE", rows, mem) == [] and tool.picked("", rows, mem) == []
    assert tool.FLAG.startswith("Move?")                                        # what the Memory card's buttons look for
    mem.update(joke["id"], review=tool.FLAG)
    assert {r["id"] for r in tool.candidates(mem)} == {fact["id"]}              # already flagged: not asked again
