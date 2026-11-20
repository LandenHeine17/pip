# Pip

A playful household voice assistant for a Raspberry Pi 5, the successor to Ziggletron.

**Status:** M1a done (steps 1–8: hearing, answers, timers and alarms, memory, people and voices, listening to the room like Ziggletron, the calendar / to-dos / Google modules, the face on the monitor, the Settings page). M1b built 2026-11-09: Pip changing his own code with the owner's approval twice, versioned installs that roll back by themselves, and the usage meter. Pip has taken over from Ziggletron. M2 built 2026-11-13: smarter conversation and memory (explaining himself, conversation notes kept 30 days, a richer face, Settings → Your data). M3 (smart home) started 2026-11-15: one smart plug through Tuya's cloud, and a chat box on the dashboard. 2026-11-16: a review of the whole codebase fixed, and no wake-word detector any more: Pip is called by his name in the words, like Ziggletron (D-134). 2026-11-17 to 19: stricter clap switch-offs, replies from anyone right after he speaks, more joining in, chattiness by voice, and the best of Ziggletron (Opus, answering at the pause, roasting) (D-135–D-138). 2026-11-19: home control (D-139, D-140): the Pi, on Ethernet, hosts a Wi-Fi network for the smart plugs (`pip-plugs`), where the `home_control` module switches the lamps and the fan directly, Tuya's cloud as fallback, with a Home page on the dashboard. Pip's dashboard: `http://<pi-tailscale-ip>:8790`; M0 results and to-dos: `http://<pi-tailscale-ip>:8780` (Tailscale only).

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

Licence: MIT (see [LICENSE](LICENSE)).
