# Pip

A playful household voice assistant for a Raspberry Pi 5, the successor to Ziggletron.

**Status:** M1a, building. Steps 1–6 done and installed (hearing, answers, timers and alarms, memory, people and voices, listening to the room like Ziggletron, the calendar / to-dos / Google modules). Step 7 built and installed (the face on the monitor, what Pip is doing, and changing the screen by voice); the owner is trying it out. Next: step 8 (settings page, accounts, moving over from Ziggletron). Pip's dashboard: `http://100.118.171.118:8790`; M0 results and to-dos: `http://100.118.171.118:8780` (Tailscale only).

## Read

| File | What |
|---|---|
| [docs/PIP-SPEC.md](docs/PIP-SPEC.md) | **Start here.** What Pip does, in plain language, and what M0 has found |
| [docs/HOW-PIP-WORKS.md](docs/HOW-PIP-WORKS.md) | How Pip works: start here if you want to read the code |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | How Pip is built (approved, D-78): programs, modules, data, security, and the step plan |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Every owner decision (`D-nn`); these override everything else |
| [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md) | Lookup tables: requirements, Ziggletron features, acceptance criteria |
| [CONTRIBUTING.md](CONTRIBUTING.md) · [docs/PROJECT-MAP.md](docs/PROJECT-MAP.md) | How to change things, and where everything is |
| [docs/archive/](docs/archive/) | The detailed reference: what Ziggletron did, and the full Pip 2.0 behaviour rules (linked from each requirement). Frozen; where it conflicts with DECISIONS, the decision wins |
