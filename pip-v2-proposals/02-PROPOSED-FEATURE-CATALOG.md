# Pip 2.0 — Proposed Feature Catalog (PROPOSAL)

All features Pip 2.0 is proposed to have. **Nothing here is implemented.** Every entry is a proposal awaiting the owner's approval.

- **Retained Ziggletron capabilities** keep their original `FEAT-nnn` IDs (Part 1). Their decision and new behaviour are in [01-FEATURE-DECISION-MATRIX.md](01-FEATURE-DECISION-MATRIX.md).
- **New capabilities** have `PIP-NEW-nnn` IDs (Part 2). They do not exist in Ziggletron.
- **Optional ideas** not yet proposed as requirements are `PIP-IDEA-nnn`, in [16-ADDITIONAL-RECOMMENDATIONS.md](16-ADDITIONAL-RECOMMENDATIONS.md). Ideas the owner adopted became PIP-NEW-084…089 (section L).
- **Milestones** (M1–M4) are defined in [15-PRIORITIZED-ROADMAP.md](15-PRIORITIZED-ROADMAP.md). **Acceptance criteria** are in [14](14-ACCEPTANCE-CRITERIA.md).
- **Priority:** P1 = needed for the milestone; P2 = important; P3 = nice to have.

## Part 1 — Retained Ziggletron capabilities (summary)

| Area | Retained features (decision in 01) |
|---|---|
| Hearing and identity | FEAT-001–014 (most redesigned around local address detection and consent) |
| Conversation | FEAT-015–027 |
| Memory | FEAT-028–032 (redesigned into the categorised memory system) |
| Scheduling and alarms | FEAT-033–042 |
| Messaging | FEAT-043 |
| Device and presence | FEAT-044–051 |
| TV | FEAT-052–065 |
| Dashboard | FEAT-066–072 (replaced by the restructured, authenticated dashboard) |
| Self-change | FEAT-073–078 (replaced by the safe self-modification lifecycle) |
| Platform | FEAT-079–087 |
| Unmerged | FEAT-088 (music, redesigned for local files, M3), FEAT-089 (replaced by PIP-NEW-064–067) |

## Part 2 — New capabilities

Columns: ID · Name · What it does · Milestone · Priority · Privacy note · Spec.

### A. Listening and speech

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-001 | Local address detection | An on-device name spotter decides whether Pip was called, before any audio leaves the house | M1 | P1 | Fixes V-01 | [04 §2](04-SPEECH-RECOGNITION-RESEARCH.md#2-proposed-pipeline-behavioural-not-a-library-choice) |
| PIP-NEW-002 | Rolling RAM pre-roll | Keeps about 10 s of audio in memory, so words spoken *before* the name are included | M1 | P1 | Never on disk | 04 §2 |
| PIP-NEW-003 | Listening modes | Private / Room-Aware / Social, visible on the dashboard, TV and by voice | M1 (Social, Private); M2 (Room-Aware refinements) | P1 | Core privacy control | [03 §1](03-CONVERSATION-AND-PERSONALITY.md#1-listening-modes) |
| PIP-NEW-004 | Per-person opt-out and guest gating | Household adults are included unless they opt out (dashboard or "Pip, leave me out"); everyone is told how. Guests and opted-out speakers → Room-Aware behaviour | M1 | P1 | Required for Social default | 03 §1.2 |
| PIP-NEW-005 | Voice privacy switch | "Pip, private please" / "Pip, start listening", handled locally and works offline | M1 | P1 | — | 03 §1.2 |
| PIP-NEW-006 | Local fast-path words | Stop / wait / actually / alarm acknowledgement / snooze detected on device, with no transcription round trip | M1 | P1 | — | 04 §2, 03 §3 |
| PIP-NEW-007 | Adaptive end of utterance | Waits longer when speech sounds unfinished; avoids clipping | M1 | P1 | — | 04 §2.3 |
| PIP-NEW-008 | Selectable STT with health-based fallback | Provider chosen in Settings; 2–3 s timeouts; automatic fallback and recovery | M1 | P1 | Provider retention terms shown | 04 §5 |
| PIP-NEW-009 | Custom vocabulary | One list of names, devices and terms, translated to each provider's hint mechanism, with insertion monitoring | M1 | P2 | — | 04 §4 |
| PIP-NEW-010 | Speech panel | Latency, failures, fallback time, usage and estimated cost per provider; transcript "was that right?" | M1 | P2 | Aggregates only | 04 §5 |
| PIP-NEW-011 | Benchmark recording mode and tool | Opt-in, announced, auto-expiring test recordings; repeatable benchmark runs | M0/M1 | P1 | Consent and expiry | [05](05-SPEECH-BENCHMARK-PLAN.md) |
| PIP-NEW-012 | Media speech detection | Recognises TV/radio speech, so it can't wake Pip, stop alarms or trigger joining | M1 | P1 | — | 03 §2.1 |
| PIP-NEW-013 | ~~Continuous local room transcription~~ **Replaced by D-51** (Groq transcribes room speech in Social mode) | On-device streaming STT for room context in Social/Room-Aware modes (budget decision) | M1 (pending Pi measurement) | P1 | Audio stays local | 04 §6 |

### B. Voice output

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-014 | Local voice upgrade and cached phrases | Kokoro vs Piper evaluation; system phrases pre-synthesised | M1 | P2 | — | [04 Part B](04-SPEECH-RECOGNITION-RESEARCH.md#part-b--spoken-output-tts) |
| PIP-NEW-015 | Pronunciation list | Correct pronunciation of names and terms; shared with the vocabulary | M1 | P2 | — | 04 Part B |
| PIP-NEW-016 | Pause/resume and ducking | "Wait" holds a reply; Pip lowers its volume when someone else talks | M2 | P2 | — | 03 §3 |
| PIP-NEW-017 | Long-answer offer | Offers more detail rather than talking at length; can send it to the TV | M2 | P3 | — | 04 Part B |

### C. Conversation and personality

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-018 | Opportunity-triggered joining (**owner version, D-52:** a fast local question check gates a Claude "anything to add?" call; ≤ every ~3 min; no budget or back-off) | Local gates and triggers before any model call; budgets; back-off when ignored | M1 (basic gates), M2 (full) | P1 | Cuts cloud exposure | 03 §2 |
| PIP-NEW-019 | Offer-before-answer | In Room-Aware mode, offers help without sending anything ("I can look that up") | M2 | P2 | Nothing sent until "yes" | 03 §2.2 |
| PIP-NEW-020 | Person-bound follow-up | Follow-up window applies to the person Pip was talking to | M2 | P2 | — | 03 §2.3 |
| PIP-NEW-021 | Correction handling | "Actually…" amends the previous request or action instead of duplicating it | M2 | P2 | — | 03 §3 |
| PIP-NEW-022 | Clarification policy | One short question when a request is ambiguous and a wrong guess matters | M1 | P1 | — | 03 §5, 07 §3.2 |
| PIP-NEW-023 | State and mode awareness | Pip can say its mode, who has opted in, what it's doing and why | M1 | P2 | — | 03 §5 |
| PIP-NEW-024 | Guided voice enrolment | Read-aloud enrolment on the dashboard or phone | M2 | P2 | Consent recorded | 03 §4.2 |
| PIP-NEW-025 | Guest voices and expiry | Session-only guest labels; persistence only with consent; profile expiry | M1 | P1 | Fixes V-04 | 03 §4.3 |
| PIP-NEW-026 | Persistent speaker corrections | Corrections survive restarts; 24 h correction window | M2 | P3 | Short retention | 03 §4.2 |
| PIP-NEW-027 | Identity-to-authorisation tiers | Voice confidence maps to allowed operations; admin power needs a signed-in account | M1 | P1 | — | 03 §4.4, [11](11-SECURITY-AND-PERMISSIONS.md) |

### D. Memory and data

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-028 | Categorised memories with owners | 8 categories, owner, visibility, confirmed/inferred | M1 | P1 | Core | [06 §2](06-MEMORY-AND-DATA-POLICY.md#2-memory-categories) |
| PIP-NEW-029 | Memory inbox and approval | Suggestions and sensitive items wait for approval; overheard content never auto-saved | M1 | P1 | Fixes V-03 | 06 §3 |
| PIP-NEW-030 | Memory Center | Browse, search, add, edit, explain, archive | M1 (basic), M2 (full) | P1 | — | 06 §5 |
| PIP-NEW-031 | No-loss retention | No count cap; duplicate and contradiction checks; stale review; archive | M1 | P1 | — | 06 §3.2–3.3 |
| PIP-NEW-032 | Sensitive-memory rules | Subject approval for sensitive kinds; refuses credentials | M1 | P1 | — | 06 §4 |
| PIP-NEW-033 | Forget a person | One action removes a person's voice, memories and related data | M2 | P2 | Right to be forgotten | 06 §4 |
| PIP-NEW-034 | Export, import, delete everything | Readable exports; reviewed imports; full wipe | M1 (import for migration), M2 (rest) | P2 | Fixes V-08 | 06 §4 |
| PIP-NEW-035 | "Why do you remember that?" | Pip explains a memory's source and offers to forget it | M2 | P3 | — | 06 §5 |

### E. Smart home and routines

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-036 | Device hub (MQTT / ESPHome / local Tuya) | Local MQTT broker with self-describing discovery; ESPHome devices; Eightree outlets over the local Tuya protocol; allow-list of approved devices. No Home Assistant (owner decision) | M3 | P1 | Local network only | [07 §1](07-SMART-HOME-AND-INTEGRATIONS.md#1-platform-choice-pip-is-the-device-hub-owner-decision-d-18) |
| PIP-NEW-037 | Device registry | Aliases, rooms, owners, risk class; feeds vocabulary | M3 | P1 | — | 07 §2 |
| PIP-NEW-038 | Contextual device commands | "My desk light", "it", "a little brighter", "everything except…" | M3 | P1 | — | 07 §3 |
| PIP-NEW-039 | Verified action lifecycle | Understood → authorised → attempted → succeeded/failed → verified; applies to **all** actions | M1 | P1 | — | [12 §1](12-RELIABILITY-AND-OFFLINE.md#1-action-lifecycle-no-false-confirmations-pip-new-039) |
| PIP-NEW-040 | Device risk classes | A/B/C, with confirmation rules | M3 | P1 | — | 07 §4 |
| PIP-NEW-041 | Routines | Named, editable, previewable; triggers need approval | M3 | P2 | — | 07 §7 |
| PIP-NEW-042 | Environmental events and sensor context | Configured sensor/event topics; announcement rules; one weather source | M3 | P2 | — | 07 §6 |
| PIP-NEW-043 | Custom hardware via ESPHome/MQTT | ESP32 devices usable with no new Pip integration | M3 | P2 | — | 07 §1.2 |

### F. Scheduling and tasks

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-044 | Offline command path | Timers, cancel, snooze, volume, stop, pause listening, status, and (M3) basic devices, all without cloud reasoning | M1 | P1 | — | 12 §3 |
| PIP-NEW-045 | Voice snooze | "Snooze ten minutes" | M1 | P2 | — | 12 §3 |
| PIP-NEW-046 | Dashboard scheduling parity | Create reminders, set repeats on events, richer recurrence (weekdays, monthly) | M1 | P2 | — | [08 §3](08-DASHBOARD-AND-TV-REDESIGN.md#3-calendar-and-tasks) |
| PIP-NEW-047 | Shopping list | Shared list by voice and dashboard; live on the TV | M3 | P2 | — | 08 §3 |
| PIP-NEW-048 | To-do lists and shared tasks | Per-person and household tasks, with optional due dates and assignee | M3 | P2 | Private tasks visible only to their owner | 08 §3 |
| PIP-NEW-049 | Google Calendar synchronisation | Two-way sync with one Google calendar (owner decision D-19); OAuth token in the vault | M3 | P2 | Calendar data to Google | 08 §3 |
| PIP-NEW-050 | Natural-language editing | "Move the dentist to Thursday", "make the pasta timer 15 minutes" | M2 | P2 | — | 08 §3 |
| PIP-NEW-051 | Authorised alarm acknowledgement | Normal alarms stop only for a non-media voice; earned-alarm attempts only from household voices | M1 | P1 | — | 03 §3 |

### G. Dashboard, security and data access

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-052 | Restructured dashboard | Home / Conversation / Memory / Home & Devices / Calendar & Tasks / Appearance / Settings / Developer | M1 (core sections) | P1 | — | [08 §1](08-DASHBOARD-AND-TV-REDESIGN.md#1-navigation) |
| PIP-NEW-053 | Settings UI | Every user setting editable without file edits; validated; most apply without restart | M1 | P1 | — | 08 §2 |
| PIP-NEW-054 | Accounts and roles | Sign-in; Admin, Member, Viewer roles; per-action permission checks | M1 | P1 | Fixes S-01/S-02 | [11 §2](11-SECURITY-AND-PERMISSIONS.md#2-roles-and-permissions) |
| PIP-NEW-055 | Secure remote access | Access away from home through a private tunnel (Tailscale-style) with HTTPS; no public port forwarding | M1 | P1 | — | 11 §3 |
| PIP-NEW-056 | Per-client data scoping | The TV and other clients receive only what they display | M1 | P1 | Fixes V-07 | 11 §4 |
| PIP-NEW-057 | Audit trail | Who did what, when, from where; no content | M1 | P1 | — | 11 §5 |
| PIP-NEW-058 | Credential vault and migration | Credentials stored separately and encrypted; imported from Ziggletron without exposure | M1 | P1 | — | 11 §6 |
| PIP-NEW-059 | Privacy-safe structured logs | Structured, redacted events; retention; time-limited content debugging | M1 | P1 | Fixes V-05 | 11 §5, 06 §6 |

### H. TV

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-060 | Live data widgets | Widgets show live data (adding milk updates the TV immediately) | M1 (existing boxes made live), M3 (new widgets) | P1 | — | [08 §5](08-DASHBOARD-AND-TV-REDESIGN.md#5-tv-widgets-pip-new-060) |
| PIP-NEW-061 | Reliable layout editing | Templates, validation, preview, undo, drag-and-drop | M1 (validation and undo), M3 (drag-and-drop) | P1 | — | 08 §6 |
| PIP-NEW-062 | Richer face | More expressions, mode indicator, better listening and speaking animation, appearance options | M2 | P2 | — | 08 §4 |
| PIP-NEW-063 | Independent unprompted-content switches | Separate switches for unprompted photos and drawings | M1 | P2 | — | 08 §7 |

### I. Self-knowledge and diagnostics

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-064 | Capability registry | A maintained, machine-readable description of every feature, setting and limit, versioned with the code | M1 | P1 | — | [09 §2](09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md#2-knowledge-sources-pip-new-064) |
| PIP-NEW-065 | Self-knowledge Q&A | Answers "how does your memory work?" from the registry, docs and read-only code; reports doc/code discrepancies | M2 | P1 | — | 09 §3 |
| PIP-NEW-066 | Decision trace and "why" explanations | Records observable decisions, so Pip can explain "why didn't you answer?" | M1 (trace), M2 (spoken explanations) | P1 | Trace contains no content by default | 09 §4 |
| PIP-NEW-067 | Read-only self-inspection tools | Sanitised status, config metadata, docs and source search, redacted events | M2 | P1 | Denies secrets and memories | 09 §5 |
| PIP-NEW-068 | Proactive self-diagnostics | Proportionate notices about its own problems | M1 (dashboard), M2 (spoken) | P2 | — | 09 §6 |

### J. Safe self-modification

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-069 | Request classification | Tells apart a question, a bug report, an idea, a code change and a config change | M1 | P1 | — | [10 §2](10-SAFE-SELF-MODIFICATION.md#2-request-intake-and-classification-pip-new-069) |
| PIP-NEW-070 | Clarification dialogue | Asks only what is needed to define the change | M1 | P1 | — | 10 §3 |
| PIP-NEW-071 | Change proposal | A readable proposal (what, why, affected features, risks, tests, data impact) approved before any coding | M1 | P1 | — | 10 §4 |
| PIP-NEW-072 | Sandboxed development | Isolated workspace and container, restricted files and network, no production secrets, limits, cleanup | M1 | P1 | Fixes S-03 | 10 §5 |
| PIP-NEW-073 | Verification pipeline | Build, unit, integration, regression, security, privacy, architecture and hardware checks | M1 | P1 | — | 10 §6 |
| PIP-NEW-074 | Authenticated review and approval | Diff, results, risks and rollback shown; admin approval on the dashboard | M1 | P1 | Fixes S-04 | 10 §7 |
| PIP-NEW-075 | Safe deployment and rollback | Keeps the previous version; post-deploy health checks; automatic rollback | M1 | P1 | — | 10 §8 |
| PIP-NEW-076 | Sanitised change history | Explains every change; commit messages built from approved summaries | M1 | P1 | Fixes V-06 | 10 §9 |
| PIP-NEW-077 | Self-improvement suggestions | Notices recurring problems and *recommends* investigations; never auto-deploys | M4 | P2 | — | 10 §10 |

### K. Reliability and platform

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-078 | Critical-function independence | Alarms, stop, timers and the offline path never wait behind slow work | M1 | P1 | — | [12 §2](12-RELIABILITY-AND-OFFLINE.md#2-critical-functions-and-latency-budgets-pip-new-078) |
| PIP-NEW-079 | Health monitoring with stall detection | Heartbeats and functional checks; detects hung jobs, not only crashes | M1 | P1 | — | 12 §4 |
| PIP-NEW-080 | Safe updates and rollback | Every release (not only self-changes) is installable, verifiable and reversible | M1 | P1 | — | 12 §5 |
| PIP-NEW-081 | Local language-model fallback | Small on-device model for short replies when the Claude subscription is unreachable (owner decision) | M2 | P2 | Local | 12 §3 |
| PIP-NEW-082 | Laptop development mode | Run Pip on a laptop with simulated audio and devices | M1 | P3 | Test data only | 12 §5 |
| PIP-NEW-083 | Ziggletron data import | One-time, read-only import of memories (to the inbox), reminders, calendar and TV layouts | M1 | P1 | Read-only on the source | 11 §6 |

### L. Adopted ideas (owner decision D-24)

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-084 | Speakerphone mute detection | The speakerphone's own mute button is the physical hard off (owner decision D-38). Pip detects the muted state from **exact digital silence**, measured in M0: mute sends no button event, and a quiet room is never exactly zero, shows "muted" on the TV and dashboard, and doesn't report it as a fault. No GPIO button or LED | M1a | P1 | Visible, physical privacy | [07 §1](07-SMART-HOME-AND-INTEGRATIONS.md#1-platform-choice-pip-is-the-device-hub-owner-decision-d-18), 12 §5 |
| PIP-NEW-085 | ~~NVMe SSD storage~~ **Withdrawn** (owner decision D-39: no SSD). SD-card care instead: batched writes, logs size-capped, wear monitored | — | — | — | 12 §5 |
| PIP-NEW-086 | Phone notifications via ntfy | Alarm, reminder, message and critical diagnostic pushes to members' phones; access-controlled topics; minimal content by default (from PIP-IDEA-004) | M3 | P2 | Content leaves the house if ntfy.sh is used; self-hosting preferred | [11 §7](11-SECURITY-AND-PERMISSIONS.md#7-integration-boundaries) |
| PIP-NEW-087 | Household messages | "Pip, tell Sam the plumber called": delivered when Sam is next recognised, or to Sam's phone (from PIP-IDEA-005) | M3 | P2 | Private messages follow the private-memory read-out rules | 03 §4.4 |
| PIP-NEW-088 | Daily briefing | An optional routine: weather, events, tasks, reminders (from PIP-IDEA-007) | M3 | P3 | Opt-in per person | [07 §7](07-SMART-HOME-AND-INTEGRATIONS.md#7-routines-pip-new-041) |
| PIP-NEW-089 | Usage meter | Model requests per day by kind, with plan-limit warnings, which matter because the plan is subscription only (from PIP-IDEA-011) | M1b | P1 | — | 12 §3 |
| PIP-NEW-090 | Local music playback | Local music library on Pip's speaker, with ducking (FEAT-088 redesign) | M3 | P3 | — | [07 §9](07-SMART-HOME-AND-INTEGRATIONS.md#9-music-playback-feat-088-redesigned-owner-decision-d-20) |

### M. Understandability and openness (owner decisions D-70, D-71)

| ID | Name | What it does | M | P | Privacy | Spec |
|---|---|---|---|---|---|---|
| PIP-NEW-091 | Docs and diagrams as the main way to understand Pip | An architecture overview, a diagram of how a request flows through Pip, one page per component, and a written reason for each major design choice. Kept up to date: the verification pipeline flags code changes that affect a documented component without touching its page | M1a | P1 | — | Architecture document (to be written) |
| PIP-NEW-092 | Easy module template | Adding a simple feature (a new action, a TV widget, a device type) means copying a documented template, filling in a few clearly marked parts, and adding a capability-registry entry. Each template comes with a worked example and its own test | M1a | P1 | Modules declare permissions (FEAT-079) | Architecture document |
| PIP-NEW-093 | Plain-language configuration | Every setting has a one-line explanation, a safe default and validation, whether it's changed in the Settings page or the file (FEAT-080, PIP-NEW-053) | M1a | P1 | Secrets never in config files | 08 §2 |
| PIP-NEW-095 | Change guide and project map for any contributor | `CLAUDE.md` (read-first rules), `CONTRIBUTING.md` (how to change things) and `docs/PROJECT-MAP.md` (where everything is) let a person or AI agent with no history of the project make a correct change. Pip's own self-change agent is given them in its sandbox (PIP-NEW-072). A commit check fails if the map misses a top-level file or folder | M0 (done) | P1 | Restates the privacy rules | `CONTRIBUTING.md` |
| PIP-NEW-096 | Works from a fresh clone | A clone of the repo with no personal data runs: generic defaults, a first-run setup (API keys, location, room), and wake-word models trained only on synthetic voices. Personal data (keys, settings, memories, voice profiles, recordings, models trained on household voices) lives only in the data folder and is layered on top; it's never needed for Pip to start (D-72) | M1a | P1 | Personal data never in the repo | Architecture document |
| PIP-NEW-094 | Always publishable | The repo stays safe to publish: secret and phone-number check on every commit (done in M0), an open-source licence file before publishing, no personal data in tests or examples | M0 (started) | P2 | Protects household data | 11 §8 |
