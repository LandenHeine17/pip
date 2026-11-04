# Pip: read this first

Pip is the second-generation household voice assistant replacing Ziggletron. It runs on a Raspberry Pi 5 (8 GB RAM, SD card, aarch64, Python 3.13) in a shared dorm room.

**Before making any change, read [`CONTRIBUTING.md`](CONTRIBUTING.md) (how to change things) and [`docs/PROJECT-MAP.md`](docs/PROJECT-MAP.md) (where everything is).** They're written so you can work well without knowing the whole project.

## Current phase
**M0: measure and decide.** No Pip application code exists yet. It comes after M0 results and the architecture phase. `m0/` holds measurement tools, not the product. Progress and to-dos: the dashboard at `http://100.118.171.118:8780` (data in `~/pip-data/m0/status.json`).

## Rules you must not break
1. Never put credentials, phone numbers, recordings or what people said into the repo, commit messages, logs or the dashboard. Secrets live only in `~/pip-data/secrets.toml`. The pre-commit check enforces this; never bypass it.
2. Owner decisions (`D-nn` in `pip-v2-proposals/17-OPEN-QUESTIONS.md`) override everything else. If a change would contradict one, ask the owner.
3. Don't modify `01-discovery/` (historical record), `~/ziggletron` or `~/ziggletron-v2`.
4. Ziggletron (`ziggletron.service`) may be stopped temporarily for M0 tests. Say so first, and always restart it (D-44).
5. Heavy jobs on the Pi run under a memory cap (`systemd-run --user -p MemoryMax=4500M`). An uncapped job froze the Pi once. No sound between 23:00 and 07:00.
6. Python tooling: `uv`; releases at least two weeks old; system (apt) packages only with the owner's OK.
7. The owner understands Pip from docs and diagrams, not code (D-71). Keep plain-language docs in step with every change, and keep `docs/PROJECT-MAP.md` current.
8. After an M0 test: write `~/pip-data/m0/results/<id>.json` and update `~/pip-data/m0/status.json`.
