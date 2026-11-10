"""Pip's names (core/names.py, D-93): called versus mentioned, mishearings ("Pippletron", not Ziggletron), names that
sound the same, and sentences that aren't finished yet."""
from pip_assistant.core.names import calls_pip, names_pip
from pip_assistant.core.people import sounds_like


def test_names_pip_spots_mishearings_but_not_ziggletron():
    from pip_assistant.core.names import names_pip
    for t in ["Hey Pip, what time is it?", "Pippletron, stop", "Hey, Pipp.", "pipple tron what's up",
              "Piplotron, what's the weather?", "what's the weather, Pippletron?"]:
        assert names_pip(t), t
    for t in ["Ziggletron, what time is it?", "pass the pipe", "I picked it up", "the pizza is in the oven",
              "Philip is coming over"]:
        assert not names_pip(t), t


def test_unfinished_sentences():
    from pip_assistant.core.conversation import looks_unfinished
    for t in ["Pippletron, what's the", "what's the...", "Pip, play my", "hey pip, um"]:
        assert looks_unfinished(t), t
    for t in ["Pippletron, what's the weather?", "what time is it.", "who are you talking to?", "turn it up"]:
        assert not looks_unfinished(t), t


def test_called_versus_mentioned():
    for t in ["Pip, what's the time?", "Hey Pip what's up", "thanks, Pip", "What do you think, Pippletron?",
              "okay so Pip, the weather?", "Pipple tron, stop."]:
        assert calls_pip(t), t
    for t in ["Pip's being weird today", "ask Pip later", "I told Pip about it", "did you hear what Pip said"]:
        assert not calls_pip(t) and names_pip(t), t
    for t in ["Ziggletron, what time is it?", "pass the pipe"]:
        assert not calls_pip(t) and not names_pip(t), t


def test_names_that_sound_the_same():
    assert sounds_like("Landon", "Landen") and sounds_like("Jon", "John")
    for heard in ("London", "Lemon", "Landing", "Brandon", "Lauren"):
        assert not sounds_like(heard, "Landen"), heard
