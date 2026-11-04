# Pip: read this first

Pip is the second-generation household voice assistant replacing Ziggletron. It runs on a Raspberry Pi 5 (8 GB RAM, SD card, aarch64, Python 3.13) in a shared dorm room.

**Before making any change, read [`CONTRIBUTING.md`](CONTRIBUTING.md) (how to change things) and [`docs/PROJECT-MAP.md`](docs/PROJECT-MAP.md) (where everything is).** What Pip does: [`docs/PIP-SPEC.md`](docs/PIP-SPEC.md). They're written so you can work well without knowing the whole project.

## Current phase
**M0: measure and decide.** No Pip application code exists yet. It comes after M0 results and the architecture phase. `m0/` holds measurement tools, not the product. Progress and to-dos: the dashboard at `http://100.118.171.118:8780` (data in `~/pip-data/m0/status.json`).

## Rules you must not break
1. Never put credentials, phone numbers, recordings or what people said into the repo, commit messages, logs or the dashboard. Secrets live only in `~/pip-data/secrets.toml`. The pre-commit check enforces this; never bypass it.
2. Owner decisions (`D-nn` in `docs/DECISIONS.md`) override everything else. If a change would contradict one, ask the owner.
3. Living docs: `docs/PIP-SPEC.md`, `docs/DECISIONS.md`, `docs/REQUIREMENTS.md`. `docs/archive/` holds the **detailed behaviour rules** you need when building a feature (follow the Detail link in REQUIREMENTS) plus Ziggletron's record. It's frozen: don't edit it (only fix links), and where it conflicts with `DECISIONS.md` the decision wins (see REQUIREMENTS §0). Don't modify `~/ziggletron` or `~/ziggletron-v2`.
4. Ziggletron (`ziggletron.service`) may be stopped temporarily for M0 tests. Say so first, and always restart it (D-44).
5. Heavy jobs on the Pi run under a memory cap (`systemd-run --user -p MemoryMax=4500M`). An uncapped job froze the Pi once. No sound between 23:00 and 07:00.
6. Python tooling: `uv`; releases at least two weeks old; system (apt) packages only with the owner's OK.
7. The owner understands Pip from docs and diagrams, not code (D-71). Keep plain-language docs in step with every change, and keep `docs/PROJECT-MAP.md` current.
8. Only run a test if it settles a specific decision (say which). Afterwards write `~/pip-data/m0/results/<id>.json`, update `~/pip-data/m0/status.json`, and add what was learned to `docs/PIP-SPEC.md` §2.
