# Pip: read this first

Pip is the second-generation household voice assistant replacing Ziggletron. It runs on a Raspberry Pi 5 (8 GB RAM, SD card, aarch64, Python 3.13) in a shared dorm room.

**Before making any change, read [`CONTRIBUTING.md`](CONTRIBUTING.md) (how to change things) and [`docs/PROJECT-MAP.md`](docs/PROJECT-MAP.md) (where everything is).** What Pip does: [`docs/PIP-SPEC.md`](docs/PIP-SPEC.md). They're written so you can work well without knowing the whole project.

## Current phase
**Built:** M1a (Ziggletron's features, D-78–D-117), M1b (self-changes, versioned installs with rollback, D-118), the structure review and conversation rework (D-120–D-127), and **M2, smarter conversation and memory (2026-11-13, D-128–D-130)**: Pip explains himself (`core/selfknow.py`, `common/capabilities.py`), conversation notes kept 30 days (`modules/recall/`), memories that may contradict others flagged, his voice lowered before stopping, "hold on", "that was Sam, not me", a richer face, Settings → Your data, and long answers said in full (a long one can also go in a notes box on the monitor, D-130). Not built (owner): the offline model, spoken problem notices, Room-Aware mode. **Next: M3 (smart home)**, see `docs/REQUIREMENTS.md`. Every decision is in `docs/DECISIONS.md`; what each phase built is in `docs/ARCHITECTURE.md` §14.

**Open, for the owner (remind them):**
- From 2026-11-13: run `tools/latency.py` and compare with `~/pip-data/m0/results/conv-baseline.json` (5.4 s from end of speech to voice before D-122); count `heard.phantom`, `asked_again` and `resumed_after_backchannel` in the trace.
- Try undoing a self-change (AC-SM-06, daytime; steps on the progress dashboard).
- Install M2 (`sh ~/pip/deploy/install.sh`) and try it (checklist on the progress dashboard).
- Still being watched: voice thresholds with a roommate; false wakes (Pippletron 0.8, Hey Pip 0.5, D-74); false clap switch-offs (each records how the claps measured).

The interactive code guide is the dashboard's **Guide** page (`/guide`, `web/static/guide.html`).

Code is in `pip_assistant/` (features that plug in: `pip_assistant/modules/`), tests in `tests/` and each module (`uv run pytest`), services installed by `sh deploy/install.sh` (the owner runs it; it installs the **committed** HEAD into `~/pip-app/current`, so commit first; never edit `~/pip-app` by hand). Pip's dashboard: `http://<pi-tailscale-ip>:8790` (the Pi's address on Tailscale: `tailscale ip -4`; kept out of the repo because it is public). `m0/` holds measurement tools, not the product. Progress and to-dos: `http://<pi-tailscale-ip>:8780` (data in `~/pip-data/m0/status.json`). The owner's notes for Claude are in that dashboard's **Feedback for Claude** box (`~/pip-data/m0/feedback.json`): read it at the start of a session, set each note's `status` to `seen` or `done` and write a short `reply`. Sound tests go on that dashboard's recording page, never live in chat.

## Rules you must not break
1. Never put credentials, phone numbers, recordings or what people said into the repo, commit messages, logs or the dashboard. Secrets live only in `~/pip-data/secrets.toml`. The pre-commit check enforces this; never bypass it.
2. Owner decisions (`D-nn` in `docs/DECISIONS.md`) override everything else. If a change would contradict one, ask the owner.
3. Living docs: `docs/PIP-SPEC.md`, `docs/DECISIONS.md`, `docs/REQUIREMENTS.md`. `docs/archive/` holds the **detailed behaviour rules** you need when building a feature (follow the Detail link in REQUIREMENTS) plus Ziggletron's record. It's frozen: don't edit it (only fix links), and where it conflicts with `DECISIONS.md` the decision wins (see REQUIREMENTS §0). Don't modify `~/ziggletron` or `~/ziggletron-v2`.
4. Ziggletron (`ziggletron.service`) is switched off and disabled by the owner (D-82). Never start or re-enable it; only the owner does.
5. Heavy jobs on the Pi run under a memory cap (`systemd-run --user -p MemoryMax=4500M`). An uncapped job froze the Pi once. No sound between 23:00 and 07:00.
6. Python tooling: `uv`; releases at least two weeks old; system (apt) packages only with the owner's OK.
7. The owner understands Pip mainly from docs and diagrams (D-71), and is starting to read the code (start: `docs/HOW-PIP-WORKS.md`). Keep plain-language docs, that guide and code comments in step with every change, and keep `docs/PROJECT-MAP.md` current.
8. Only run a test if it settles a specific decision (say which). Afterwards write `~/pip-data/m0/results/<id>.json`, update `~/pip-data/m0/status.json`, and add what was learned to `docs/PIP-SPEC.md` §2.
