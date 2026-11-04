"""The conversation part of the conductor.

Step 2 (now): when Pip hears his name he chimes and says a fixed, playful line.
Step 3 replaces the fixed line with: record what you say → Groq → Claude → spoken answer.
"""
import random

from ..common.messages import Message

STEP2_LINES = [
    "Hey! I heard you. My brain arrives in the next step, so for now I just look pretty.",
    "That's me! I can hear you loud and clear. Thinking comes later.",
    "Yep, I'm listening. I can't answer questions yet, but I'm working on it.",
    "Hi there! Wake word heard. Next up, actual opinions.",
]


class Conversation:
    def __init__(self, bus):
        self.bus = bus
        self.lines = list(STEP2_LINES)
        bus.core_listen("heard.wake", self.on_wake)

    async def on_wake(self, msg: Message) -> None:
        await self.bus.route(Message("command", "ears.chime", source="core", target="ears"))
        line = random.choice(self.lines)
        await self.bus.route(Message("command", "ears.say", source="core", target="ears",
                                     data={"reason": "wake word", "word": msg.data.get("word")},
                                     private={"text": line}))
