"""Start pip-ears. Step 1 (skeleton): connects, sends heartbeats, answers ears.ping."""
from ..common.program import make_bus, serve, start


async def main() -> None:
    bus, _cfg = make_bus("ears")
    bus.status["stage"] = "skeleton: listening arrives in step 2"
    await serve(bus)


def run() -> None:
    start("ears", main)


if __name__ == "__main__":
    run()
