# Pip 2.0 — Acceptance Criteria (PROPOSAL)

Each criterion is measurable or independently testable. IDs are `AC-<area>-nn`. Thresholds marked † are starting targets to confirm after the M0 measurements. Criteria refer to requirement IDs from [02](02-PROPOSED-FEATURE-CATALOG.md) and to `FEAT-nnn`.

## A. Listening and speech
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
| AC-SP-09 | Local room transcription runs for 8 hours alongside all other services within the CPU and RAM budget in 12 §5 | PIP-NEW-013 |

## B. Voice output
| ID | Criterion | Covers |
|---|---|---|
| AC-TTS-01 | First audio ≤0.4 s P90 after text is ready, on the Pi, with listening running | PIP-NEW-014 |
| AC-TTS-02 | ≥28 of 30 household and device names pronounced acceptably (household judgement) | PIP-NEW-015 |

## C. Conversation
| ID | Criterion | Covers |
|---|---|---|
| AC-CV-01 | "Stop" halts audio within 100 ms P95, measured on a loopback recording, online and offline | PIP-NEW-006 |
| AC-CV-02 | "Wait" then "go on" within 30 s resumes at the next unspoken sentence | PIP-NEW-016 |
| AC-CV-03 | A correction ("actually fifteen") changes the existing timer; no second timer exists | PIP-NEW-021 |
| AC-CV-04 | In Social mode, with an unknown voice heard in the last 15 minutes, no unaddressed room text is sent to the model (verified from request logs) | PIP-NEW-004 |
| AC-CV-05 | No unprompted contribution starts while anyone is speaking (100 scripted trials) | PIP-NEW-018 |
| AC-CV-06 | Unprompted contributions never exceed the per-hour budget for the configured level over a 1-week trial | PIP-NEW-018 |
| AC-CV-07 | Media-only speech (a TV playing dialogue for 30 minutes) causes no join, greeting, alarm acknowledgement or "stop listening" | PIP-NEW-012 |
| AC-CV-08 | Guest voice profiles are absent from storage after restart unless consent was recorded | PIP-NEW-025 |
| AC-CV-09 | No administrative action can be performed by voice alone (test every admin action) | PIP-NEW-027 |
| AC-CV-10 | After an adult opts out (dashboard or "Pip, leave me out"), none of their unaddressed lines reach the model provider (request-log check) | PIP-NEW-004 |
| AC-CV-11 | Every household adult is shown the opt-out notice at enrolment, and it is reachable in one step from the dashboard | PIP-NEW-004 |
| AC-CV-12 | When the speakerphone is muted, Pip shows "muted" on the TV and dashboard within 2 s and raises no fault; unmuting resumes listening | PIP-NEW-084 |

## D. Memory and data
| ID | Criterion | Covers |
|---|---|---|
| AC-MEM-01 | Unaddressed speech never creates an active memory (scripted overheard-facts test) | PIP-NEW-029 |
| AC-MEM-02 | Saving 1,000 memories deletes none | PIP-NEW-031 |
| AC-MEM-03 | Text that looks like a credential or card number is refused as a memory | PIP-NEW-032 |
| AC-MEM-04 | A member cannot read another member's private memory via dashboard, API or voice | PIP-NEW-028 |
| AC-MEM-05 | After restart: no conversation transcript, prompts or raw audio exist on disk (file-system scan for test phrases) | 06 §1 |
| AC-MEM-06 | "Forget Sam" removes every listed item; a search for Sam's data finds nothing except audit entries | PIP-NEW-033 |
| AC-MEM-07 | Export, then import into a clean install, reproduces the memories via the inbox | PIP-NEW-034 |

## E. Smart home (M3)
| ID | Criterion | Covers |
|---|---|---|
| AC-SH-01 | A command to an offline device yields a spoken failure, never "done" | PIP-NEW-039 |
| AC-SH-02 | An ambiguous request with two matching devices produces a question, not an action | PIP-NEW-038 |
| AC-SH-03 | Class C actions cannot be executed by voice without second-device confirmation | PIP-NEW-040 |
| AC-SH-04 | Discovered devices not on the allow-list cannot be read or controlled | PIP-NEW-036 |
| AC-SH-05 | A new ESPHome device with MQTT discovery appears in the Devices inbox and, once allowed, is usable with no Pip code change | PIP-NEW-043 |
| AC-SH-06 | Time and event routine triggers do nothing until approved | PIP-NEW-041 |
| AC-SH-07 | Eightree outlets switch on and off with the internet disconnected, and state is reported back | PIP-NEW-036 |

## F. Scheduling
| ID | Criterion | Covers |
|---|---|---|
| AC-SCH-01 | With the network disconnected, set/cancel/snooze timers by voice succeeds ≥95% on the benchmark command set | PIP-NEW-044 |
| AC-SCH-02 | Alarms ring ≤1 s late under full load (model request, image generation and local transcription running) | PIP-NEW-078 |
| AC-SCH-03 | A ringing alarm resumes after a power cut and reboot | FEAT-041 |
| AC-SCH-04 | Pip never says a timer, reminder or event is set unless it exists in storage | PIP-NEW-039 |

## G. Dashboard and TV
| ID | Criterion | Covers |
|---|---|---|
| AC-UI-01 | Every setting in UI-AND-SETTINGS §4 is editable in the Settings UI | PIP-NEW-053 |
| AC-UI-02 | The TV client's received events contain no transcript text beyond its displayed captions (traffic inspection) | PIP-NEW-056 |
| AC-UI-03 | Adding an item by voice appears on the TV within 1 s | PIP-NEW-060 |
| AC-UI-04 | A voice layout edit that doesn't fit yields a proposed fix, not a bare failure, in ≥90% of the P-09 failure cases replayed | PIP-NEW-061 |
| AC-UI-05 | Undo restores the previous layout exactly | PIP-NEW-061 |

## H. Security
| ID | Criterion | Covers |
|---|---|---|
| AC-SEC-01 | Every dashboard endpoint and event stream rejects unauthenticated access (automated sweep) | PIP-NEW-054 |
| AC-SEC-02 | The dashboard is unreachable from the public internet, and reachable over the private tunnel with HTTPS | PIP-NEW-055 |
| AC-SEC-03 | No credential value appears in logs, events, prompts, git or the sandbox (secret-canary test) | PIP-NEW-058/059 |
| AC-SEC-04 | Credential migration leaves Ziggletron's files byte-identical (checksums before and after) | PIP-NEW-083 |
| AC-SEC-05 | A plugin without a granted permission cannot call that capability | FEAT-079 |
| AC-SEC-06 | Every admin action appears in the audit trail with account and time | PIP-NEW-057 |
| AC-SEC-07 | ntfy notifications contain only a generic title unless the member enabled full content; topics reject unauthenticated publishers and subscribers | PIP-NEW-086 |

## I. Self-knowledge and diagnostics
| ID | Criterion | Covers |
|---|---|---|
| AC-SK-01 | For a set of 40 self-knowledge questions, answers are judged correct against the current registry and code in ≥90% of cases, and none presents an unsupported claim as fact | PIP-NEW-065 |
| AC-SK-02 | A seeded doc/code discrepancy is reported as a discrepancy when asked about | PIP-NEW-065 |
| AC-SK-03 | "Why didn't you answer?" cites the actual trace reason for 10 scripted causes (low spotter score, mode gate, STT timeout, model error…) | PIP-NEW-066 |
| AC-SK-04 | Self-inspection tools cannot read secrets, the data folder or paths outside the code tree (path traversal and symlink tests) | PIP-NEW-067 |
| AC-SK-05 | A stalled background task is detected and restarted within 2× its heartbeat period | PIP-NEW-079 |
| AC-SK-06 | Every feature and setting has a registry entry (the verification pipeline enforces it) | PIP-NEW-064 |

## J. Safe self-modification
| ID | Criterion | Covers |
|---|---|---|
| AC-SM-01 | A question about how something works never starts code generation | PIP-NEW-069 |
| AC-SM-02 | No code is generated before a proposal is approved | PIP-NEW-071 |
| AC-SM-03 | A sandbox test that tries to read the secrets file, the data folder, `~/.ssh` or a non-allow-listed host fails and is logged | PIP-NEW-072 |
| AC-SM-04 | Sandbox workspaces are gone after success, failure, timeout and reboot mid-change | PIP-NEW-072 |
| AC-SM-05 | Deployment cannot happen without a signed-in admin approval, including when every check passes | PIP-NEW-074 |
| AC-SM-06 | A deliberately broken deployment rolls back automatically within 3 minutes, and alarms still ring | PIP-NEW-075 |
| AC-SM-07 | Commit messages contain no text from the spoken request that isn't in the approved summary; the PII scan passes | PIP-NEW-076 |
| AC-SM-08 | A detected recurring problem produces a suggestion only, never development | PIP-NEW-077 |

## K. Reliability
| ID | Criterion | Covers |
|---|---|---|
| AC-REL-01 | 50 random power cuts during operation leave no corrupted state, and start-up succeeds each time | 12 §5 |
| AC-REL-02 | 7-day soak: no memory growth beyond 10%, no unrecovered stalls | PIP-NEW-079 |
| AC-REL-03 | Two rapid consecutive requests never fail with a session collision (1,000-trial test) | FEAT-015 / P-01 |
| AC-REL-05 | The usage meter warns on the dashboard before the daily plan limit is reached (at 80%), and Pip switches to the local fallback when it is hit | PIP-NEW-089 |
| AC-REL-04 | The local LLM fallback answers a simple question within 10 s† on the Pi 5, or Pip states it can't | PIP-NEW-081 |
