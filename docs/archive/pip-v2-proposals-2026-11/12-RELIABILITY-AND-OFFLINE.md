# Pip 2.0 — Reliability, Responsiveness and Offline Functionality (PROPOSAL)

> Ziggletron today: one ordered queue where a slow STT call delays everything except alarms (R-01); no watchdog for hung jobs (R-02); no offline reasoning, so no voice timers offline (R-03); "done" reported for actions that didn't happen (P-03, P-12); a reply collision (P-01).
> Requirement IDs: PIP-NEW-039, 044–045, 078–082. This document states **product** requirements and does not prescribe queues or frameworks (brief Part 14).

## 1. Action lifecycle: no false confirmations (PIP-NEW-039)

Every action (timer, reminder, memory, device, message, layout, setting, routine step) moves through observable states:

| State | Meaning | What Pip may say |
|---|---|---|
| Understood | The request was interpreted into an action | Nothing yet, or "Setting that up…" for slow actions |
| Authorised | Permission and confirmation tiers satisfied | If confirmation is needed: the confirmation question |
| Attempted | Execution started | — |
| Succeeded | The executor reported success | "Done" wording allowed for actions with no physical state |
| Failed | The executor reported failure, or not found | Must say it failed, and why if known |
| Verified | Independent evidence of the result (device state event, stored record read back) | "Confirmed" wording; required before "done" for devices that report state |
| Unverifiable | Accepted but can't be confirmed | "Sent, but I can't confirm it" |

- The model's words are not a confirmation. Spoken confirmations are generated **after** the result is known, or worded as in-progress ("Setting a ten-minute timer…" followed by a beep or "on" when created).
- Fuzzy matches below confidence → ask, don't act. No-match → failure.

## 2. Critical functions and latency budgets (PIP-NEW-078)

| Function | Budget (P95, on the Pi 5) | Must not be blocked by |
|---|---|---|
| Stop speaking on "stop" or barge-in | ≤100 ms from detection | Model requests, STT, TTS synthesis, image generation, plugins |
| Alarm/timer ringing on time | ≤1 s late | Everything |
| Alarm acknowledgement | ≤500 ms | Cloud STT (local keyword path) |
| Offline command (timer, cancel, snooze, volume, pause listening) | ≤1 s from end of speech to confirmation | Network |
| Chime after being called | ≤300 ms from end of speech | Cloud STT |
| First audio of an answer (online) | ≤2.5 s typical, 4 s P95 (Ziggletron: 1.5–3.5 s to first sentence) | — |
| Dashboard command acknowledgement | ≤500 ms | Model requests |

**Cancellation and timeouts:**
- Every external call has a timeout: STT 2–3 s, model first token about 8 s (then "this is taking a while…"), image generation 5 minutes, plugin calls 5 s.
- A newer request cancels an older pending one for the same person. One model session per request (P-01).
- Plugin and image work run with lower priority, and their failures are isolated.

## 3. Offline capabilities (PIP-NEW-044, 081)

| Works offline | Needs internet |
|---|---|
| Hearing, address detection, speaker ID, local transcription | Cloud STT (local fallback used instead) |
| Set / cancel / change timers; set simple reminders ("remind me at 6 to…") | Free-form conversation and complex questions (local LLM gives short, limited replies) |
| Snooze, shut off and acknowledge alarms | Web search, current facts |
| Stop, wait, volume, pause or start listening, mode changes | Drawings, photos |
| Read local status: time, timers, reminders, today's events, shopping list | Weather updates (last cached forecast shown, marked stale) |
| Add to shopping and to-do lists (common phrasings) | SMS |
| Basic smart-home commands (broker and Tuya control are local, M3) | Calendar sync |
| TV face, widgets, dashboard on the local network | Remote dashboard access (needs the tunnel) |

- **Model access is the Claude subscription only** (owner decision D-17: no pay-per-use API key). The same fallback applies when the **plan's usage limit is reached**, not only when the network is down. The usage meter (PIP-NEW-089) warns at 80% of the expected daily allowance, and self-change development is paused first to save quota for conversation.
- **Local command grammar** first; if no match and the model is unreachable, the **local LLM fallback** (owner decision) attempts a short reply or a simple action mapping. Its speed on the Pi 5 must be measured. Pip says when it is offline ("I'm offline, so I can only do the basics right now").
- The dashboard shows a clear "Offline: these features are unavailable" panel.

## 4. Health monitoring (PIP-NEW-079)

| Monitored | Liveness (running?) | **Functional check (actually working?)** |
|---|---|---|
| Microphone | Device open | Audio frames arriving, not all-zero (FEAT-051), level variance plausible |
| Speaker | Device present | Playback completes; optional periodic silent-level check |
| Bluetooth (if used) | Connected | Playback latency within bounds |
| Network | Interface up | Reaches the model provider and STT endpoints |
| Model provider | — | Last successful request time; error rate |
| STT provider | — | Latency and error rate (Speech panel) |
| Each background job | Process/task alive | **Heartbeat** within its expected period. A missed heartbeat is a stall → restart, plus a notice |
| Decision loop | Alive | Synthetic "tick" processed within 1 s |
| Alarm scheduler | Alive | Next-alarm computation fresh; self-test timer every hour (silent) |
| CPU, memory, temperature | — | Thresholds; throttling detected |
| Storage | Free space | Write test; SD/SSD health where available |
| TV client | Connected | Last render acknowledgement |
| MQTT broker and devices (M3) | Broker running | Per-device last-seen time; Tuya outlets reachable |

Stalls are restarted with back-off (as FEAT-083). Repeated restarts escalate to a Critical notice ([09 §6](09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md#6-proactive-self-diagnostics-pip-new-068)).

## 5. Deployment on the Raspberry Pi 5 (PIP-NEW-080, 082)

| Requirement | Proposal |
|---|---|
| Headless start at boot without login, including after power loss | As Ziggletron (HC-6) |
| Automatic recovery | Supervisor restarts; stall detection |
| Safe updates and rollback | Versioned installs side by side; post-update health check; automatic rollback; last 3 versions kept. Applies to normal releases and self-changes alike |
| Storage writes | Atomic writes with backup (FEAT-084 kept); batched writes; size-capped logs; SD-card wear monitored (owner decision D-39: no SSD) |
| Power loss | Data consistency after sudden power loss is an acceptance test. An optional UPS HAT is suggested (PIP-IDEA) |
| Resource budget | Target ≤70% average CPU and ≤5 GB RAM, with local room transcription, speaker ID, sound tagging, TTS and the TV kiosk running. Verified in the M0 measurements |
| Persistent settings | All settings and state survive restarts (fixes the U-04 inconsistency: the mic switch persists, owner decision D-22) |
| Laptop development | Optional mode with simulated mic input from files, simulated MQTT devices and a fake model, for development and tests (PIP-NEW-082) |
