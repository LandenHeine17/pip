# Pip 2.0 — Dashboard and TV Redesign (PROPOSAL)

> Ziggletron today: one live page with password-free viewing and most controls, an admin password for the rest, settings only in files, and a TV page that receives the full event stream. See [UI-AND-SETTINGS](../ziggletron-discovery/UI-AND-SETTINGS.md), FEAT-052–072, S-01/S-02, U-07–U-09, P-09, V-07.
> Requirement IDs: PIP-NEW-046–048, 052–053, 056, 060–063. Access control is in [11](11-SECURITY-AND-PERMISSIONS.md).
> **Owner decision:** the dashboard will be used **away from home**, so sign-in and secure remote access are M1 requirements.

## 1. Navigation

The brief's candidate structure has 8 areas. Proposed adjustments:

| Area | Proposal | Who sees it |
|---|---|---|
| **Home** | Keep. Status, mode and privacy chip, next alarms, ringing banner, health summary, recent important events. The landing page on phone | Members |
| **Conversation** | Keep, but **merge into Home as a panel on wide screens** and a tab on phone. Text input, current processing stage, latency, temporary context (what Pip currently "remembers" from this conversation, with a **Clear** button) | Members (feed hides lines from people who haven't consented) |
| **Memory** | Keep (Memory Center, [06 §5](06-MEMORY-AND-DATA-POLICY.md#5-memory-center-dashboard)) | Members (scoped) |
| **Home & Devices** | Keep; **hidden until the first device is allowed (M3)**. Includes discovery inbox, scenes, routines, music | Members |
| **Calendar & Tasks** | Keep: timers, alarms, reminders, calendar, shopping, to-dos | Members |
| **Appearance & Personality** | Keep: face, voice, rate, reactions, TV layouts, joining preferences | Members view; admins edit personality |
| **Settings** | Keep, with **Privacy as the first sub-page** (modes, consent per person, retention, delete/export). Then Providers, Audio, Credentials, Permissions & Accounts, Integrations | Admins (members see their own privacy and consent) |
| **Developer** | Keep: logs, health, latency, resources, version, architecture docs, **Changes** (self-improvement requests, proposals, diffs, test results, deployment history) | Admins |

Reasoning: privacy controls must be easy to find, so Privacy is first under Settings and also appears as a chip on Home. Seven or eight visible areas is acceptable on phone with a bottom bar of 5 (Home, Calendar & Tasks, Memory, Devices, More).

## 2. Settings without file edits (PIP-NEW-053)
- Every user-facing Ziggletron setting ([UI-AND-SETTINGS §4](../ziggletron-discovery/UI-AND-SETTINGS.md#4-settings-configuration-file)) is editable in Settings, including the 5 hidden ones.
- Validation as today (FEAT-080), shown inline. Most changes apply live; ones that need a restart say so and offer "restart when quiet".
- Every change is recorded in the audit trail, with undo for the last change of each setting.
- Microphone and speaker selection with a live level meter and a test.
- Configuration export and import (without credentials).

## 3. Calendar and tasks
- **Timers and reminders:** create, edit, snooze and cancel on the dashboard. Reminders get a creation form (U-09). Recurrence: daily, weekdays, weekly on chosen days, monthly by date, every N days (PIP-NEW-046).
- **Calendar:** month/week/day views; repeat on the form (P-05); reminder offset as a free value.
- **Shopping list (PIP-NEW-047):** one shared list; "Pip, add milk" → appears on the dashboard and TV within 1 s; check-off from either; offline voice add for common items.
- **To-dos and shared tasks (PIP-NEW-048):** per-person and household lists; assignee; optional due date (creates a reminder if asked); "what's on my list?".
- **Natural-language editing (PIP-NEW-050):** "move the dentist to Thursday at 4", "make the pasta timer 15 minutes", "take bread off the list". Pip confirms the change it made.
- **Calendar sync (PIP-NEW-049, M3, evaluate):** two-way with one external calendar. Open question: which provider ([17](17-OPEN-QUESTIONS.md)). Synced events are data, never instructions.

## 4. The face (PIP-NEW-062)
Keep the canvas face, its state colours, weather clothing, the six-seven reaction and screen care. Proposed improvements:

| Improvement | Detail | Guardrail |
|---|---|---|
| Expressions mapped to real states | Curious (offer pending), concentrating (long task), apologetic (failure), pleased (thanks), sleepy (quiet hours) | Each is triggered by an observable event, never by invented emotions |
| Listening animation | Eyes turn toward the active speaker direction if the mic reports direction of arrival; otherwise a subtle "attending" pose while the address window is open | — |
| Speaking animation | Mouth driven by audio energy, as today, with smoother shapes | — |
| Mode indicator | Small icon: Private (closed ear), Room-Aware, Social; red dot while benchmark recording | Always visible |
| Customisation | Colours (except state colours), eye style, accessory set, face size per layout | State meaning stays recognisable |
| Calmness | Idle motion reduced after 10 minutes without speech; reactions at most once per 30 s, except alarms | Not distracting |

## 5. TV widgets (PIP-NEW-060)
Every widget is bound to **live data**, never a snapshot:
- Clock
- Weather (same source as speech)
- Calendar/upcoming
- Timers with countdowns
- Shopping list
- Tasks
- Devices (M3: chosen entities with live state)
- System health
- Music (if FEAT-088 is revived)
- Notes (a live named note, editable by voice and dashboard)
- Images
- Reminders
- Captions

Widgets get only the data they display (PIP-NEW-056). A conversation widget shows only lines addressed to Pip from consenting people.

## 6. Layout editing (PIP-NEW-061)
Addresses P-09 (frequent "no room" failures):
1. **Templates** (Default, Morning, Kitchen, Focus, Night, Photo) as starting points.
2. **Validation before applying:** if a voice edit doesn't fit, Pip proposes a fix ("No room for the weather box at that size. I can shrink the clock to make room, okay?") instead of failing.
3. **Preview** on the dashboard; on the TV a 5-second preview with "keep it?".
4. **Undo** ("Pip, undo that" or dashboard undo) for the last 10 layout changes.
5. **Drag-and-drop** editor on the dashboard (M3).
6. Named saved layouts, as today; the limit of 20 is kept unless storage requires otherwise.

## 7. Pictures and photos
- Requested drawings and photos: **keep** (FEAT-059, 061).
- Unprompted drawings: **recommend off by default and REVIEW** (cost, 1–2 minute latency, frequent failures P-10). Unprompted photos: a separate switch (PIP-NEW-063), off by default.
- Both are independently configurable, with daily caps as today.
