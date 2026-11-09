# Pip: read this first

Pip is the second-generation household voice assistant replacing Ziggletron. It runs on a Raspberry Pi 5 (8 GB RAM, SD card, aarch64, Python 3.13) in a shared dorm room.

**Before making any change, read [`CONTRIBUTING.md`](CONTRIBUTING.md) (how to change things) and [`docs/PROJECT-MAP.md`](docs/PROJECT-MAP.md) (where everything is).** What Pip does: [`docs/PIP-SPEC.md`](docs/PIP-SPEC.md). They're written so you can work well without knowing the whole project.

## Current phase
**M1a: building Pip** (architecture approved, D-78). Follow the step plan in `docs/ARCHITECTURE.md` §14. Steps 1–6 are done, installed and tested by the owner (2026-11-08), including the post-test changes D-102–D-109 and the tuned two-clap switch (D-91). Step 7 is built and installed (D-110–D-113); review fixes and D-114–D-115 on 2026-11-09. **Step 8 is built (D-116, D-117), not installed yet.** Decisions are in `docs/DECISIONS.md` (D-90 onward cover listening like Ziggletron, natural language, modules, Google, the conversation feed and settings).

**Where we left off (2026-11-09):** **Step 8 is built** but not installed: the Settings page (`web/static/settings.html`, `core/settingsapi.py`, `SCHEMA` in `common/settings.py`: every setting with an explanation, saved to `settings.toml`, restarting only the programs that read it once Pip is quiet), an editable personality (`common/personality.py`, `core/personalityapi.py`; `thinker/main.py` keeps the fixed `RULES`), the read-only Ziggletron import (`core/migrate.py`: 80 memories to the inbox and the personality text, nothing else), and **no logins (D-116)**: `web/security.py` same-site check, https via `deploy/install-https.sh` (owner runs it with sudo). Groq's free limit isn't offline (D-115, a spare model). The owner reads the code too: keep `docs/HOW-PIP-WORKS.md` and comments in step. Next: the owner installs and tries step 8, then M1b. Still open: voice thresholds with a roommate; web search ~15 s; false wakes (Pippletron 0.8, Hey Pip 0.5, D-74).

Code is in `pip_assistant/` (features that plug in: `pip_assistant/modules/`), tests in `tests/` and each module (`uv run pytest`), services installed by `sh deploy/install.sh` (the owner runs it). Pip's dashboard: `http://100.118.171.118:8790`. `m0/` holds measurement tools, not the product. Progress and to-dos: `http://100.118.171.118:8780` (data in `~/pip-data/m0/status.json`). Sound tests go on that dashboard's recording page, never live in chat.

## Rules you must not break
1. Never put credentials, phone numbers, recordings or what people said into the repo, commit messages, logs or the dashboard. Secrets live only in `~/pip-data/secrets.toml`. The pre-commit check enforces this; never bypass it.
2. Owner decisions (`D-nn` in `docs/DECISIONS.md`) override everything else. If a change would contradict one, ask the owner.
3. Living docs: `docs/PIP-SPEC.md`, `docs/DECISIONS.md`, `docs/REQUIREMENTS.md`. `docs/archive/` holds the **detailed behaviour rules** you need when building a feature (follow the Detail link in REQUIREMENTS) plus Ziggletron's record. It's frozen: don't edit it (only fix links), and where it conflicts with `DECISIONS.md` the decision wins (see REQUIREMENTS §0). Don't modify `~/ziggletron` or `~/ziggletron-v2`.
4. Ziggletron (`ziggletron.service`) is switched off and disabled by the owner (D-82). Never start or re-enable it; only the owner does.
5. Heavy jobs on the Pi run under a memory cap (`systemd-run --user -p MemoryMax=4500M`). An uncapped job froze the Pi once. No sound between 23:00 and 07:00.
6. Python tooling: `uv`; releases at least two weeks old; system (apt) packages only with the owner's OK.
7. The owner understands Pip mainly from docs and diagrams (D-71), and is starting to read the code (start: `docs/HOW-PIP-WORKS.md`). Keep plain-language docs, that guide and code comments in step with every change, and keep `docs/PROJECT-MAP.md` current.
8. Only run a test if it settles a specific decision (say which). Afterwards write `~/pip-data/m0/results/<id>.json`, update `~/pip-data/m0/status.json`, and add what was learned to `docs/PIP-SPEC.md` §2.
