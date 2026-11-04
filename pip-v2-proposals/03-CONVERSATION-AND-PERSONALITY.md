# Pip 2.0 — Conversation and Personality (PROPOSAL)

> Covers listening modes, turn-taking, interruptions, speaker recognition (§1–4) and personality (§5). Requirement IDs (PIP-NEW-018…027) are in [02-PROPOSED-FEATURE-CATALOG.md](02-PROPOSED-FEATURE-CATALOG.md#c-conversation-and-personality).
>
> **Owner decisions (2026-11-03):**
> - **Social is the default mode.**
> - The household is the owner plus other adults (no children); guests are occasional.
> - Cloud budget is under $10/month, so in Social mode **room speech is transcribed on the Pi**, and only text context reaches the model provider, under the consent rules in §1.2.
> - **Consent is opt-out for household adults** (owner decision D-13). Guests are never included. Admins can read all memories (D-15, see [06](06-MEMORY-AND-DATA-POLICY.md)).

> Ziggletron today: every utterance is transcribed (cloud when enabled). Join checks send the last 40 lines to the model every 15–120 s depending on chattiness. Interruptions are classified after transcription. See [FEAT-003–005, 017–021](../01-discovery/COMPLETE-FEATURE-CATALOG.md), [W2–W5](../01-discovery/USER-WORKFLOWS.md), and V-01/V-02 in [KNOWN-PROBLEMS](../01-discovery/KNOWN-PROBLEMS-AND-LIMITATIONS.md#3-privacy).

## 1. Listening modes

A single, visible mode setting replaces Ziggletron's "Joins conversations" dropdown. The current mode shows on the dashboard header and as a small icon on the TV face. Pip can say which mode it is in when asked.

| | **Private** | **Room-Aware** | **Social** |
|---|---|---|---|
| How addressing is detected | Local name spotter only | Local name spotter, plus local transcript context | Same as Room-Aware |
| Unaddressed speech | Discarded once VAD and the spotter have run. Not transcribed | **Transcribed locally**, kept in RAM for about 5 minutes, never sent | May be transcribed by a cloud provider and analysed by the model, **only while consent rules (§1.2) allow** |
| Follow-up without the name | Yes (window) | Yes, smarter (§2.3) | Yes |
| Joining in unprompted | Never | Rarely, and only through **local** triggers. Offers rather than answers (§2.2) | Yes, by usefulness score (§2) |
| What goes to the cloud | Addressed requests only (audio to STT; text to the model) | Addressed requests. Optionally a short slice of recent room context (setting *"Include recent room context when I ask Pip something"*, **off by default**) | Room transcript and context, within the consent rules |
| Persistent recording | None | None | None |
| Model usage | Lowest | Low | Highest. Cost shown |

### 1.1 Is Room-Aware practical on a Pi 5?

- **Continuous local transcription:** plausible. Moonshine's streaming models report Pi 5 latency of about 0.8 s (Medium) and very low real-time factors (Tiny) [vendor/3P figures]. CPU load alongside speaker ID, sound tagging, TTS and the TV kiosk is **unmeasured**. This is the main risk, and it is in the benchmark plan as "Pi resource use".
- **Local understanding of the conversation:** a local LLM on a Pi 5 is too slow to judge every utterance in real time (an assumption to confirm; a few tokens per second for 1–3B models is typical [M]). Room-Aware therefore uses **rules and small classifiers**, not a local LLM: question detection, "does anyone know", topic words matching Pip's abilities, Pip's name mentioned, a recognised request phrasing without the name.
- **If measurements fail:** Room-Aware falls back to Private behaviour plus a smarter follow-up window. The feature list stays honest about it.

### 1.2 Consent rules for Social mode (owner decision D-13: opt-out)

- **Household adults are included by default and can opt out** at any time. The choice is in their profile: "cloud analysis of my unaddressed conversation: included / opted out".
- **Everyone is told.** Opt-out only works if people know about it:
  - At voice enrolment, Pip says that Social mode is on, what it sends (text of room conversation, not audio) and how to opt out.
  - The same notice is the first card on each member's dashboard until they acknowledge it.
  - The TV mode icon shows when Social analysis is active.
- **Voice opt-out:** "Pip, leave me out" (a recognised voice) opts that person out immediately; "Pip, include me again" reverses it. Both are confirmed out loud and recorded in the audit trail.
- **Guests and unknown voices are never included.** While an unrecognised voice has been heard in the last 15 minutes, or an opted-out person is speaking, **Social behaves as Room-Aware**. The dashboard shows why ("Social paused: a guest is here").
- A spoken "**Pip, private please**" switches the whole room to Private immediately, for a set time (default 1 hour) or until switched back on the dashboard. It works offline (local spotter plus keyword) and from any voice.
- Quiet hours suppress joining in all modes (kept from Ziggletron).

### 1.3 Things that never happen in any mode
- No raw audio written to disk (except opt-in benchmark sessions).
- No room transcript in logs (fixes V-05).
- The TV page does not receive room transcript events at all (fixes V-07). It receives only what it displays.

## 2. Turn-taking: when Pip speaks unprompted

The goal is fewer, better contributions. Pip is comfortable staying silent.

### 2.1 Hard gates (all must pass; checked locally, no model call)

1. Mode allows joining; not quiet hours; Pip not muted from joining ("Pip, not now" mutes joining for 30 minutes).
2. Nobody is speaking, and nobody started speaking during Pip's thinking time.
3. **Not media:** the sound classifier does not detect TV, radio or music speech as the source. Where a smart-home media player is reported "playing" in the room, unaddressed speech is assumed to be media unless a known voice is recognised.
4. **Recent-contribution budget:** at least N minutes since Pip's last unprompted line (Rarely 20 / Sometimes 8 / Often 3 minutes; starting values), and at most M per hour.
5. **Back-off:** if Pip's last unprompted line got no response (nobody replied or acknowledged within 10 s), the next gap doubles, up to 1 hour. A reply or laugh resets it.
6. **Private-looking conversation:** two or more people talking with no question to the room, no Pip-relevant topic, and lowered voices (energy) → no join.

### 2.2 Opportunity triggers (at least one needed before a model is consulted)

| Trigger | Local signal | Room-Aware behaviour | Social behaviour |
|---|---|---|---|
| Direct invitation | "What do you think, Pip?", "right, Pip?" | Answer (counts as addressed) | Answer |
| Question to the room Pip could answer | Question form plus "anyone know", "I wonder", "what's the…" | **Offer:** "I can look that up if you want." Nothing is sent unless they say yes | Model is consulted with context; answers if useful |
| Something Pip manages | Mentions of timers, alarms, calendar, shopping list, devices ("is the oven timer still going?") | Answer from local state (no cloud needed) | Same |
| Factual dispute | "No, it was 1998", "I'm sure it's…" | Offer | Consult the model |
| Sound event | Doorbell, smoke alarm, baby crying | Brief local remark only if useful ("That was the doorbell") and configured | Same |

The model is asked "anything useful to add?" only after a gate pass **and** a trigger. That alone should cut model calls by an order of magnitude compared with Ziggletron's timer-based checks.

### 2.3 Follow-ups

- Keep the follow-up window. Start at 8 s (Ziggletron's value), but **only for the person Pip was talking to** when their voice is recognised. Another voice inside the window is not treated as addressed unless it is clearly about the same topic. This fixes room chatter being answered (FEAT-005 issue).
- The window is extended while that person is still speaking.
- No window after greetings, announcements or alarms.

### 2.4 Greeting
Welcome-back greetings require a **recognised household voice** (not any voice, as now; fixes U-01), at most once per person per 4 hours, and can be switched off.

## 3. Interruptions

All of these are handled locally, with no model call, unless stated otherwise.

| Situation | Detection | Behaviour |
|---|---|---|
| **"Stop" / "quiet" / "never mind"** | Local keyword detector, from any voice | Audio stops (<100 ms). Reply recorded as cut off. No model call |
| **"Wait" / "hold on"** | Local keyword | **Pause**, not stop. Pip holds the remaining reply for 30 s. "Go on" resumes; anything else is a new turn and the held reply is dropped |
| **"Actually…" / "no, I meant…"** | Local keyword at the start of speech | Stop speaking; extend end-of-utterance patience (expect a correction); treat the next utterance as **a correction of the previous request**. If an action already ran, Pip amends it rather than creating a second one ("set a timer for 10… actually 15" → the same timer becomes 15 minutes; it says "Changed it to fifteen") |
| **A new question** | Speech over Pip that is not a stop/wait word | Stop; answer the new question; the old reply is marked cut off |
| **Someone correcting Pip** ("That's wrong", "no, it's Tuesday") | Model, as part of the next turn | Acknowledge briefly, correct, don't argue. If the correction concerns a stored memory, offer to update it (memory inbox) |
| **Changing a request mid-sentence** | Adaptive endpointing keeps the whole utterance | The model receives the full utterance ("ten, no, twelve minutes") and acts on the final intent |
| **Another person starts talking while Pip speaks, not to Pip** | Voice is not the addressee, no stop word | **Duck** Pip's volume immediately. If they keep talking for more than about 1.5 s, pause. If they were talking to someone else, Pip resumes after they finish or drops a non-essential reply ("Sorry, go ahead") |
| Alarm ringing | Local acknowledgement keywords **plus** a requirement that the voice is not media (§2.1 gate 3) | Stops normal alarms. A TV saying "okay" no longer stops it (fixes U-02) |

## 4. Speaker recognition

### 4.1 What it is for
Personalisation (names, which memories to use), targeted reminders, and rough presence. **Never sufficient authorisation by itself** (§4.4).

### 4.2 Enrolment and profiles

| Topic | Proposal |
|---|---|
| Enrolment | Two paths: (a) by voice ("Pip, I'm Alex"), kept from Ziggletron; (b) guided on the dashboard or phone: read 5 short phrases, about 30 s total, giving a better first profile. The person record is created first, by an admin |
| Confidence labels | Keep Ziggletron's three levels (Alex / Alex? / Someone). Thresholds are re-tuned on the new mic. Pip uses "Alex?" only for low-stakes personalisation |
| Corrections | Corrections persist across restarts (fixes P-07). Line voiceprints are kept for 24 hours, not 200 lines, then discarded. Any household member can correct their own lines; admins can correct anyone's |
| Auto-learning | Kept, with the same conservative rules. Paused while media is playing |
| Multi-speaker | Labels per utterance. Overlapping speech is labelled "Several people" rather than forced onto one person |
| Model change | Profiles tied to a model version. On change, Pip re-enrols people automatically from their next confident lines where possible |

### 4.3 Guests and expiry

- **Guests:** an unrecognised consistent voice gets a temporary in-memory label ("Guest 1") for the session, never saved, discarded after 2 hours of silence or on restart.
- Saying "I'm Sam" as a guest gives a **session-only** profile. Keeping it requires Sam's explicit "yes" to "Should I remember your voice?" *and* an admin's approval on the dashboard. It is then stored with an expiry (default 90 days, renewable).
- **Expiry for everyone:** profiles not recognised for 180 days are flagged for review on the dashboard and deleted after 30 more days unless kept. This fixes V-04 (17 profiles, guests included, kept forever).
- The voice-profile list shows each profile's creation date, consent record and expiry.

### 4.4 Identity and authorisation

| Operation class | Example | Needs |
|---|---|---|
| Personal, low-stakes | "What's on my calendar?", "remind me" | Voice "sure" (or "probably", for non-sensitive data) |
| Sensitive information | Reading out someone's private memories, messages | Voice "sure" **and** no unrecognised voices present, or confirmation on the person's phone/dashboard |
| Household-affecting | Texting, unlocking, disabling alarms, device class C ([smart home](07-SMART-HOME-AND-INTEGRATIONS.md#4-risk-classes)) | Voice "sure" plus explicit spoken confirmation; some actions dashboard-only |
| Administrative | Approving self-changes, changing settings, managing people and voices | **Signed-in dashboard account** with the admin role. Voice alone is never enough (replaces Ziggletron's voice-approved self-changes, S-04) |

Typed dashboard messages carry the identity of the signed-in account (they are anonymous in Ziggletron).


## 5. Personality

### 5.1 What stays from Ziggletron
The editable personality file (FEAT-022) stays: warm, a bit cheeky, curious, short spoken replies, fragments allowed, no assistant clichés. The product's mechanics (actions, labels, speakable output) stay separate from the personality text, so editing the personality can't break them.

### 5.2 Concrete personality behaviours (proposed defaults)

| Behaviour | Example |
|---|---|
| Says outcomes, not intentions | "Timer's on, ten minutes." only after the timer exists. "I couldn't set that, the time didn't make sense." on failure |
| Honest about uncertainty | "I think that was Sam?" when the voice is "probably" |
| One clarifying question, not several | "The desk lamp or the floor lamp?" |
| Comfortable with silence | Not joining is the default outcome. Pip never apologises for not speaking up |
| Brief by default; offers more | "Short version: yes. Want the details?" |
| Knows its state | "I'm in Social mode, but Jordan hasn't opted in, so I'm only half-listening." |
| Owns mistakes without grovelling | "My mistake, it's Tuesday." |
| No invented facts about itself | "I don't know why that failed; the logs only show a timeout." ([09](09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md)) |

### 5.3 Customisation
- Personality text is editable on the dashboard under Appearance & Personality ([08](08-DASHBOARD-AND-TV-REDESIGN.md)), with a preview ("how would Pip answer…?") before saving, and version history with undo.
- Sliders that map to written guidance: **verbosity**, **playfulness**, **how often to join** (the mode budgets in §2.1).
- The personality cannot grant permissions or change safety rules. It is style only.
