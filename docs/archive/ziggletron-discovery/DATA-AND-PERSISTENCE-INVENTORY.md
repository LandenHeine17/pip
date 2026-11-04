# Data and Persistence Inventory

What information the current product handles, why, for how long, and where it goes. Storage *mechanisms* are noted only to describe lifecycle; they are not requirements. **No credential values, conversation content, recordings or personal details are reproduced here.** Live counts were taken read-only on 2026-11-03.

Legend: **Collect** = captured from people/environment · **Temp** = held only while running · **Persist** = kept across restarts · **Send** = transmitted off the device · **Delete** = how it is removed.

## 1. Summary matrix

| # | Category | Collect | Temp | Persist | Send externally | Retention / deletion |
|---|---|---|---|---|---|---|
| D1 | Raw room audio | ✔ continuous | ✔ seconds | ✖ never written to disk | ✔ every utterance to cloud STT when enabled | Discarded after processing |
| D2 | Utterance transcripts (conversation) | ✔ | ✔ last 40 lines (model context), last 300 events (dashboard) | ✖ (but see D3, D4, D15) | ✔ to model provider with every reply/join-check | Lost on restart |
| D3 | Model request log (prompts + replies) | derived | ✔ last 60 | ✖ | (already sent) | Lost on restart |
| D4 | Application log | derived | ✔ last 500 on dashboard | ✔ system journal (service log) | ✖ | Per journal retention |
| D5 | Voiceprints / voice profiles (biometric) | ✔ | ✔ | ✔ (17 people live) | ✖ | Manual forget only; no expiry |
| D6 | Line-level voiceprints (for corrections) | ✔ | ✔ last 200 lines | ✖ | ✖ | Lost on restart |
| D7 | "Last heard" per person (presence) | ✔ | ✔ | ✔ | ✖ | Never pruned |
| D8 | Household memories | ✔ (asked or overheard) | ✔ | ✔ (58 live; cap 150) | ✔ relevant ones with each request | Voice forget, dashboard delete, silent eviction at cap |
| D9 | Timers, reminders, alarm state | ✔ | ✔ | ✔ (5 live) | ✔ summary in context | Removed when finished/cancelled; ringing >12 h dropped |
| D10 | Calendar events | ✔ | ✔ | ✔ (1 live) | ✔ next 30 days in context | Voice/dashboard removal; past non-repeating events remain stored |
| D11 | Text-message recipients (names→numbers) | config | ✔ | ✔ in settings file | ✔ number + message to SMS provider | Edit settings |
| D12 | Text counts per day | ✔ | ✔ | ✔ | ✖ | Overwritten daily |
| D13 | Text message contents | ✔ | ✔ | ✖ (appears in notes/log) | ✔ SMS provider | Log retention |
| D14 | Pictures (drawings) and photos | ✔ | ✔ | ✔ last 50 (≈11 MB live), failed replies last 10 | Drawing prompts to model; photo search words to Wikimedia | Rolling deletion |
| D15 | Self-change history | ✔ | ✔ | ✔ last 50 (request text, who asked/okayed, instructions, steps, files) | Request text to coding agent (model provider) | Dashboard remove/clear |
| D16 | Code commits made by the assistant | ✔ | — | ✔ version history (may be pushed to a remote) | Possibly (if pushed) | Effectively permanent |
| D17 | Coding console session & chat requests | ✔ | ✔ | ✔ session id + last 400 messages; coding CLI keeps its own transcripts | ✔ to model provider | Not exposed for deletion |
| D18 | Coding agent activity events | ✔ | ✔ | ✔ event files in data folder | ✖ | Cleared when a new session starts |
| D19 | Preferences (chattiness) | ✔ | ✔ | ✔ | ✖ | Overwritten |
| D20 | Ringtone settings | ✔ | ✔ | ✔ | ✖ | Overwritten |
| D21 | TV state (current view, saved dashboards, own-pictures switch) | ✔ | ✔ | ✔ (3 saved) | ✔ summary in context | Voice delete/reset |
| D22 | Picture/photo daily counts | ✔ | ✔ | ✔ | ✖ | Daily reset |
| D23 | Location | config | ✔ | ✔ settings | ✔ to weather service; ✔ to model provider | Edit settings |
| D24 | Weather data | fetched | ✔ | ✖ | — | Refreshed every 30 min |
| D25 | Machine vitals | ✔ | ✔ | ✖ | ✖ | — |
| D26 | Ambient sound labels | ✔ | ✔ in transcript | ✖ | ✔ as part of transcript | Lost on restart |
| D27 | Credentials (API keys, tokens, dashboard password) | config | ✔ in process environment | ✔ private secrets file | ✔ each to its own service | Edit file |
| D28 | Personality text | config | ✔ | ✔ (in version control) | ✔ to model provider | Edit file |
| D29 | Own-voice reference | generated | ✔ | ✖ | ✖ | Rebuilt each start |
| D30 | Browser-side admin password | entered | ✔ tab session storage | ✖ | ✖ | Closing tab / Lock |

## 2. Category details

### D1 Raw audio
- **Purpose:** detect speech, transcribe, identify speaker, classify sounds.
- **Lifecycle:** captured continuously while the mic is open; split into utterances; each utterance is processed then discarded. Never recorded to disk.
- **External:** with cloud STT enabled, **every utterance**, not only those addressed to the assistant, is uploaded to the STT provider (no local wake word).
- **Off switches:** mic switch, sleep, away (sensor), local-only STT mode.

### D2 Conversation transcript
- **Purpose:** context for answers and join decisions; display.
- **Content:** time, speaker label, text; sound notes; action outcomes; the assistant's own lines (marked cut off where relevant).
- **External:** last 40 lines go to the model provider with every request, including join checks (so room chatter is sent during pauses unless chattiness is off).
- **Retention:** memory only; restart clears. Also mirrored line-by-line into the service log (D4), so in practice room conversation persists in the system journal.

### D3 Model requests
Full prompts (including memories and conversation) and raw replies, shown on the Prompts tab; last 60 in memory.

### D4 Logs
Everything noteworthy, including each heard line and reply, goes to the system journal. Viewable on the dashboard without a password.

### D5 Voice profiles (biometric)
- **Purpose:** label speakers; authorise admins; targeted reminders; greetings.
- **Content per person:** up to 60 compact voice embeddings with weights and line keys, plus last-recognised time. Tied to a specific recognition model; changing the model sets old profiles aside in a separate file.
- **Retention:** indefinite; delete per person (admin). No guest expiry. 17 profiles live.

### D7 Last-heard times
Lower-cased name → time last recognised (sure or probable). Used for "is X home?". Never pruned.

### D8 Memories
- **Content:** fact, date saved, kind, about-terms, "guessed" flag.
- **Collection:** on request or **unprompted while joining in** (model told not to store private things; not enforced).
- **Retention:** until forgotten/deleted; oldest silently dropped past 150. Backup copy of the previous save is kept.

### D9/D10 Alerts and calendar
Timer/reminder label, due time, repeat, condition, person, text-to; events with title, start/end, notes, reminder offset, repeat. Ringing state persisted for restart recovery.

### D11–D13 Texting
Recipient numbers are kept in the (non-secret) settings file; the model and dashboard only see names. Message text is sent to the SMS provider and appears in notes and logs.

### D14 Pictures
Drawings (vector) and downloaded photos (with title, credit, source page). The index records prompt/search words and whether unprompted. Served only to the dashboard/TV with a restrictive content policy.

### D15–D18 Self-change and console data
- Self-change records include the full request text and who asked and approved (names).
- Commits made by the assistant embed the request and names in version history. **Observed:** at least one commit subject in history contains a personal phone number taken verbatim from a request. If the repository is pushed to a hosting service, this data leaves the device.
- The coding CLI keeps its own session transcripts (resumable from a shell), outside the product's control.

### D27 Credentials
Names only: cloud STT key, model API key, model subscription token, dashboard password, SMS account SID, SMS auth token. Stored in a private file (warns if readable by others); loaded into the process environment; withheld from child programs except the subscription token (needed by the coding CLI and the model CLI).

## 3. Storage properties (observable)

- All data is kept under one data folder, private to the owning user (folder 700, files 600, confirmed live [R]).
- Saves are atomic; the previous version is kept as a backup; damaged files are set aside and the backup used, with a dashboard note.
- Large frequently-changing data (voice profiles) is saved in batches every 15 s.
- No user-facing export, import, backup or "delete everything" function.
- Moving to another machine requires manually copying the data folder, settings and secrets; copies do not sync.

## 4. What crosses the device boundary

| Destination | Data |
|---|---|
| Cloud STT (optional) | Every utterance's audio; the assistant's name as a hint |
| Model provider | Personality, mechanics, action list; last 40 transcript lines with speaker names; relevant memories; timers, reminders, calendar (30 days); texting names and remaining counts; TV state and recent picture prompts; known voice names; home location; date and time; plugin context; drawing descriptions; memory facts for sorting; self-change requests and the full codebase (coding agent); console chat |
| Provider web search | Queries chosen by the model |
| Weather service | Place name and resolved coordinates |
| Photo service | Search words; image downloads |
| SMS provider | Recipient number, sender number, message text, account credentials |
| Code hosting (if the owner pushes) | Commit history including self-change requests |

## 5. Data that should be considered sensitive in redesign
Voiceprints (biometric), room conversation and its log mirror, memories about people, calendar, presence (who is home), phone numbers, self-change requests embedded in version history, and the code console's ability to read anything on the device.
