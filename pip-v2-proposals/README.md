# Pip 2.0 — Proposed Product Specification

> **Status: all owner decisions recorded (2026-11-03). Ready for M0 measurements, then the architecture phase. Nothing is implemented.**
> **Ziggletron 1.0 (what exists)** is recorded, unchanged, in [`../01-discovery/`](../01-discovery/PRODUCT-OVERVIEW.md).
> **Pip 2.0 (what we want to build)** is described here. Architecture is deliberately **not** designed yet (brief Part 14).

## Summary

Pip is a companion for one room. It follows the room conversation locally, is called by name, answers and acts quickly, and contributes unprompted only when useful and consented. It remembers what people approve, explains itself from real evidence, and can propose and safely deploy changes to itself after a human reviews them. It keeps Ziggletron's best parts: name-anywhere addressing, the follow-up window, barge-in, real ringing alarms, earned alarms, the expressive TV face and an editable personality. It fixes Ziggletron's biggest problems:
- All room speech going to the cloud.
- No dashboard login.
- Unsandboxed, voice-approved self-changes.
- Memories saved from overheard speech and silently dropped.
- Transcripts in logs.
- False "done" confirmations.
- No offline voice commands.

## Owner decisions applied
All 66 decisions are listed in [17](17-OPEN-QUESTIONS.md#decisions-made-by-the-owner-2026-11-03). The ones that shape the product most:
- **Listening:** Social mode by default, with opt-out consent. As with Ziggletron, room speech is transcribed by Groq (D-51); a local wake-word detector decides when Pip is addressed; a fast local question check gates chime-ins (D-52).
- **Hardware:** a USB speakerphone as mic and speaker, using its own mute button; SD card storage (no SSD).
- **Model:** Claude subscription only (via Claude Code), with a local fallback; local voice only.
- **Devices:** no Home Assistant. Pip is the device hub (MQTT + ESPHome; Eightree outlets via local Tuya). Google Calendar sync, ntfy phone notifications, local music.
- **Data:** memories default to household visibility; admins can read all memories (audited).
- **Delivery:** Python, built on the Pi side by side with Ziggletron. Parity first, split into M1a and M1b (safe self-change in M1b). Next: M0 measurements, then architecture.

## How to review (suggested order)

1. [00 Vision](00-PIP-PRODUCT-VISION.md): is this the assistant you want?
2. [17 Decisions and open items](17-OPEN-QUESTIONS.md): check the recorded decisions; the remaining items R-01…R-08 are resolved in M0.
3. [01 Decision matrix](01-FEATURE-DECISION-MATRIX.md): mark agreement per FEAT (rows with "Approval? Yes").
4. [02 Feature catalog](02-PROPOSED-FEATURE-CATALOG.md): strike or add PIP-NEW items.
5. [15 Roadmap](15-PRIORITIZED-ROADMAP.md): check M0/M1 scope.
6. The detailed specs (03–12), then [13 Workflows](13-USER-WORKFLOWS.md) and [14 Acceptance criteria](14-ACCEPTANCE-CRITERIA.md).

## Document index

| # | Document | Contents |
|---|---|---|
| 00 | [PIP-PRODUCT-VISION](00-PIP-PRODUCT-VISION.md) | Identity, audience, qualities, differentiation |
| 01 | [FEATURE-DECISION-MATRIX](01-FEATURE-DECISION-MATRIX.md) | KEEP/IMPROVE/REDESIGN/REPLACE/MERGE/REMOVE/REVIEW for FEAT-001…089 |
| 02 | [PROPOSED-FEATURE-CATALOG](02-PROPOSED-FEATURE-CATALOG.md) | Retained features + PIP-NEW-001…090 |
| 03 | [CONVERSATION-AND-PERSONALITY](03-CONVERSATION-AND-PERSONALITY.md) | Modes, consent, turn-taking, interruptions, speaker ID, personality |
| 04 | [SPEECH-RECOGNITION-RESEARCH](04-SPEECH-RECOGNITION-RESEARCH.md) | Audio pipeline, provider research (verified 2026-11-03), vocabulary, fallback, TTS |
| 05 | [SPEECH-BENCHMARK-PLAN](05-SPEECH-BENCHMARK-PLAN.md) | Consent-based, reproducible benchmark |
| 06 | [MEMORY-AND-DATA-POLICY](06-MEMORY-AND-DATA-POLICY.md) | Categories, lifecycle, Memory Center, privacy, logs, migration of memories |
| 07 | [SMART-HOME-AND-INTEGRATIONS](07-SMART-HOME-AND-INTEGRATIONS.md) | Pip as device hub (MQTT, ESPHome, local Tuya), safe switches, commands, risk classes, verified execution, routines, music |
| 08 | [DASHBOARD-AND-TV-REDESIGN](08-DASHBOARD-AND-TV-REDESIGN.md) | Navigation, settings, tasks and lists, face, widgets, layouts |
| 09 | [SELF-KNOWLEDGE-AND-DIAGNOSTICS](09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md) | Registry, Q&A, decision trace, read-only inspection, diagnostics |
| 10 | [SAFE-SELF-MODIFICATION](10-SAFE-SELF-MODIFICATION.md) | Intake → proposal → sandbox → verification → approval → deployment → rollback → history |
| 11 | [SECURITY-AND-PERMISSIONS](11-SECURITY-AND-PERMISSIONS.md) | Roles, confirmation tiers, remote access, scoping, logs, credential migration |
| 12 | [RELIABILITY-AND-OFFLINE](12-RELIABILITY-AND-OFFLINE.md) | Action lifecycle, latency budgets, offline, health, Pi deployment |
| 13 | [USER-WORKFLOWS](13-USER-WORKFLOWS.md) | PW-01…19 |
| 14 | [ACCEPTANCE-CRITERIA](14-ACCEPTANCE-CRITERIA.md) | AC-* measurable criteria |
| 15 | [PRIORITIZED-ROADMAP](15-PRIORITIZED-ROADMAP.md) | M0, M1a, M1b, M2–M4 with dependencies |
| 16 | [ADDITIONAL-RECOMMENDATIONS](16-ADDITIONAL-RECOMMENDATIONS.md) | PIP-IDEA-001…016 and simplifications |
| 17 | [OPEN-QUESTIONS](17-OPEN-QUESTIONS.md) | Decisions D-01…D-66; open items R-01…R-08 for M0 |
| 18 | [REQUIREMENTS-TRACEABILITY](18-REQUIREMENTS-TRACEABILITY.md) | FEAT → PIP-NEW → AC; problem coverage |

## Conventions
- `FEAT-nnn` = an original Ziggletron feature (historical). `PIP-NEW-nnn` = a proposed new capability. `PIP-IDEA-nnn` = an optional idea, not proposed. `AC-*` = an acceptance criterion. `PW-nn` = a proposed workflow.
- Evidence tags for external research: [V] vendor, [3P] third party, [I] independent benchmark, [M] prior knowledge not re-checked. Discovery tags ([R], [T], [S], [D], [X]) are as defined in [PRODUCT-OVERVIEW](../01-discovery/PRODUCT-OVERVIEW.md#evidence-tags-used-throughout).
