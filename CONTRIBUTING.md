# How to change Pip

For anyone making a change, person or AI agent, including Pip's own self-change agent. It's written so you can do a good job **without knowing the whole project**. Start with [`docs/PROJECT-MAP.md`](docs/PROJECT-MAP.md) to find where things are. New to the code? Read [`docs/HOW-PIP-WORKS.md`](docs/HOW-PIP-WORKS.md) first: how Pip works, for someone reading the code.

## 1. Before you change anything

1. **Find out what phase we're in.** See `CLAUDE.md` → "Current phase". M1a and M1b are built (`docs/ARCHITECTURE.md` §14); next is M2. The application is `pip_assistant/`; `m0/` holds the measurement tools from M0.
2. **Find the requirement.** Read the relevant part of `docs/PIP-SPEC.md`, then look it up in `docs/REQUIREMENTS.md` (`PIP-NEW-nnn`, Ziggletron's `FEAT-nnn`, acceptance criteria `AC-*`). **When building a feature, open its Detail link**: the archived proposal holds the exact rules, edge cases and examples. Check REQUIREMENTS §0 (overridden passages) and `docs/DECISIONS.md` first; decisions win.
3. **Check the owner's decisions** in `docs/DECISIONS.md` (`D-nn`). Decisions override the rest of the spec. If your change contradicts one, **stop and ask the owner**.
4. **Read only what you need.** The map says which file covers what. You don't need to read everything.

## 2. Making the change

- **Small and focused.** One purpose per change. Don't refactor unrelated code.
- **New features are modules** (ARCHITECTURE §7, D-96): copy `pip_assistant/modules/_template/`, rename it, and fill in `module.toml` (permissions, settings, what Claude is told, its dashboard page) and `module.py`. A module keeps to its own tables (`<name>_…`), its own secrets section and the internet hosts it declares; it reaches Pip only through its `ctx`, and adds to the dashboard, the monitor and Pip's offline commands only through the hooks in `core/modules.py` (the recipe: HOW-PIP-WORKS "Making a module"). Put its tests next to it. Don't add a feature by editing pip-core when a module can do it, and **never name a module in pip-core, pip-web or their pages** (`tools/check_map.py` refuses the commit).
- **Match what's there.** Same style, same patterns, same tools (`uv`; Python).
- **Keep traceability.** When you change the spec, cite the IDs (FEAT, PIP-NEW, AC, D). New requirements get the next free `PIP-NEW` number and a row in `docs/REQUIREMENTS.md`; new decisions get the next `D-nn` in `docs/DECISIONS.md`.
- **Keep the docs few.** Update the three living docs (`PIP-SPEC`, `DECISIONS`, `REQUIREMENTS`) instead of adding new spec files. `docs/archive/` is frozen: don't add to it or edit it (except to fix links). If a new decision overrides an archived passage, add a row to REQUIREMENTS §0. How Pip is built lives in one page, `docs/ARCHITECTURE.md`; keep it in step with the code.
- **Never rewrite history.** `docs/archive/ziggletron-discovery/` is a record of Ziggletron; don't edit it except to fix a proven factual error or a broken link.
- **Privacy and secrets:**
  - Never put credential values, phone numbers, recordings or what people said into code, commits, logs, tests or the dashboard.
  - Secrets live only in `~/pip-data/secrets.toml`.
  - Commit messages describe the change; never paste a spoken or private request.
- **The owner reads docs, not code** (D-71). If a change affects how something works, update the plain-language docs and diagrams in the same change.
- **Dependencies:** use releases at least two weeks old. System (apt) packages only with the owner's OK.
- **Pip's own changes** (asked by voice or on the dashboard) go through the **Changes page** and pip-builder (D-118), never by hand-editing what's running. If you're Pip's coding agent: you work on a plain copy with no git, the only command you can run is `tools/sandbox-test`, and edits to `pyproject.toml`, `uv.lock`, `deploy/systemd/`, `tools/`, `.claude/` or `.gitignore` are refused.
- **Third-party code we run** (e.g. openWakeWord) is pinned, and any local fixes are saved as patches in `m0/patches/`.

## 3. Long-running or heavy jobs (on the Pi)

- Run them as a systemd user job with a **memory cap**: `systemd-run --user --unit=pip-<name> -p MemoryMax=4500M -p Nice=10 …`. An uncapped job froze the Pi once.
- Write progress to `~/pip-data/m0/tasks/<name>.json` (fields: `name, stage, done, total, note, finished, failed, updated`) so the dashboard shows it.
- **Don't make sound** between 23:00 and 07:00, or when the owner isn't around (shared dorm room).
- Ziggletron is switched off and disabled (D-82). Never start or re-enable it; only the owner does.

## 4. After the change

1. **Check it works.** Run it, or its test. **Tests go in the folder of the part they test** (`tests/core/test_alarms.py` for alarms; a module's next to the module); shared fakes and harnesses are in `tests/helpers.py`, and test files never import each other. Report results honestly, including failures. Run the automated tests under a memory cap, from the repo folder: `systemd-run --user --same-dir -p MemoryMax=4500M --wait --pipe --collect uv run pytest`. Without `--same-dir` (or `--working-directory`), uv starts in your home folder and fails.
2. **Measurement tests (recordings, sound, speed, wake words): only run one that settles a specific decision**, and say which one. Write the result to `~/pip-data/m0/results/<id>.json` (fields: `id, ran_at, passed, summary, details`). Update `~/pip-data/m0/status.json`: to-do list, progress and findings in plain language.
3. **Update the map** if you added, moved or removed files or folders.
4. **Commit and push.** The pre-commit check must pass (`tools/install_hooks.sh` installs it). End commit messages with the attribution line your tool requires.
5. **Install it** (the owner, or with the owner's OK): `sh deploy/install.sh`. It installs the **committed** HEAD as a new version in `~/pip-app` and refuses if tracked files have uncommitted changes, so commit first. It waits for a quiet moment, restarts Pip, checks his health and switches back by itself if he isn't healthy (see [`docs/HOW-PIP-WORKS.md`](docs/HOW-PIP-WORKS.md) §7). Pip runs from `~/pip-app/current`, so restarting a service alone doesn't pick up edits in `~/pip`. **Never edit `~/pip-app` by hand.**

## 5. When to ask the owner instead

- Anything that contradicts a `D-nn` decision, or changes what Pip does for people.
- Spending money, creating accounts, or sending data to a new service.
- Running third-party code for the first time.
- Anything that makes sound late at night, or deletes data.

Put questions in `~/pip-data/m0/questions.json` (the dashboard's "Questions for you" section) or ask in chat. Record the answers as decisions in `docs/DECISIONS.md`.
