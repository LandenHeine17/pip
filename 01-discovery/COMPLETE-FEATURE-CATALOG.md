# Complete Feature Catalog

Every discovered capability of the current Ziggletron, described by observable behaviour. Identifiers are stable: later phases should refer to features by `FEAT-nnn`.

**How to read an entry:** the heading carries the name, and each entry lists 15 attributes in a fixed order: Category · Purpose · Need · Trigger · Inputs · Outputs · Experience · Expected functionality · Rules · Settings · Dependencies · Related · Errors & edge cases · Status · Known issues.

**Status vocabulary:** *Working* (verified [R] or [T]) · *Working (source only)* [S] · *Partial* · *Unverified* [D] · *Not merged* [X] · *Broken* [X].
Evidence tags are defined in [PRODUCT-OVERVIEW.md](PRODUCT-OVERVIEW.md#evidence-tags-used-throughout). Precise numbers (thresholds, timings) are in [BEHAVIORAL-SPECIFICATION.md](BEHAVIORAL-SPECIFICATION.md).

## Index

| Area | Features |
|---|---|
| A. Hearing & speaker identity | FEAT-001 – FEAT-014 |
| B. Conversation & responding | FEAT-015 – FEAT-027 |
| C. Household memory | FEAT-028 – FEAT-032 |
| D. Timers, reminders, calendar & alarms | FEAT-033 – FEAT-042 |
| E. Communication | FEAT-043 |
| F. Device control | FEAT-044 – FEAT-047 |
| G. Presence & power | FEAT-048 – FEAT-051 |
| H. TV display | FEAT-052 – FEAT-065 |
| I. Web dashboard | FEAT-066 – FEAT-072 |
| J. Self-modification & code console | FEAT-073 – FEAT-078 |
| K. Platform & extensibility | FEAT-079 – FEAT-087 |
| L. Unmerged / experimental | FEAT-088 – FEAT-089 |

---

## A. Hearing & speaker identity

### FEAT-001 · Continuous room listening and utterance detection
- **Category:** Voice input
- **Purpose:** Capture everything said in the room and cut it into separate utterances.
- **Need:** People should be able to talk naturally without pressing anything, and the assistant must follow the whole conversation to know when it is addressed or could join in.
- **Trigger:** Always on while the microphone is open.
- **Inputs:** Live microphone audio.
- **Outputs:** Discrete utterances (audio segments). Status changes to "Hearing speech" and back. A live mic level meter on the dashboard.
- **Experience:** Someone starts speaking → the status shows "Hearing speech" → they pause → the utterance is handed on for transcription.
- **Expected functionality:** Detects the start of speech quickly, keeps a short lead-in so first words are not clipped, ends an utterance after a configurable pause, ignores very short blips, caps utterance length. While the assistant is talking, utterances are cut short so interruptions are noticed fast, and detection is stricter.
- **Rules:** A "dozing" room uses stricter detection. Any start of speech cancels a pending decision to join the conversation.
- **Settings:** microphone device, speech-detection sensitivity, pause length that ends an utterance, maximum utterance length (hidden), interruptibility (barge-in).
- **Dependencies:** A microphone. An on-device voice-activity model.
- **Related:** FEAT-002, 007, 017, 019, 046, 048, 087.
- **Errors & edge cases:** Mic unplugged or busy → reported on the dashboard and retried every 5 s (FEAT-087). If processing falls far behind, audio is dropped rather than buffered without limit. A muted mic (pure digital silence) triggers a warning (FEAT-051).
- **Status:** Working [R][T].
- **Known issues:** No local wake word. Every utterance is transcribed, which sends all room speech to cloud STT when it is enabled. Thresholds are hand-tuned. Very quiet or distant voices depend on the sensitivity setting.

### FEAT-002 · Speech-to-text with cloud/local choice and automatic fallback
- **Category:** Voice input
- **Purpose:** Turn each utterance into text.
- **Need:** Everything downstream works on text.
- **Trigger:** Each utterance that passes the echo-loudness check.
- **Inputs:** Utterance audio. The assistant's name as a spelling hint.
- **Outputs:** Text, or nothing. Status shows "Transcribing". A quota meter for the cloud service.
- **Experience:** Invisible except for the "Transcribing" status and the quota bar.
- **Expected functionality:** Three modes: *auto* (cloud if a key exists, else local), *local only*, *cloud*. In cloud mode, any network or service failure silently falls back to the local model, with one notice when it starts failing and one when it recovers. The local model size is picked automatically to suit the hardware. Quiet audio is normalised up.
- **Rules:** Only the assistant's name is sent as a hint, never recent conversation. Known "phantom" phrases (e.g. "thanks for watching") are discarded. If the result is little more than the name, it is re-transcribed without the hint and the name is dropped if it was only heard because of the hint.
- **Settings:** STT engine (auto/local/groq), local model (auto/tiny/base/small/large-v3-turbo), compute type (hidden), cloud API key.
- **Dependencies:** Optional Groq cloud API. Local Whisper model (always loaded, even in cloud mode).
- **Related:** FEAT-001, 003, 008, 070.
- **Errors & edge cases:** Cloud quota exhausted → falls back to local. Cloud requests time out after 10 s, so a slow cloud delays everything queued behind it.
- **Status:** Working [R][T] (cloud in use live; fallback path tested).
- **Known issues:** All room speech goes to the cloud in cloud mode. Typed messages, sounds and decisions wait behind a slow transcription. The local model uses memory even when unused.

### FEAT-003 · Being called by name (forgiving wake-name, nicknames)
- **Category:** Conversation control
- **Purpose:** Decide that an utterance is addressed to the assistant.
- **Need:** Natural address ("Zigg, …", "…, Ziggletron?") with no button, tolerant of mis-transcriptions.
- **Trigger:** Every heard line.
- **Inputs:** Transcribed text. Configured name and nicknames.
- **Outputs:** Classification: *called directly*, *only mentioned* (FEAT-004), or *not for us*.
- **Experience:** "Hey Zigg what time is it" → chime → answer. "Ziggle tron, ..." still works.
- **Expected functionality:** Counts as called when a clause consists of the name, or a greeting followed by the name ("hey", "ok", "so"...). Matching tolerates mishearings (letter similarity, words run together up to three words). Nicknames need a closer match.
- **Rules:** Being in the follow-up window (FEAT-005) also counts as direct. Talking over an answer counts as direct. During a conditional alarm (FEAT-038), everything counts as direct.
- **Settings:** Name, nicknames.
- **Dependencies:** None.
- **Related:** FEAT-004, 005, 006, 015.
- **Errors & edge cases:** Similar-sounding words may false-trigger. Very short nicknames are constrained to avoid matching common words.
- **Status:** Working [T].
- **Known issues:** Matching is heuristic and English-only.

### FEAT-004 · Name merely mentioned: model decides whether to reply
- **Category:** Conversation control
- **Purpose:** Avoid butting in when people talk *about* the assistant rather than *to* it.
- **Need:** "Ziggletron's been weird today" should not get a reply unless one is natural.
- **Trigger:** Line contains the name but not as a call.
- **Inputs:** Transcript, context.
- **Outputs:** Either a spoken reply or silence, plus a dashboard note "Heard my name, but they were talking about me, not to me."
- **Experience:** No chime. Usually silence.
- **Expected functionality:** The model is asked to reply only if a person would naturally be expected to, else to reply "PASS". On PASS nothing is spoken (actions in the reply still run).
- **Rules:** On error or refusal in this mode it stays silent (no apology). After a PASS, the join-in timer is reset so it does not chime in about it straight away.
- **Settings:** None specific.
- **Dependencies:** Language model.
- **Related:** FEAT-003, 015, 019.
- **Errors & edge cases:** Model unreachable → silent, error noted on dashboard.
- **Status:** Working [T].
- **Known issues:** Costs a model request every time the name is mentioned.

### FEAT-005 · Follow-up window (talk without the name)
- **Category:** Conversation control
- **Purpose:** Allow natural back-and-forth.
- **Need:** Repeating the name for every follow-up is unnatural.
- **Trigger:** The assistant finishes speaking.
- **Inputs:** Last sentence spoken.
- **Outputs:** For a window of time, any utterance counts as addressed.
- **Experience:** "Zigg, what's 12 times 12?" → "144." → "And times 13?" → answered.
- **Expected functionality:** Window length is configurable (default 8 s), ×1.5 if the assistant's last sentence was a question.
- **Rules:** A greeting ("welcome back") does not open a follow-up window.
- **Settings:** Follow-up seconds.
- **Dependencies:** None.
- **Related:** FEAT-003, 021.
- **Errors & edge cases:** Room chatter inside the window is treated as addressed, which may produce unwanted replies.
- **Status:** Working [T].
- **Known issues:** None beyond the above.

### FEAT-006 · Acknowledgement chime
- **Category:** Feedback
- **Purpose:** Signal immediately that it heard its name.
- **Need:** Latency to first spoken word is 1.5–3.5 s, so people need early confirmation.
- **Trigger:** A spoken (not typed) line directly addressed to it.
- **Inputs:** —
- **Outputs:** A short two-tone beep.
- **Experience:** Beep right after you stop talking, then the answer.
- **Expected functionality:** Plays alongside the answer being prepared.
- **Rules:** Not for typed input, not for "only mentioned", not while a conditional alarm rings.
- **Settings:** Chime on/off.
- **Dependencies:** Speaker.
- **Related:** FEAT-003, 015.
- **Errors & edge cases:** —
- **Status:** Working [S][T].
- **Known issues:** —

### FEAT-007 · Not hearing itself (echo rejection)
- **Category:** Voice input
- **Purpose:** Keep listening while speaking (so people can interrupt) without treating its own voice as someone talking.
- **Need:** Without this it answers itself.
- **Trigger:** Any utterance captured while, or just after, it was speaking.
- **Inputs:** Utterance audio and loudness, transcribed text, voiceprint, what it recently said.
- **Outputs:** The utterance is dropped silently, or passed on as a real interruption.
- **Experience:** You can talk over it. Its own voice through the speaker is ignored.
- **Expected functionality:** Three checks: (1) *loudness*: an utterance that started while talking and is not clearly louder than the recent echo level is dropped before transcription; (2) *words*: text that substantially matches what it said in the last few seconds (even garbled, numbers normalised) is dropped, and its voiceprint refines the "own voice" model; (3) *voice*: a voiceprint matching its own synthetic voice is dropped.
- **Rules:** A bare stop request ("stop") is never treated as echo unless the assistant itself just said a stop phrase. Speech in the half-second after playback ends is still treated as possible echo (Bluetooth lag).
- **Settings:** Barge-in on/off (off = stops listening while talking).
- **Dependencies:** On-device speaker-recognition model, speaker.
- **Related:** FEAT-001, 010, 017.
- **Errors & edge cases:** Quiet interruptions may be mistaken for echo; users are told to "speak up a little". Hardware echo cancellation is recommended in docs.
- **Status:** Working [T] (tuned against recordings per docs [D]).
- **Known issues:** Depends on room acoustics. No built-in acoustic echo cancellation.

### FEAT-008 · Transcription hygiene (phantom phrases, name-hint check)
- **Category:** Voice input
- **Purpose:** Prevent spurious lines.
- **Need:** STT models hallucinate text in noise and can "hear" the hinted name in a breath.
- **Trigger:** Every transcription.
- **Inputs/Outputs:** Text in → cleaned text or nothing.
- **Experience:** Invisible.
- **Expected functionality:** Discards known subtitle-style hallucinations and lone filler. Re-checks name-only results without the hint. Local engine discards low-confidence segments.
- **Rules:** —
- **Settings:** —
- **Dependencies:** STT engine.
- **Related:** FEAT-002, 003.
- **Errors & edge cases:** A genuine one-word "you" is discarded.
- **Status:** Working [T].
- **Known issues:** Phantom list is short and English-specific.

### FEAT-009 · Ambient sound awareness
- **Category:** Context awareness
- **Purpose:** Notice non-speech sounds (music, dog, doorbell, alarm, smoke detector, laughter, applause, baby crying, knocking) and tell the model about them.
- **Need:** Lets replies and join-ins react to the room ("is that the doorbell?") and makes the TV face react.
- **Trigger:** Continuous classification of ~1 s windows of mic audio.
- **Inputs:** Mic audio.
- **Outputs:** A `[sound: music]` line in the conversation the model sees. A dashboard/TV event. TV face reacts (dances to music, smiles at laughter/applause, perks up otherwise).
- **Experience:** Music starts → TV face dances; next reply may reference it.
- **Expected functionality:** A sound is reported only when it starts, and not again until it has been absent for 30 s. Ignored while the assistant itself is speaking. Never slows down speech handling. Switches itself off if the model is missing, fails, or is too slow (avg > 0.5 s per 1 s window), and says so.
- **Rules:** Sound lines do not count as "the conversation moved on" for join-in purposes.
- **Settings:** On/off, confidence threshold, list of sound classes, model path (hidden).
- **Dependencies:** On-device audio-tagging model.
- **Related:** FEAT-023, 052.
- **Errors & edge cases:** See auto-disable above.
- **Status:** Working [R][T].
- **Known issues:** The TV's own sound, or a radio, is "heard" as room sound.

### FEAT-010 · Speaker recognition with honest labels
- **Category:** Identity
- **Purpose:** Know who said each line.
- **Need:** Personalised replies, person-targeted reminders, and admin authority for self-changes.
- **Trigger:** Every spoken line (in parallel with transcription).
- **Inputs:** Utterance audio. Stored voice profiles.
- **Outputs:** Label per line: `Name` (sure), `Name?` (probably), `Someone` (unknown). The model is told the labels and their meaning.
- **Experience:** Dashboard shows the label on each line. The model may say "Hey Alex".
- **Expected functionality:** Only people who have been named are recognised; unknown voices are never given invented labels. Clear matches of long enough samples refine the profile automatically. Records when each person was last recognised.
- **Rules:** Sure and probably thresholds require both a minimum similarity and a lead over the next-best person. Own voice is excluded. Learning happens only from clean, long-enough samples and not while it is talking.
- **Settings:** Voice recognition on/off, speaker model (large or small).
- **Dependencies:** On-device speaker-embedding model.
- **Related:** FEAT-011–014, 035, 073.
- **Errors & edge cases:** Model missing → recognition off, with a notice. Changing the model makes old profiles incomparable: they are set aside and everyone must re-introduce themselves.
- **Status:** Working [R][T]. Documented accuracy in simulated rooms: confident labels wrong ~4%, ~1 in 6 lines stay "Someone" [D].
- **Known issues:** Profiles never expire (17 stored on the live device). Guests accumulate.

### FEAT-011 · Learning a voice by introduction
- **Category:** Identity
- **Purpose:** Enrol a person.
- **Need:** No setup screen; people just say who they are.
- **Trigger:** The model judges that someone said who they are ("Zigg, I'm Alex", "it's Sam") or corrected it about who is talking, including overheard introductions while joining in.
- **Inputs:** The line being replied to; the name.
- **Outputs:** A profile created or extended. Dashboard note "Learning Alex's voice – the next few things they say will help." Nearby lines may be relabelled.
- **Experience:** Say "I'm Alex", keep chatting a few sentences; the model is instructed to keep you talking.
- **Expected functionality:** Learns from that line and up to 4 more things the same voice says within the next minute (if plausibly them and clearly not another known person).
- **Rules:** Typed lines and very short lines cannot be used. **An admin's voice cannot be (re)claimed by saying their name** once their profile exists (FEAT-014).
- **Settings:** —
- **Dependencies:** FEAT-010.
- **Related:** FEAT-012, 014.
- **Errors & edge cases:** Overheard TV dialogue "I'm Sam" could enrol a TV voice.
- **Status:** Working [T].
- **Known issues:** Relies on the model noticing introductions.

### FEAT-012 · Correcting who said a line (dashboard)
- **Category:** Identity / dashboard
- **Purpose:** Fix or confirm a speaker label.
- **Need:** Recognition errs; corrections improve profiles.
- **Trigger:** Admin clicks the speaker label on a line → "Who said this?" picker.
- **Inputs:** Existing person, a new name, or "Not sure / not one of us – don't learn from this".
- **Outputs:** Line relabelled with a ✓. That sample moves out of the wrong profile into the right one. Up to 30 recent unconfirmed lines are re-checked and may be relabelled. The model sees corrected names.
- **Experience:** Click "Alex?" → pick "Sam" → line shows "Sam ✓", neighbours may update with a brief highlight.
- **Expected functionality:** Exact sample removal is possible because each learned sample is tied to its line.
- **Rules:** **Requires the dashboard password.** Only the last 200 lines' voiceprints are kept for correction. Typed lines cannot be labelled.
- **Settings:** —
- **Dependencies:** FEAT-010, 071.
- **Related:** FEAT-013, 014.
- **Errors & edge cases:** Old lines cannot be corrected after 200 newer lines or after a restart ("Couldn't tell whose voice that was").
- **Status:** Working [T].
- **Known issues:** Corrections are lost for lines from before a restart.

### FEAT-013 · Managing known voices (rename, merge, forget, last heard)
- **Category:** Identity / dashboard
- **Purpose:** Maintain the set of profiles.
- **Need:** Fix duplicates (one person under two spellings), remove guests.
- **Trigger:** Dashboard "Voices it knows" list: ✎ rename, × forget.
- **Inputs:** New name; confirmation dialogs.
- **Outputs:** Renamed or merged or deleted profile; note "Merged X into Y."
- **Experience:** Each row shows name, sample count, "heard 3 h ago". Renaming to an existing name asks to merge.
- **Expected functionality:** Names keep the capitalisation users type ("McDonald", "JJ"); lower-case input is capitalised.
- **Rules:** **Requires the dashboard password.**
- **Settings:** —
- **Dependencies:** FEAT-010.
- **Related:** FEAT-014.
- **Errors & edge cases:** —
- **Status:** Working [T].
- **Known issues:** No automatic expiry of old guests.

### FEAT-014 · Protecting admin identities
- **Category:** Security
- **Purpose:** Stop someone from making their voice count as an admin's.
- **Need:** Admin voices authorise code changes.
- **Trigger:** Any attempt to name, relabel, rename or forget voices.
- **Inputs/Outputs:** Refusal message, e.g. "Not learning Alex's voice from that: admins' voices can only be corrected on the dashboard."
- **Experience:** Saying "I'm Alex" does nothing if Alex is an admin with a profile (unless the line already sounds like Alex).
- **Expected functionality:** Voice-profile changes on the dashboard need the password.
- **Rules:** As above.
- **Settings:** Admin list.
- **Dependencies:** FEAT-071.
- **Related:** FEAT-073.
- **Errors & edge cases:** Before an admin's profile exists, anyone saying the admin's name can create it.
- **Status:** Working [T].
- **Known issues:** Bootstrapping gap above.

---

## B. Conversation & responding

### FEAT-015 · Answering when spoken to (streamed spoken reply)
- **Category:** Core interaction
- **Purpose:** Respond to direct requests quickly and naturally.
- **Need:** The core use.
- **Trigger:** A line classified as addressed (FEAT-003/004/005), a typed "To Ziggletron" message, or any line during a conditional alarm.
- **Inputs:** Recent conversation (last 40 lines), situation context (FEAT-023), current date and time, system instructions (personality + mechanics + list of actions).
- **Outputs:** Spoken sentences, streamed. Actions performed (FEAT-025). Transcript and dashboard entries. Latency breakdown (FEAT-070).
- **Experience:** Chime → status "Thinking" → first sentence spoken as soon as it is ready → following sentences without gaps.
- **Expected functionality:** Speaks sentence by sentence as text arrives. A long first sentence (>140 chars) is split at a comma to start sooner. Action tags are never spoken. A newer question cancels one still being prepared.
- **Rules:** Replies are told to be short, spoken-style, no markdown, lists, emoji or links; numbers said aloud. Lines marked "(cut off)" must not be repeated.
- **Settings:** Brain backend and model, effort level, web search, transcript length (hidden), personality file.
- **Dependencies:** Language model provider.
- **Related:** FEAT-016, 017, 022–026.
- **Errors & edge cases:** Model unreachable → "Sorry, something went wrong on my end." plus a dashboard error. Refusal → "Sorry, I can't help with that one."
- **Status:** Working [R][T].
- **Known issues:** A concurrency defect was observed once in the logs on 2026-11-02 ("readuntil() called while another coroutine is already waiting"): two answer requests read the same pre-started model process, and the answer failed. See KNOWN-PROBLEMS P-01.

### FEAT-016 · Spoken output (text-to-speech)
- **Category:** Voice output
- **Purpose:** Speak replies in a natural voice.
- **Need:** Voice-first product.
- **Trigger:** Anything to say: answers, join-ins, alarms, greetings, plugin announcements, self-change announcements.
- **Inputs:** Sentences.
- **Outputs:** Audio on the default output device. Speaker loudness level (drives TV mouth).
- **Experience:** Natural speech with no gaps between sentences.
- **Expected functionality:** Synthesises the next sentence while the current one plays. Stops within ~50 ms when interrupted. Strips markdown, links (said as "the link"), emoji and symbols. Skips sentences that produce no sound. Records what was actually said, marking "(cut off)" if interrupted.
- **Rules:** Treated as "still talking" for 0.5 s after audio ends (echo tail).
- **Settings:** Voice model file, speech rate.
- **Dependencies:** On-device TTS voice model. Audio output (PipeWire/PulseAudio; Bluetooth works).
- **Related:** FEAT-007, 017, 052.
- **Errors & edge cases:** Bluetooth speakers that sleep clip the first word (docs).
- **Status:** Working [R][T]. An earlier crash on empty audio was fixed [T].
- **Known issues:** English voice only, configured by file.

### FEAT-017 · Interrupting it (barge-in) and stop phrases
- **Category:** Conversation control
- **Purpose:** Let people cut it off.
- **Need:** Natural conversation, and a way to stop long or wrong answers.
- **Trigger:** Someone speaks while it is speaking or thinking.
- **Inputs:** The interrupting line.
- **Outputs:** Speech stops. Either nothing more ("Stopped talking.") or the new request is answered.
- **Experience:** "Stop" / "never mind" / "thanks" → it goes quiet. "Wait, what about cats?" → it stops and answers the new question.
- **Expected functionality:** Stop-only phrases (with filler words allowed) end the activity. Anything else replaces it. Talking over an *answer* is treated as addressing it. Talking over a *join-in* is treated as the conversation carrying on.
- **Rules:** A stop phrase that also names the assistant is treated as a new request. Dashboard "Stop talking" does the same.
- **Settings:** Barge-in on/off.
- **Dependencies:** FEAT-007.
- **Related:** FEAT-018, 037.
- **Errors & edge cases:** During a normal ringing alarm, only responses (FEAT-037) count. Others are recorded but the alarm continues.
- **Status:** Working [T].
- **Known issues:** —

### FEAT-018 · Cut-off awareness
- **Category:** Conversation memory
- **Purpose:** Avoid repeating what was interrupted.
- **Trigger:** Speech interrupted.
- **Inputs/Outputs:** The partial reply is stored as "Ziggletron (cut off)". The model is told not to repeat it unless asked.
- **Experience:** After you cut it off, the next answer does not restart the old one.
- **Expected functionality / Rules:** Only sentences that actually started playing are recorded.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-017.
- **Errors & edge cases:** —
- **Status:** Working [T].
- **Known issues:** —

### FEAT-019 · Joining conversations on its own
- **Category:** Proactive behaviour
- **Purpose:** Participate like a household member: jokes, answers to things people wonder aloud, opinions.
- **Need:** Core "part of the household" positioning.
- **Trigger:** After someone else speaks, a natural pause of `turn_pause_s` with no new speech, subject to many conditions (see Rules).
- **Inputs:** Transcript, context, chattiness "mood".
- **Outputs:** A spoken contribution, or silence. Dashboard notes: "listened, had nothing to add" or "had a thought, but the conversation moved on: '…'". Every check appears on the Prompts tab.
- **Experience:** People chat → pause → maybe it adds one short line.
- **Expected functionality:** The model replies PASS or "SAY: words". Only the SAY text is spoken, so reasoning never leaks. If anyone spoke or started speaking meanwhile, the thought is dropped. While joining in, the only actions allowed are a short safe list: remember, learn a voice, unprompted picture or photo.
- **Rules:** Only when: nothing else is happening; presence is awake (not dozing or away); not quiet hours; mic open and not currently hearing speech; nothing queued; something new was said and the last line was not its own; the conversation is under 60 s since the last line; minimum interval since the last check depends on chattiness (quiet 120 s, normal 40 s, chatty 15 s; off = never).
- **Settings:** Chattiness (off/quiet/normal/chatty; dashboard-changeable, persisted), turn pause, quiet hours, personality file guidance on when to join.
- **Dependencies:** Language model (each check is a request).
- **Related:** FEAT-020, 022, 028, 048, 050, 060, 061.
- **Errors & edge cases:** Model error → noted, silent.
- **Status:** Working [R][T].
- **Known issues:** Every check costs model usage. Each check sends the last 40 lines of room conversation to the provider. Overheard facts may be remembered unprompted (privacy).

### FEAT-020 · "Anything to add?" on demand
- **Category:** Proactive behaviour / dashboard
- **Purpose:** Let a user prompt it to consider joining now.
- **Trigger:** Dashboard "Ask" button.
- **Inputs/Outputs:** As FEAT-019, but with a "be a bit more willing than usual" mood and bypassing the timing conditions.
- **Experience:** Press Ask → it may say something.
- **Expected functionality / Rules:** Goes through the same ordered queue as speech.
- **Settings:** —
- **Dependencies:** Language model.
- **Related:** FEAT-019.
- **Errors & edge cases:** —
- **Status:** Working [S].
- **Known issues:** —

### FEAT-021 · Welcome-back greeting
- **Category:** Proactive behaviour
- **Purpose:** Greet people returning to a quiet room.
- **Trigger:** A spoken line after ≥20 minutes without any speech, that is not addressed to it.
- **Outputs:** "Hey Alex, welcome back." (name only if recognised for sure), otherwise "Hey, welcome back."
- **Experience:** Walk in, say something to someone → it says hello.
- **Expected functionality / Rules:** At most once per hour; not when busy, not during quiet hours, not during a conditional alarm; typed lines never trigger it. No follow-up window opens afterwards.
- **Settings:** Quiet hours (indirectly). Thresholds are fixed.
- **Dependencies:** —
- **Related:** FEAT-050, 052 (TV face looks pleased).
- **Errors & edge cases:** —
- **Status:** Working [T].
- **Known issues:** Greets any voice, including a TV or radio, after a long silence (accepted by owner).

### FEAT-022 · Editable personality (plain-language behaviour file)
- **Category:** Customisation
- **Purpose:** Let the household shape tone, brevity and when it joins in, without code.
- **Trigger:** The file is re-read automatically on the next request after it changes.
- **Inputs:** Plain-English text with a `{name}` placeholder. Comments are stripped.
- **Outputs:** Becomes the first part of the model's standing instructions. Dashboard note "Reloaded behavior.md." The current full instructions are visible on the Prompts tab.
- **Experience:** Edit file → next reply reflects it.
- **Expected functionality:** Default persona: warm, a bit cheeky, curious, opinionated; one short thing at a time; fragments OK; no assistant clichés; match the vibe; examples given. Join-in guidance: good moments and when to stay out.
- **Rules:** Mechanics (how actions work, voice labels, calendar etiquette, speakable output) are added separately, so the personality file cannot break them. If the file is mid-save or deleted, the last good text is kept.
- **Settings:** Personality file path.
- **Dependencies:** —
- **Related:** FEAT-015, 019.
- **Errors & edge cases:** Missing at startup → startup fails.
- **Status:** Working [R][T].
- **Known issues:** Persona text also drives the self-change and plugin descriptions only indirectly.

### FEAT-023 · Situation awareness (context given with every request)
- **Category:** Context
- **Purpose:** Give the model what it needs to answer well.
- **Trigger:** Every answer and join-in request.
- **Inputs (assembled):** Date/time; home location; nicknames; last 40 lines (with speaker labels, `[sound: …]`, and `[done: …]` / `[didn't work: …]` action results); relevant memories (FEAT-030) and a count of omitted ones; active timers and reminders with time left and flags; calendar for next 30 days (max 25 lines); a ringing conditional alarm and its condition; pending or in-progress self-change; who can be texted and how many texts remain; TV state, available dashboards, box types, colours, picture and photo allowances, recent pictures, last picture failure; names of known voices; plugin-provided context.
- **Outputs:** —
- **Experience:** Invisible. Inspectable on the Prompts tab.
- **Expected functionality / Rules:** Phone numbers are never included. The calendar should only be brought up when it really matters.
- **Settings:** Location, transcript length (hidden).
- **Dependencies:** —
- **Related:** Most features.
- **Errors & edge cases:** A failing plugin context provider is logged and skipped. If memory selection fails, all memories are sent.
- **Status:** Working [R][T].
- **Known issues:** The conversation transcript is in memory only and is lost on restart.

### FEAT-024 · Up-to-date knowledge via web search
- **Category:** Knowledge
- **Purpose:** Answer about news, weather, sports and other current facts.
- **Trigger:** At the model's discretion during answers and join-ins.
- **Inputs/Outputs:** Search performed by the model provider; the result is woven into the spoken reply.
- **Rules:** Up to 3 searches per request (API backend). Search is disabled for drawing and memory sorting.
- **Settings:** Web search on/off.
- **Dependencies:** Provider-side search tool.
- **Related:** FEAT-015.
- **Errors & edge cases:** Adds latency.
- **Status:** Working (source only) [S][D].
- **Known issues:** Search results are an injection surface (KNOWN-PROBLEMS).

### FEAT-025 · Performing actions, reporting real outcomes, self-correcting
- **Category:** Tool execution
- **Purpose:** Let the model trigger capabilities, and make sure it learns whether they worked.
- **Trigger:** Action tags at the end of a model reply.
- **Inputs:** Action name and fields.
- **Outputs:** The action is performed. A dashboard note (info or error). A `[done: …]` or `[didn't work: …]` line in the conversation after what was spoken.
- **Experience:** "Ten minutes, starting now." The timer appears. If an action fails, it promptly says so and, if it can, retries with a corrected action ("Sorry, that didn't work – …").
- **Expected functionality:** On any failure during an answer, the model is asked exactly once more with a "your action failed" prompt; that follow-up may correct it. Unknown actions or bad arguments become failures rather than crashes.
- **Rules:** The model writes its words before actions run, so it never sees results in the same turn. While joining in, only actions marked "may be used unprompted" run; others are silently ignored.
- **Settings:** —
- **Dependencies:** —
- **Related:** All action features (FEAT-028/029/033–045/055–061/073).
- **Errors & edge cases:** A second failure is left in the conversation for next time.
- **Status:** Working [T].
- **Known issues:** Information-producing plugin actions can't feed results into the same reply; plugins must speak results themselves.

### FEAT-026 · Graceful failure and refusal speech
- **Category:** Error handling
- **Purpose:** Fail audibly but briefly.
- **Behaviour:** Model unavailable → short apology (except in "only mentioned" mode) and dashboard error. Model refusal → "Sorry, I can't help with that one." (silent in "only mentioned" or join mode). Missing CLI at startup → startup error with guidance.
- **Trigger / Inputs / Outputs / Experience / Rules:** As above.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-015, 019.
- **Errors & edge cases:** No offline fallback brain: timers cannot be set by voice while offline.
- **Status:** Working [T].
- **Known issues:** No local fallback for simple commands.

### FEAT-027 · Typing instead of talking (dashboard composer)
- **Category:** Alternate input
- **Purpose:** Interact silently or test behaviour.
- **Trigger:** Dashboard composer, with a toggle "To Ziggletron" or "To the room".
- **Inputs:** Text; addressed or not.
- **Outputs:** "To Ziggletron" → answered like a direct request (no chime). "To the room" → treated as overheard chatter that may trigger a join-in.
- **Experience:** Type and press Send; the line appears as "typed".
- **Rules:** Typed lines have no speaker identity and can never authorise admin actions or claim a voice. Typing while it is speaking interrupts it. Typed responses always count as answering a ringing alarm.
- **Settings:** —
- **Dependencies:** Dashboard.
- **Related:** FEAT-015, 019, 037.
- **Errors & edge cases:** Queued behind any in-progress transcription.
- **Status:** Working [T].
- **Known issues:** A typed "to the room" line naming the assistant is not treated as addressed (the toggle wins). An "auto-decide" mode exists in the command interface but not in the UI.

---

## C. Household memory

### FEAT-028 · Long-term household memories
- **Category:** Memory
- **Purpose:** Remember lasting facts: names, birthdays, preferences, routines, standing requests, running jokes.
- **Trigger:** The model decides to remember (asked or not). Allowed while joining in.
- **Inputs:** Fact text, kind (rule/household/person/note), "about" terms.
- **Outputs:** Stored memory with date. Dashboard note "Remembered: …". Shown in "What it remembers".
- **Expected functionality:** Near-duplicates (>90% similar) are skipped. Maximum 150 memories; the oldest are dropped silently beyond that.
- **Rules:** Instructed never to store anything private or embarrassing (instruction only, not enforced).
- **Experience:** "Zigg, remember Sam is allergic to peanuts." → "Got it."
- **Settings:** —
- **Dependencies:** Language model.
- **Related:** FEAT-029–032, 023.
- **Errors & edge cases:** —
- **Status:** Working [R][T] (58 memories live).
- **Known issues:** Unprompted remembering of overheard facts; silent eviction at the cap; no edit of the fact text itself.

### FEAT-029 · Forgetting by voice
- **Category:** Memory
- **Trigger:** "Forget that…" → model issues forget with fact text.
- **Behaviour:** Removes the best fuzzy match (≥50% similarity). Note "Forgot: …". If nothing matches well, nothing happens and nothing is reported.
- **Purpose / Need / Inputs / Outputs / Experience / Rules:** As described.
- **Settings:** —
- **Dependencies:** Language model.
- **Related:** FEAT-028, 032.
- **Errors & edge cases:** A fuzzy match may remove the wrong memory.
- **Status:** Working [T].
- **Known issues:** A no-match fails silently (no "didn't work" line).

### FEAT-030 · Memory classification and selective recall
- **Category:** Memory / context
- **Purpose:** Send only relevant memories with each request.
- **Need:** Smaller prompts; less exposure of unrelated personal facts.
- **Behaviour:** Each memory has a kind and "about" terms. *Rules* and *household basics* are always sent. *Person* and *note* memories are sent when any "about" term (all its words) appears in the recent conversation, including speaker names. Memories about nothing in particular are always sent. **Everything** is sent when the last line asks what it remembers, asks it to forget, or "what do you know". The prompt states how many were omitted.
- **Trigger / Inputs / Outputs / Experience / Rules:** As above.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-028, 031, 032.
- **Errors & edge cases:** If selection fails, all memories are sent.
- **Status:** Working [T].
- **Known issues:** Word-matching recall can miss synonyms.

### FEAT-031 · Background memory sorting
- **Category:** Memory maintenance
- **Purpose:** Give old or unclassified memories a proper kind and topics.
- **Behaviour:** Memories without a kind get an immediate heuristic guess (shown in italics as "guessed"). A background job batches up to 60 guessed memories to the model, every 10 minutes or straight after a batch, and applies the results. Note "Sorted N memories…". If offline, it retries later. A memory the model skipped keeps its guess until restart.
- **Trigger / Inputs / Outputs / Experience / Rules / Need / Purpose:** As above.
- **Settings:** —
- **Dependencies:** Language model.
- **Related:** FEAT-030, 032.
- **Errors & edge cases:** Edits made on the dashboard meanwhile take precedence.
- **Status:** Working [T] (0 guessed memories live [R]).
- **Known issues:** —

### FEAT-032 · Managing memories on the dashboard
- **Category:** Memory / dashboard
- **Behaviour:** List of memories with kind dropdown, "about: …" (✎ to edit, comma-separated), × to delete. Changes apply immediately.
- **Rules:** **No password required** to delete or re-sort memories.
- **Purpose / Need / Trigger / Inputs / Outputs / Experience:** User-driven memory curation.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-028–031.
- **Errors & edge cases:** Deleting a memory that is gone gives a clear error.
- **Status:** Working [T].
- **Known issues:** Cannot edit the fact text or add a memory from the dashboard. No export or backup.

---

## D. Timers, reminders, calendar & alarms

### FEAT-033 · Timers
- **Category:** Scheduling
- **Purpose:** Countdown timers.
- **Trigger:** Voice ("set a pasta timer for 10 minutes") or dashboard form (minutes + optional label).
- **Inputs:** Duration, label, optional condition (FEAT-038).
- **Outputs:** Timer listed with live countdown on the dashboard and TV. Rings when due (FEAT-037).
- **Experience:** As described.
- **Expected functionality:** Minimum 1 s. Default label "timer". Spoken confirmation from the model plus note "Timer set: pasta, 10 minutes."
- **Rules:** —
- **Settings:** Ringtone for timers (FEAT-040).
- **Dependencies:** —
- **Related:** FEAT-037–042.
- **Errors & edge cases:** —
- **Status:** Working [T] (end-to-end test).
- **Known issues:** —

### FEAT-034 · Reminders (one-off and repeating)
- **Category:** Scheduling
- **Trigger:** Voice ("remind me at 6 to take the bins out", "every day at 8").
- **Inputs:** Local date-time, text, optional repeat (daily/weekly), optional condition, optional person (FEAT-035), optional text-to (FEAT-043).
- **Outputs:** Scheduled reminder. When due: "Reminder: take the bins out." with ringtone.
- **Expected functionality:** A time already past means the next occurrence (tomorrow if not repeating). Repeating reminders schedule their next time when they go off, keeping the local clock time across daylight-saving changes.
- **Rules:** Repeat values limited to daily or weekly.
- **Settings:** Ringtone for reminders.
- **Dependencies:** Language model to create.
- **Related:** FEAT-035, 037, 043.
- **Errors & edge cases:** Invalid times are reported as a failed action.
- **Status:** Working [R][T] (5 repeating reminders live).
- **Known issues:** No dashboard form to create reminders; only voice. No monthly/weekday patterns.

### FEAT-035 · Reminders for one person ("only if they're home")
- **Category:** Scheduling / presence
- **Purpose:** Target a reminder at someone who isn't always home.
- **Behaviour:** Always goes off. If that person's voice (sure or probable) has not been heard in the last 6 hours, it opens with "Is Alex home? Reminder for Alex: …" and notes it on the dashboard. "Last heard" times persist across restarts.
- **Trigger / Inputs / Outputs / Experience / Rules / Need:** As above.
- **Settings:** —
- **Dependencies:** FEAT-010.
- **Related:** FEAT-034.
- **Errors & edge cases:** —
- **Status:** Working [T].
- **Known issues:** Docs and the action description still say it "only goes off if their voice has been heard recently", which no longer matches behaviour (inconsistent wording).

### FEAT-036 · Household calendar
- **Category:** Scheduling
- **Purpose:** Shared events, all-day or timed, with optional reminder and repeat.
- **Trigger:** Voice ("put the dentist on the calendar for Tuesday at 3") or dashboard month view (+ Add event, click to edit).
- **Inputs:** Title, start (date or date-time), optional end, notes, remind-before minutes, repeat (daily/weekly; voice only).
- **Outputs:** Event stored; month grid and day list on the dashboard; "upcoming" on TV; part of the model's context; optional ring before it starts ("Heads up: Dentist tomorrow at 3:30 PM." / "… is starting now.").
- **Expected functionality:** Events can't end before they start. All-day event reminders count back from 08:00 on the day. Deleting an event removes its reminder. Repeating events roll forward to their next time once over. Removal by voice uses a fuzzy title match, optionally on a date.
- **Rules:** The model is told to mention events only when relevant (soon and forgotten, or a clash).
- **Settings:** Ringtone for events.
- **Dependencies:** —
- **Related:** FEAT-037, 058.
- **Errors & edge cases:** Bad dates → "Couldn't save that event: …".
- **Status:** Working [R][T].
- **Known issues:** Dashboard form lacks a repeat option (repeat can only be set by voice, though editing preserves it). No external calendar sync. Dashboard reminder choices are fixed presets.

### FEAT-037 · Alarms that ring like real ones
- **Category:** Alerting
- **Purpose:** Make timers, reminders and events hard to miss.
- **Trigger:** An alert falls due. Alarms take priority over everything and do not wait behind transcription.
- **Behaviour:** Interrupts whatever is happening. Announces it ("Your pasta timer is done.") and posts it on the dashboard/TV (banner, shaking face). Loops the ringtone, repeating the announcement every 30 s if enabled. **Stops only on a real response:** someone saying its name, or an acknowledgement ("okay", "thanks", "stop", "got it", "turn it off", "I'm up", "coming"…), or any typed message, or Shut off / Snooze on the dashboard, or after `ring_minutes` (default 5) with a note "Nobody answered…". Other speech (TV, chatter) is transcribed and recorded but doesn't stop it.
- **Inputs / Outputs / Experience / Need / Purpose / Rules:** As above.
- **Settings:** Ringtone per kind, loudness, ring duration, speak-between-rings (FEAT-040).
- **Dependencies:** Speaker.
- **Related:** FEAT-038–041.
- **Errors & edge cases:** A second alarm due while one rings takes over.
- **Status:** Working [T].
- **Known issues:** Acknowledgement phrases are English heuristics. A TV saying "okay" will stop it.

### FEAT-038 · Alarms you have to earn (conditional alarms)
- **Category:** Alerting
- **Purpose:** "Wake us up at 7 and keep bugging us until we recite 50 digits of pi."
- **Behaviour:** A timer or reminder with an "until" condition. When it fires: "… I'll keep going until …". Rings ~4 s, then gives people 15 s to try. **Everything anyone says goes to the model** (no name needed, no chime), which judges fairly (allowing for STT errors) and dismisses only when met, otherwise says playfully what's missing ("Still waiting: …"). "Stop" doesn't count. Dashboard Shut off always works. Gives up after 30 minutes ("Okay, I give up on that alarm."). Resumes ringing after any other activity finishes.
- **Trigger / Inputs / Outputs / Experience / Need / Purpose / Rules:** As above.
- **Settings:** As FEAT-040.
- **Dependencies:** Language model to judge.
- **Related:** FEAT-037.
- **Errors & edge cases:** Model offline → cannot be dismissed by voice (dashboard only).
- **Status:** Working [T].
- **Known issues:** Any room voice (TV) during the alarm is sent to the model.

### FEAT-039 · Shut off and snooze from the dashboard
- **Category:** Alerting / dashboard
- **Behaviour:** Status-panel buttons "Shut off alarm" and "Snooze 5 min". Timers tab: Shut off, Snooze with 1/5/10/15 min. Snooze re-creates the same alert later (not re-texted, repeat not duplicated). Notes "Shut off the … alarm from the dashboard." / "Snoozed … for N minutes."
- **Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** As above. No password needed.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-037, 038.
- **Errors & edge cases:** Non-numeric snooze → 5 minutes.
- **Status:** Working [T].
- **Known issues:** No voice "snooze" (only dashboard).

### FEAT-040 · Ringtone settings and preview
- **Category:** Customisation
- **Behaviour:** Dashboard Timers & calendar tab: choose ringtone separately for timers, reminders, events from 8 synthesised tones (classic, kitchen timer, digital watch, bell, chimes, marimba, urgent, gentle rise); loudness (quiet/medium/loud/very loud); ring minutes (0.5–60); whether to speak between rings; ▶ preview plays once. Persisted.
- **Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** As above.
- **Settings:** These are the settings.
- **Dependencies:** Speaker.
- **Related:** FEAT-037.
- **Errors & edge cases:** Invalid values ignored.
- **Status:** Working [R][T].
- **Known issues:** Loudness is relative to system volume.

### FEAT-041 · Alarms survive restarts
- **Category:** Reliability
- **Behaviour:** An alarm that has gone off stays recorded as "ringing" until it's over; after a restart or crash it rings again, unless it went off more than 12 h ago (then dropped with a note). All alerts, events and memories persist. Repeating alerts don't double-schedule.
- **Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** As above.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-037, 084.
- **Errors & edge cases:** Corrupt saved alert entries are skipped.
- **Status:** Working [T].
- **Known issues:** A conditional alarm's "nag" state after restart rings again as a fresh alarm (same condition).

### FEAT-042 · Cancelling by voice / removing on the dashboard
- **Category:** Scheduling
- **Behaviour:** Voice "cancel the pasta timer" → fuzzy label match (≥40%; with no label, the soonest alert). "Take the dentist off the calendar" → fuzzy title match. Dashboard × on any timer/reminder/event. No password.
- **Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** As above.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-033–036.
- **Errors & edge cases:** No match → nothing happens and nothing is reported (no failure line).
- **Status:** Working [T].
- **Known issues:** Silent no-match; fuzzy match may pick the wrong item.

---

## E. Communication

### FEAT-043 · Text messages (SMS)
- **Category:** Messaging
- **Purpose:** "Text Jordan that dinner's ready", and texting a reminder to someone when it goes off.
- **Trigger:** Voice request (answers only, never while joining in); a reminder with "text_to".
- **Inputs:** Recipient **name**, message (≤480 chars).
- **Outputs:** SMS sent via the provider. Note "Texted Jordan: …". Model told who can be texted and how many remain today.
- **Expected functionality:** Recipients are only names configured in settings; numbers never appear to the model, the dashboard or in speech. Per-person daily limit (default 10) persists across restarts and resets daily. Failed attempts also count (a timed-out request may have gone through).
- **Rules:** Off unless sender number, recipients and account credentials are all set.
- **Settings:** Sender number, name→number map, daily limit; account SID and token (secrets).
- **Dependencies:** Twilio.
- **Related:** FEAT-034.
- **Errors & edge cases:** "Twilio refused it (error N)", "couldn't reach Twilio", "has already had N texts today" → failed action, spoken correction.
- **Status:** Working (source/test only) [T]; configured live [R]; real delivery not verified.
- **Known issues:** Message content goes to a third party. Configured numbers live in a plain settings file.

---

## F. Device control

### FEAT-044 · Volume control
- **Behaviour:** "Turn it down a bit" / "volume 40 percent" → sets system output volume (5–100%), absolute or relative (relative to the current level; 50% assumed if unknown). Note "Volume set to N%."
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Device control via voice.
- **Settings:** —
- **Dependencies:** PipeWire (`wpctl`) or PulseAudio (`pactl`).
- **Related:** FEAT-040.
- **Errors & edge cases:** Neither tool present → reports a level but changes nothing.
- **Status:** Working (source only) [S].
- **Known issues:** Silent no-op on systems without those tools (e.g. macOS).

### FEAT-045 · "Stop listening" (sleep) and wake
- **Behaviour:** "Stop listening for an hour" → microphone **released** (not muted) for N minutes. Status "Asleep – Mic off until 3:15 PM, as asked." Dashboard "Wake up" ends it early. TV face sleeps and dims.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Privacy control. Default 60 minutes. Not available while joining in.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-046, 052.
- **Errors & edge cases:** While asleep it can't hear "wake up"; only the dashboard or the timeout ends it.
- **Status:** Working [T].
- **Known issues:** Anyone (including a TV) can trigger it by voice.

### FEAT-046 · Microphone switch
- **Behaviour:** Dashboard toggle closes the microphone device entirely. Status "Mic off". **Always on again after a restart** (deliberate).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies:** Privacy control on the dashboard; no password.
- **Related:** FEAT-045.
- **Errors & edge cases:** —
- **Status:** Working [T].
- **Known issues:** Anyone with dashboard access can turn it off or on.

### FEAT-047 · Speaker test
- **Behaviour:** Dashboard "Test" → interrupts current activity and says "Testing, testing. Can you hear me?"
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Setup aid.
- **Status:** Working (source only) [S].
- **Known issues:** —

---

## G. Presence & power

### FEAT-048 · Dozing in a quiet room
- **Behaviour:** No speech for `doze_after_min` (default 10) → status "Dozing": stops considering join-ins (saving model usage) and uses stricter speech detection. Still answers its name. Any speech → awake.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Resource saving.
- **Settings:** Doze after minutes.
- **Dependencies:** —
- **Related:** FEAT-019, 049.
- **Errors & edge cases:** —
- **Status:** Working [T].
- **Known issues:** —

### FEAT-049 · Away (motion sensor)
- **Behaviour:** With a PIR sensor wired to a GPIO pin: no motion for `away_after_min` (default 15) → microphone switched off, status "Nobody around". Motion → mic back on and fully attentive.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Power and privacy saving.
- **Settings:** Sensor GPIO pin (0 = none), away after minutes.
- **Dependencies:** Raspberry Pi GPIO, PIR sensor, GPIO library.
- **Related:** FEAT-048.
- **Errors & edge cases:** Configuring a pin on a non-Pi fails at startup.
- **Status:** Unverified [S][D] (no sensor configured live; no tests).
- **Known issues:** Without a sensor it never reaches "away".

### FEAT-050 · Quiet hours
- **Behaviour:** During configured hours (default 23:00–07:00, can cross midnight) it doesn't join conversations or greet. Answering by name still works. Dashboard chip "quiet hours … · no joining in".
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Etiquette.
- **Settings:** Quiet hours ("HH:MM-HH:MM" or empty).
- **Dependencies:** —
- **Related:** FEAT-019, 021.
- **Errors & edge cases:** Malformed value → startup config error.
- **Status:** Working [T].
- **Known issues:** Alarms are not affected (by design).

### FEAT-051 · Muted-microphone warning
- **Behaviour:** If an open mic produces perfect digital silence for 5 s, a dashboard error: "The microphone is sending pure silence. Is it muted in your system sound settings?" (once, until sound returns).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Diagnostics.
- **Status:** Working (source only) [S].
- **Known issues:** —

---

## H. TV display

### FEAT-052 · Animated face on a TV
- **Category:** Ambient display
- **Purpose:** Give the assistant a visible presence and show what it's doing.
- **Trigger:** A screen showing the TV page (kiosk mode from boot).
- **Inputs:** Live status, speaker loudness, mic level, sounds, heard/replies, notes.
- **Outputs:** A canvas face that blinks, glances around, breathes; expressions per state (listening, hearing, transcribing, thinking, speaking, dozing, asleep, away, paused, offline, ringing); mouth moves with its actual voice; colour per state; brief reactions (perks up when addressed, pleased at greetings/laughter, dances with music notes for music, confused on errors, shakes on alarms); sleepy "zZz".
- **Experience:** Always-on, glanceable.
- **Expected functionality:** Read-only page; sends no commands; loads nothing from the internet.
- **Rules:** Reactions don't hide that it's busy; only alarms or being offline outrank the six-seven reaction.
- **Settings:** None (TV page has no controls).
- **Dependencies:** A display and a browser in kiosk mode.
- **Related:** FEAT-053–063.
- **Errors & edge cases:** See FEAT-063.
- **Status:** Working [R][T].
- **Known issues:** —

### FEAT-053 · Dressing for the weather (and holiday hat)
- **Behaviour:** Face wears: storm cloud with lightning (thunderstorms), umbrella hat with rain landing on it (rain/drizzle/showers), warm hat with falling snow (snow), knitted beanie (feels ≤40 °F / 4 °C), sunglasses (clear, daytime, feels ≥65 °F / 18 °C). **November through 6 January**, warm hats become a Santa hat (and it wears one even with no forecast).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Delight / ambient information.
- **Settings:** Location.
- **Dependencies:** FEAT-064.
- **Related:** FEAT-052.
- **Errors & edge cases:** No forecast → no accessory (except the holiday hat).
- **Status:** Working [R][T].
- **Known issues:** —

### FEAT-054 · "Six seven" reaction (easter egg)
- **Behaviour:** Anyone in the room saying "six seven" (or "6 7") → for 4.5 s the face shakes, glows orange, sprays 6s and 7s, and grows two see-sawing hands (the only time it has hands).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Delight. Triggered by any heard line, addressed or not; only fresh events.
- **Status:** Working (source only) [S].
- **Known issues:** —

### FEAT-055 · TV dashboards (built-in views) and switching
- **Behaviour:** The TV shows one dashboard at a time. Built-ins: **default** (large face + clock, upcoming, machine health), **activity** ("What's going on"), **weather**, **conversation**, **memories**, **pictures**. Switch by voice ("display the weather", "go back to your default view") or the dashboard's "TV shows" menu. The current choice persists across restarts. Alarms show on any view.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Ambient information on demand.
- **Settings:** —
- **Dependencies:** —
- **Related:** FEAT-056–058.
- **Errors & edge cases:** Unknown name → error listing available ones.
- **Status:** Working [R][T].
- **Known issues:** —

### FEAT-056 · Custom dashboards by voice
- **Behaviour:** "Make me a morning dashboard with the weather, what's coming up and a shopping list" → saved, shown, recallable by name. Can change any dashboard including the default ("put the weather on the main screen"), restore a changed built-in ("put the main screen back"), delete custom ones. "Show … once" shows a temporary layout ("for now") without saving. Up to 20 saved, up to 8 boxes each.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** Personalisation without code; layouts are data.
- **Settings:** —
- **Dependencies:** Language model.
- **Related:** FEAT-057.
- **Errors & edge cases:** Too many boxes, overlaps, off-screen → explained failure; model can retry.
- **Status:** Working [R][T] (3 modified views live).
- **Known issues:** Logs show repeated layout failures ("no room left for the … box"), indicating friction for voice layout editing.

### FEAT-057 · Arranging boxes on the TV grid
- **Behaviour:** The screen is a 12×8 grid. The face is a box in the top-left (min 3×2, never hidden), sizes large/medium/small/tiny or w×h, and can sit top/middle/bottom in its box. By voice: add a box, remove, move/resize ("make the weather bigger", "move the clock to the bottom"), colour a box or all boxes (10 named colours or hex; "none" resets). Unplaced boxes are packed in columns and grow into free space; placed ones keep their spot; boxes in the way move. Boxes pop in/out and slide. "What can go on the TV?" lights up a rail listing all box types.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** As above. The face's colour can't be changed (it shows state).
- **Settings:** —
- **Dependencies:** Language model.
- **Related:** FEAT-056, 058.
- **Errors & edge cases:** Overlap or no-room errors are explained to the model, which can say so.
- **Status:** Working [T].
- **Known issues:** See FEAT-056.

### FEAT-058 · TV box types (sections)
- **Behaviour:** Clock; Calendar/"upcoming" (timers with countdowns, reminders, events); Machine health ("Pi health": CPU, temperature, memory, disk, uptimes); Weather (now + 4 days); Activity (what it's hearing, deciding, doing, including join decisions and timing); Conversation (only lines said *to* it, unless asked for the room's); Memories (optionally "about" one person); Picture (newest or a specific one, with photo credit); Notes/text (title + up to 12 lines written by the model at the time; static).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Display building blocks.
- **Status:** Working [R][T].
- **Known issues:** Notes are snapshots and don't update (e.g. a shopping list won't change unless rewritten).

### FEAT-059 · Drawn pictures on request
- **Behaviour:** "Draw a fox reading a newspaper" → the model says it's on its way; the TV switches to Pictures (if no picture box is showing) and shows "making…". A separate model request produces an illustration (vector, 1600×900, may animate gently). The result is sanitised so it can only draw. Takes ~1–2 minutes. On failure, it says "Sorry, that picture didn't work: …". Malformed drawings get one retry with an explanation of what went wrong. Last 50 kept; can show a recent one again. Daily limit (default 20).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** One picture at a time; prompt ≤1000 chars; must not be used just because it feels like it.
- **Settings:** Drawing effort level, daily limit.
- **Dependencies:** Language model (uses plan quota).
- **Related:** FEAT-060, 058.
- **Errors & edge cases:** Timeout 5 min; too large (>500 KB); truncated; invalid. Failed replies are kept (last 10) for diagnosis.
- **Status:** Working [R][T].
- **Known issues:** Slow; costly; failures observed in logs.

### FEAT-060 · Pictures of its own (unprompted)
- **Behaviour:** Dashboard switch "Its own pictures" (off by default; persisted). When on, the model may rarely draw something a conversation inspires, up to `own_daily_limit` (default 5)/day. Shown for 5 minutes, then the TV returns to its previous view (also after restart).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Delight. Counts towards the overall daily limit.
- **Status:** Working [R][T] (switch on live).
- **Known issues:** Uses plan quota without anyone asking.

### FEAT-061 · Real photos (Wikimedia Commons)
- **Behaviour:** "Show me a red panda" / "what does the Eiffel Tower look like?" → finds a real photo in seconds, downloads it (≤8 MB, ≥300 px, verified to really be JPEG/PNG/WebP/GIF, only from Wikimedia's servers), saves it locally, shows it with title and author/licence credit. Unprompted photos are allowed without a switch, at least 3 minutes apart, shown 5 minutes then back. Searches and results with adult or gory words are refused ("that isn't something to put on the family TV"). Daily limits: 100 asked, 30 unprompted.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Visual aid. Only search words leave the house.
- **Status:** Working [R][T] (16 photos stored).
- **Known issues:** Keyword filter is a rough net, not a guarantee. Unprompted photos have no on/off switch, unlike drawings.

### FEAT-062 · TV privacy rules
- **Behaviour:** The TV shows only what was said *to* the assistant (as captions, for 60 s), never room chatter, unless a conversation box was explicitly asked to show the room. Memories appear only on a dashboard that asks for them. Speaker names in captions only when recognised for sure.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Privacy for a wall display.
- **Status:** Working [T].
- **Known issues:** The page receives the full event stream (including room chatter and prompts) even if it doesn't display it.

### FEAT-063 · TV resilience and screen care
- **Behaviour:** If the assistant stops, the face looks sad/"offline" and says so; on return it reloads the page (picking up new code). A stream silent for 20 s counts as lost. The whole picture creeps slowly to avoid burn-in and dims when asleep or away. The kiosk browser restarts daily, starts fresh (incognito) and waits for the assistant before loading.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Unattended display robustness.
- **Status:** Working [R][T] (TV service active).
- **Known issues:** —

### FEAT-064 · Weather
- **Behaviour:** Looks up the configured location (picking among same-named places using the rest of the text), fetches current conditions and a 4-day forecast every 30 min (retry 5 min on failure), in °F/mph for US locations else °C/km/h. Feeds the weather box and the face's clothing. Missing location → "Set location in config.toml to see the weather."
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Ambient information. Only the place name leaves the house. Not available to the model as context (it uses web search for weather questions).
- **Status:** Working [R][T].
- **Known issues:** Weather shown on TV and weather spoken by the model come from different sources and may disagree.

### FEAT-065 · Machine health (vitals)
- **Behaviour:** Every 5 s: CPU %, temperature, memory %, disk %, assistant uptime, machine uptime; shown on the TV ("Pi health") with warning thresholds.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Diagnostics. Missing metrics show as unavailable.
- **Status:** Working [R][T].
- **Known issues:** Linux-specific sources.

---

## I. Web dashboard

### FEAT-066 · Live status and control panel
- **Behaviour:** Header: name, brain and hearing services, cloud STT quota, last-reply timing, quiet-hours chip, ringing banner. Status orb with state label and description (Starting up, Listening, Hearing speech, Transcribing, Thinking, Speaking, Dozing, Nobody around, Asleep, Mic off, Disconnected), mic level meter. Contextual buttons: Stop talking, Wake up, Shut off alarm, Snooze 5 min. Controls: mic switch, "Joins conversations" (Never/Rarely/Sometimes/Often; persisted), "TV shows" menu, "Its own pictures" switch, "Anything to add?" Ask, speaker Test. Side panel: timers & reminders, voices it knows, what it remembers.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Observability and control. Reconnects automatically every 2 s if disconnected. Fits a phone screen.
- **Status:** Working [R][T].
- **Known issues:** All of this, and all password-free controls, are available to anyone who can reach the page.

### FEAT-067 · Conversation feed
- **Behaviour:** Chat-style feed of heard/typed lines (marked "to Ziggletron" when addressed, with clickable speaker labels) and replies (marked joined in / alarm / cut off), notes and errors, join-in decisions that weren't spoken ("had a thought, but the conversation moved on: '…'"), sounds. History replays on connect (last 300 events).
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Transparency.
- **Status:** Working [R][T].
- **Known issues:** Lost on restart (no persistent history).

### FEAT-068 · Prompts inspector
- **Behaviour:** "Prompts" tab: current full system instructions; every model request (answers, name-mentioned, failed-action follow-ups, join checks, pictures, memory sorting) with prompt, raw reply, duration, outcome; filter All/Answers/Joining in; last 60 kept.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Explainability ("why did it say that?").
- **Status:** Working [R][T].
- **Known issues:** Exposes all memories and conversation to any dashboard viewer.

### FEAT-069 · Logs viewer
- **Behaviour:** "Logs" tab: raw log as it happens (last 500 on open), filter Everything / Warnings & errors, search, follow, copy. Badge for new warnings.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Diagnostics.
- **Status:** Working [T].
- **Known issues:** Logs contain conversation text; viewable without password.

### FEAT-070 · Latency breakdown and STT quota meter
- **Behaviour:** After each spoken answer to a spoken request: "Last reply 2.8 s · hearing 0.5 · thinking 2.1 · voice 0.2" (measured from end of speech to first audio). Cloud STT quota: "Groq: 1,850 / 2,000 left · resets in 3h 10m", colour-coded when low or out.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Performance tuning.
- **Status:** Working [R][T].
- **Known issues:** —

### FEAT-071 · Admin unlock (dashboard password) with lockout
- **Behaviour:** "Unlock admin" with the password from secrets. Remembered for the browser tab session. Unlocks: voice changes, self-change controls, Claude Code tab, Restart (a persistent Restart button appears top-right). 5 wrong passwords within a minute lock admin actions for a minute ("Too many wrong passwords…"). With no password configured, admin features are off entirely.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Access control.
- **Status:** Working [T].
- **Known issues:** One shared password; no accounts; plain HTTP unless tunnelled.

### FEAT-072 · Dashboard access protection
- **Behaviour:** Listens on localhost by default. Refuses requests whose Host is not localhost, this machine's name (or .local, or its Tailscale name), an IP address or a configured name; refuses cross-site Origins; commands must be JSON. Unknown or outdated commands get a clear "restart to bring it up to date" message; commands before startup completes get "still starting up". Pictures are served with a restrictive content policy.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Security.
- **Settings:** Host, port, allowed host names.
- **Status:** Working [T].
- **Known issues:** Viewing (the event stream) needs no login.

---

## J. Self-modification & code console

### FEAT-073 · Asking it to change itself (propose and approve by voice)
- **Category:** Self-modification
- **Purpose:** Add or change abilities by asking ("make it so when someone says goodnight, you remind them to lock the back door").
- **Trigger:** A recognised (by voice) person asks; the model proposes; a configured admin approves out loud.
- **Inputs:** Request text (≤1,500 chars, written by the model as programmer instructions), the proposer's identity, an admin's spoken okay.
- **Outputs:** Spoken read-back and request for okay; pending suggestion shown on the dashboard with countdown and refused attempts.
- **Experience:** "Zigg, make it so…" → "So: … Alex or Jordan, okay?" → admin: "Yes, go ahead." → "On it."
- **Expected functionality:** Approval is checked by the product, not left to the model: the approving line must come from the mic, after the read-back, from a voice recognised **for sure** as an admin, within 10 minutes. Refusals are spoken with the reason (not an admin, not sure, no change pending, already working, uncommitted code changes, tooling missing).
- **Rules:** Only people known by voice can suggest ("I only take requests to change my code from people I know by voice."). Typed lines never count. Off unless admins are configured.
- **Settings:** Admin list.
- **Dependencies:** FEAT-010, Claude Code, git, tmux.
- **Related:** FEAT-074–076.
- **Errors & edge cases:** Expired suggestion → can still be approved on the dashboard.
- **Status:** Working [R][T] (5 merged changes live).
- **Known issues:** The admin hears a summary; the coding agent gets the full written request.

### FEAT-074 · Making the change (isolated copy, watchable terminal)
- **Behaviour:** Works in a separate copy of the code on its own branch. A coding agent (Claude Code) runs in a real terminal that can be opened on the dashboard to watch, type to it, or answer its questions. It may read/edit files and run tests/linter without asking; anything else waits for an answer. It can never edit version-control internals, environments, data or settings, or read secrets, and can't push. Steps (each tool use) are recorded live. If it asks a question and nobody's watching, the assistant says aloud "Claude Code has a question about my change. It's on the dashboard." Finishes when the agent stops and 20 s pass unwatched, or the admin presses "Finish now"; if watched, the admin decides. "Stop this change" discards it. Exiting early keeps partial work on the branch. Unwatched and unfinished for `timeout_min` (default 30) → stopped.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** As above. Refuses to start if there are uncommitted edits.
- **Settings:** Coding model, timeout.
- **Status:** Working [T].
- **Known issues:** Not a sandbox: tests the agent writes and runs are ordinary code with the user's privileges. Stale working copies remain on disk ([R]: 2 leftover).

### FEAT-075 · Test gate, merge, announcement and restart
- **Behaviour:** Commits on the branch (short subject, full request in the body, "Asked for by X, okayed by Y"). Checks the code still starts and the test suite passes; then fast-forward merges. Outcomes: *switched on* ("…I'll restart now to switch it on."), *kept on branch* (tests failed or code moved on: "I made that change, but it broke my tests, so it's not on."), *nothing changed* (says why), *stopped*, *failed*. Announcement waits up to 30 minutes for a quiet moment. Restart waits until no alarm is ringing, nothing is being said, and the code console isn't busy.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules:** As above.
- **Settings:** Restart after change on/off.
- **Dependencies:** Test tools installed (else only the startup check).
- **Related:** FEAT-076, 078.
- **Errors & edge cases:** A restart mid-change marks it "stopped" after restart.
- **Status:** Working [T].
- **Known issues:** Merges directly onto the running branch with no human diff review. The request text becomes commit history, so personal data spoken in a request can end up in version control (observed).

### FEAT-076 · Self-change history and admin controls
- **Behaviour:** "Self-changes" tab: pending suggestion (Approve / Turn down, even after the spoken window expires), refused okays with reasons, and the last 50 changes, persisted (request, who asked, who okayed, exact instructions given, live steps, files changed, outcome, problem). Admin actions: Open terminal, Finish now, Stop, Try again, Remove, Clear unfinished ones, Delete branch (for kept-on-branch), **Undo** (reverts a switched-on change as a new revertible commit, then restarts; refused if later changes depend on it or there are uncommitted edits). Blocked buttons explain why.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Governance. All need the password.
- **Status:** Working [T].
- **Known issues:** —

### FEAT-077 · Claude Code tab (coding terminal on live code)
- **Behaviour:** Admin-only tab hosting a real terminal running a coding agent in the project folder (on the **live** code, not a copy). Persists if the browser closes; reopening continues the same session; "New session" starts another; sessions are resumable from a shell. It receives the household calendar as context. Same permission limits as FEAT-074. Shows uncommitted files with **Commit them** (message from the chat's requests and the file list) and **Set them aside** (stash). A "restart needed" prompt appears when committed code differs from what's running. Uncommitted edits block voice changes.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Admin development at a keyboard. No automatic test gate. Needs the password and tmux.
- **Status:** Working [R][T].
- **Known issues:** Highest-privilege surface; anyone with the password can run code as the user.

### FEAT-078 · Restart
- **Behaviour:** Admin Restart button (any tab once unlocked) restarts immediately into the code on disk. Automatic restarts after changes and undo wait for a quiet moment. Before restarting, it saves who-was-heard and voice profiles.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Maintenance. Microphone comes back on; chattiness and TV view persist; the conversation transcript is lost.
- **Status:** Working [T].
- **Known issues:** A manual restart cuts off anything in progress, including a ringing alarm (which then rings again after restart).

---

## K. Platform & extensibility

### FEAT-079 · Plugins
- **Behaviour:** Enable named plugin files in settings, with per-plugin settings. A plugin can add actions the model can use, react to any event, add context lines, add dashboard commands, run background jobs, speak when free, and post dashboard notes. A broken plugin is reported and skipped; a crashing plugin job is reported. Plugins can't replace built-in actions or commands. Example: dice roller.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Extensibility. Plugins are trusted code with full access.
- **Status:** Working [T]; none enabled live [R].
- **Known issues:** Plugin dashboard commands are password-free. Slow listeners can miss events.

### FEAT-080 · Validated configuration
- **Behaviour:** One settings file in sections; every key optional with defaults; unknown sections/keys, wrong types, out-of-range numbers, invalid choices, bad quiet hours, malformed phone numbers → a clear startup error naming the problem. Relative paths are relative to the settings file.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Robust setup. Changes take effect on restart (except personality).
- **Status:** Working [T].
- **Known issues:** Some settings are undocumented in the example (see UI-AND-SETTINGS).

### FEAT-081 · Secrets handling
- **Behaviour:** Separate private secrets file with fixed allowed keys (cloud STT key, model API key, model subscription token, dashboard password, SMS account SID and token). Environment variables win. Warns if the file is readable by others. Unknown keys → error. Secrets are withheld from helper programs and the coding agent's environment.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Security.
- **Status:** Working [T].
- **Known issues:** Secrets live in the process environment.

### FEAT-082 · Startup sequence
- **Behaviour:** Dashboard comes up first, showing "Starting up – Loading speech recognition and voice models…" and progress notes. Speech models load in parallel; it learns what its own voice sounds like; loads sound classifier, plugins; pre-warms the model connection; then "Ready. Say 'Ziggletron' and ask something, or just chat nearby." Opens a browser if a desktop is present (unless disabled). Admin commands work during loading; others get "still starting up".
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Lifecycle. Command line: optional settings path, no-browser, verbose.
- **Status:** Working [R].
- **Known issues:** —

### FEAT-083 · Self-healing background jobs
- **Behaviour:** Each continuous job (listening, decision loop, alarm clock, presence, mic meter, vitals, weather, code watcher, voice saving, memory sorting, plugin events) restarts on failure with a growing pause (1 s→60 s), reporting "Something went wrong with … ; starting it again." Individual request errors are reported without stopping the loop.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Reliability.
- **Status:** Working [T].
- **Known issues:** No watchdog for a *hung* (not crashed) job.

### FEAT-084 · Safe, private data storage
- **Behaviour:** Data folder and files readable only by the owner. Saves are atomic and flushed, keeping the previous version as a backup. A damaged file is moved aside and the backup used (or it starts empty), with a dashboard note, rather than refusing to start. Voice profiles are saved in batches every 15 s to spare SD cards.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Reliability and privacy.
- **Settings:** Data folder.
- **Status:** Working [R][T].
- **Known issues:** No export/backup/restore feature for users.

### FEAT-085 · Choice of language-model access
- **Behaviour:** *Subscription* (default): uses the user's Claude plan via the Claude Code CLI, with a pre-started process to hide ~1 s startup. *API*: pay-per-use key. Configurable model (API) or plan model (CLI), effort level, web search. Drawing and memory sorting use their own connections so conversation never waits.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Cost/flexibility. Missing CLI → startup error with guidance.
- **Status:** Subscription Working [R][T]; API Working (source/test only) [T].
- **Known issues:** See P-01 (concurrency). Every reply and join check consumes plan usage.

### FEAT-086 · Install, boot start, TV kiosk
- **Behaviour:** Setup script (any machine) creates the environment and downloads voice and models; a Pi script installs audio, Bluetooth, GPIO and tmux packages and the coding CLI; a user service starts at boot (with lingering) and restarts on failure; a TV script installs a kiosk service that takes over the console screen and shows the TV page from boot.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Deployment.
- **Status:** Working [R] (both services active).
- **Known issues:** Needs a one-time interactive login or a token for the subscription CLI.

### FEAT-087 · Microphone error recovery
- **Behaviour:** On audio-device errors (e.g. USB mic replugged) → "Microphone problem (…); retrying in 5 seconds." and reopens automatically.
- **Category / Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Reliability.
- **Status:** Working (source only) [S] (no tests for the microphone path).
- **Known issues:** —

---

## L. Unmerged / experimental

### FEAT-088 · Music playback (experimental, not merged)
- **Category:** Media
- **Description:** A self-change branch dated 2026-10-28 attempts to add "play a song/artist/genre/playlist, pause, resume, stop, skip, volume; duck music while speaking or listening".
- **Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Not part of the running product.
- **Status:** Not merged [X]. Exists only on a branch.
- **Known issues:** Unknown whether it worked; FEATURES.md lists music as "not built yet".

### FEAT-089 · Explaining its own features from its source (experimental, not merged)
- **Category:** Self-knowledge
- **Description:** A self-change branch dated 2026-11-03 adds "answer questions about how its own features work by having the model read its own source code", approved by an admin, but not merged (later code moved on; a working copy remains).
- **Purpose / Need / Trigger / Inputs / Outputs / Experience / Rules / Settings / Dependencies / Related / Errors:** Not part of the running product.
- **Status:** Not merged [X].
- **Known issues:** —

---

## Ideas named in project docs but not built (not features)

Smart-home control, music/podcasts (see FEAT-088), calendar sync and shared shopping list, multiple languages, local wake word, local fallback brain, HTTPS/accounts, memory editor, backups/export, watchdog. See [KNOWN-PROBLEMS-AND-LIMITATIONS.md](KNOWN-PROBLEMS-AND-LIMITATIONS.md#6-not-built).
