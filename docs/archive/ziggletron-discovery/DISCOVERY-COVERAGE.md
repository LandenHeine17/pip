# Discovery Coverage

Checklist of what was inspected and how features were cross-checked. Discovery date 2026-11-03; working tree at `efc08c2` (branch `claude-code-terminal`).

## 1. Repository inventory

| Area | Items | Inspected |
|---|---|---|
| Entry point & configuration | command line, settings loader, secrets loader, example settings, example secrets, live settings (structure only), dependency pins, packaging | ✅ all |
| Core coordination | assistant wiring, shared state, decision loop, hearing, answering, joining, alarms, activities, speaking, acting, commands, monitor, vitals, weather, screen, layout, pictures, photos, self-change, console, terminal, repository helpers | ✅ all |
| Audio | microphone/segmenter, voice-activity, echo gate, speaker, sounds/ringtones/volume, ambient sounds | ✅ all |
| Speech | STT engines and fallback, text heuristics | ✅ all |
| Brain | request types, prompts, model backends, stream parsing | ✅ all |
| Conversation | transcript, turn-taking, presence | ✅ all |
| Memory | household store, memory kinds, voice profiles | ✅ all |
| Skills (actions) | registry, built-ins, TV display actions | ✅ all |
| Platform | event bus, plugins, storage, texting, coding-agent environment | ✅ all |
| Web | server, dashboard page (markup, states, every command), TV page (states, reactions, accessories, sections, resilience) | ✅ server fully; pages by structure, commands, handlers and key logic |
| Plugins | example plugin | ✅ |
| Scripts & services | setup, Pi install, TV install, kiosk, user service, TV service | ✅ all |
| Documentation | README, FEATURES, HOW_IT_WORKS, behavior (personality), CODE_REVIEW, STATUS, temp notes | ✅ all (temp notes = this task's brief) |
| Tests | 22 test files, 328 tests | ✅ names reviewed; suite executed |
| Version history | 35 commits on branch, 4 self-change branches, 2 stale working copies | ✅ subjects and branch contents summarised |
| Live data folder | file list, permissions, record counts and field names | ✅ (no content) |
| Live service | service status, state events, error categories in logs | ✅ (read-only) |
| Third-party bundled assets (terminal emulator library) | — | ⏭ skipped (vendor code) |

## 2. Verification performed

| Check | Result |
|---|---|
| Full automated test suite (incl. real-browser dashboard and TV tests) | **328 passed, 0 failed, 0 skipped** in 68.9 s |
| Tests isolate data (temporary folders) | Confirmed before running |
| Live services | Assistant user service **active**; TV kiosk service **active** |
| Live state via event stream (types only, no content) | status, settings (subscription backend, Groq STT, chattiness normal, mic on), vitals, weather (no problem), screen (6 views listed, own-pictures on, 36 pictures), self-changes enabled (5 merged), voices enabled (17), console enabled (tmux present), STT quota limit 2,000, 8 ringtones, 4 loudness levels |
| Data folder privacy | Folder `drwx------`, files `-rw-------` |
| Data shapes | 58 memories (person 20, note 16, household 13, rule 9; 0 guessed); 5 repeating reminders; 1 event; 3 modified TV views; 36 pictures (16 photos); 17 voice profiles; 17 last-heard entries; console with 21 messages |
| Log error categories (7 days) | Layout failures, picture failures, one photo not found, one text failure, one historic empty-audio crash (fixed), one reply-collision error (P-01) |
| Hardware | Raspberry Pi 5 Model B, 4 cores, 8 GB RAM, Python 3.13.5 |

## 3. Cross-check matrix

Each feature was checked against: **E** entry point/trigger path · **U** user interface · **C** configuration · **I** integration · **T** tests · **R** runtime.

| Features | E | U | C | I | T | R |
|---|---|---|---|---|---|---|
| FEAT-001 Listening | ✅ | ✅ status/meter | ✅ | ✅ audio | ✅ segmenter | ✅ |
| FEAT-002 STT | ✅ | ✅ quota | ✅ | ✅ Groq | ✅ | ✅ |
| FEAT-003–006 Addressing, follow-up, chime | ✅ | ✅ feed | ✅ | — | ✅ | ◻ |
| FEAT-007–008 Echo, hygiene | ✅ | — | ✅ | — | ✅ | ◻ |
| FEAT-009 Sounds | ✅ | ✅ feed/TV | ✅ | — | ✅ | ✅ |
| FEAT-010–014 Voice ID | ✅ | ✅ voices/picker | ✅ | — | ✅ | ✅ |
| FEAT-015–018 Answering, TTS, barge-in | ✅ | ✅ | ✅ | ✅ model | ✅ | ✅ |
| FEAT-019–021 Joining, Ask, greeting | ✅ | ✅ | ✅ | ✅ | ✅ (Ask: ◻) | ✅ |
| FEAT-022–027 Personality, context, search, actions, errors, typing | ✅ | ✅ prompts | ✅ | ✅ | ✅ (search: ◻) | ✅ |
| FEAT-028–032 Memory | ✅ | ✅ | — | ✅ | ✅ | ✅ |
| FEAT-033–042 Timers/reminders/calendar/alarms | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| FEAT-043 Texting | ✅ | ✅ notes | ✅ | ✅ Twilio | ✅ | ◻ (configured) |
| FEAT-044–047 Volume, sleep, mic, test | ✅ | ✅ | — | ✅ | ✅ (volume, test: ◻) | ◻ |
| FEAT-048–051 Presence | ✅ | ✅ | ✅ | GPIO | ✅ (sensor, mic warning: ◻) | ◻ |
| FEAT-052–065 TV | ✅ | ✅ TV/menu | ✅ | ✅ weather/photos | ✅ | ✅ |
| FEAT-066–072 Dashboard | ✅ | ✅ | ✅ | — | ✅ | ✅ |
| FEAT-073–078 Self-change & console | ✅ | ✅ | ✅ | ✅ git/tmux/CLI | ✅ | ✅ (history) |
| FEAT-079 Plugins | ✅ | ✅ notes | ✅ | — | ✅ | ◻ (none enabled) |
| FEAT-080–087 Platform | ✅ | ✅ | ✅ | ✅ systemd | ✅ (mic recovery: ◻) | ✅ |
| FEAT-088–089 Unmerged | branch only | — | — | — | branch tests not run | — |

✅ checked · ◻ not checked or not possible safely · — not applicable.

## 4. Completeness checks

| Check | Method | Result |
|---|---|---|
| Every dashboard command maps to a feature | Listed all 34 commands sent by the page and the server's command table (incl. admin-only) | ✅ all mapped (see legacy-reference index) |
| Every model action maps to a feature | 12 built-in, 12 TV/picture, 2 self-change, plugin-defined | ✅ |
| Every settings key documented | Compared code defaults vs example file | ✅ 5 hidden keys identified |
| Every secret key documented | 6 keys | ✅ (names only) |
| Every event type documented | 22 event types in the bus contract | ✅ reflected in UI doc |
| Every background job | 11 supervised jobs + plugin jobs | ✅ |
| Every outbound network destination | Searched for URLs and HTTP clients | ✅ 6 runtime + install-time downloads |
| Every persisted file | Live data folder listing vs code | ✅ |
| Incomplete/unreachable functionality | Branches, unused UI paths, ideas in docs | ✅ FEAT-088/089, P-11, "Not built" list |

## 5. Known gaps in this discovery

- The dashboard and TV pages (≈2,600 lines) were inspected for structure, controls, commands, state handling and key logic, not every styling or animation detail.
- The two unmerged branches were summarised from commit messages and file lists, not executed.
- No live audio interaction was performed (by design).
