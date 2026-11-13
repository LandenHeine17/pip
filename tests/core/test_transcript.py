"""What Claude reads of the room (core/transcript.py, D-92): every line heard and said, the last 40 and at most
3 hours back, who said it, and Pip's own lines cut short by someone talking over him."""
import time

from pip_assistant.core.transcript import Transcript


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def test_lines_are_kept_with_who_said_them():
    clock = Clock()
    t = Transcript(clock=clock)
    t.add("Landen", "did you see the game")
    t.add("Someone", "no, who won?")
    t.add("Pip", "Nebraska, by three.", ours=True)
    hm = time.strftime("%H:%M", time.localtime(clock.t))
    assert t.render() == [f"[{hm}] Landen: did you see the game", f"[{hm}] Someone: no, who won?",
                          f"[{hm}] Pip: Nebraska, by three."]
    assert t.last_human()["text"] == "no, who won?" and t.last_ours()["text"] == "Nebraska, by three."
    assert t.last()["ours"] and t.count == 3


def test_only_the_last_40_lines_and_3_hours_are_kept():
    clock = Clock()
    t = Transcript(clock=clock)
    for i in range(45):
        t.add("Someone", f"line {i}")
    assert len(t.render()) == 40 and t.render()[0].endswith("line 5") and t.count == 45
    clock.t += 3 * 3600 + 1
    assert t.render() == [] and t.last() is None and t.last_human() is None


def test_a_line_pip_was_talked_over_on_is_marked_cut_off():
    t = Transcript(clock=Clock())
    t.add("Pip", "The capital of Australia is", ours=True)
    t.add("Sam", "okay thanks")
    t.mark_cut()
    assert t.render()[0].endswith("Pip: The capital of Australia is (cut off)")
    assert not t.render()[1].endswith("(cut off)")


def test_nothing_from_pip_means_nothing_to_cut():
    t = Transcript(clock=Clock())
    t.add("Sam", "hi")
    t.mark_cut()
    assert t.render()[0].endswith("Sam: hi") and t.last_ours() is None


def test_a_quiet_spell_is_marked_so_old_talk_stays_old():
    clock = Clock()
    t = Transcript(clock=clock)
    t.add("Landen", "that sounds like gibberish")
    clock.t += 4 * 60
    t.add("Caleb", "ha")
    clock.t += 25 * 60
    t.add("Caleb", "Pip, what's an H200?")
    lines = t.render()
    assert len(lines) == 4 and lines[2] == "(25 minutes later)" and lines[3].endswith("Pip, what's an H200?")
