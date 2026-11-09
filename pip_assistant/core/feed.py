"""What was said: the room transcript and the dashboard's conversation feed (D-108).

Part of the conversation (core/conversation.py): `Conversation` takes these methods in as `Feed`, so they
read and set its fields like its own. Words travel only in the messages' `private` part: shown live on
the dashboard, never logged or saved (D-34, D-53).

    hear / said    a person said this / Pip said this: into the room transcript (core/transcript.py)
    show / feed    a line on the conversation feed: Pip's answer / something heard in the room
    note           a small event bar on the feed ("chimed", "mic off for 12 s"…), no words
"""
import time

from ..common.messages import Message


class Feed:
    async def show(self, who: str, text: str, **data) -> None:
        """Live conversation feed for the dashboard (private: shown, never logged)."""
        private = {"text": text}
        if who == "you" and self.speaker is not None and self.people is not None:
            private["speaker"] = self.speaker.label
        await self.bus.publish(Message("event", "conversation.line", source="core",
                                       data={"who": who, **data}, private=private))

    async def feed(self, text: str, how: str, speaker=None, **data) -> None:
        """Everything heard goes on the dashboard's conversation feed (D-108, like Ziggletron's): grey when it
        wasn't for Pip, green when it was. Private: shown live, never logged or saved."""
        await self.bus.publish(Message("event", "conversation.line", source="core", data={"who": "room", "how": how, **data},
                                       private={"text": text, "speaker": self.label(speaker)}))

    async def note(self, kind: str, **data) -> None:
        """A small event bar on the conversation feed ("chimed", "mic off for 12 s", "stayed quiet"…). No words."""
        await self.bus.publish(Message("event", "conversation.event", source="core", data={"kind": kind, **data}))

    def label(self, speaker=None) -> str:
        sp = speaker if speaker is not None else self.speaker
        return sp.label if sp is not None else "Someone"

    def hear(self, text: str, speaker=None) -> None:
        """A person said this (to Pip or not): into the room transcript."""
        self.transcript.add(self.label(speaker), text)
        self.last_voice_at = time.time()

    def said(self, text: str) -> None:
        """Pip said this."""
        if text:
            self.transcript.add("Pip", text, ours=True)
