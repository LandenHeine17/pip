# Known Problems and Limitations

Product-level problems found during discovery. Each item says what the user experiences, how it was established, and which features are affected. Source-level detail is in [legacy-reference/TECHNICAL-AUDIT-NOTES.md](legacy-reference/TECHNICAL-AUDIT-NOTES.md). This is not a fix list for the legacy code; it records what the redesign should take into account.

Severity: 🔴 high · 🟠 medium · 🟡 low.

## 1. Defects

| ID | Sev | Problem | User impact | Evidence | Features |
|---|---|---|---|---|---|
| P-01 | 🟠 | **Concurrent replies can collide.** Two answer requests can end up reading from the same pre-started model session; one fails with an internal error | "Sorry, something went wrong on my end." and the request is lost. Most likely when a new question replaces one still being prepared, or with rapid follow-ups | [R] Observed once in the service log on 2026-11-02; traceback confirms two readers on one session | FEAT-015, 085 |
| P-02 | 🟡 | **Self-change working copies and branches left behind.** 2 isolated working copies and 4 change branches remain on the device | Disk clutter; confusing branch list; stale copies of old code | [R] | FEAT-074–076 |
| P-03 | 🟠 | **"Not found" is reported as nothing.** Forget, cancel and remove-event with no good match do nothing and report nothing, so the model (which already said "done") is never corrected | It says "Forgotten" / "Cancelled" when nothing happened | [S] | FEAT-029, 042 |
| P-04 | 🟡 | **Typed "To the room" ignores the name.** A typed line naming the assistant but sent "to the room" is never answered | Testing addressing from the dashboard gives misleading results | [S] | FEAT-027 |
| P-05 | 🟡 | **Calendar repeat only by voice.** The dashboard event form has no repeat option | Users can't create repeating events on the dashboard | [S] | FEAT-036 |
| P-06 | 🟡 | **The model is told a targeted reminder "only goes off if their voice has been heard recently"**, but the product always rings it (asking "Is X home?") | The model may tell people the wrong thing about how the reminder behaves | [S] | FEAT-035 |
| P-07 | 🟡 | **Voice corrections expire.** Lines can only be relabelled for the last 200 lines and never after a restart | "Couldn't tell whose voice that was" on older lines | [S][T] | FEAT-012 |
| P-08 | 🟡 | **Two weather sources.** TV weather and spoken weather come from different services | Face wears an umbrella while the voice says "sunny" | [S] | FEAT-053, 064, 024 |
| P-09 | 🟡 | **Layout requests by voice fail often** ("no room left for the … box", "at most N sections") | Repeated failed TV edits seen in logs | [R] | FEAT-056, 057 |
| P-10 | 🟡 | **Picture drawing fails or takes minutes** | Delays and apologies; uses plan quota even on failure | [R] | FEAT-059 |
| P-11 | 🟡 | A join-in outcome label "had a thought, but was busy" exists on the dashboard but is never produced | None (dead UI path) | [S] | FEAT-067 |
| P-12 | 🟡 | **Volume control silently does nothing** on systems without PipeWire/PulseAudio tools, yet reports success | Says "Volume set to 40%" with no effect | [S] | FEAT-044 |
| — | ✅ | *Fixed historically:* crash on sentences producing no audio (seen 2026-10-28); fired alarms lost on restart; targeted reminders skipped after restart; dashboard cross-site commands; voice gate bypass | — | [T] | — |

## 2. Security

| ID | Sev | Problem | Detail |
|---|---|---|---|
| S-01 | 🔴 | **No login for viewing or for most controls.** | Anyone who can reach the dashboard sees the whole conversation, every prompt (with memories), the calendar, voice names and logs, and can type to it, switch the mic off, delete memories, change timers and events, and change TV and ringtone settings. Safe only if access is restricted to the device, an SSH tunnel or a private network. |
| S-02 | 🟠 | **Single shared admin password, plain HTTP.** | No accounts, no roles beyond admin/non-admin, no audit of who did what on the dashboard, no TLS unless tunnelled. Lockout after 5 wrong guesses per minute. |
| S-03 | 🔴 | **Self-changes and the code console are not sandboxed.** | The coding agent's file restrictions apply to its own tools, but tests it writes are ordinary code run with the user's full privileges. They can read the secrets file, SSH keys, the subscription token in their environment, and use the network. Admin approval (or the dashboard password) is therefore equivalent to granting arbitrary code execution. |
| S-04 | 🟠 | **Voice-approved changes are merged and switched on automatically**, with no human review of the actual code diff. | The admin approves a spoken summary; the agent gets the fuller written request. |
| S-05 | 🟠 | **Prompt injection through the room, TV, radio and web search.** | Any audible voice is transcribed and can steer the model to remember facts, set reminders, change volume, put the mic to sleep, show photos, or (during an earned alarm) dismiss it. Code-change suggestions require a known voice; approval requires an admin voice. |
| S-06 | 🟡 | **Admin identity bootstrap gap.** | Until an admin has a voice profile, anyone saying "I'm <admin>" creates it. |
| S-07 | 🟡 | **Plugins are fully trusted.** | They can do anything; plugin dashboard commands are password-free. |
| S-08 | 🟡 | **Phone numbers live in the ordinary settings file**, not the secrets file. | Sharing settings shares numbers. |
| S-09 | 🟡 | **Secrets are held in the process environment.** | Withheld from most child programs, but the subscription token is passed to the coding agent's environment. |

## 3. Privacy

| ID | Sev | Problem |
|---|---|---|
| V-01 | 🔴 | **All room speech goes to the cloud when cloud STT is on**: there is no local wake word, so every utterance is uploaded, not only those addressed to it. |
| V-02 | 🟠 | **Join checks send room conversation (last 40 lines) to the model provider** every time it considers chiming in (up to every 15 s on "Often"). |
| V-03 | 🟠 | **Overheard facts can be remembered without being asked**; "never anything private" is only an instruction. Memories over the 150 cap are evicted silently. |
| V-04 | 🟠 | **Voiceprints (biometric) never expire**; 17 profiles on the live device including any guests ever named. |
| V-05 | 🟠 | **"The transcript is only in memory" is not the whole truth**: every heard line and reply is written to the system log. |
| V-06 | 🟠 | **Spoken requests become permanent version history.** Self-change requests and console requests are written into commit messages verbatim; at least one historical commit subject contains a personal phone number. If the repository is pushed to a hosting service, this leaves the house. |
| V-07 | 🟡 | The TV page receives the full event stream (room chatter, prompts), though it displays only addressed lines. |
| V-08 | 🟡 | No data export, retention settings or "forget everything". |

## 4. Reliability and operations

| ID | Sev | Problem |
|---|---|---|
| R-01 | 🟠 | **A slow cloud transcription delays everything else** in the queue (typed messages, sounds, decisions) by up to ~10 s. Alarms are exempt. |
| R-02 | 🟠 | **No watchdog for a hung (not crashed) job**, e.g. a stalled mic or decision loop; only crashes are restarted. |
| R-03 | 🟠 | **No offline fallback** for the reasoning step: with no internet it can't set a timer by voice. |
| R-04 | 🟡 | The local STT model is always loaded even when the cloud is used (memory on a Pi). |
| R-05 | 🟡 | A restart during a self-change abandons it ("stopped"). Manual restart cuts off a ringing alarm (it rings again after restart). |
| R-06 | 🟡 | Slow plugin event listeners miss events. |
| R-07 | 🟡 | **Untested paths:** live microphone capture and reopening, voice-activity detection, the presence monitor, the motion sensor, real playback. |
| R-08 | 🟡 | Several waits are implemented by periodic checking (announcement wait, restart wait, presence tick). |

## 5. UX and product design

| ID | Sev | Problem |
|---|---|---|
| U-01 | 🟡 | **Greets a TV or radio** after 20 quiet minutes (any voice counts). |
| U-02 | 🟡 | **A TV saying "okay" or "coming" stops a normal alarm.** Acknowledgement detection is keyword-based. |
| U-03 | 🟡 | **"Stop listening" can't be undone by voice**; only the dashboard or the timeout ends it. Anyone (including a TV) can trigger it. |
| U-04 | 🟡 | **Inconsistent persistence of dashboard settings**: chattiness, TV and ringtones persist; the mic switch deliberately does not. |
| U-05 | 🟡 | **Unprompted photos have no on/off switch** while unprompted drawings do. |
| U-06 | 🟡 | **No visibility of model usage/cost** (only cloud STT quota is shown), although every reply and join check uses the plan. |
| U-07 | 🟡 | **Most settings need a file edit and restart**; there is no settings screen. |
| U-08 | 🟡 | **"Notes" boxes on the TV are static snapshots**; a "shopping list" box never updates by itself. |
| U-09 | 🟡 | **Memories can't be added or reworded on the dashboard**, only re-sorted or deleted. No reminder creation form (only timers). |
| U-10 | 🟡 | **Addressing heuristics are English-only** and tuned to one name. |
| U-11 | 🟡 | **Two code-change paths with different safety levels** (voice: isolated copy and test gate; console: live code, no gate) can confuse admins. Uncommitted console edits block voice changes. |

## 6. Not built

Listed in project docs as ideas, or absent:

| Capability | Notes |
|---|---|
| Local wake-word detection | Would address V-01 and R-01 |
| Smart-home control | Plugin pattern documented only |
| Music / podcasts | An unmerged experimental branch exists (FEAT-088) |
| Calendar sync, shared shopping list | — |
| Multiple languages | Underlying models support others; product does not |
| Local/offline brain fallback | — |
| Dashboard accounts, roles, HTTPS | — |
| Memory editor, export/backup/restore | — |
| Required diff review before a voice change merges | Owner's choice to not require it |
| Health watchdog | — |
| Answering questions about its own code | Unmerged branch (FEAT-089) |

## 7. Documentation drift observed

- Project docs say the conversation transcript lives only in memory (it is also logged; V-05).
- The targeted-reminder action description contradicts behaviour (P-06).
- The internal event list doesn't document all event fields (developer-facing).
- The code review document's status table predates the newest features (TV photos, weather clothing, the six-seven reaction), so it doesn't cover them.
