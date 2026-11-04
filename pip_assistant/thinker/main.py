"""Start pip-thinker. Step 1 (skeleton): connects, sends heartbeats, answers thinker.ping."""
from ..common.program import make_bus, serve, start


async def main() -> None:
    bus, _cfg = make_bus("thinker")
    bus.status["stage"] = "skeleton: answers arrive in step 3"
    await serve(bus)


def run() -> None:
    start("thinker", main)


if __name__ == "__main__":
    run()
