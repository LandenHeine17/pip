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
| `docs/DECISIONS.md` | Every owner decision (`D-nn`); overrides everything | Add new ones at the end; never silently change one |
| `docs/REQUIREMENTS.md` | Lookup tables: `PIP-NEW` requirements, Ziggletron `FEAT` dispositions, `AC` acceptance criteria | Yes, with IDs |
| `docs/archive/` | **Finished historical record**: `ziggletron-discovery/` (what Ziggletron did) and `pip-v2-proposals-2026-11/` (the original 20-file proposal with full reasoning) | **No**, except fixing broken links |
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
| *(future)* `pip/` or `src/` | The Pip application | **Doesn't exist yet**: created after the architecture phase |
| *(future)* `docs/architecture/` | Architecture overview, diagrams, component pages, design decisions | Created in the architecture phase |

## Outside the repository (never commit these)

| Path | What it is | Notes |
|---|---|---|
| `~/pip-data/secrets.toml` | API keys (mode 600) | **Never read into logs, prompts, commits or the dashboard** |
| `~/pip-data/m0/recordings/` | The owner's consented test recordings | Private. Deleted after 30 days. Never commit or upload except to the STT provider under test |
| `~/pip-data/m0/results/*.json` | One result file per M0 test | The dashboard reads these |
| `~/pip-data/m0/status.json` | Dashboard to-do list, progress, findings, test list | Update after each M0 step |
| `~/pip-data/m0/questions.json` | Questions for the owner and their answers | Read answers; record decisions in `17-OPEN-QUESTIONS.md` |
| `~/pip-data/m0/tasks/*.json` | Progress of long-running jobs (shown under "Running now") | Long jobs should write one |
| `~/pip-data/m0/models/`, `wake-train/`, `tts/`, `llm/` | Downloaded models, training data and outputs | Large; not in git |
| `~/pip-dashboard/` | The M0 dashboard (Python stdlib server + pages), Tailscale only (`http://100.118.171.118:8780`) | Runs as `pip-dashboard.service` (systemd user unit) |
| `~/ziggletron`, `~/ziggletron-v2` | The old assistant and an unrelated folder | **Not part of this project. Don't modify.** Ziggletron may be stopped briefly for tests (D-44) and must be restarted |

## Running services (systemd user units)

| Unit | What | Notes |
|---|---|---|
| `ziggletron.service` | The current assistant | Keep running unless a test needs the mic or CPU |
| `pip-dashboard.service` | M0 dashboard | Restart after editing `~/pip-dashboard` |
| `pip-*` transient units (`systemd-run`) | Long M0 jobs | **Don't survive a reboot.** Run heavy jobs with a memory cap (`-p MemoryMax=4500M`), because the Pi froze once from running out of memory |
