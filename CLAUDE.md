# Pip — guidance for Claude Code

Pip is the second-generation household voice assistant replacing Ziggletron. It runs on a Raspberry Pi 5 (8 GB, SD card, aarch64, Python 3.13).

## Where things are
| Path | What |
|---|---|
| `01-discovery/` | Historical record of Ziggletron 1.0. **Never rewrite** (only fix demonstrable factual errors) |
| `pip-v2-proposals/` | The Pip 2.0 specification. Requirements: `02-PROPOSED-FEATURE-CATALOG.md` (PIP-NEW IDs); decisions: `17-OPEN-QUESTIONS.md` (D-nn); roadmap: `15-PRIORITIZED-ROADMAP.md`; acceptance: `14-ACCEPTANCE-CRITERIA.md` |
| `m0/` | M0 measurement tools (Pi load test, speakerphone check, speech benchmark) |
| `tools/` | Repository tooling (secret and phone-number check) |
| `~/pip-data/` | **Outside the repo.** Secrets (`secrets.toml`, mode 600), M0 results and consented recordings. Never commit or copy into the repo |
| `~/pip-dashboard/` | **Outside the repo.** Local M0 dashboard (Tailscale only) |
| `~/ziggletron`, `~/ziggletron-v2` | Not part of this project. Do not modify. Ignore unless asked |

## Current phase
**M0: measure and decide.** No Pip application code until M0 results and the architecture phase are done. Tools in `m0/` are measurement scripts, not the product.

## Rules
- Never put credential values, phone numbers or private recordings in the repo, commit messages, logs or the dashboard. The pre-commit check (`tools/check_secrets.py`) enforces this; don't bypass it.
- Commit messages describe changes; never paste spoken or private requests verbatim.
- Keep requirements traceable: reference FEAT-nnn / PIP-NEW-nnn / AC-* / D-nn IDs when changing the spec.
- The spec is implementation-independent; architecture decisions go in a separate architecture document (not yet written).
- Python tooling: `uv` with the project venv. System (apt) packages only after asking the owner.
- Ziggletron (`ziggletron.service`) may be stopped temporarily for M0 measurements, and must be restarted afterwards (D-44). Say so before stopping it.
- After running an M0 test, write its result JSON to `~/pip-data/m0/results/` so the dashboard shows it, and update `~/pip-data/m0/status.json` (next actions, progress).
