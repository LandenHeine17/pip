# Pip 2.0 — Product Vision and Identity (PROPOSAL)

> Proposed behaviour. For what exists today see [../01-discovery/PRODUCT-OVERVIEW.md](../01-discovery/PRODUCT-OVERVIEW.md). "Ziggletron" refers only to the original product.

## 1. One-sentence identity

Pip is a companion that lives in one room. It hears when it is being spoken to, takes real actions, says what it knows and what it just did, and stays quiet the rest of the time. Its silence should feel like good manners, not like being switched off.

## 1a. Who Pip serves

- **The household:** the owner and other adults who share the room (owner decision D-03). Each has their own voice profile, memories (household-visible by default; admins can read all), an opt-out choice for Social mode, and a dashboard account.
- **Guests:** occasional visitors. Pip is helpful to them for general questions and timers, but does not remember them and does not send their speech to the cloud.
- **The owner as maintainer and developer:** wants to understand, configure and extend Pip without editing files. Pip explains itself ([09](09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md)) and changes itself safely ([10](10-SAFE-SELF-MODIFICATION.md)).

## 1b. Defining qualities, as testable behaviours

| Quality | Concrete behaviour | Evidence |
|---|---|---|
| Natural | Name anywhere in the sentence; follow-ups without the name; interrupt, pause, correct | [03 §3](03-CONVERSATION-AND-PERSONALITY.md#3-interruptions) |
| Context-aware | Knows who is speaking, the mode, recent turns, room sensors (M3) | 03 §4, 07 §6 |
| Helpful, not intrusive | Speaks unprompted only past local gates, within budgets, and backs off when ignored | 03 §2 |
| Reliable when services fail | Offline timers, alarms, stop and volume; local STT fallback | [12 §3](12-RELIABILITY-AND-OFFLINE.md#3-offline-capabilities-pip-new-044-081) |
| Transparent | Never claims success it can't verify; explains decisions from its trace | 12 §1, 09 §4 |
| Respects privacy | Consent per person; nothing unaddressed leaves the house without consent; no transcript archive | 03 §1, [06](06-MEMORY-AND-DATA-POLICY.md) |
| Self-aware | Explains features, configuration and code from current sources | 09 |
| Self-improving, safely | Proposes changes; humans approve and deploy | 10 |

## 2. Personality (proposed defaults, editable)

Carried over from Ziggletron's personality file (FEAT-022): warm, a bit cheeky, curious, short spoken replies, no assistant clichés.

Changes:
- **Honest about uncertainty, out loud.** It says "I think that was Sam" rather than assuming. If it is unsure what someone meant, it asks one short question instead of guessing. For actions it cannot easily undo, such as devices, messages or deletions, it always asks.
- **Says the outcome, not the intent.** "Done", "Forgotten" or "Volume 40%" only after the action has actually succeeded (this fixes P-03 and P-12). If it fails, it says so and why.
- **Knows itself.** It can describe its own abilities, current mode and limits from a list kept by the product itself, not by reading its own source code (FEAT-089). For example: "I'm in Private mode, so I'm not following the room right now."
- **Proactive, but rarely.** Unprompted speech is judged by usefulness, not by a frequency target (see the Part 4 document).

## 3. Concrete differences from Alexa, Google Assistant and AI chat apps

| Behaviour | Typical voice assistant | Typical AI chat app | Pip |
|---|---|---|---|
| Being addressed | Fixed wake word at the start | Typing | Its name anywhere in a sentence, plus follow-ups without the name. The listening mode decides how much of the room it follows |
| Joining in | Never | Never | Optional and consent-gated. At most one short line, only when it would actually help |
| Who is speaking | Sometimes, by enrolled voice | Signed-in account | Labels with a confidence level. A voice is never enough on its own to approve admin or dangerous actions |
| Memory | Opaque or none | Chat history | No transcript archive. Memories are proposed, approved, can be inspected and edited, and each has an owner |
| Transparency | Minimal | Chat log | Dashboard shows why it spoke, what it sent where, and what each action did |
| Without internet | Mostly fails | Fails | Timers, alarms, reminders, device control and stop/volume still work through a local command path |
| Extending it | Vendor skills | Plugins | Permission-scoped plugins. Self-changes are proposed, sandboxed, and the code diff is reviewed by a person |
| Display | Optional screen UI | None | Expressive TV face and voice-arranged dashboards (kept from Ziggletron) |

## 4. Kept, redesigned or dropped (headline only; full table in [01-FEATURE-DECISION-MATRIX.md](01-FEATURE-DECISION-MATRIX.md))

- **Keep:** the TV face and its reactions, including weather clothing; name-anywhere addressing; follow-up window; barge-in with a sub-100 ms stop; alarms that keep ringing until someone responds; earned alarms; honest speaker labels; editable personality.
- **Redesign:**
  - All room speech going to the cloud → mode-dependent processing.
  - Join checks → local pre-filtering before any model call.
  - Memory → categories, owners and approval.
  - Self-change → sandboxed, with diff review.
  - Dashboard → accounts and roles.
  - Alarm acknowledgement → must come from someone actually in the room, not a TV saying "okay" (U-02).
  - "Stop listening" → can be undone by voice through a local wake word (U-03).
- **Reconsider or drop:** greeting any voice after 20 quiet minutes (U-01); unprompted photos with no switch (U-05); exact Ziggletron timings (8 s follow-up, 1.2 s join pause). These become starting values to tune with the benchmark, not requirements.
