# Unknown and Unverified

What discovery could not confirm, and why. Verification rules followed: no personal audio was captured, no external actions were triggered (no texts, no model requests, no pictures, no self-changes), and no persistent data was modified. Runtime observation was limited to reading the live event stream's *state* events, file metadata, record counts, service status and log error categories.

## 1. Built, but not observed working

| Item | Why unverified | How it could be verified safely |
|---|---|---|
| Motion-sensor "away" (FEAT-049) | No sensor configured; no tests; requires Pi GPIO | Bench test with a PIR sensor |
| API (pay-per-use) model backend at runtime (FEAT-085) | Live instance uses subscription; tests use a mocked client | Run with a test key |
| Real SMS delivery (FEAT-043) | Would send a real message; tests use a fake transport | Twilio test credentials |
| Web search quality and limits (FEAT-024) | Provider-side; not exercised | Observe Prompts tab during a weather question |
| Microphone reopen after unplugging (FEAT-087) | No test for live capture; would disturb the running service | Unplug test on a spare device |
| Muted-mic warning (FEAT-051) | Source only | Mute system input |
| Volume control (FEAT-044) | Would change the live speaker volume | Run on a spare device |
| "Ask" join trigger (FEAT-020), speaker test (FEAT-047) | Would make the live device speak | Spare device |
| Six-seven reaction (FEAT-054) | Visual; source only | Say it in front of the TV |
| Bluetooth speaker pairing and reconnection | OS-level, documented only | Manual |
| macOS laptop support | Claimed in docs; vitals and some tools are Linux-specific | Run on macOS |
| Pi 4 performance with small models | Documented only | Run on a Pi 4 |
| PipeWire echo-cancellation recipe | Documented only; options vary by version | Manual |
| Plugin system in production (FEAT-079) | No plugins enabled live; covered by tests | Enable the example plugin |
| Self-change end-to-end with a real coding agent | Tests use real git repos but a simulated agent; live history shows 5 merged changes, which indirectly confirms it | Observe the next real change |
| Self-change "question announced aloud" and unwatched timeout | Tested with simulation only | Observe live |
| Accuracy figures for voice ID (~4% wrong, ~1 in 6 unknown) and echo tuning | From project docs; measured on synthetic recordings | Labelled real-room recordings |
| Latency figures (1.5–3.5 s to first sentence etc.) | From docs; one live timing event observed but not recorded here | Collect timing events over a day |

## 2. Behaviour that is uncertain

| Question | What is known | Uncertainty |
|---|---|---|
| Exact trigger of the reply collision (P-01) | Traceback shows two readers on one pre-started model session during an answer | Which user sequence triggers it (rapid follow-up vs failed-action follow-up vs replacement) is inferred, not reproduced |
| Why self-change working copies remain (P-02) | Two remain; cleanup runs on normal completion | Most likely left by a restart mid-change (startup cleanup prunes records but not folders). Not reproduced |
| Whether the music branch (FEAT-088) ever worked | Branch exists with tests; never merged; no history record | Unknown outcome; records may have been cleared |
| Whether the "explain own code" branch (FEAT-089) passed tests | Committed on a branch, approved by an admin, not merged | Its history record was not present; likely "kept on branch" (code moved on) then removed |
| Model behaviour quality (when it PASSes, joins, remembers) | Governed by prompts and the personality file | Inherently probabilistic; not measured |
| Wikimedia family filter effectiveness | Keyword list only | No measurement |
| Cloud STT data retention | Third-party policy | Outside the repository |
| Model provider data retention | Third-party policy | Outside the repository |
| Whether the repository is pushed to a public host | A remote named `origin` and a `github-main` branch exist | Visibility and content of the remote not checked |
| How long the system journal keeps conversation lines | Depends on OS journal settings | Not inspected |
| The coding CLI's own session transcripts | Resumable sessions exist outside the data folder | Location and retention belong to the third-party tool |

## 3. Documentation claims not independently confirmed

- "Replies count against your plan's usage limits": provider policy.
- "Groq free tier 2,000 requests a day": the live meter showed a 2,000 limit [R], but pricing beyond that is not verified.
- "A Claude process started in advance saves about a second": not measured.
- "Voice profiles file ~137 KB": not measured (count of 17 profiles observed).
- "Tests take about 30 s": measured ~69 s on the Pi [R].

## 4. Things deliberately not inspected

- Contents of memories, conversation, calendar, voice profiles, self-change requests, console chats, pictures, texts and logs beyond category counts (privacy).
- Secret values (only key names were listed, with values redacted).
- The remote repository.
