"""The messages Pip's programs send each other (docs/ARCHITECTURE.md §3).

One message per line, as JSON, over a local Unix socket. Kinds:
  hello      a program introduces itself       (source = its name)
  heartbeat  "I'm alive", with memory use       (every few seconds)
  subscribe  "send me events matching these"    (data.patterns, e.g. ["timer.*"])
  event      something happened                 (delivered to subscribers)
  command    please do this                     (delivered to `target`)
  request    ask and wait for a reply           (delivered to `target`, has an `id`)
  reply      the answer to a request            (routed back by `id`)

Privacy rule: `data` is written to the decision trace; `private` (what people said, audio,
memory contents) is delivered but **never logged** (D-34, D-53).
"""
import json
import time
import uuid
from dataclasses import asdict, dataclass, field

KINDS = {"hello", "heartbeat", "subscribe", "event", "command", "request", "reply"}
MAX_LINE = 4 * 1024 * 1024  # 4 MB: room for a few seconds of audio in `private`
READ_LIMIT = 2 * MAX_LINE   # what each end of the socket will read in one line (asyncio's default is only 64 KB)
# The "tone" an ears.ring command may carry: the ringtones ears/audio.py can play (the Settings page offers these)
RINGTONES = ("classic", "kitchen", "chimes", "gentle")


class BadMessage(ValueError):
    pass


async def lines(reader, log, who: str):
    """Each line from a socket until it closes. A line longer than READ_LIMIT is skipped with a
    warning instead of breaking the connection (asyncio has already thrown that line away)."""
    while True:
        try:
            line = await reader.readline()
        except ValueError:  # LimitOverrunError: the line was too long
            log.warning("%s: skipped a message that was too large", who)
            continue
        if not line:
            return
        yield line


@dataclass
class Message:
    kind: str
    type: str
    source: str = ""
    target: str = ""
    id: str = ""
    data: dict = field(default_factory=dict)
    private: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def encode(self) -> bytes:
        line = json.dumps(asdict(self), separators=(",", ":")).encode() + b"\n"
        if len(line) > MAX_LINE:
            raise BadMessage(f"message {self.type} is too large ({len(line)} bytes)")
        return line

    @classmethod
    def decode(cls, line: bytes) -> "Message":
        try:
            raw = json.loads(line)
        except ValueError as e:
            raise BadMessage(f"not JSON: {e}") from e
        if not isinstance(raw, dict) or raw.get("kind") not in KINDS or not isinstance(raw.get("type"), str):
            raise BadMessage("missing or unknown kind/type")
        known = {k: raw[k] for k in cls.__dataclass_fields__ if k in raw}
        if not isinstance(known.get("data", {}), dict) or not isinstance(known.get("private", {}), dict):
            raise BadMessage("data and private must be objects")
        return cls(**known)

    def reply(self, data: dict | None = None, private: dict | None = None, source: str = "") -> "Message":
        return Message("reply", self.type, source=source, target=self.source, id=self.id,
                       data=data or {}, private=private or {})


def new_id() -> str:
    return uuid.uuid4().hex[:12]
