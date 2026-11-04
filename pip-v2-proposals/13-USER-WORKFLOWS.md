# Pip 2.0 — Proposed User Workflows (PROPOSAL)

End-to-end experiences for new and redesigned features. For Ziggletron's current workflows see [USER-WORKFLOWS](../01-discovery/USER-WORKFLOWS.md) (W1–W20). Spoken lines are examples, not fixed wording.

## PW-01 Asking a question in Social mode with an opted-out person present
1. Jordan (who has opted out) and the owner are chatting. Pip transcribes locally. Jordan's lines are excluded from anything sent to the model.
2. Owner: "Hey Pip, what time does the hardware store close?" The local spotter fires; the RAM pre-roll includes the whole sentence; the chime plays.
3. The addressed audio goes to the selected STT; the text goes to the model, with **no room context from Jordan**.
4. Pip answers. The follow-up window opens for the owner only.
5. Dashboard Home shows: "Social (limited: Jordan opted out)".

## PW-02 Joining in, done well
1. Two consenting members wonder aloud: "When did that bridge open… 1930-something?"
2. Local trigger: question to the room + factual. Gates pass: 9 minutes since Pip last joined, nobody is speaking, no media detected.
3. The model is consulted with the consented context and returns a short line.
4. 1.2 s of silence has held → Pip: "1937, I think. The Golden Gate, if that's the one."
5. Nobody responds within 10 s → back-off doubles the next gap. "Pip, not now" would mute joining for 30 minutes.

## PW-03 Interrupting and correcting
1. "Pip, set a timer for ten minutes." Pip: "Ten-minute timer…" (the timer is created and verified) "…on."
2. "Actually, make it fifteen." The local "actually" keyword marks a correction; endpointing patience is extended.
3. The **same timer** becomes 15 minutes. Pip: "Changed it to fifteen."

## PW-04 Stop and wait
1. Pip is reading a long answer. "Wait" (local keyword) → pause.
2. 12 s later: "Go on" → it resumes from the next sentence.
3. Later, "stop" → silence within 100 ms. The reply is marked cut off.

## PW-05 Offline timer
1. The internet is down. "Pip, timer for five minutes."
2. The local grammar matches; the timer is created, read back from storage (verified); Pip: "Five-minute timer, on." The TV countdown appears.
3. "Pip, what's the weather?" → "I'm offline right now. The last forecast I had, from 2 hours ago, said rain this afternoon."

## PW-06 Alarm with the TV on
1. A timer rings while the TV is showing a film in which a character says "okay, okay".
2. The media-speech detector flags it → the alarm continues.
3. The owner says "got it" → non-media voice + acknowledgement → stopped.

## PW-07 Memory suggestion and approval
1. Owner: "Pip, I always take my coffee black, by the way." The request was addressed.
2. Pip: "Want me to remember that?" "Yes." → saved as a *confirmed* preference, owner-private by default.
3. Later, Jordan says (in Social mode, not to Pip) "Sam's allergic to shellfish." **Not saved.** Pip may put a suggestion in Sam's and Jordan's inbox (if neither opted out), or nothing.

## PW-08 Reviewing imported memories
1. After migration, Memory → Inbox shows 58 "imported from Ziggletron" items.
2. The owner approves, edits, re-categorises or rejects each, or approves in bulk by category. Sensitive items require the subject.

## PW-09 Smart-home: ambiguous command (M3)
1. "Pip, turn on the lamp." Two lamps exist in the room → "The floor lamp or the desk lamp?" → "Desk." → state event confirms → "On."
2. "Bit brighter." "It" = the desk lamp → +15% → confirmed silently by the visible change.
3. "Turn everything off except the lamp." → "Turning off 4 lights and the fan, leaving the desk lamp on." → executes → "Done, except the hallway light, which didn't respond."

## PW-10 Running a routine (M3)
"Pip, goodnight" → the Bedtime routine runs its steps → "Night. Your first thing tomorrow is the dentist at 9." Mode switches to Private until 07:00, and the face dims.

## PW-11 "Why didn't you answer?"
1. "Pip, why didn't you answer me just now?"
2. Pip queries the decision trace for the last 2 minutes → "I heard speech at 3:12, but the name detector scored it below threshold, so I treated it as conversation. From across the room 'Hey Pip' works better."

## PW-12 "How does your memory work?"
Pip consults the capability registry and the memory docs → a short spoken answer. "Show me" → a diagram on the TV. If the docs and code disagree: "My docs say X, but my code does Y. I've logged that as a documentation bug."

## PW-13 Requesting a self-change (M1)
1. A recognised member: "Pip, can you make your face blink more naturally?"
2. Classified as a code change request. Clarification: "Slower blinks, or more random timing?" → "More random."
3. Proposal: cosmetic tier, affects FEAT-052, test plan, rollback. Pip: "I've put a proposal on the dashboard."
4. The admin approves the proposal on the dashboard → sandboxed development (watchable) → verification passes.
5. Review screen: diff, results, risks → the admin approves deployment.
6. Pip waits for a quiet moment → installs side by side → health checks pass → "My new blinking is live. Say 'undo that change' if you don't like it."

## PW-14 Rolling back
"Pip, undo that change." → Pip: "Rolling back the blink change needs an admin to confirm on the dashboard." → the admin confirms → the previous version is restored → health check → "Back to the old blink."

## PW-15 Remote dashboard sign-in
The owner is away → opens Pip's private-network address on the phone → signs in (with a second factor for admin) → Home shows status → snoozes an alarm that is ringing at home, or turns the mic off.

## PW-16 Credential migration (M1)
The admin opens Settings → Credentials → "Import from Ziggletron" → sees the credential **names** found → selects them → Pip tests each one ("Groq: works", "Twilio: accepted") → the report lists imported and skipped items. Ziggletron's files are untouched.

## PW-17 Forgetting a person
The admin (or Sam) chooses People → Sam → Forget → sees the list of what will be removed (voice profile, 6 memories, 2 reminders, vocabulary entry) → confirms → done, with an audit entry.

## PW-18 Shopping list to TV (M3)
"Pip, add milk." → the item is stored (verified) → the TV shopping widget updates within 1 s → "Added." Offline, the same works through the local grammar.

## PW-19 Proactive diagnostic
The cloud STT starts failing → fallback to local. The next time someone talks to Pip and it mishears: "I'm using my offline hearing for now, so sorry if I get words wrong." The dashboard shows a degraded notice. On recovery, a note appears on the dashboard; nothing is spoken.

## PW-20 Opting out by voice
1. Jordan, recognised: "Pip, leave me out."
2. Pip: "Okay Jordan, I won't send anything you say to the cloud unless you're talking to me." The audit trail records it; Jordan's dashboard shows "opted out".
3. Social mode now behaves as Room-Aware whenever Jordan speaks. "Pip, include me again" reverses it.

## PW-21 Speakerphone mute
Someone presses the speakerphone's mute button → Pip detects digital silence → the TV face and dashboard show "muted". Unmute → listening resumes. Works without the network.
