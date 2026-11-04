# Pip 2.0 — Smart Home and Integrations (PROPOSAL)

> Ziggletron today: no smart-home control. Only a documented "lights via a bridge" plugin pattern ([EXTERNAL-INTEGRATIONS §5](../01-discovery/EXTERNAL-INTEGRATIONS.md#5-extension-integrations)). Volume and microphone are its only device controls.
> Requirement IDs PIP-NEW-036…043 are in [02-PROPOSED-FEATURE-CATALOG.md](02-PROPOSED-FEATURE-CATALOG.md#e-smart-home-and-routines). The general action lifecycle that §5 applies to devices is defined in [12-RELIABILITY-AND-OFFLINE §1](12-RELIABILITY-AND-OFFLINE.md#1-action-lifecycle-no-false-confirmations-pip-new-039).
> **Roadmap note:** the owner chose parity with Ziggletron first, so smart home and music are **Milestone 3** ([15](15-PRIORITIZED-ROADMAP.md)).

## 1. Platform choice: Pip is the device hub (owner decision D-18)

> **Owner decision (2026-11-03): no Home Assistant.** Everything runs on the Pi, and Pip talks to devices directly. Earlier drafts recommended Home Assistant as the hub. That recommendation is withdrawn.

To avoid writing a new integration per device, Pip supports a **small number of established protocols**. Devices then describe themselves:

| Path | Devices | How a new device is added | Notes |
|---|---|---|---|
| **Local MQTT broker on the Pi** | Anything that speaks MQTT: ESPHome, Tasmota and custom ESP32 firmware | The device publishes a **self-describing discovery message** in an established MQTT discovery format (the same format ESPHome and Tasmota already emit [V]). Pip adds it to the registry as "new, not yet allowed" | Retained config messages, so devices reappear after restarts. The broker is bound to the local network, with per-device credentials |
| **ESPHome devices** | Owner's ESP32 builds | Via MQTT discovery (preferred, one path), or ESPHome's native API if a feature needs it | No per-device Pip code |
| **Local Tuya protocol** | **Eightree smart outlets** (believed to be Tuya/Smart Life based, **unverified**) | One-time extraction of each outlet's local key (through a free Tuya developer account), stored in the credential vault. After that, control is **local, with no cloud at runtime** | Outlet firmware updates can change the protocol version or key; Pip reports "can't reach" rather than failing silently. Feasibility checked in M0 |
| **Pi GPIO** | Mute button + LED (PIP-NEW-084), optional PIR presence sensor | Configured in Settings | Low voltage only |

**Allow-list rule:** every discovered device starts as **not allowed**. An admin allows it on the Devices page and sets its name, room, owner and risk class. Pip can see and control only allowed devices.

### 1.1 What being the hub adds to Pip's scope
- Pip keeps the authoritative **device state** (last reported state, availability, last-seen time), from MQTT state topics or Tuya status polling.
- **Scenes** (named sets of device states) are defined in Pip and used by routines (§7).
- An optional **Zigbee** dongle later (via a Zigbee-to-MQTT bridge) would plug into the same MQTT path. Not in scope now.

### 1.2 Custom hardware (ESP32 and sensors) without new integrations

- **ESPHome firmware** with MQTT discovery enabled (the default when MQTT is configured [V]): the device appears on Pip's Devices page automatically. No Pip code per device.
- **Other firmware:** publish the same discovery format. Same result.
- Pip-specific extras (e.g. "this sensor means someone is at the desk") are **metadata in Pip's device registry**, not code.
- The legacy PIR presence sensor (FEAT-049) is kept as a GPIO sensor, or becomes an MQTT sensor on an ESP32.

### 1.3 Traditional physical switches: safe options only

| Approach | Notes |
|---|---|
| **Certified smart wall switches or dimmers** (Zigbee, Z-Wave, Matter, Wi-Fi) | Installed per local electrical code, **by a qualified electrician where required** |
| **Certified in-wall relay modules** behind existing switches (Shelly-type) | Keeps the existing switch working. Same installation caveat |
| **Smart bulbs or certified smart plugs** | No wiring at all. The easiest start |
| **Mechanical button pushers** on the existing switch | Non-invasive; renter-friendly |
| **DIY ESP32 builds** | **Low voltage only** (LED strips at 5–24 V, sensors, relays switching low-voltage loads). Pip's documentation will **not** include DIY mains-voltage relay builds |

## 2. Device registry (Pip's view of devices)

For each allowed device, Pip keeps: friendly name · aliases ("desk light", "my lamp") · room · owner (optional: "Alex's desk light") · device type · **risk class** (§4) · whether it may be used unprompted by automations.
- Names come from discovery messages and are editable on Pip's dashboard, along with aliases. They are and fed into the STT vocabulary and the pronunciation list.
- Pip's room is configured ("Pip lives in the living room"), so "the lights" defaults to that room.

## 3. Contextual commands

### 3.1 Resolving what a request means

| Request | Resolution |
|---|---|
| "Turn off **my** desk light" | Owner = the identified speaker ("sure" or "probably" is enough for class A). Unknown speaker plus several desk lights → ask "Whose desk light?" |
| "Make it a little brighter" | "It" = the device most recently acted on or mentioned in the last 2 minutes (temporary context). "A little" = +15% brightness (configurable). No recent device → ask |
| "Set the room lights to something warmer" | All lights in Pip's room that support colour temperature, about −500 K each (bounded by device range). Lights without colour temperature are left alone, and Pip says so if any were skipped |
| "Turn everything off except the lamp" | "Everything" = class A devices in Pip's room only (never the whole house, never class B/C). "The lamp" resolved by alias. Pip states the scope **before** acting when more than 3 devices are affected: "Turning off 5 lights and the fan, leaving the lamp on." Then acts; "undo" works for 60 s |
| "Run movie night" | A Pip scene or routine by name |

### 3.2 Ambiguity policy

- **Ask, don't guess,** when a request could match two or more devices with different effects, or when the effect could be unwelcome (it's dark and someone else is in the room). The question is one short sentence listing the options ("The desk lamp or the floor lamp?").
- **No ambiguity question for low-risk, single-match requests.** Speed matters for "lights off".
- **Room scope:** never act outside Pip's room unless the room is named ("turn off the kitchen lights").

### 3.3 Offline path

Simple device commands ("turn on/off X", "brighter/dimmer", "set X to N percent", scenes by name) are matched **locally** against the registry with a small grammar. They work when the internet or the model provider is down, because the broker, the Tuya control and the registry are all local on the Pi. This also cuts latency for the commonest commands (fixes R-03 for devices).

## 4. Risk classes

| Class | Examples | Voice behaviour | Unprompted / automation use |
|---|---|---|---|
| **A: low, reversible** | Lights, lamp plugs, scenes made only of class A devices | Act when resolved; announce the scope when more than 3 devices | Allowed if enabled per device |
| **B: comfort or cost** | Thermostat, fans, media, plugs with unknown loads, blinds | Spoken confirmation for big changes (heating more than ±3 °C); otherwise act | Only through explicit automations |
| **C: safety or security** | Locks, garage doors, alarm systems, plugs powering heaters or ovens, water valves | **Not by voice alone.** Voice "sure" plus confirmation on the person's phone or the dashboard. Some actions (unlocking) can be set to dashboard-only | Never |

TV, radio or web content cannot trigger class B/C actions. Class A actions from unaddressed speech never happen (addressing is a precondition). This limits prompt-injection impact (S-05).

## 5. Verified execution

- Each device request passes through: **interpretation → permission check (risk class, identity) → execution → verification → feedback**. Pip's wording distinguishes "command sent" from "confirmed": a device that only accepts commands (no state reporting) gets "Sent. I can't confirm that light's state".
- After every device command, Pip **waits for the device's reported state change** (MQTT state topic or Tuya status; timeout about 3 s) before saying "done".
  - Confirmed → brief confirmation, or simply the visible effect (Pip can stay silent for "lights off" if the light visibly went off; a personality setting).
  - Not confirmed → "The desk light didn't respond. It might be offline." No false success (the lesson from P-03/P-12).
  - Partially confirmed (group command) → "Done, except the hallway lamp, which didn't respond."
- Device availability (unavailable or offline entities) is known before acting, so Pip can say so immediately.
- Every device action goes to the operational log (device, action, requested by, outcome; no transcript).

## 6. Environmental awareness and events

- Pip subscribes to a **configured list** of sensor and event topics: doorbell, door or window left open, temperature, humidity, CO₂, air quality, leak, presence. Pip does not replace a certified smoke alarm.
- **Reactions are rules, written by the household** on the dashboard (e.g. "if the front door is open more than 10 minutes, tell whoever's in the room"). Pip can propose rules ("want me to tell you when the washer finishes?") that take effect only after approval.
- Announcements respect quiet hours, except safety events (smoke, leak, CO), which always announce.
- Sensor data is context the model receives for relevant questions ("is it cold in here?" → reads the room sensor rather than guessing). Weather on the TV and spoken weather use the same source (fixes P-08).
- Device names and attributes from discovery messages are treated as **data, never instructions** in prompts.

## 7. Routines (PIP-NEW-041)

### 7.1 What a routine is
A **named, editable list of steps**, readable as plain text on the dashboard:

> **Bedtime** (say "Pip, bedtime" or "goodnight, Pip")
> 1. Turn off living-room lights except the hall lamp (dim to 10%)
> 2. Set the TV to the night layout and dim the face
> 3. Read tomorrow's first event and any alarm set
> 4. Switch to Private mode until 07:00

Steps may be: device actions (any risk class, with that class's confirmation rules), Pip scenes, Pip actions (mode, TV layout, volume, announcements), information read-outs (weather, calendar, tasks), timers and reminders, and short waits.

### 7.2 Proposed starter routines (disabled until reviewed)

| Routine | Default steps |
|---|---|
| Good morning | Lights to a warm morning scene; weather, today's events and tasks; TV morning layout |
| Bedtime | As above |
| Leaving the room | Room lights off (class A only); Pip to Private mode; TV face dims |
| Study mode | Desk light bright and cool; joining off; TV focus layout with a timer |
| Movie mode | Lights dim; joining off; follow-up window shortened; TV face tiny |
| Quiet mode | Joining off; announcements silent except alarms and safety events; volume low |

### 7.3 Rules
- **Preview before saving** ("This will turn off 5 lights and switch me to Private mode"), and **dry-run** from the dashboard.
- **Creation by voice:** "Pip, make a routine called reading time that dims the lights and turns joining off." Pip reads it back, and it is saved only after "yes". New routines that contain class B/C steps need dashboard approval.
- **Triggers:**
  - A spoken name (default).
  - A dashboard button.
  - A time.
  - A sensor event or sunset/sunrise (computed locally from the configured location).

  **Time and event triggers are off until approved on the dashboard**, and every automatic run is announced or logged ("Ran Good morning at 7:00").
- **Partial failure:** report which steps failed, don't roll back completed class A steps, and stop before any class B/C step if an earlier step failed.
- **Undo:** "Pip, undo that" reverses the class A steps of the last routine for 60 s.
- Routines and scenes are stored in Pip and can be exported.

## 8. Dashboard

**Devices** tab: newly discovered devices waiting to be allowed; allowed devices grouped by room, with live state, last seen, alias and owner editing, risk class and a "test" toggle; scenes; event and announcement rules. Status chips show the MQTT broker and the Tuya outlets as reachable or not, with the last error.

## 9. Music playback (FEAT-088 redesigned; owner decision D-20)

- Source: **local music files** (a folder on the Pi or a USB drive). Indexed by artist, album, title and genre tags.
- Voice: "play some jazz", "play the album …", pause, resume, skip, stop, volume. Works offline.
- Output: Pip's speakerphone. Music **ducks** while Pip listens after its name, or speaks. Because music comes out of the same device, the speakerphone's echo cancellation can remove it from the mic signal (to be verified in M0).
- The TV gets a now-playing widget.
- No streaming services (owner decision). Milestone M3.

## Sources
- [ESPHome MQTT component (discovery)](https://git.lipovcan.cz/Upstream/esphome-docs/src/commit/241ebd7ec9d44d6eee153a40d2cde3c87a01affe/components/mqtt.rst)
- Tuya local-protocol details: to be verified from the chosen library's documentation in M0 (not yet checked)
