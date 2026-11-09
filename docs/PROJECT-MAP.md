# Project map

Where everything is, what it's for, and whether you may change it. **Keep this file up to date**: any change that adds, moves or removes a folder or important file must update this map in the same commit.

Last reviewed: 2026-11-09 (M1a done; M1b built: self-changes, versioned installs, usage meter).

## In this repository (`~/pip`, GitHub: private repo `pip`)

| Path | What it is | Change it? |
|---|---|---|
| `README.md` | Front page: what's where | Yes, keep short |
| `CLAUDE.md` | Read-first rules for Claude Code (loaded automatically) | Yes, carefully: keep it short; detail goes in `CONTRIBUTING.md` |
| `CONTRIBUTING.md` | How to make a change, for people and AI agents | Yes |
| `docs/PROJECT-MAP.md` | This file | Yes, whenever the layout changes |
| `docs/HOW-PIP-WORKS.md` | **How Pip works, for someone reading the code**: the programs, a request's path through them, the face state machine, how Pip changes himself, how an update goes live and rolls back. Start here before reading code | Yes: keep in step with the code |
| `docs/PIP-SPEC.md` | **What Pip does**, in plain language, plus M0 findings. The living spec | Yes: keep it short and current |
| `docs/ARCHITECTURE.md` | **How Pip is built**: the five programs (ears, core, thinker, web, builder), messages, modules, data, security, tech choices, build plan. Plain language + diagrams (D-71) | Yes: keep in step with the code |
| `docs/DECISIONS.md` | Every owner decision (`D-nn`); overrides everything | Add new ones at the end; never silently change one |
| `docs/REQUIREMENTS.md` | Lookup tables: `PIP-NEW` requirements, Ziggletron `FEAT` dispositions, `AC` acceptance criteria | Yes, with IDs |
| `docs/archive/` | **Frozen detailed reference**: `ziggletron-discovery/` (what Ziggletron did; for parity) and `pip-v2-proposals-2026-11/` (the full behaviour rules for every requirement, linked from REQUIREMENTS' Detail column). Decisions override it (REQUIREMENTS §0) | **No**, except fixing broken links |
| `m0/` | M0 **measurement tools** (not the Pip product) | Yes |
| `m0/phrases.json` | Scripted test phrases (no personal names, D-47) | Add sessions; don't renumber existing IDs (recordings use them) |
| `m0/benchmark.py`, `rescore.py` | Speech-to-text benchmark and re-scoring | Yes |
| `m0/wakeword.py` | First (untrained) wake-word test | Superseded; keep for the record |
| `m0/wake_prepare.py`, `wake_train.py`, `wake_eval.py`, `wake_soak.py` | Wake-word detector data download, training, evaluation, overnight false-wake check | Yes |
| `m0/tts_bench.py`, `llm_bench.py` | Voice (TTS) and local language-model speed tests | Yes |
| `m0/wake_realmix.py` | Builds "Hey Pip" v2 training data from synthetic clips plus half the owner's recordings (other half held out) | Yes |
| `m0/sandbox_test.py`, `load_test.py` | Self-change sandbox test (systemd user sandbox) and Pi load test | Yes |
| `m0/patches/` | Patches applied to third-party tools we run (e.g. openWakeWord low-memory fix) | Yes; document why in the patch commit |
| `m0/pyproject.toml`, `uv.lock` | M0 Python environment (uv; only releases ≥2 weeks old) | Yes, via `uv` |
| `tools/check_secrets.py`, `check_map.py`, `install_hooks.sh` | The pre-commit check (installed by `install_hooks.sh`): `check_secrets.py` blocks secrets and phone numbers (pip-builder also runs it on every self-change); `check_map.py` fails if a top-level file or folder isn't in this map | Yes, but never weaken them. Self-changes may not touch them |
| `tools/sandbox-test` | Runs the tests with **no network and no home folder** (`unshare`); the only command Pip's coding agent may run (D-118). Run from a project folder with a `.venv` | Yes, carefully: loosening it lets generated code reach the internet or secrets. Self-changes may not touch it |
| `pip_assistant/` | **The Pip application**, five programs plus shared code (rows below). `defaults.toml`: every setting, explained | Yes: follow ARCHITECTURE.md; tests in `tests/` |
| `pip_assistant/common/` | Shared by all programs: `messages.py` (the messages), `client.py` (connecting to pip-core, heartbeats), `program.py` (shared start-up and clean stop; reads the version's `RELEASE` file for the heartbeat), `paths.py` (where data lives: `~/pip-data`, the socket folder), `settings.py` (settings: loading, plus SCHEMA, checking and saving for the Settings page), `personality.py` (Pip's editable personality and its versions), `voices.py` (downloading Piper voices), `usage.py` (the usage meter: Claude requests, errors and cost per day by kind; spotting Claude's usage-limit message), `modules.py` (finds and checks modules), `secrets.py` (reads secrets; each module gets only its own section), `weather.py` (the forecast and what the face wears), `vitals.py` (the Pi's health for the monitor) | Yes |
| `pip_assistant/core/` | **pip-core**, the conductor: `main.py` (builds and connects everything), `bus.py` (message hub), `trace.py` (decision trace), `health.py` (watchdog), `conversation.py` (what Pip does with what he hears), `settingsapi.py` (the Settings page: get, save, restart what needs it when Pip is quiet), `personalityapi.py` (the personality editor), `migrate.py` (bringing memories and the personality over from Ziggletron, read-only), `states.py` (Pip's states and the words for each: the face state machine), `stt.py` (speech-to-text, incl. offline Whisper), `schedule.py` (timers, alarms, reminders, ringing, calendar heads-ups), `commands.py` (Pip's own fixed phrasings: offline only, plus instant stop/snooze while ringing, D-94), `actions.py` (checks and carries out Claude's actions; hands modules' actions to them), `voicecheck.py` (drops sounds without a voice), `memory.py` (memories and the inbox), `people.py` (people and voice recognition), `peoplecmds.py` (people and voices without Claude), `net.py` (online check, D-95), `transcript.py` (the room conversation Claude sees, RAM only), `joining.py` (when Pip joins in), `mic.py` (the Listening switches), `prefs.py` (dashboard preferences), `when.py` (understanding times), `modules.py` (plugs modules in) | Yes |
| `pip_assistant/ears/` | **pip-ears**: `main.py` (the program), `audio.py` (microphone in, speaker out, through PipeWire), `segmenter.py` (cuts each thing said out of the sound), `wake.py` (wake words), `voice.py` (Pip's voice; engines are swappable, only Piper today), `clap.py` (the two-clap detector on its own USB microphone) | Yes |
| `pip_assistant/thinker/` | **pip-thinker**: `main.py` (turns what was said into an answer; the fixed `RULES` part of what Claude is told), `claude.py` (Claude through the Claude Code CLI) | Yes |
| `pip_assistant/web/` | **pip-web**, the dashboard: `main.py` (pages and live streams), `static/index.html` (the dashboard), `static/changes.html` (the Changes page: ask for a change, approve plans and finished changes, live log, Stop, Undo), `static/settings.html` (the Settings page: every setting, the personality editor, the Ziggletron import), `static/tv.html` (the face on the monitor and its boxes, from Ziggletron's), `static/pip.css` and `pip.js` (shared with module pages) | Yes |
| `pip_assistant/builder/` | **pip-builder**, Pip changing his own code (D-118): `main.py` (the program), `store.py` (each change's record, table `builder_changes`), `pipeline.py` (the steps, the checks, git and the install), `agent.py` (running Claude Code to plan and build), `sandbox.py` (the agent and strict sandboxes). See HOW-PIP-WORKS §6 | Yes, carefully: it's security-sensitive (a self-change touching it needs "I've read the diff") |
| `pip_assistant/release.py` | **Versioned installs with automatic rollback** (PIP-NEW-080): makes a version in `~/pip-app/releases/`, switches to it, checks health, switches back if unhealthy. Standard library only; run with the system `python3` (`python3 -m pip_assistant.release` with `install`, `rollback`, `list` or `status`). See HOW-PIP-WORKS §7 | Yes, carefully: every update goes through it |
| `pip_assistant/web/security.py` | **The same-site check** (FEAT-072, D-116): with no logins, it's what stops other websites from changing Pip. Pip answers only to its own names; changes must be JSON from Pip's own pages; module pages may only read with GET (`module_get_allowed`). Tests: `tests/test_security.py` | Yes, carefully: loosening it lets other websites change Pip |
| `pip_assistant/modules/` | **Plug-in features** (ARCHITECTURE §7, D-96), one folder each with `module.toml`, `module.py`, `static/` (its dashboard page) and its tests: `google/` (accounts per person, sign-in; `static/account.html` each account's settings page: calendars, heads-ups, to-do lists; `testing.py` is a pretend Google for tests), `calendar/` (`store.py` events on the Pi, `sync.py` two-way Google Calendar), `todos/` (`store.py`, `sync.py` Google Tasks), `screen/` (what's on the monitor: `screen.py` boxes, screens, fixes and undo; `layout.py` the 12×8 grid, both ported from Ziggletron; `static/index.html` the Screen page), `_template/` (copy this to add a feature) | Yes: copy `_template/`; each module's tests run with the rest |
| `models/` | Public-safe models shipped with Pip: wake words trained on synthetic voices only, plus openWakeWord's feature models (see `models/README.md`) | Only add models with no household data |
| `tests/` | Automated tests for the application (`uv run pytest`; on the Pi run it memory-capped, see CONTRIBUTING §4). `conftest.py` shared setup; `modhelp.py` plugs the real modules into a test; `test_builder.py`, `test_sandbox.py` (the real sandboxes), `test_release.py`, `test_usage.py`, `test_changes_voice.py` cover M1b | Yes: add tests with every change |
| `deploy/` | systemd service files (`deploy/systemd/`: `pip-core`, `pip-ears`, `pip-thinker`, `pip-web`, `pip-builder`; all start from `~/pip-app/current`) and `install.sh`, which installs the last commit as a new version through `release.py` (refuses uncommitted changes; `--allow-dirty`, `--now`); `tv/` and `install-tv.sh`: the monitor's kiosk (`pip-tv`, a system service, installed once with sudo) | Yes |
| `deploy/install-https.sh` | Gives the dashboard its https address on the owner's Tailscale devices (`tailscale serve` → port 8790, D-116), replacing Ziggletron's old entry. The owner runs it once with sudo | Yes |
| `pyproject.toml`, `uv.lock` | The application's Python project and pinned packages (uv; releases ≥ 2 weeks old) | Via `uv` |

## Outside the repository (never commit these)

| Path | What it is | Notes |
|---|---|---|
| `~/pip-data/settings.toml` | Your Pip settings (override `pip_assistant/defaults.toml`) | Edit freely; unknown or wrong settings are reported at start-up |
| `~/pip-data/pip.db` | Pip's database: decision trace (`trace`), timers/alarms/reminders (`alerts`), memories with inbox (`memories`), people and voiceprints (`people`, `voices`), the Listening switches and preferences (`settings`), and each module's own tables: calendar (`calendar_events`, `calendar_calendars`, `calendar_prefs`), to-dos (`todos_lists`, `todos_items`), Google accounts (`google_accounts`), the screen (`screen_state`), self-changes (`builder_changes`; requests blanked after 90 days), the usage meter (`usage`) | Private. Voiceprints never leave the Pi |
| `~/pip-data/models/speaker/` | Voice recognition model (TitaNet small; downloaded by `deploy/install.sh`) | Large; not in git |
| `~/pip-data/models/voices/` | Pip's Piper voice | Large; not in git |
| `~/pip-data/personality.md`, `personality-history/` | Pip's personality as edited on the Settings page, and its last 20 versions (missing = the built-in one) | Settings page |
| `~/pip-data/builder/` | Self-change workspaces (`work/<id>/`: `src/` the copy Claude edits, `base/` the untouched copy, `home/` Claude's throwaway home) and install results (`releases/`) | Made and deleted by pip-builder; don't edit |
| `~/pip-data/run/` | pip-core's connection socket (`core.sock`; see `common/paths.py`) | Made at start-up |
| `~/pip-data/secrets.toml` | API keys, plus a section per module (`[google]`: the Google client and each person's sign-in key) (mode 600) | **Never read into logs, prompts, commits or the dashboard** |
| `~/pip-data/m0/recordings/` | The owner's consented test recordings | Private. Kept on the Pi and usable for training (D-72). Never commit or upload except to the STT provider under test |
| `~/pip-data/m0/results/*.json` | One result file per M0 test | The dashboard reads these |
| `~/pip-data/m0/status.json` | Dashboard to-do list, progress, findings, test list | Update after each M0 step |
| `~/pip-data/m0/questions.json` | Questions for the owner and their answers | Read answers; record decisions in `docs/DECISIONS.md` |
| `~/pip-data/m0/tasks/*.json` | Progress of long-running jobs (shown under "Running now") | Long jobs should write one |
| `~/pip-data/m0/models/`, `wake-train/`, `tts/`, `llm/` | Downloaded models, training data and outputs | Large; not in git |
| `~/pip-app/` | **The live copy Pip runs from** (D-118): `releases/<id>/` (one version each, a git worktree of `~/pip` with its own `.venv`; newest 3 kept), `current` (link to the running one), `release-state.json` (current, previous, history), `release.log` (every install's progress), `units-before-releases/` (the service files from before versioned installs) | **Never edit by hand.** Commit, then `sh deploy/install.sh` |
| `~/pip-dashboard/` | The M0 dashboard (Python stdlib server + pages), Tailscale only (`http://100.118.171.118:8780`) | Runs as `pip-dashboard.service` (systemd user unit) |
| `~/ziggletron`, `~/ziggletron-v2` | The old assistant and an unrelated folder | **Not part of this project. Don't modify.** Ziggletron is switched off (D-82) and Pip has taken over; it stays installed as the owner's fallback, and only the owner starts him |

## Running services (systemd user units)

| Unit | What | Notes |
|---|---|---|
| `ziggletron.service` | The current assistant | Stopped and disabled by the owner (D-82). Don't start it |
| `pip-dashboard.service` | M0 dashboard (port 8780) | Restart after editing `~/pip-dashboard` |
| `pip-core`, `pip-ears`, `pip-thinker`, `pip-web`, `pip-builder` `.service` | **Pip itself** (dashboard on port 8790), run from `~/pip-app/current`. Installed by `deploy/install.sh` | Commit, then `sh deploy/install.sh`; restarting alone doesn't pick up edits in `~/pip` |
| `pip-release-*`, `pip-builder-agent-*`, `pip-builder-check-*` (transient) | An install in progress; a self-change's Claude Code run; its test check | Started by `install.sh` and pip-builder; leave them alone |
| `pip-tv.service` (system) | **Pip's face on the monitor** (Chromium kiosk on tty1). Installed once by `deploy/install-tv.sh`, which also switches Ziggletron's `ziggletron-tv` off | `sudo systemctl restart pip-tv` |
| `pip-*` transient units (`systemd-run`) | Long M0 jobs | **Don't survive a reboot.** Run heavy jobs with a memory cap (`-p MemoryMax=4500M`), because the Pi froze once from running out of memory |
