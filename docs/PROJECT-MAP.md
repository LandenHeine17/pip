# Project map

Where everything is, what it's for, and whether you may change it. **Keep this file up to date**: any change that adds, moves or removes a folder or important file must update this map in the same commit.

Last reviewed: 2026-11-04 (phase M0; docs condensed into three living files + archive).

## In this repository (`~/pip`, GitHub: private repo `pip`)

| Path | What it is | Change it? |
|---|---|---|
| `README.md` | Front page: what's where | Yes, keep short |
| `CLAUDE.md` | Read-first rules for Claude Code (loaded automatically) | Yes, carefully: keep it short; detail goes in `CONTRIBUTING.md` |
| `CONTRIBUTING.md` | How to make a change, for people and AI agents | Yes |
| `docs/PROJECT-MAP.md` | This file | Yes, whenever the layout changes |
| `docs/PIP-SPEC.md` | **What Pip does**, in plain language, plus M0 findings. The living spec | Yes: keep it short and current |
| `docs/ARCHITECTURE.md` | **How Pip is built**: the five programs, messages, modules, data, security, tech choices, build plan. Plain language + diagrams (D-71) | Yes: keep in step with the code |
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
| `tools/check_secrets.py`, `install_hooks.sh` | Commit check blocking secrets and phone numbers | Yes, but never weaken it |
| `pip_assistant/` | **The Pip application** (M1a). `common/` (messages, settings, bus client, `modules.py` finds and checks modules, `secrets.py` reads secrets and gives each module only its own section), `core/` (hub, decision trace, watchdog, conversation, speech-to-text incl. offline Whisper, `schedule.py` timers/alarms/reminders, ringing and calendar heads-ups, `commands.py` Pip's own fixed-phrasing commands (offline only, plus instant stop/snooze while ringing, D-94), `actions.py` checks and carries out Claude's actions (timers, alarms, reminders, memory, and hands modules' actions to them; stop/snooze and learn_voice are done in `conversation.py`), `voicecheck.py` drops sounds without a voice, `memory.py` what Pip remembers and the inbox, `people.py` people and voice recognition, `peoplecmds.py` people and voices without Claude (offline; also does the work for Claude's learn_voice action), `net.py` the online check behind the dashboard's offline label (D-95), `transcript.py` the room conversation Claude sees (RAM only), `joining.py` when Pip joins in, `mic.py` the Listening switches (dashboard + claps), `when.py` understanding times, `modules.py` plugs modules in: their context, actions, summaries, dashboard requests, background jobs), `ears/` (incl. `clap.py`, the two-clap detector), `thinker/`, `web/` (dashboard; `static/pip.css` and `pip.js` are shared with module pages), `defaults.toml` (every setting, explained) | Yes: follow ARCHITECTURE.md; tests in `tests/` |
| `pip_assistant/modules/` | **Plug-in features** (ARCHITECTURE §7, D-96), one folder each with `module.toml`, `module.py`, `static/` (its dashboard page) and its tests: `google/` (accounts per person, sign-in; `testing.py` is a pretend Google for tests), `calendar/` (`store.py` events on the Pi, `sync.py` two-way Google Calendar), `todos/` (`store.py`, `sync.py` Google Tasks), `_template/` (copy this to add a feature) | Yes: copy `_template/`; each module's tests run with the rest |
| `models/` | Public-safe models shipped with Pip: wake words trained on synthetic voices only, plus openWakeWord's feature models (see `models/README.md`) | Only add models with no household data |
| `tests/` | Automated tests for the application (`uv run pytest`) | Yes: add tests with every change |
| `deploy/` | systemd service files (`deploy/systemd/`) and `install.sh`, which installs/updates and restarts Pip's services | Yes |
| `pyproject.toml`, `uv.lock` | The application's Python project and pinned packages (uv; releases ≥ 2 weeks old) | Via `uv` |
| *(future)* `docs/architecture/` | Architecture overview, diagrams, component pages, design decisions | Created in the architecture phase |

## Outside the repository (never commit these)

| Path | What it is | Notes |
|---|---|---|
| `~/pip-data/settings.toml` | Your Pip settings (override `pip_assistant/defaults.toml`) | Edit freely; unknown or wrong settings are reported at start-up |
| `~/pip-data/pip.db` | Pip's database: decision trace, timers/alarms/reminders, calendar, memories (with inbox), people and their voiceprints, the Listening switches | Private. Voiceprints never leave the Pi |
| `~/pip-data/models/speaker/` | Voice recognition model (TitaNet small; downloaded by `deploy/install.sh`) | Large; not in git |
| `~/pip-data/secrets.toml` | API keys, plus a section per module (`[google]`: the Google client and each person's sign-in key) (mode 600) | **Never read into logs, prompts, commits or the dashboard** |
| `~/pip-data/m0/recordings/` | The owner's consented test recordings | Private. Deleted after 30 days. Never commit or upload except to the STT provider under test |
| `~/pip-data/m0/results/*.json` | One result file per M0 test | The dashboard reads these |
| `~/pip-data/m0/status.json` | Dashboard to-do list, progress, findings, test list | Update after each M0 step |
| `~/pip-data/m0/questions.json` | Questions for the owner and their answers | Read answers; record decisions in `17-OPEN-QUESTIONS.md` |
| `~/pip-data/m0/tasks/*.json` | Progress of long-running jobs (shown under "Running now") | Long jobs should write one |
| `~/pip-data/m0/models/`, `wake-train/`, `tts/`, `llm/` | Downloaded models, training data and outputs | Large; not in git |
| `~/pip-dashboard/` | The M0 dashboard (Python stdlib server + pages), Tailscale only (`http://100.118.171.118:8780`) | Runs as `pip-dashboard.service` (systemd user unit) |
| `~/ziggletron`, `~/ziggletron-v2` | The old assistant and an unrelated folder | **Not part of this project. Don't modify.** Ziggletron is switched off (D-82); only the owner starts him |

## Running services (systemd user units)

| Unit | What | Notes |
|---|---|---|
| `ziggletron.service` | The current assistant | Stopped and disabled by the owner (D-82). Don't start it |
| `pip-dashboard.service` | M0 dashboard (port 8780) | Restart after editing `~/pip-dashboard` |
| `pip-core`, `pip-ears`, `pip-thinker`, `pip-web` `.service` | **Pip itself** (dashboard on port 8790). Installed by `deploy/install.sh` | `sh deploy/install.sh` after code changes |
| `pip-*` transient units (`systemd-run`) | Long M0 jobs | **Don't survive a reboot.** Run heavy jobs with a memory cap (`-p MemoryMax=4500M`), because the Pi froze once from running out of memory |
