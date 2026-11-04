# Pip 2.0 — Feature Decision Matrix (PROPOSAL)

A decision for every Ziggletron feature in the [catalog](../ziggletron-discovery/COMPLETE-FEATURE-CATALOG.md). The "current" column describes **Ziggletron as discovered**. The other columns are **proposals**.

**Decisions:** KEEP (as is) · IMPROVE (same design, fixes and tuning) · REDESIGN (same purpose, new behaviour) · REPLACE (superseded by a new capability) · MERGE (folded into another feature) · REMOVE · REVIEW (the owner should decide).
**Priority:** P1 / P2 / P3 within the [roadmap](15-PRIORITIZED-ROADMAP.md). The owner chose **parity first**, so most kept features are in M1.
**Approval?** "Yes" = a behaviour change the owner should explicitly approve. "—" = a straightforward fix.

## A. Hearing and speaker identity

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 001 | Continuous listening | Cut room audio into utterances | REDESIGN (**owner: keep cloud transcription of the room in Social mode, D-51**) | Speakerphone AEC → RAM buffer → local VAD and name spotter; nothing leaves before the address decision (PIP-NEW-001/002) | V-01: all speech uploaded | Hardware; PIP-NEW-001 | Spotter false activations with "Pip" | P1 | Yes |
| 002 | STT with fallback | Utterance → text | REDESIGN | Benchmark-chosen provider for addressed speech; local room transcription; 2–3 s timeouts; non-blocking (PIP-NEW-008/013) | R-01, R-04, budget | Benchmark | Pi CPU for local room STT | P1 | Yes (provider) |
| 003 | Called by name | Decide it was addressed | REDESIGN | Local spotter for "Pip"/"Hey Pip"; clause rule kept | Wake detection must be local | PIP-NEW-001 | Short name | P1 | Yes (wake form) |
| 004 | Name merely mentioned | Avoid replying when talked about | IMPROVE | Classified locally; model consulted only in Social/Room-Aware with consent | Fewer model calls | 003 | — | P2 | — |
| 005 | Follow-up window | Talk without the name | REDESIGN | Bound to the recognised person; extends while they speak (PIP-NEW-020) | Chatter answered by mistake | 010 | Unrecognised speaker gets a shorter window | P2 | Yes |
| 006 | Chime | Immediate acknowledgement | KEEP | Unchanged; configurable | Works | — | — | P1 | — |
| 007 | Echo rejection | Don't hear itself | IMPROVE | Speakerphone AEC first; word, voice and loudness checks re-tuned as a second line | Quiet interruptions dropped | Hardware | Speakerphone AEC quality unknown | P1 | — |
| 008 | Transcription hygiene | Drop phantom text | IMPROVE | Keep the phantom filter (provider-specific lists); name-hint re-check removed (local spotter) | Simpler | 001 | — | P2 | — |
| 009 | Ambient sounds | Notice non-speech sounds | IMPROVE | Also detects media speech to gate joining and alarms (PIP-NEW-012) | U-01, U-02 | — | Misclassifying a real person as TV | P1 | — |
| 010 | Speaker labels | Who said each line | IMPROVE | Same three levels; re-tuned; "Several people" label | Works [R] | New mic | Accuracy after a mic change | P1 | — |
| 011 | Enrol by introduction | Learn a voice | REDESIGN | Kept, plus guided enrolment (PIP-NEW-024); guests session-only unless consented (PIP-NEW-025) | V-04; TV enrolment risk | 010 | — | P1 | Yes |
| 012 | Correct a line's speaker | Fix labels | IMPROVE | Persists across restarts; members correct their own lines (PIP-NEW-026) | P-07 | Accounts | — | P3 | — |
| 013 | Manage voices | Rename, merge, forget | IMPROVE | Shows consent and expiry; review after 180 days | V-04 | 025 | — | P2 | Yes (expiry values) |
| 014 | Admin voice protection | Stop voice impersonation of admins | REPLACE | Admin authority comes from accounts, not voice (PIP-NEW-027/054) | S-04, S-06 | Accounts | — | P1 | Yes |

## B. Conversation and responding

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 015 | Streamed answers | Reply quickly | IMPROVE | One model session per request (P-01); outcome-first wording | P-01 | Model access | Subscription CLI dependency | P1 | — |
| 016 | TTS | Speak | IMPROVE | Local only; Kokoro vs Piper; cached phrases; pronunciation list (PIP-NEW-014/015) | Owner: local only | Pi CPU | Kokoro speed | P1 | — |
| 017 | Barge-in and stop phrases | Interrupt Pip | REDESIGN | Local keywords; wait/resume; "actually"; ducking (PIP-NEW-006/016/021) | Faster; offline | AEC | False stops from TV | P1 | Yes |
| 018 | Cut-off awareness | Don't repeat | KEEP | Unchanged | Works | — | — | P1 | — |
| 019 | Joining conversations | Participate | REDESIGN (**owner: Ziggletron's way + local question check, D-52**) | Modes; local gates and triggers; budgets; back-off; consent (PIP-NEW-003/004/018) | V-02; cost; naturalness | 013 | Too silent or too chatty; tuning needed | P1 | Yes |
| 020 | "Anything to add?" button | Prompt a join | KEEP | Respects mode and consent | Useful for testing | 019 | — | P3 | — |
| 021 | Welcome-back greeting | Greet returnees | IMPROVE | Recognised household voices only; at most once per 4 h; can be switched off | U-01 | 010 | — | P3 | Yes |
| 022 | Editable personality | Shape tone | IMPROVE | Dashboard editor, preview, history, sliders | U-07 | Settings UI | — | P2 | — |
| 023 | Situation context | Context for the model | IMPROVE | Only memories visible to those present; sensor context; trimmed by mode | Privacy | 028 | Less context → worse answers | P1 | — |
| 024 | Web search | Current facts | KEEP | Results treated as untrusted data | S-05 | Model | Injection | P2 | — |
| 025 | Actions + outcomes | Tool execution | REDESIGN | Verified action lifecycle for every action (PIP-NEW-039); no-match = failure | P-03, P-12 | — | — | P1 | — |
| 026 | Failure speech | Fail briefly | IMPROVE | Offline path and local LLM fallback (PIP-NEW-044/081); says what still works | R-03 | 044 | — | P1 | — |
| 027 | Typing | Silent input | IMPROVE | Typed lines carry the account identity; P-04 fixed; "auto" addressing option | P-04 | Accounts | — | P2 | — |

## C. Memory

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 028 | Long-term memories | Remember facts | REDESIGN | Categories, owner, visibility, confirmed/inferred; never from overheard speech; no cap (PIP-NEW-028/029/031) | V-03, silent eviction | — | More prompts for approval | P1 | Yes |
| 029 | Forget by voice | Remove a fact | IMPROVE | Confirms the exact item; reports no-match; asks when uncertain | P-03 | 028 | — | P1 | — |
| 030 | Selective recall | Send relevant memories | IMPROVE | Plus visibility rules for the people present | Privacy | 010 | Missed recall | P1 | — |
| 031 | Background sorting | Classify memories | MERGE | Into save-time categorisation and the inbox | Simpler | 029 | — | P3 | — |
| 032 | Dashboard memory management | Curate | REPLACE | Memory Center (PIP-NEW-030) | U-09, V-08 | Accounts | — | P1 | — |

## D. Timers, reminders, calendar and alarms

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 033 | Timers | Countdowns | IMPROVE | Created offline by local commands (PIP-NEW-044); confirmed only after creation | R-03 | 044 | — | P1 | — |
| 034 | Reminders | Scheduled reminders | IMPROVE | Dashboard creation; weekday and monthly repeats (PIP-NEW-046) | U-09 | — | — | P1 | — |
| 035 | Targeted reminders | For one person | IMPROVE | P-06 wording fixed; uses recognised presence (and MQTT/GPIO presence sensors in M3) | P-06 | 010 | — | P2 | — |
| 036 | Calendar | Household events | IMPROVE | Repeat on the dashboard (P-05); sync evaluated (PIP-NEW-049) | P-05 | — | Sync privacy | P1 | Yes (sync) |
| 037 | Ringing alarms | Hard-to-miss alerts | IMPROVE | Acknowledged only by a non-media voice, typed input or the dashboard (PIP-NEW-051); voice snooze | U-02 | 012 | Missed acknowledgement if mis-classified | P1 | — |
| 038 | Earned alarms | "Until…" alarms | IMPROVE | Only household voices' attempts are judged | FEAT-038 issue | 010 | — | P2 | — |
| 039 | Shut off / snooze | Dashboard alarm controls | KEEP | Plus voice snooze (PIP-NEW-045) | Works | — | — | P1 | — |
| 040 | Ringtones | Customise sounds | KEEP | Unchanged | Works | — | — | P2 | — |
| 041 | Alarms survive restarts | Reliability | KEEP | Unchanged; covered by acceptance tests | Works | — | — | P1 | — |
| 042 | Cancel / remove | Remove items | IMPROVE | Reports no-match; asks when the match is ambiguous | P-03 | — | — | P1 | — |

## E–G. Communication, devices, presence

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 043 | SMS | Text people | IMPROVE | Numbers in the vault (S-08); household-affecting confirmation tier; message text not logged | S-08 | 058 | Third-party content | P2 | — |
| 044 | Volume | Change volume | IMPROVE | Real outcome reported (P-12); offline path | P-12 | 039 | — | P1 | — |
| 045 | Stop listening | Privacy sleep | REDESIGN | Only the local name spotter runs while asleep, so "Pip, start listening" works; nothing else is processed; a separate hard-off (mic switch or mute button) releases the mic entirely; media voices can't trigger it | U-03; owner decision D-14 | 001 | People must understand the spotter still runs (shown on the TV and LED) | P1 | — |
| 046 | Mic switch | Hard mic off | IMPROVE | Requires a signed-in member; **persists across restarts** (D-22); speakerphone mute detection (PIP-NEW-084, D-38) | S-01, U-04 | Accounts | — | P1 | — |
| 047 | Speaker test | Setup aid | KEEP | Unchanged | — | — | — | P3 | — |
| 048 | Dozing | Save effort | KEEP | Unchanged | Works | — | — | P2 | — |
| 049 | Away (PIR) | Mic off when nobody is around | IMPROVE | GPIO PIR kept (owner: devices connect directly to the Pi); MQTT presence sensors also accepted (M3); verified with a bench test | Owner decision D-18 | 036 | Untested today (R-07) | P3 | — |
| 050 | Quiet hours | Etiquette | KEEP | Safety events still announce | Works | — | — | P1 | — |
| 051 | Muted-mic warning | Diagnostics | MERGE | Into health monitoring (PIP-NEW-079) | — | 079 | — | P2 | — |

## H. TV

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 052 | Animated face | Presence | IMPROVE | More expressions, mode indicator (PIP-NEW-062) | Owner wants it kept | — | Distraction | P1 | — |
| 053 | Weather clothing | Delight | KEEP | Same source as spoken weather (P-08) | Works | 064 | — | P2 | — |
| 054 | "Six seven" | Easter egg | KEEP | Unchanged; part of a general "reactions" list | Cheap delight | — | — | P3 | — |
| 055 | TV dashboards | Views | IMPROVE | Templates; persisted; live widgets | P-09 | 060 | — | P1 | — |
| 056 | Custom dashboards by voice | Personalise | REDESIGN | Validate, then preview, then apply; undo (PIP-NEW-061) | P-09 | 061 | — | P1 | — |
| 057 | Grid arranging | Layout | REDESIGN | Validation with automatic fit suggestions; drag-and-drop on the dashboard | P-09 | 061 | — | P2 | — |
| 058 | Box types | Building blocks | IMPROVE | Live data; notes become live lists (U-08) | U-08 | 060 | — | P1 | — |
| 059 | Drawings on request | Illustrations | KEEP | Requested only; daily cap; clear progress | Owner wants it optional | Model quota | Slow, costly | P3 | — |
| 060 | Pictures of its own | Unprompted drawings | IMPROVE | Kept, **off by default**, own switch (owner decision D-21) | Cost and latency vs value (P-10) | 059 | Plan quota | P3 | — |
| 061 | Real photos | Visual aid | IMPROVE | Separate switch for unprompted photos (U-05) | U-05 | — | Filter limits | P3 | — |
| 062 | TV privacy | Wall-display privacy | IMPROVE | The client receives only displayed data (PIP-NEW-056) | V-07 | 056 | — | P1 | — |
| 063 | TV resilience | Unattended display | KEEP | Unchanged | Works | — | — | P1 | — |
| 064 | Weather | Ambient info | IMPROVE | Single source shared with the model | P-08 | — | — | P2 | — |
| 065 | Vitals | Diagnostics | MERGE | Into health monitoring; the TV widget stays | — | 079 | — | P3 | — |

## I. Dashboard

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 066 | Status and controls | Observe and control | REDESIGN | Home section (PIP-NEW-052) | S-01 | 054 | — | P1 | — |
| 067 | Conversation feed | Transparency | KEEP (**owner: no login, D-53**) | Members only; RAM only; respects consent (unconsented speakers' lines not shown) | S-01, V-05 | 054 | — | P1 | Yes |
| 068 | Prompts inspector | Explainability | REDESIGN | Developer section, admin only; memories redacted unless the viewer may see them | S-01 | 054 | — | P2 | — |
| 069 | Logs viewer | Diagnostics | REDESIGN | Structured, redacted logs (PIP-NEW-059) | V-05 | 059 | — | P1 | — |
| 070 | Latency and quota | Tuning | IMPROVE | Into the Speech panel and Developer section | — | 010 | — | P2 | — |
| 071 | Admin password | Access control | REPLACE | Accounts and roles (PIP-NEW-054) | S-02 | — | Lock-out recovery needed | P1 | — |
| 072 | Access protection | Host/origin checks | IMPROVE | Keep the host/origin checks; add authentication and HTTPS via the private tunnel | S-01 | 055 | — | P1 | — |

## J. Self-modification and console

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 073 | Propose and approve by voice | Request changes | REDESIGN | Voice initiates; classification and proposal (PIP-NEW-069–071); approval on the dashboard (PIP-NEW-074) | S-04 | 054 | — | P1 | Yes |
| 074 | Isolated copy, watchable terminal | Make the change | REPLACE | Sandboxed development (PIP-NEW-072); watchable terminal kept | S-03 | Sandbox on Pi | Pi resources | P1 | — |
| 075 | Test gate, merge, restart | Ship the change | REPLACE | Verification pipeline + safe deploy + rollback (PIP-NEW-073/075) | Tests ≠ safe | 080 | — | P1 | — |
| 076 | History and admin controls | Governance | IMPROVE | Sanitised history (PIP-NEW-076); explainable | V-06 | — | — | P1 | — |
| 077 | Code console on live code | Keyboard development | REDESIGN | Console opens on a sandboxed workspace, not live code; same review path as voice changes | U-11, S-03 | 072 | Slower admin workflow | P2 | **Yes** |
| 078 | Restart | Maintenance | IMPROVE | Waits for quiet by default; "restart now" asks for confirmation while an alarm rings | R-05 | — | — | P2 | — |

## K. Platform

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 079 | Plugins | Extensibility | REDESIGN | Plugins declare the permissions they need (actions, events, data, network); admin grants them; plugin commands are permission-checked | S-07 | 054 | Plugin API effort | P2 | Yes |
| 080 | Validated configuration | Robust setup | IMPROVE | Same validation, behind the Settings UI | U-07 | 053 | — | P1 | — |
| 081 | Secrets | Credential safety | REPLACE | Credential vault (PIP-NEW-058) | S-09 | — | — | P1 | — |
| 082 | Startup sequence | Lifecycle | KEEP | Plus readiness checks | — | 079 | — | P1 | — |
| 083 | Self-healing jobs | Reliability | IMPROVE | Plus stall detection (PIP-NEW-079) | R-02 | — | — | P1 | — |
| 084 | Safe storage | Data integrity | KEEP | Plus export and backup | Works | — | SD card wear | P1 | — |
| 085 | Model access choice | Brain | IMPROVE | Owner decision: Claude subscription via Claude Code, one session per request, plus a local fallback (PIP-NEW-081) | P-01, R-03 | — | CLI and plan dependency | P1 | — |
| 086 | Install, boot, kiosk | Deployment | IMPROVE | Plus versioned installs with rollback (PIP-NEW-080) | Safe updates | — | — | P1 | — |
| 087 | Mic error recovery | Reliability | IMPROVE | Plus a functional check (audio actually flowing) | R-07 | 079 | — | P1 | — |

## L. Unmerged

| FEAT | Name | Current purpose | Decision | New expected behaviour | Reasoning | Depends on | Risks | Pri | Approval? |
|---|---|---|---|---|---|---|---|---|---|
| 088 | Music (branch) | Media playback | REDESIGN | Local music files on Pip's own speaker: play/pause/skip/volume, ducking while Pip listens or speaks; M3 ([07 §9](07-SMART-HOME-AND-INTEGRATIONS.md#9-music-playback-feat-088-redesigned-owner-decision-d-20)) | Owner decision D-20 | — | Speakerphone not built for music | P3 | — |
| 089 | Explain own code (branch) | Self-knowledge | REPLACE | PIP-NEW-064–067 | Owner priority | — | — | P1 | — |
