# How to change Pip

For anyone making a change, person or AI agent, including Pip's own self-change agent. It's written so you can do a good job **without knowing the whole project**. Start with [`docs/PROJECT-MAP.md`](docs/PROJECT-MAP.md) to find where things are.

## 1. Before you change anything

1. **Find out what phase we're in.** See `CLAUDE.md` → "Current phase". In M0 there is no Pip application code. Only the spec, the measurement tools and the dashboard exist.
2. **Find the requirement.** Read the relevant part of `docs/PIP-SPEC.md`, then look it up in `docs/REQUIREMENTS.md` (`PIP-NEW-nnn`, Ziggletron's `FEAT-nnn`, acceptance criteria `AC-*`). The full reasoning, if you need it, is in `docs/archive/pip-v2-proposals-2026-11/`.
3. **Check the owner's decisions** in `docs/DECISIONS.md` (`D-nn`). Decisions override the rest of the spec. If your change contradicts one, **stop and ask the owner**.
4. **Read only what you need.** The map says which file covers what. You don't need to read everything.

## 2. Making the change

- **Small and focused.** One purpose per change. Don't refactor unrelated code.
- **Match what's there.** Same style, same patterns, same tools (`uv`; Python).
- **Keep traceability.** When you change the spec, cite the IDs (FEAT, PIP-NEW, AC, D). New requirements get the next free `PIP-NEW` number and a row in `docs/REQUIREMENTS.md`; new decisions get the next `D-nn` in `docs/DECISIONS.md`.
- **Keep the docs few.** Update the three living docs (`PIP-SPEC`, `DECISIONS`, `REQUIREMENTS`) instead of adding new spec files. `docs/archive/` is a finished record: don't add to it or edit it (except to fix links).
- **Never rewrite history.** `docs/archive/ziggletron-discovery/` is a record of Ziggletron; don't edit it except to fix a proven factual error or a broken link.
- **Privacy and secrets:**
  - Never put credential values, phone numbers, recordings or what people said into code, commits, logs, tests or the dashboard.
  - Secrets live only in `~/pip-data/secrets.toml`.
  - Commit messages describe the change; never paste a spoken or private request.
- **The owner reads docs, not code** (D-71). If a change affects how something works, update the plain-language docs and diagrams in the same change.
- **Dependencies:** use releases at least two weeks old. System (apt) packages only with the owner's OK.
- **Third-party code we run** (e.g. openWakeWord) is pinned, and any local fixes are saved as patches in `m0/patches/`.

## 3. Long-running or heavy jobs (on the Pi)

- Run them as a systemd user job with a **memory cap**: `systemd-run --user --unit=pip-<name> -p MemoryMax=4500M -p Nice=10 …`. An uncapped job froze the Pi once.
- Write progress to `~/pip-data/m0/tasks/<name>.json` (fields: `name, stage, done, total, note, finished, failed, updated`) so the dashboard shows it.
- **Don't make sound** between 23:00 and 07:00, or when the owner isn't around (shared dorm room).
- If a job needs the microphone or lots of CPU, Ziggletron may be stopped. Say so first, and always restart it (D-44).

## 4. After the change

1. **Check it works.** Run it, or its test. Report results honestly, including failures.
2. **M0 tests: only run a test that settles a specific decision**, and say which one. Write the result to `~/pip-data/m0/results/<id>.json` (fields: `id, ran_at, passed, summary, details`). Update `~/pip-data/m0/status.json`: to-do list, progress and findings in plain language.
3. **Update the map** if you added, moved or removed files or folders.
4. **Commit and push.** The pre-commit check must pass (`tools/install_hooks.sh` installs it). End commit messages with the attribution line your tool requires.

## 5. When to ask the owner instead

- Anything that contradicts a `D-nn` decision, or changes what Pip does for people.
- Spending money, creating accounts, or sending data to a new service.
- Running third-party code for the first time.
- Anything that makes sound late at night, or deletes data.

Put questions in `~/pip-data/m0/questions.json` (the dashboard's "Questions for you" section) or ask in chat. Record the answers as decisions in `docs/DECISIONS.md`.
