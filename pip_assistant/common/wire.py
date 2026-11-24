"""How a phone's messages travel between pip-web and pip-ears (D-149, ears/remote.py), on a Unix socket:

    4 bytes: the message's length (big-endian) · 1 byte: 0 = sound, 1 = JSON text · the message

Each is one websocket message from or to the /phone page, passed through unchanged. Kept here (not in
ears/) so pip-web can use it without loading pip-ears' sound libraries.
"""
import asyncio
import struct

SOUND, TEXT = 0, 1
MAX_MESSAGE = 1 << 20   # nothing sensible is bigger (80 ms of sound is 2.5 KB)


def pack(kind: int, payload: bytes) -> bytes:
    return struct.pack(">IB", len(payload), kind) + payload


async def read_message(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
    """The next message, or None when the other side has gone (or sent something malformed)."""
    try:
        length, kind = struct.unpack(">IB", await reader.readexactly(5))
        if length > MAX_MESSAGE:
            return None
        return kind, await reader.readexactly(length)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
