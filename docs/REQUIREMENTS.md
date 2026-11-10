# Requirements

Lookup tables for building Pip. For what Pip does in plain language, read [PIP-SPEC.md](PIP-SPEC.md). **Decisions (`D-nn`) in [DECISIONS.md](DECISIONS.md) override anything here and in the archive.**

**The "Detail" column links to the full behaviour description** in the archived proposal ([archive/pip-v2-proposals-2026-11/](archive/pip-v2-proposals-2026-11/README.md)). When building a feature, read its detail section: that's where the exact rules, edge cases and examples are. Check the overridden list below first.

**IDs:** `PIP-NEW-nnn` = new Pip requirement · `FEAT-nnn` = Ziggletron feature · `AC-*` = acceptance criterion. Milestones: M0 (measure) · M1a · M1b · M2 · M3 · M4.

## 0. Parts of the archive that later decisions override

Where the archive says one thing and a decision says another, **the decision wins**. Known overrides:

| Archived section | It says | Now | Because |
|---|---|---|---|
| [03 §1](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) (mode table), §1.1; [04](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) owner table, §6 item 1; [12 §5](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) resource budget; [15](archive/pip-v2-proposals-2026-11/15-PRIORITIZED-ROADMAP.md) M0 load-test row | Room speech in Social mode is transcribed **on the Pi** (Moonshine) | **Groq transcribes room speech** in Social mode, as in Ziggletron; Private mode still sends nothing | D-50, D-51 |
| [03 §2.1–2.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) (budgets, back-off, "Pip, not now" gate); [00](archive/pip-v2-proposals-2026-11/00-PIP-PRODUCT-VISION.md) qualities table; [13](archive/pip-v2-proposals-2026-11/13-USER-WORKFLOWS.md) PW-[02](archive/pip-v2-proposals-2026-11/02-PROPOSED-FEATURE-CATALOG.md) | Joining in limited by per-hour budgets and back-off when ignored | A fast **local question check** gates a Claude "anything to add?" call, at most every ~3 min; **no budget or back-off** | D-52, D-58 |
| [08 §1](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) Conversation row; [11 §4](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) data scoping (feed for members only) | Conversation feed needs a login | **Feed visible without login** (dashboard still reachable only over Tailscale); ~~admin actions still need a login with 2-factor~~ no logins at all (D-116) | D-53, D-35, D-116 |
| [05 §2](archive/pip-v2-proposals-2026-11/05-SPEECH-BENCHMARK-PLAN.md) (expiry), §3 (3 speakers, TV) | Test recordings deleted after 30 days; several speakers; TV background | Owner's recordings **kept on the Pi** and usable for training; owner only; phone/laptop media instead of a TV | D-41, D-46, D-72 |
| [04 Part B](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) (Kokoro vs Piper evaluation) | Voice not chosen | **Piper "lessac"** for now; voice engine swappable | D-73, PIP-NEW-097 |
| [04 §2.1](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) ("Pip" vs "Hey Pip") | Choose "Pip" or "Hey Pip" from a false-activation test | "Pip" alone is too short (M0). **Trained "Hey Pip" detector**, plus "Pippletron" if it tests well | D-01, D-68 |
| [04 §2.2](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) (mic options) | Far-field array suggested | Speakerphone over USB as mic and speaker; echo cancellation verified in M0; no hardware upgrade | D-12, D-49 |
| [14](archive/pip-v2-proposals-2026-11/14-ACCEPTANCE-CRITERIA.md) AC-SP-09 | 8-hour local room transcription criterion | Obsolete | D-51 |
| [10 §5](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) (sandbox "container or OS sandbox") | Sandbox technology open | M0 showed a **systemd user sandbox** works with no installs (secrets, recordings, repo and internet blocked) | M0 result |
| [03 §1.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) (guests gate Social mode) | Guests and unknown voices are never sent to the cloud unless addressed | **Everyone except opted-out people** is sent to Groq in Social mode; guests can opt out by voice (visit-only voice memory) | D-77 **Replaced by D-90** (everyone is transcribed while the mic is on) |
| [11 §2–§5](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) (roles, sign-in and 2-factor, per-account scoping, audit trail); [08 §1](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) where it assumes sign-in | Accounts with Admin/Member/Viewer roles, sign-in with 2-factor, per-account permission checks, an audit trail of who did what | **No logins or accounts.** The dashboard is reachable only on the owner's Tailscale devices (https via `tailscale serve`, or http on the Tailscale address), and a same-site check (FEAT-072) stops other websites from changing Pip. Admin things still need the dashboard rather than voice | D-116 |
| [10 §5](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md#5-isolated-development-pip-new-072) (main remaining risk), [10 §7](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md#7-human-approval-pip-new-074) | The agent runs **outside** the sandbox with the Claude credential, only its tools inside; voice can approve **proposals** | The agent (Claude Code) runs **inside** a systemd sandbox with only the workspace and the Claude login file; its only command, `tools/sandbox-test`, runs tests with **no network and no home folder**, and the builder's checks run the code in the strict M0 sandbox. **Both approvals are on the dashboard**; voice can only ask | D-118 |

Requirement rows below already include their own overrides (struck-through or marked with the decision).

## 1. New requirements (97)

| ID | Name | What it does | Milestone | Priority | Detail |
|---|---|---|---|---|---|
| PIP-NEW-001 | Local address detection | An on-device name spotter decides whether Pip was called, before any audio leaves the house | M1 | P1 | [04 §2](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md#2-proposed-pipeline-behavioural-not-a-library-choice) |
| PIP-NEW-002 | Rolling RAM pre-roll | Keeps about 10 s of audio in memory, so words spoken *before* the name are included | M1 | P1 | [04 §2](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-003 | Listening modes | Private / Room-Aware / Social, visible on the dashboard, TV and by voice | M1 (Social, Private); M2 (Room-Aware refinements) | P1 | [03 §1](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md#1-listening-modes) |
| PIP-NEW-004 | ~~Per-person opt-out and guest gating~~ **Replaced by D-90** (no opt-outs; the mic switch, D-91) | Household adults are included unless they opt out (dashboard or "Pip, leave me out"); everyone is told how. Guests and opted-out speakers → Room-Aware behaviour | M1 | P1 | [03 §1.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-005 | Voice privacy switch | "Pip, private please" / "Pip, start listening", handled locally and works offline | M1 | P1 | [03 §1.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-006 | Local fast-path words | Stop / wait / actually / alarm acknowledgement / snooze detected on device, with no transcription round trip | M1 | P1 | [04 §2](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md), [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-007 | Adaptive end of utterance | Waits longer when speech sounds unfinished; avoids clipping | **Built 2026-11-07 (step 6, D-106)** | P1 | [04 §2.3](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-008 | Selectable STT with health-based fallback | Provider chosen in Settings; 2–3 s timeouts; automatic fallback and recovery | M1 | P1 | [04 §5](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-009 | Custom vocabulary | One list of names, devices and terms, translated to each provider's hint mechanism, with insertion monitoring | M1 | P2 | [04 §4](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-010 | Speech panel | Latency, failures, fallback time, usage and estimated cost per provider; transcript "was that right?" | M1 | P2 | [04 §5](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-011 | Benchmark recording mode and tool | Opt-in, announced, auto-expiring test recordings; repeatable benchmark runs | M0/M1 | P1 | [05](archive/pip-v2-proposals-2026-11/05-SPEECH-BENCHMARK-PLAN.md) |
| PIP-NEW-012 | Media speech detection | Recognises TV/radio speech, so it can't wake Pip, stop alarms or trigger joining | M1 | P1 | [03 §2.1](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-013 | ~~Continuous local room transcription~~ **Replaced by D-51** (Groq transcribes room speech in Social mode) | On-device streaming STT for room context in Social/Room-Aware modes (budget decision) | M1 (pending Pi measurement) | P1 | [04 §6](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-014 | Local voice upgrade and cached phrases | Kokoro vs Piper evaluation; system phrases pre-synthesised | M1 | P2 | [04 Part B](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md#part-b--spoken-output-tts) |
| PIP-NEW-015 | Pronunciation list | Correct pronunciation of names and terms; shared with the vocabulary | M1 | P2 | [04 Part B](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-016 | Pause/resume and ducking | "Wait" holds a reply; Pip lowers its volume when someone else talks | M2 | P2 | [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-017 | Long-answer offer | Offers more detail rather than talking at length; can send it to the TV | M2 | P3 | [04 Part B](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| PIP-NEW-018 | Opportunity-triggered joining (**owner version, D-52:** a fast local question check gates a Claude "anything to add?" call; ≤ every ~3 min; no budget or back-off) | Local gates and triggers before any model call; budgets; back-off when ignored | M1 (basic gates): **Built 2026-11-07 (step 6, D-92)**; M2 (full) | P1 | [03 §2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-019 | Offer-before-answer | In Room-Aware mode, offers help without sending anything ("I can look that up") | M2 | P2 | [03 §2.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-020 | Person-bound follow-up | Follow-up window applies to the person Pip was talking to | M2 | P2 | [03 §2.3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-021 | Correction handling | "Actually…" amends the previous request or action instead of duplicating it | M2 | P2 | [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-022 | Clarification policy | One short question when a request is ambiguous and a wrong guess matters | M1 | P1 | [03 §5](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md), [07 §3.2](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-023 | State and mode awareness | Pip can say its mode, who has opted in, what it's doing and why | M1 | P2 | [03 §5](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-024 | Guided voice enrolment | Read-aloud enrolment on the dashboard or phone | M2 | P2 | [03 §4.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-025 | Guest voices and expiry | Session-only guest labels; persistence only with consent; profile expiry | M1 | P1 | [03 §4.3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-026 | Persistent speaker corrections | Corrections survive restarts; 24 h correction window | M2 | P3 | [03 §4.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-027 | Identity-to-authorisation tiers | Voice confidence maps to allowed operations; admin power needs ~~a signed-in account~~ the dashboard, not voice (D-116: no accounts) | M1 | P1 | 03 §4.4, [11](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-028 | Categorised memories with owners | 8 categories, owner, visibility, confirmed/inferred | M1 | P1 | [06 §2](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md#2-memory-categories) |
| PIP-NEW-029 | Memory inbox and approval | Suggestions and sensitive items wait for approval; overheard content never auto-saved | **Built 2026-11-05 (step 5, D-88); changed by D-119 (2026-11-10): everything asked is saved straight away, the inbox is only for imports.** Overheard content is still never saved: only what someone asks Pip to remember | P1 | [06 §3](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-030 | Memory Center | Browse, search, add, edit, explain, archive | M1 (basic): **Built 2026-11-05 (step 5)**; M2 (full) | P1 | [06 §5](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-031 | No-loss retention | No count cap; duplicate and contradiction checks; stale review; archive | M1 | P1 | [06 §3.2–3.3](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-032 | Sensitive-memory rules | Subject approval for sensitive kinds; refuses credentials | **Built 2026-11-05 (step 5, D-88)** | P1 | [06 §4](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-033 | Forget a person | One action removes a person's voice, memories and related data | M2 | P2 | [06 §4](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-034 | Export, import, delete everything | Readable exports; reviewed imports; full wipe | M1 (import for migration), M2 (rest) | P2 | [06 §4](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-035 | "Why do you remember that?" | Pip explains a memory's source and offers to forget it | M2 | P3 | [06 §5](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-036 | Device hub (MQTT / ESPHome / local Tuya) | Local MQTT broker with self-describing discovery; ESPHome devices; Eightree outlets over the local Tuya protocol; allow-list of approved devices. No Home Assistant (owner decision) | M3 | P1 | [07 §1](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md#1-platform-choice-pip-is-the-device-hub-owner-decision-d-18) |
| PIP-NEW-037 | Device registry | Aliases, rooms, owners, risk class; feeds vocabulary | M3 | P1 | [07 §2](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-038 | Contextual device commands | "My desk light", "it", "a little brighter", "everything except…" | M3 | P1 | [07 §3](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-039 | Verified action lifecycle | Understood → authorised → attempted → succeeded/failed → verified; applies to **all** actions | M1 | P1 | [12 §1](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md#1-action-lifecycle-no-false-confirmations-pip-new-039) |
| PIP-NEW-040 | Device risk classes | A/B/C, with confirmation rules | M3 | P1 | [07 §4](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-041 | Routines | Named, editable, previewable; triggers need approval | M3 | P2 | [07 §7](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-042 | Environmental events and sensor context | Configured sensor/event topics; announcement rules; one weather source | M3 | P2 | [07 §6](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-043 | Custom hardware via ESPHome/MQTT | ESP32 devices usable with no new Pip integration | M3 | P2 | [07 §1.2](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| PIP-NEW-044 | Offline command path | Timers, cancel, snooze, volume, stop, pause listening, status, and (M3) basic devices, all without cloud reasoning. **D-94:** these fixed phrasings run only offline (and if Claude fails); online, Claude understands any wording | M1 | P1 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-045 | Voice snooze | "Snooze ten minutes" | **Built 2026-11-04 (step 4)** | P2 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-046 | Dashboard scheduling parity | Create reminders, set repeats on events, richer recurrence (weekdays, monthly) | M1 | P2 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md#3-calendar-and-tasks) |
| PIP-NEW-047 | Shopping list | Shared list by voice and dashboard; live on the TV | M3 | P2 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-048 | To-do lists and shared tasks | Per-person and household tasks, with optional due dates and assignee | **Built 2026-11-07 (D-100, to-dos module with Google Tasks)** | P2 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-049 | Google Calendar synchronisation | Two-way sync with Google Calendar (D-19); **D-97/D-98: any household member can connect theirs**; tokens only in secrets.toml | **Built 2026-11-07 (calendar + google modules)** | P2 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-050 | Natural-language editing | "Move the dentist to Thursday", "make the pasta timer 15 minutes" | M2 | P2 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-051 | Authorised alarm acknowledgement | Normal alarms stop only for a non-media voice; earned-alarm attempts only from household voices | M1 | P1 | [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-052 | Restructured dashboard | Home / Conversation / Memory / Home & Devices / Calendar & Tasks / Appearance / Settings / Developer | M1 (core sections) | P1 | [08 §1](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md#1-navigation) |
| PIP-NEW-053 | Settings UI | Every user setting editable without file edits; validated; most apply without restart | M1: **Built 2026-11-09 (step 8, D-117)**: a Settings page; changes restart only the part that needs it, when Pip is quiet | P1 | [08 §2](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-054 | ~~Accounts and roles~~ **Dropped (D-116)** | ~~Sign-in; Admin, Member, Viewer roles; per-action permission checks~~ | — | — | [11 §2](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md#2-roles-and-permissions) |
| PIP-NEW-055 | Secure remote access | Access away from home through a private tunnel (Tailscale-style) with HTTPS; no public port forwarding | **Built (D-116): https over `tailscale serve`** (`deploy/install-https.sh`), plus the same-site check | P1 | [11 §3](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-056 | Per-client data scoping | The TV and other clients receive only what they display | M1: **TV part built 2026-11-08 (step 7, D-110: the monitor's own narrow feed)**; other clients: not needed without accounts (D-116) | P1 | [11 §4](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-057 | ~~Audit trail~~ **Dropped (D-116)** | ~~Who did what, when, from where; no content~~ (no accounts to record) | — | — | [11 §5](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-058 | Credential vault and migration | Credentials stored separately and encrypted; imported from Ziggletron without exposure | M1 | P1 | [11 §6](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-059 | Privacy-safe structured logs | Structured, redacted events; retention; time-limited content debugging | M1 | P1 | [11 §5](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md), [06 §6](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| PIP-NEW-060 | Live data widgets | Widgets show live data (adding milk updates the TV immediately) | M1 (existing boxes made live): **Built 2026-11-08 (step 7, D-110, D-111)**; M3 (new widgets) | P1 | [08 §5](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md#5-tv-widgets-pip-new-060) |
| PIP-NEW-061 | Reliable layout editing | Templates, validation, preview, undo, drag-and-drop | M1 (validation and undo): **Built 2026-11-08 (step 7, D-111: fixes offered, undo)**; M3 (drag-and-drop) | P1 | [08 §6](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-062 | Richer face | More expressions, mode indicator, better listening and speaking animation, appearance options | M2 | P2 | [08 §4](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-063 | Independent unprompted-content switches | Separate switches for unprompted photos and drawings | M1 | P2 | [08 §7](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-064 | Capability registry | A maintained, machine-readable description of every feature, setting and limit, versioned with the code | M1 | P1 | [09 §2](archive/pip-v2-proposals-2026-11/09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md#2-knowledge-sources-pip-new-064) |
| PIP-NEW-065 | Self-knowledge Q&A | Answers "how does your memory work?" from the registry, docs and read-only code; reports doc/code discrepancies | M2 | P1 | [09 §3](archive/pip-v2-proposals-2026-11/09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md) |
| PIP-NEW-066 | Decision trace and "why" explanations | Records observable decisions, so Pip can explain "why didn't you answer?" | M1 (trace): **Built 2026-11-04 (step 1)**; M2 (spoken explanations) | P1 | [09 §4](archive/pip-v2-proposals-2026-11/09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md) |
| PIP-NEW-067 | Read-only self-inspection tools | Sanitised status, config metadata, docs and source search, redacted events | M2 | P1 | [09 §5](archive/pip-v2-proposals-2026-11/09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md) |
| PIP-NEW-068 | Proactive self-diagnostics | Proportionate notices about its own problems | M1 (dashboard), M2 (spoken) | P2 | [09 §6](archive/pip-v2-proposals-2026-11/09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md) |
| PIP-NEW-069 | Request classification | Tells apart a question, a bug report, an idea, a code change and a config change | **Built 2026-11-09 (M1b, D-118):** Claude's `request_change` action only for a clear request to change how Pip works, never a question; only voices he's sure of, not guests; or the Changes page** | P1 | [10 §2](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md#2-request-intake-and-classification-pip-new-069) |
| PIP-NEW-070 | Clarification dialogue | Asks only what is needed to define the change | **Built 2026-11-09 (M1b, D-118):** the plan's questions wait on the Changes page (not by voice), up to 3 rounds** | P1 | [10 §3](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-071 | Change proposal | A readable proposal (what, why, affected features, risks, tests, data impact) approved before any coding | **Built 2026-11-09 (M1b, D-118):** summary, why, files, risk (cosmetic / behaviour / data / security), tests, how to undo; approval 1 on the dashboard** | P1 | [10 §4](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-072 | Sandboxed development | Isolated workspace and container, restricted files and network, no production secrets, limits, cleanup | **Built 2026-11-09 (M1b, D-118):** a plain copy with no `.git`; Claude Code in a systemd sandbox (home hidden except the workspace and the Claude login, 2 GB, 30 min); its only command is `tools/sandbox-test`, which runs tests with no network and no home folder; workspaces deleted on every outcome** | P1 | [10 §5](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-073 | Verification pipeline | Build, unit, integration, regression, security, privacy, architecture and hardware checks | **Built 2026-11-09 (M1b, D-118):** forbidden files, secret scan, the full suite and an import check in the strict M0 sandbox (no network); no hardware checks** | P1 | [10 §6](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-074 | Authenticated review and approval | Diff, results, risks and rollback shown; approval on the dashboard (no login, D-116) | **Built 2026-11-09 (M1b, D-118):** approval 2 on the Changes page; security-risk changes also need "I've read the diff"; voice never approves** | P1 | [10 §7](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-075 | Safe deployment and rollback | Keeps the previous version; post-deploy health checks; automatic rollback | **Built 2026-11-09 (M1b, D-118):** installed through `release.py`: health checked up to 2 min plus 30 s; switches back by itself if unhealthy; Undo button reverts a live change** | P1 | [10 §8](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-076 | Sanitised change history | Explains every change; commit messages built from approved summaries | **Built 2026-11-09 (M1b, D-118):** commit message from the approved summary, never the request; requests blanked after 90 days; Pip never pushes** | P1 | [10 §9](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-077 | Self-improvement suggestions | Notices recurring problems and *recommends* investigations; never auto-deploys | M4 | P2 | [10 §10](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| PIP-NEW-078 | Critical-function independence | Alarms, stop, timers and the offline path never wait behind slow work | M1 | P1 | [12 §2](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md#2-critical-functions-and-latency-budgets-pip-new-078) |
| PIP-NEW-079 | Health monitoring with stall detection | Heartbeats and functional checks; detects hung jobs, not only crashes | **Built 2026-11-04 (step 1: heartbeats, stuck programs restarted)** | P1 | [12 §4](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-080 | Safe updates and rollback | Every release (not only self-changes) is installable, verifiable and reversible | **Built 2026-11-09 (M1b, D-118):** `deploy/install.sh` installs the committed HEAD into `~/pip-app/releases/`, with health check and automatic rollback; newest 3 kept** | P1 | [12 §5](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-081 | Local language-model fallback | Small on-device model for short replies when the Claude subscription is unreachable (owner decision) | M2 | P2 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-082 | Laptop development mode | Run Pip on a laptop with simulated audio and devices | M1 | P3 | [12 §5](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-083 | Ziggletron data import | One-time, read-only import of memories (to the inbox), reminders, calendar and TV layouts | M1: **Built 2026-11-09 (D-117): memories (to the inbox) and the personality only, by the owner's choice** | P1 | [11 §6](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-084 | Speakerphone mute detection | The speakerphone's own mute button is the physical hard off (owner decision D-38). Pip treats long exact digital silence as **"muted or very quiet"**: mute sends no button event, and a second M0 test showed the speakerphone's noise suppression can also output exact zeros in a silent room, so the two can't always be told apart. Shown as a status, never as an error, shows "muted" on the TV and dashboard, and doesn't report it as a fault. No GPIO button or LED | **Built 2026-11-04 (step 2, D-38)** | P1 | [07 §1](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md#1-platform-choice-pip-is-the-device-hub-owner-decision-d-18), 12 §5 |
| PIP-NEW-085 | ~~NVMe SSD storage~~ **Withdrawn** (owner decision D-39: no SSD). SD-card care instead: batched writes, logs size-capped, wear monitored | — | — | — |  |
| PIP-NEW-086 | Phone notifications via ntfy | Alarm, reminder, message and critical diagnostic pushes to members' phones; access-controlled topics; minimal content by default (from PIP-IDEA-004) | M3 | P2 | [11 §7](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md#7-integration-boundaries) |
| PIP-NEW-087 | Household messages | "Pip, tell Sam the plumber called": delivered when Sam is next recognised, or to Sam's phone (from PIP-IDEA-005) | M3 | P2 | [03 §4.4](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| PIP-NEW-088 | Daily briefing | An optional routine: weather, events, tasks, reminders (from PIP-IDEA-007) | M3 | P3 | [07 §7](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md#7-routines-pip-new-041) |
| PIP-NEW-089 | Usage meter | Model requests per day by kind, with plan-limit warnings, which matter because the plan is subscription only (from PIP-IDEA-011) | **Built 2026-11-09 (M1b, D-118):** requests, errors and cost per day by kind; when Claude says the limit is reached, joining in and self-changes pause. **No 80% warning:** the Pro plan doesn't report what's left | P1 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| PIP-NEW-090 | Local music playback | Local music library on Pip's speaker, with ducking (FEAT-088 redesign) | M3 | P3 | [07 §9](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md#9-music-playback-feat-088-redesigned-owner-decision-d-20) |
| PIP-NEW-091 | Docs and diagrams as the main way to understand Pip | An architecture overview, a diagram of how a request flows through Pip, one page per component, and a written reason for each major design choice. Kept up to date: the verification pipeline flags code changes that affect a documented component without touching its page | M1a | P1 | [ARCHITECTURE.md](ARCHITECTURE.md) |
| PIP-NEW-092 | Easy module template | Adding a simple feature (a new action, a TV widget, a device type) means copying a documented template, filling in a few clearly marked parts, and adding a capability-registry entry. Each template comes with a worked example and its own test | M1a | P1 | Architecture document |
| PIP-NEW-093 | Plain-language configuration | Every setting has a one-line explanation, a safe default and validation, whether it's changed in the Settings page or the file (FEAT-080, PIP-NEW-053) | M1a: **Built 2026-11-09 (D-117)** | P1 | [08 §2](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| PIP-NEW-094 | Always publishable | The repo stays safe to publish: secret and phone-number check on every commit (done in M0), an open-source licence file before publishing, no personal data in tests or examples | M0 (started) | P2 | [11 §8](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| PIP-NEW-095 | Change guide and project map for any contributor | `CLAUDE.md` (read-first rules), `CONTRIBUTING.md` (how to change things) and `docs/PROJECT-MAP.md` (where everything is) let a person or AI agent with no history of the project make a correct change. Pip's own self-change agent is given them in its sandbox (PIP-NEW-072). A commit check fails if the map misses a top-level file or folder | M0 (done) | P1 | `CONTRIBUTING.md` |
| PIP-NEW-096 | Works from a fresh clone | A clone of the repo with no personal data runs: generic defaults, a first-run setup (API keys, location, room), and wake-word models trained only on synthetic voices. Personal data (keys, settings, memories, voice profiles, recordings, models trained on household voices) lives only in the data folder and is layered on top; it's never needed for Pip to start (D-72) | M1a | P1 | Architecture document |
| PIP-NEW-097 | Swappable voice | Every text-to-speech engine (Piper, Kokoro, or a future local or cloud voice) sits behind one voice interface. Engine, voice and speed are chosen in Settings and can be changed at any time; adding an engine is one module from the template (PIP-NEW-092) with no changes elsewhere. On a change, Pip re-learns its own voice for echo rejection and re-makes its cached phrases. The same pattern applies to speech-to-text (PIP-NEW-008) and the AI model (D-05) | M1a (**voice and speed chosen in Settings, 2026-11-09; only the Piper engine so far**) | P1 | [04 Part B](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |

## 2. Ziggletron features and what happens to them (89)

KEEP · IMPROVE · REDESIGN · REPLACE · MERGE · REVIEW. What each FEAT did originally: [archive/ziggletron-discovery/COMPLETE-FEATURE-CATALOG.md](archive/ziggletron-discovery/COMPLETE-FEATURE-CATALOG.md).

| FEAT | Name | Decision | Pip behaviour | Pip requirements | Detail |
|---|---|---|---|---|---|
| 001 | Continuous listening | REDESIGN (**owner: keep cloud transcription of the room in Social mode, D-51**) | Speakerphone AEC → RAM buffer → local VAD and name spotter; nothing leaves before the address decision (PIP-NEW-001/002) | PIP-NEW-001, 002, 013 | [04 §2](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| 002 | STT with fallback | REDESIGN | Benchmark-chosen provider for addressed speech; local room transcription; 2–3 s timeouts; non-blocking (PIP-NEW-008/013) | PIP-NEW-008, 010, 013 | [04 §3](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md), §5 |
| 003 | Called by name | REDESIGN | Local spotter for "Pip"/"Hey Pip"; clause rule kept | PIP-NEW-001 | [04 §2.1](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| 004 | Name merely mentioned | IMPROVE | Classified locally; model consulted only in Social/Room-Aware with consent | PIP-NEW-018 | [03 §2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 005 | Follow-up window | REDESIGN | Bound to the recognised person; extends while they speak (PIP-NEW-020) | PIP-NEW-020 | [03 §2.3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 006 | Chime | KEEP | Unchanged; configurable | — | — |
| 007 | Echo rejection | IMPROVE | Speakerphone AEC first; word, voice and loudness checks re-tuned as a second line | PIP-NEW-014 (own-voice) | [04 §2.2](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| 008 | Transcription hygiene | IMPROVE | Keep the phantom filter (provider-specific lists); name-hint re-check removed (local spotter) | PIP-NEW-009 | [04 §4](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| 009 | Ambient sounds | IMPROVE | Also detects media speech to gate joining and alarms (PIP-NEW-012) | PIP-NEW-012 | [03 §2.1](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 010 | Speaker labels | IMPROVE | Same three levels; re-tuned; "Several people" label | PIP-NEW-027 | [03 §4](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 011 | Enrol by introduction | REDESIGN | Kept, plus guided enrolment (PIP-NEW-024); guests session-only unless consented (PIP-NEW-025) | PIP-NEW-024, 025 | [03 §4.2–4.3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 012 | Correct a line's speaker | IMPROVE | Persists across restarts; members correct their own lines (PIP-NEW-026) | PIP-NEW-026 | [03 §4.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 013 | Manage voices | IMPROVE | Shows consent and expiry; review after 180 days | PIP-NEW-025 | [03 §4.3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 014 | Admin voice protection | REPLACE | Admin authority comes from the dashboard, not voice (PIP-NEW-027; no accounts, D-116) | PIP-NEW-027 | [11 §2](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 015 | Streamed answers | IMPROVE | One model session per request (P-01); outcome-first wording | PIP-NEW-039 | [12 §1–2](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 016 | TTS | IMPROVE | Local only; Kokoro vs Piper; cached phrases; pronunciation list (PIP-NEW-014/015) | PIP-NEW-014, 015 | [04 Part B](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| 017 | Barge-in and stop phrases | REDESIGN | Local keywords; wait/resume; "actually"; ducking (PIP-NEW-006/016/021) | PIP-NEW-006, 016, 021 | [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 018 | Cut-off awareness | KEEP | Unchanged | — | — |
| 019 | Joining conversations | REDESIGN (**owner: Ziggletron's way + local question check, D-52**) | Modes; local gates and triggers; budgets; back-off; consent (PIP-NEW-003/004/018) | PIP-NEW-003, 004, 018, 019 | [03 §1–2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 020 | "Anything to add?" button | KEEP | Respects mode and consent | PIP-NEW-004 | [03 §1.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 021 | Welcome-back greeting | IMPROVE | Recognised household voices only; at most once per 4 h; can be switched off | — | [03 §2.4](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 022 | Editable personality | IMPROVE | Dashboard editor, preview, history, sliders (**built 2026-11-09, D-117: editor, "try it" preview, last 20 versions; no sliders**) | PIP-NEW-053 | [03 §5](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 023 | Situation context | IMPROVE | Only memories visible to those present; sensor context; trimmed by mode | PIP-NEW-028 | [06 §4](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| 024 | Web search | KEEP | Results treated as untrusted data | — | [11 §7](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 025 | Actions + outcomes | REDESIGN | Verified action lifecycle for every action (PIP-NEW-039); no-match = failure | PIP-NEW-039 | [12 §1](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 026 | Failure speech | IMPROVE | Offline path and local LLM fallback (PIP-NEW-044/081); says what still works | PIP-NEW-044, 081 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 027 | Typing | IMPROVE | ~~Typed lines carry the account identity~~ (no accounts, D-116); P-04 fixed; "auto" addressing option | — | [11 §2](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 028 | Long-term memories | REDESIGN | Categories, owner, visibility, confirmed/inferred; never from overheard speech; no cap (PIP-NEW-028/029/031) | PIP-NEW-028, 029, 031 | [06 §2–3](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| 029 | Forget by voice | IMPROVE | Confirms the exact item; reports no-match; asks when uncertain | PIP-NEW-039 | [06 §3](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| 030 | Selective recall | IMPROVE | Plus visibility rules for the people present | PIP-NEW-028 | [06 §4](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| 031 | Background sorting | MERGE | Into save-time categorisation and the inbox | PIP-NEW-029 | [06 §3.1](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| 032 | Dashboard memory management | REPLACE | Memory Center (PIP-NEW-030) | PIP-NEW-030, 034 | [06 §5](archive/pip-v2-proposals-2026-11/06-MEMORY-AND-DATA-POLICY.md) |
| 033 | Timers | IMPROVE | Any wording via Claude online (D-94), local commands offline (PIP-NEW-044); confirmed only after creation | PIP-NEW-044 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 034 | Reminders | IMPROVE | Dashboard creation; weekday and monthly repeats (PIP-NEW-046) | PIP-NEW-046 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 035 | Targeted reminders | IMPROVE | P-06 wording fixed; uses recognised presence (and MQTT/GPIO presence sensors in M3) | — | — |
| 036 | Calendar | IMPROVE | Repeat on the dashboard (P-05); sync evaluated (PIP-NEW-049) | PIP-NEW-046, 049 | [08 §3](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 037 | Ringing alarms | IMPROVE | Acknowledged only by a non-media voice, typed input or the dashboard (PIP-NEW-051); voice snooze | PIP-NEW-051 | [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 038 | Earned alarms | IMPROVE | Only household voices' attempts are judged | PIP-NEW-051 | [03 §3](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 039 | Shut off / snooze | KEEP | Plus voice snooze (PIP-NEW-045) | PIP-NEW-045 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 040 | Ringtones | KEEP | Unchanged | — | — |
| 041 | Alarms survive restarts | KEEP | Unchanged; covered by acceptance tests | — | — |
| 042 | Cancel / remove | IMPROVE | Reports no-match; asks when the match is ambiguous | PIP-NEW-039 | [12 §1](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 043 | SMS | IMPROVE | Numbers in the vault (S-08); household-affecting confirmation tier; message text not logged | PIP-NEW-058 | [11 §6](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 044 | Volume | IMPROVE | Real outcome reported (P-12); offline path | PIP-NEW-039, 044 | [12](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 045 | Stop listening | REDESIGN | Only the local name spotter runs while asleep, so "Pip, start listening" works; nothing else is processed; a separate hard-off (mic switch or mute button) releases the mic entirely; media voices can't trigger it | PIP-NEW-005 | [03 §1.2](archive/pip-v2-proposals-2026-11/03-CONVERSATION-AND-PERSONALITY.md) |
| 046 | Mic switch | IMPROVE | ~~Requires a signed-in member~~ (no logins, D-116: the dashboard switch works on the owner's devices); **persists across restarts** (D-22); speakerphone mute detection (PIP-NEW-084, D-38) | PIP-NEW-084 | [11](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 047 | Speaker test | KEEP | Unchanged | — | — |
| 048 | Dozing | KEEP | Unchanged | — | — |
| 049 | Away (PIR) | IMPROVE | GPIO PIR kept (owner: devices connect directly to the Pi); MQTT presence sensors also accepted (M3); verified with a bench test | PIP-NEW-036 | [07 §1.2](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| 050 | Quiet hours | KEEP | Safety events still announce | PIP-NEW-042 | [07 §6](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| 051 | Muted-mic warning | MERGE | Into health monitoring (PIP-NEW-079) | PIP-NEW-079 | [12 §4](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 052 | Animated face | IMPROVE | More expressions, mode indicator (PIP-NEW-062) | PIP-NEW-062 | [08 §4](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 053 | Weather clothing | KEEP | Same source as spoken weather (P-08) | — | [08 §5](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 054 | "Six seven" | KEEP | Unchanged; part of a general "reactions" list | — | — |
| 055 | TV dashboards | IMPROVE | Templates; persisted; live widgets | PIP-NEW-060 | [08 §5](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 056 | Custom dashboards by voice | REDESIGN | Validate, then preview, then apply; undo (PIP-NEW-061) | PIP-NEW-061 | [08 §6](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 057 | Grid arranging | REDESIGN | Validation with automatic fit suggestions; drag-and-drop on the dashboard | PIP-NEW-061 | [08 §6](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 058 | Box types | IMPROVE | Live data; notes become live lists (U-08) | PIP-NEW-060 | [08 §5](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 059 | Drawings on request | KEEP | Requested only; daily cap; clear progress | — | [08 §7](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 060 | Pictures of its own | IMPROVE | Kept, **off by default**, own switch (owner decision D-21) | PIP-NEW-063 | [08 §7](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 061 | Real photos | IMPROVE | Separate switch for unprompted photos (U-05) | PIP-NEW-063 | [08 §7](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 062 | TV privacy | IMPROVE | The client receives only displayed data (PIP-NEW-056) | PIP-NEW-056 | [11 §4](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 063 | TV resilience | KEEP | Unchanged | — | — |
| 064 | Weather | IMPROVE | Single source shared with the model | PIP-NEW-042 | [07 §6](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| 065 | Vitals | MERGE | Into health monitoring; the TV widget stays | PIP-NEW-079 | [12 §4](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 066 | Status and controls | REDESIGN | Home section (PIP-NEW-052); no sign-in (D-116) | PIP-NEW-052 | [08 §1](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 067 | Conversation feed | KEEP (**owner: no login, D-53**) | Members only; RAM only; respects consent (unconsented speakers' lines not shown) | PIP-NEW-056 | [11 §4](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 068 | Prompts inspector | REDESIGN | Developer section, admin only; memories redacted unless the viewer may see them | PIP-NEW-056 | [11 §4](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 069 | Logs viewer | REDESIGN | Structured, redacted logs (PIP-NEW-059) | PIP-NEW-059 | [11 §5](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 070 | Latency and quota | IMPROVE | Into the Speech panel and Developer section | PIP-NEW-010 | [04 §5](archive/pip-v2-proposals-2026-11/04-SPEECH-RECOGNITION-RESEARCH.md) |
| 071 | Admin password | REPLACE | ~~Accounts and roles (PIP-NEW-054)~~ **Dropped (D-116):** no password or login; the dashboard is reachable only on the owner's Tailscale devices | — | [11 §3](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 072 | Access protection | IMPROVE | **Kept as the same-site check** (`web/security.py`, D-116): Pip answers only to its own names, and changes must be JSON from Pip's own pages; HTTPS via `tailscale serve`; ~~add authentication~~ (no logins, D-116) | PIP-NEW-055 | [11 §3](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 073 | Propose and approve by voice | REDESIGN | Voice initiates; classification and proposal (PIP-NEW-069–071); approval on the dashboard (PIP-NEW-074) (**built 2026-11-09, D-118: both approvals on the dashboard; voice never approves**) | PIP-NEW-069, 070, 071, 074 | [10 §2–4](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md), §7 |
| 074 | Isolated copy, watchable terminal | REPLACE | Sandboxed development (PIP-NEW-072); watchable terminal kept (**built 2026-11-09, D-118: the agent's steps show live on the Changes page, with a Stop button**) | PIP-NEW-072 | [10 §5](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| 075 | Test gate, merge, restart | REPLACE | Verification pipeline + safe deploy + rollback (PIP-NEW-073/075) (**built 2026-11-09, D-118: main is fast-forwarded only if `~/pip` is clean; otherwise the branch waits to be merged**) | PIP-NEW-073, 075 | [10 §6](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md), §8 |
| 076 | History and admin controls | IMPROVE | Sanitised history (PIP-NEW-076); explainable (**built 2026-11-09, D-118: the Changes page's history, with plan, diff, test report and log; Undo**) | PIP-NEW-076 | [10 §9](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| 077 | Code console on live code | REDESIGN | Console opens on a sandboxed workspace, not live code; same review path as voice changes (**2026-11-09: no console; the Changes page's request box takes the same path**) | PIP-NEW-072 | [10 §11](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| 078 | Restart | IMPROVE | Waits for quiet by default; "restart now" asks for confirmation while an alarm rings (**built 2026-11-09, D-118: installs wait up to 2 min for a quiet moment; `--now` skips it**) | PIP-NEW-075 | [10 §8](archive/pip-v2-proposals-2026-11/10-SAFE-SELF-MODIFICATION.md) |
| 079 | Plugins | REDESIGN | Plugins declare the permissions they need (actions, events, data, network); admin grants them; plugin commands are permission-checked | — (plugin permissions) | [11 §7](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 080 | Validated configuration | IMPROVE | Same validation, behind the Settings UI | PIP-NEW-053 | [08 §2](archive/pip-v2-proposals-2026-11/08-DASHBOARD-AND-TV-REDESIGN.md) |
| 081 | Secrets | REPLACE | Credential vault (PIP-NEW-058) | PIP-NEW-058 | [11 §6](archive/pip-v2-proposals-2026-11/11-SECURITY-AND-PERMISSIONS.md) |
| 082 | Startup sequence | KEEP | Plus readiness checks | PIP-NEW-079 | [12 §4](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 083 | Self-healing jobs | IMPROVE | Plus stall detection (PIP-NEW-079) | PIP-NEW-079 | [12 §4](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 084 | Safe storage | KEEP | Plus export and backup | — | [12 §5](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 085 | Model access choice | IMPROVE | Owner decision: Claude subscription via Claude Code, one session per request, plus a local fallback (PIP-NEW-081) | PIP-NEW-081 | [12 §3](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 086 | Install, boot, kiosk | IMPROVE | Plus versioned installs with rollback (PIP-NEW-080) (**built 2026-11-09, D-118**) | PIP-NEW-080 | [12 §5](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 087 | Mic error recovery | IMPROVE | Plus a functional check (audio actually flowing) | PIP-NEW-079 | [12 §4](archive/pip-v2-proposals-2026-11/12-RELIABILITY-AND-OFFLINE.md) |
| 088 | Music (branch) | REDESIGN | Local music files on Pip's own speaker: play/pause/skip/volume, ducking while Pip listens or speaks; M3 ([07 §9](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md#9-music-playback-feat-088-redesigned-owner-decision-d-20)) | PIP-NEW-090 | [07 §9](archive/pip-v2-proposals-2026-11/07-SMART-HOME-AND-INTEGRATIONS.md) |
| 089 | Explain own code (branch) | REPLACE | PIP-NEW-064–067 | PIP-NEW-064–067 | [09](archive/pip-v2-proposals-2026-11/09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md) |

## 3. Acceptance criteria (72)

Targets marked † are starting values (confirmed in M0, D-25).

| ID | Criterion | Covers |
|---|---|---|
| AC-SP-01 | In Private mode, a network capture over a 1-hour session with unaddressed conversation shows **no audio or transcript leaving the device** | PIP-NEW-001, 003 |
| AC-SP-02 | Utterances with the name in the middle or at the end are transcribed in full (no missing words before the name) in ≥98% of benchmark cases† | PIP-NEW-002 |
| AC-SP-03 | False activations ≤1 per 8 hours in the room soak test with the TV on† | FEAT-003 |
| AC-SP-04 | Wake-name recall ≥95% at 1–4 m in the benchmark set† | FEAT-003 |
| AC-SP-05 | Truncated utterances ≤2% in the benchmark "pauses" category† | PIP-NEW-007 |
| AC-SP-06 | With the primary STT unreachable, the next utterance is transcribed by the fallback within the timeout + 1 s, and no utterance is lost | PIP-NEW-008 |
| AC-SP-07 | Transcription in progress does not delay a typed message, alarm or stop command by more than 100 ms | PIP-NEW-078 |
| AC-SP-08 | Vocabulary insertion rate ≤0.5% of utterances† | PIP-NEW-009 |
| AC-SP-09 | ~~Local room transcription runs for 8 hours alongside all other services within the CPU and RAM budget in 12 §5~~ **Obsolete: room speech is transcribed by Groq (D-51)** | PIP-NEW-013 |
| AC-TTS-01 | First audio ≤0.4 s P90 after text is ready, on the Pi, with listening running | PIP-NEW-014 |
| AC-TTS-02 | ≥28 of 30 household and device names pronounced acceptably (household judgement) | PIP-NEW-015 |
| AC-CV-01 | "Stop" halts audio within 100 ms P95, measured on a loopback recording, online and offline | PIP-NEW-006 |
| AC-CV-02 | "Wait" then "go on" within 30 s resumes at the next unspoken sentence | PIP-NEW-016 |
| AC-CV-03 | A correction ("actually fifteen") changes the existing timer; no second timer exists | PIP-NEW-021 |
| AC-CV-04 | ~~In Social mode, with an unknown voice heard in the last 15 minutes, no unaddressed room text is sent to the model (verified from request logs)~~ **Replaced by D-90** | PIP-NEW-004 |
| AC-CV-05 | No unprompted contribution starts while anyone is speaking (100 scripted trials) | PIP-NEW-018 |
| AC-CV-06 | Unprompted contributions never exceed the per-hour budget for the configured level over a 1-week trial | PIP-NEW-018 |
| AC-CV-07 | Media-only speech (a TV playing dialogue for 30 minutes) causes no join, greeting, alarm acknowledgement or "stop listening" | PIP-NEW-012 |
| AC-CV-08 | Guest voice profiles are absent from storage after restart unless consent was recorded | PIP-NEW-025 |
| AC-CV-09 | No administrative action can be performed by voice alone (test every admin action); "admin" means the dashboard, not an account (D-116) | PIP-NEW-027 |
| AC-CV-10 | ~~After an adult opts out (dashboard or "Pip, leave me out"), none of their unaddressed lines reach the model provider (request-log check)~~ **Replaced by D-90** | PIP-NEW-004 |
| AC-CV-11 | ~~Every household adult is shown the opt-out notice at enrolment~~ **Changed by D-90:** shown how to switch listening off (dashboard switch, two claps) | PIP-NEW-004 |
| AC-CV-12 | When the speakerphone is muted, Pip shows "muted" on the TV and dashboard within 2 s and raises no fault; unmuting resumes listening | PIP-NEW-084 |
| AC-MEM-01 | Unaddressed speech never creates an active memory (scripted overheard-facts test) | PIP-NEW-029 |
| AC-MEM-02 | Saving 1,000 memories deletes none | PIP-NEW-031 |
| AC-MEM-03 | Text that looks like a credential or card number is refused as a memory | PIP-NEW-032 |
| AC-MEM-04 | A member cannot read another member's private memory via dashboard, API or voice | PIP-NEW-028 |
| AC-MEM-05 | After restart: no conversation transcript, prompts or raw audio exist on disk (file-system scan for test phrases) | 06 §1 |
| AC-MEM-06 | "Forget Sam" removes every listed item; a search for Sam's data finds nothing except audit entries | PIP-NEW-033 |
| AC-MEM-07 | Export, then import into a clean install, reproduces the memories via the inbox | PIP-NEW-034 |
| AC-SH-01 | A command to an offline device yields a spoken failure, never "done" | PIP-NEW-039 |
| AC-SH-02 | An ambiguous request with two matching devices produces a question, not an action | PIP-NEW-038 |
| AC-SH-03 | Class C actions cannot be executed by voice without second-device confirmation | PIP-NEW-040 |
| AC-SH-04 | Discovered devices not on the allow-list cannot be read or controlled | PIP-NEW-036 |
| AC-SH-05 | A new ESPHome device with MQTT discovery appears in the Devices inbox and, once allowed, is usable with no Pip code change | PIP-NEW-043 |
| AC-SH-06 | Time and event routine triggers do nothing until approved | PIP-NEW-041 |
| AC-SH-07 | Eightree outlets switch on and off with the internet disconnected, and state is reported back | PIP-NEW-036 |
| AC-SCH-01 | With the network disconnected, set/cancel/snooze timers by voice succeeds ≥95% on the benchmark command set | PIP-NEW-044 |
| AC-SCH-02 | Alarms ring ≤1 s late under full load (model request, image generation and local transcription running) | PIP-NEW-078 |
| AC-SCH-03 | A ringing alarm resumes after a power cut and reboot | FEAT-041 |
| AC-SCH-04 | Pip never says a timer, reminder or event is set unless it exists in storage | PIP-NEW-039 |
| AC-UI-01 | Every setting in UI-AND-SETTINGS §4 is editable in the Settings UI | PIP-NEW-053 |
| AC-UI-02 | The TV client's received events contain no transcript text beyond its displayed captions (traffic inspection) | PIP-NEW-056 |
| AC-UI-03 | Adding an item by voice appears on the TV within 1 s | PIP-NEW-060 |
| AC-UI-04 | A voice layout edit that doesn't fit yields a proposed fix, not a bare failure, in ≥90% of the P-09 failure cases replayed | PIP-NEW-061 |
| AC-UI-05 | Undo restores the previous layout exactly | PIP-NEW-061 |
| AC-SEC-01 | ~~Every dashboard endpoint and event stream rejects unauthenticated access (automated sweep)~~ **Replaced (D-116):** every endpoint refuses unknown Host names, and every request that changes something refuses other websites (`tests/test_security.py`) | FEAT-072 |
| AC-SEC-02 | The dashboard is unreachable from the public internet, and reachable over the private tunnel with HTTPS | PIP-NEW-055 |
| AC-SEC-03 | No credential value appears in logs, events, prompts, git or the sandbox (secret-canary test) | PIP-NEW-058/059 |
| AC-SEC-04 | Credential migration leaves Ziggletron's files byte-identical (checksums before and after) | PIP-NEW-083 |
| AC-SEC-05 | A plugin without a granted permission cannot call that capability | FEAT-079 |
| AC-SEC-06 | ~~Every admin action appears in the audit trail with account and time~~ **Dropped (D-116)** | PIP-NEW-057 |
| AC-SEC-07 | ntfy notifications contain only a generic title unless the member enabled full content; topics reject unauthenticated publishers and subscribers | PIP-NEW-086 |
| AC-SK-01 | For a set of 40 self-knowledge questions, answers are judged correct against the current registry and code in ≥90% of cases, and none presents an unsupported claim as fact | PIP-NEW-065 |
| AC-SK-02 | A seeded doc/code discrepancy is reported as a discrepancy when asked about | PIP-NEW-065 |
| AC-SK-03 | "Why didn't you answer?" cites the actual trace reason for 10 scripted causes (low spotter score, mode gate, STT timeout, model error…) | PIP-NEW-066 |
| AC-SK-04 | Self-inspection tools cannot read secrets, the data folder or paths outside the code tree (path traversal and symlink tests) | PIP-NEW-067 |
| AC-SK-05 | A stalled background task is detected and restarted within 2× its heartbeat period | PIP-NEW-079 |
| AC-SK-06 | Every feature and setting has a registry entry (the verification pipeline enforces it) | PIP-NEW-064 |
| AC-SM-01 | A question about how something works never starts code generation (**Tested 2026-11-09** (`tests/test_changes_voice.py`): only a clear change request uses `request_change`; joining in never asks; and nothing is coded before approval 1) | PIP-NEW-069 |
| AC-SM-02 | No code is generated before a proposal is approved (**Tested 2026-11-09** (`tests/test_builder.py`): planning has read-only tools; building starts only after approval 1) | PIP-NEW-071 |
| AC-SM-03 | A sandbox test that tries to read the secrets file, the data folder, `~/.ssh` or a non-allow-listed host fails and is logged (**Tested 2026-11-09** on the Pi, `tests/test_sandbox.py`: inside `tools/sandbox-test` and the strict sandbox these reads and the internet fail; the agent sandbox sees only the workspace and the login file) | PIP-NEW-072 |
| AC-SM-04 | Sandbox workspaces are gone after success, failure, timeout and reboot mid-change (**Tested 2026-11-09** (`tests/test_builder.py`): success, failure, stop and restart) | PIP-NEW-072 |
| AC-SM-05 | Deployment cannot happen without ~~a signed-in admin~~ the owner's approval on the dashboard (D-116), including when every check passes (**Tested 2026-11-09** (`tests/test_builder.py`)) | PIP-NEW-074 |
| AC-SM-06 | A deliberately broken deployment rolls back automatically within 3 minutes, and alarms still ring (**rollback tested with a fake health check, `tests/test_release.py`; the real test on the Pi is still to do**) | PIP-NEW-075 |
| AC-SM-07 | Commit messages contain no text from the spoken request that isn't in the approved summary; the PII scan passes (**Tested 2026-11-09** (`tests/test_builder.py`): the message is made from the summary; the secret scan runs on every change) | PIP-NEW-076 |
| AC-SM-08 | A detected recurring problem produces a suggestion only, never development | PIP-NEW-077 |
| AC-REL-01 | 50 random power cuts during operation leave no corrupted state, and start-up succeeds each time | 12 §5 |
| AC-REL-02 | 7-day soak: no memory growth beyond 10%, no unrecovered stalls | PIP-NEW-079 |
| AC-REL-03 | Two rapid consecutive requests never fail with a session collision (1,000-trial test) | FEAT-015 / P-01 |
| AC-REL-05 | ~~The usage meter warns on the dashboard before the daily plan limit is reached (at 80%), and Pip switches to the local fallback when it is hit~~ **Changed (D-118):** the Pro plan doesn't report what's left, so there's no 80% warning; when the limit is hit the dashboard says so and joining in and self-changes pause (the local fallback is M2) | PIP-NEW-089 |
| AC-REL-04 | The local LLM fallback answers a simple question within 10 s† on the Pi 5, or Pip states it can't | PIP-NEW-081 |
