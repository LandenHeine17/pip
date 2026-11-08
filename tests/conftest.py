"""Shared test setup."""
import pytest

from pip_assistant.core.conversation import Conversation


@pytest.fixture(autouse=True)
def quick_turns(monkeypatch, request):
    """Older tests expect an answer straight after speech; Pip now waits for the end of the turn (D-106).
    Shrink those waits unless a test is about them (marked @pytest.mark.real_turn_waits)."""
    if "real_turn_waits" not in request.keywords:
        monkeypatch.setattr(Conversation, "TURN_WAITS", {"short": 0.01, "statement": 0.01, "long": 0.01,
                                                         "after_interrupt": 0.0})
