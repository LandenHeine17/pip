# Pip 2.0 — Additional Recommendations (PROPOSAL, not approved requirements)

My own ideas beyond the brief. **Owner decision D-24:** ideas 001, 002, 004, 005, 007 and 011 are adopted as PIP-NEW-084…089. 010 and 012 are not adopted. The rest remain optional. **Essential** items are already folded into the requirements (listed first, for visibility). The rest are `PIP-IDEA-nnn`: optional and **not** in the catalog until the owner approves them.

## 1. Essential improvements (already in the requirements)

| Improvement | Why | Where |
|---|---|---|
| Wake word test before naming is final | "Pip" is acoustically weak | 04 §2.1 |
| Speakerphone over USB, with Pip's audio played through it | AEC only works on its own output; Bluetooth mic audio is narrowband | 04 §2.2 |
| Local room transcription for Social mode | Only way to have Social by default within $10/month | 04 §6 |
| Capability registry enforced by the verification pipeline | Self-knowledge stays accurate as Pip changes itself | 09 §2 |
| One code-change path (console goes through the sandbox too) | Removes U-11 confusion and the unsandboxed back door | 10 §11 |
| Versioned installs with automatic rollback for all releases | Self-change safety depends on it | 12 §5 |
| New git repository for Pip | Keeps the phone number in Ziggletron's history from spreading | 11 §8 |

## 2. Optional ideas

| ID | Idea | What it does | Why useful | Relates to | Complexity | Risks | Recommend? |
|---|---|---|---|---|---|---|---|
| PIP-IDEA-001 | **Physical mute button + LED** | A GPIO button cuts the mic; an LED shows listening state and mode | Trustworthy privacy you can see and touch; works when the dashboard is unreachable | FEAT-046, PIP-NEW-005 | S | None notable | Replaced by the speakerphone mute (D-38) |
| PIP-IDEA-002 | **NVMe SSD instead of SD card** | Boot and data on SSD | Write endurance, speed, fewer corruptions | HC-10/11 | S (hardware) | Cost | Declined (D-39) |
| PIP-IDEA-003 | **UPS HAT** | Short battery backup; clean shutdown | Survives brief power cuts; alarms keep ringing | HC-10 | S | Cost | Optional |
| PIP-IDEA-004 | **Phone notifications** | Push alarm, reminder and diagnostic notices to members' phones (owner chose ntfy) | Reminders reach people who aren't in the room; alternative to SMS | FEAT-035, 043 | M | Another integration | **Yes** (M3) |
| PIP-IDEA-005 | **Household messages** | "Pip, tell Sam the plumber called." Delivered when Sam's voice is next recognised, or to Sam's phone | Natural household collaboration | FEAT-035, 010 | M | Private messages read aloud with others present → apply private-memory rules | **Yes** (M2/M3) |
| PIP-IDEA-006 | **Music via external players** | Superseded: the owner chose local files on Pip's speaker (PIP-NEW-090) | — | FEAT-088 | — | — | Withdrawn |
| PIP-IDEA-007 | **Daily briefing** | Optional morning summary: weather, events, tasks, reminders | High value for little effort; fits routines | PIP-NEW-041 | S | Annoying if forced; opt-in only | **Yes** (as a routine) |
| PIP-IDEA-008 | **Voice satellites (multi-room)** | ESP32-S3 microphone/speaker units in other rooms; local spotter on device, audio to Pip | Pip in more rooms | 07 §1.2, PIP-NEW-001 | L | Privacy multiplies; AEC per room; complexity | Later (M4) |
| PIP-IDEA-009 | **Direction-aware face** | The face looks toward the speaker, using the mic's direction-of-arrival | Feels attentive | PIP-NEW-062 | S–M | Needs a mic that reports direction | Optional |
| PIP-IDEA-010 | **Simulator and replay harness** | Run Pip against scripted conversations (text or audio) to test turn-taking and actions | Makes conversation behaviour testable; makes self-change verification stronger | PIP-NEW-073, 082 | M | — | **Yes** (M1, developer tool) |
| PIP-IDEA-011 | **Usage and plan-quota meter** | Shows model requests per day by kind (answers, joins, drawings) | U-06; the subscription has limits | FEAT-070 | S | — | **Yes** |
| PIP-IDEA-012 | **Encrypted backups** | Nightly encrypted backup of memories, schedules and settings to USB or a NAS | Recovery from SD/SSD failure | FEAT-084 | S | Backup holds personal data; must be encrypted | **Yes** |
| PIP-IDEA-013 | **Kitchen helpers offline** | Unit conversions, simple maths, multiple named timers by voice | Common, fast, no cloud | PIP-NEW-044 | S | — | Optional |
| PIP-IDEA-014 | **"What did I miss?"** | Summary of notable events while you were away (alarms, deliveries, sensor events), never conversation | Useful with presence | PIP-NEW-042 | M | Must not summarise other people's conversations | Optional (M3) |
| PIP-IDEA-015 | **Custom sound events** | Teach Pip a sound (washer beep, kettle) from a few samples | Environmental awareness without smart devices | FEAT-009 | M | False positives | Optional |
| PIP-IDEA-016 | **Cloud voice later** | Optional natural cloud TTS if the budget allows | Naturalness | 04 Part B | S | Cost; latency; voice switching | Not now (owner chose local) |

## 3. Simplification opportunities

| Simplify | How | Saves |
|---|---|---|
| Unprompted drawings (FEAT-060) | Remove, or keep off by default | Plan quota, failure handling, P-10 |
| Background memory sorting (FEAT-031) | Merge into save-time categorisation | A background job and its failure modes |
| PIR GPIO support (FEAT-049) | Kept (owner wants devices on the Pi); handle a missing sensor gracefully instead of failing startup | Startup failures |
| Two self-change paths (FEAT-073/077) | One pipeline | Duplicate safety logic |
| Room-Aware mode | If the Pi measurements are poor, offer only Private and Social (with Social degrading gracefully) | One mode's tuning and testing |
| Calendar sync | Defer until a single provider is chosen | Sync conflict complexity |
