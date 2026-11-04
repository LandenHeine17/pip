# User Interfaces and Settings

Ziggletron has five user-facing surfaces:

| Surface | Audience | Interaction |
|---|---|---|
| **Voice** (mic + speaker) | Everyone in the room | Speak; hear replies, chimes, ringtones |
| **Web dashboard** (`/`) | Anyone who can reach it; admin with password | Observe, control, configure some things |
| **TV page** (`/tv`) | Room | Watch only; no controls |
| **Files** (settings, secrets, personality, plugins) | Installer/maintainer | Edit text files; restart for most changes |
| **Service/command line** | Maintainer | Start/stop, logs, flags |

---

## 1. Voice interface

### 1.1 Things people say (recognised by the product itself, not the model)
| Utterance pattern | Effect |
|---|---|
| Its name or nickname as a call ("Zigg, …", "hey Ziggy …", "…, Ziggletron?") | Directly addressed: chime + answer |
| Its name mentioned in passing | Model may answer or stay silent |
| Anything within the follow-up window | Addressed |
| Stop phrases while it talks ("stop", "never mind", "thanks", "got it", "hold on"…) | Goes quiet |
| Acknowledgements while an alarm rings ("okay", "turn it off", "I'm up", "coming"…) | Stops a normal alarm |
| "six seven" | TV face reaction |

### 1.2 Things people ask (interpreted by the model → actions)
Timers ("…until …" for earned alarms), reminders (repeat daily/weekly, for a person, text it), calendar add/remove, cancel, remember/forget, introduce yourself / correct who's talking, text someone, volume (absolute or relative), stop listening for N minutes, TV dashboards (show, make, change, delete, reset default, add/remove/move/resize/colour boxes, face size/position, "what can go on the TV"), draw a picture, find a photo, propose a code change, approve a code change (admins), dismiss an earned alarm (model-judged), plus anything answerable with knowledge or web search, plus plugin actions.

### 1.3 Audible outputs
| Sound | When |
|---|---|
| Two-tone chime | Directly addressed by voice (configurable) |
| Spoken reply | Answers, join-ins, greetings, announcements |
| Ringtone loop (8 choices, 4 loudness levels) | Alarms |
| "Testing, testing. Can you hear me?" | Speaker test |
| Self-change announcements | Results, "Claude Code has a question…", refusals |

---

## 2. Web dashboard

Single page, live-updating, phone-friendly. It reconnects automatically; while disconnected the header shows "disconnected – retrying" and the status shows "Disconnected – Is Ziggletron still running?". Every failed button press shows a toast with the reason.

### 2.1 Header
| Element | Behaviour |
|---|---|
| Name | Assistant name; also the browser tab title |
| Services line | "Claude subscription (via Claude Code) · hearing: Groq Whisper large-v3-turbo" (or local model) |
| Cloud STT quota bar | "Groq: N / 2,000 left · resets in Xh Ym"; turns amber below 15%, red at 0 |
| Last reply timing | "Last reply 2.8 s · hearing … · thinking … · voice …" |
| Quiet-hours chip | Shown during quiet hours: "quiet hours 23:00-07:00 · no joining in" |
| Ringing banner | "⏰ Ringing: pasta timer" / "wake up – until …" |
| Unlock admin | Password field + button; then **Lock** and a persistent **Restart** button (disabled while the code console is busy) |

### 2.2 Status panel
Animated orb + state label + description:

| State | Label | Description |
|---|---|---|
| loading | Starting up | Loading speech recognition and voice models… |
| listening | Listening | Following the conversation. Say "<name>" to ask something. |
| hearing | Hearing speech | Someone's talking. It'll transcribe when they pause. |
| transcribing | Transcribing | Turning speech into text. |
| thinking | Thinking | Working out what to say… |
| speaking | Speaking | Still listening – talk over it to interrupt. |
| dozing | Dozing | Quiet for a while, so it only listens for its name. |
| away | Nobody around | No motion detected, so the mic is off until someone comes in. |
| asleep | Asleep | Mic off until <time>, as asked. |
| paused | Mic off | Not listening. Turn the microphone back on to resume. |

Contextual buttons: **Stop talking** (while speaking), **Wake up** (while asleep), **Shut off alarm** and **Snooze 5 min** (while ringing). Mic level meter ("quiet" hint when near zero).

### 2.3 Controls panel (password-free)
| Control | Options | Persisted across restart? |
|---|---|---|
| Microphone switch | on/off (releases device) | **No** (always on after restart) |
| Joins conversations | Never / Rarely / Sometimes / Often | Yes |
| TV shows | All built-in and saved dashboards (current temporary view shown disabled) | Yes |
| Its own pictures | on/off | Yes |
| Anything to add? | **Ask** button | — |
| Speaker | **Test** button | — |

### 2.4 Household side panel (password-free)
- **Timers & reminders:** each with live countdown (timers) or day/time; 🗓 for event reminders; × cancels.
- **Voices it knows:** name · N samples · "heard 3 h ago" (tooltip with exact time); ✎ rename/merge, × forget (**admin**). Hint about introducing yourself.
- **What it remembers:** each fact with a **kind** dropdown (rule/household/person/note), "about: …" (italic when guessed; ✎ edit, comma-separated), × delete.

### 2.5 Tabs
| Tab | Contents & actions | Password? |
|---|---|---|
| **Conversation** | Feed of heard/typed lines (speaker label button opens "Who said this?" picker), replies (joined in / alarm / cut off), notes, errors, unspoken join decisions, sounds. Composer: text box + **To Ziggletron / To the room** + Send. | Picker: yes |
| **Timers & calendar** (badge when ringing) | Ringing box (Shut off; Snooze 1/5/10/15 min); timers & reminders list; "Start timer" form (minutes, label); month calendar (‹ Today ›), day list, **+ Add event** form (What, Day, all-day, until date, start–end time, Notes, Ring: don't / at start / 5/10/15/30 min / 1 h / 2 h / 1 day before; Save/Cancel/Delete); **Ringtones**: per-kind tone, ▶ preview, loudness, ring duration, speak between rings | No |
| **Prompts** | Full system instructions (collapsible); every model request card (kind, duration, outcome, raw reply, prompt); filter All / Answers / Joining in | No |
| **Self-changes** (badge) | Facts (admins, running commit, enabled); "uncommitted changes in the way" warning with link to console; pending suggestion (request, by, time, countdown, refused okays) with **Approve / Turn down**; history cards (status, request, asked by, okayed by, timings, instructions given, live steps, files, problem, branch) with **Open terminal / Finish now / Stop this change / Try again / Remove / Delete branch / Undo**; **Clear** unfinished | Actions: yes |
| **Claude Code** (badge) | Explanation; status bar (No terminal open / Ready / working / asking you something / committing…); **Open terminal / New session / Close**; embedded terminal; uncommitted files box with **Commit them / Set them aside / Restart Ziggletron**; "restart needed" prompt; recent commit/stash/error messages | Yes |
| **Logs** (badge on warnings) | Live raw log; Everything / Warnings & errors; search; Follow; Copy | No |

### 2.6 Notifications (dashboard notes)
Notes appear inline in the feed (errors highlighted) and in logs. Representative messages:
- Lifecycle: "Loading speech models…", "Ready. Say …", "Restarting…", "Reloaded behavior.md."
- Hearing: "Microphone problem (…); retrying in 5 seconds.", "The microphone is sending pure silence…", "Groq … failed (…); using local Whisper … until it works again.", "… is working again."
- Conversation: "Stopped talking.", "Someone started talking, so I stopped.", "Heard my name, but they were talking about me, not to me."
- Actions: "Timer set: …", "Reminder set for …", "Added to the calendar: …", "Remembered: …", "Forgot: …", "Texted …", "Volume set to N%.", "Microphone off for …", "Couldn't run …: …".
- Alarms: "Shut off the … alarm from the dashboard.", "Snoozed …", "Nobody answered the … timer, so it stopped ringing.", "The reminder … is for X, who I haven't heard lately."
- Voices: "Learning X's voice…", "Got it – that was X.", "Merged X into Y.", "Forgot X's voice."
- Memories: "Sorted N memories by who and what they're about."
- Pictures: "Finished a picture in Ns: …", "Couldn't make the picture of …", "Found a photo in …", "Couldn't find a photo of …".
- Data: "<file> was damaged (…), so it was moved to …; using the backup…".
- Self-change: "Changing my code: …", "Okayed from the dashboard: …", "The change failed its checks…", "Undid a change…".
- Reliability: "Something went wrong with <job> (…); starting it again.", "Plugin 'x' failed to load: …".

### 2.7 Dashboard HTTP behaviours visible to users
| Response | Shown as |
|---|---|
| Wrong password | Admin stays locked; "wrong password" |
| Too many wrong passwords | "Too many wrong passwords. Wait a minute and try again." |
| Unknown command (page newer than server) | "The running Ziggletron doesn't know the command X. Restart it to bring it up to date." |
| Still starting | "Ziggletron is still starting up. Try again in a moment." |
| Blocked action | Specific reason (e.g. "Another change is still being made…") |
| Wrong host / other website | "Unknown host name. Add it to [dashboard] allowed_hosts…" / "Requests from other websites aren't allowed." |

---

## 3. TV page

Full-screen, no controls, offline (serves its own assets and locally stored pictures).

| Element | Behaviour |
|---|---|
| Face box (top-left, ≥3×2 of a 12×8 grid) | Animated face; state label beneath; colour per state; weather/holiday accessory; reactions |
| Captions | Last thing said *to* it ("Alex: '…'", "Someone" if not sure) and its reply; fade after 60 s |
| Alarm banner | Across the whole screen, whichever dashboard is up |
| Boxes | Clock; Calendar (upcoming timers/reminders/events with countdowns); Pi health meters; Weather (now + 4 days); Activity log; Conversation; Memories; Picture (with photo credit); Notes |
| Widget rail | List of box types down the right edge; lights up on request |
| Facts line | e.g. uptime, reply timing, STT quota when running low |
| Offline | Sad face, "can't reach" state; auto-reload when back |
| Screen care | Slow drift; dims when asleep/away; daily browser restart |

---

## 4. Settings (configuration file)

All keys optional. Changes take effect on restart. Unknown keys and invalid values stop startup with a message. ★ = present in code but **not shown** in the example file.

### [assistant]
| Key | Default | Meaning | Affects |
|---|---|---|---|
| name | "Ziggletron" | What it answers to and calls itself | Addressing, prompts, UI, own-memory classification |
| nicknames | [] (example: Zigg, Ziggy) | Other names | Addressing |
| location | "" | Home location | Weather (TV), model context |
| behavior_file | "behavior.md" | Personality file | All replies |
| data_dir | "data" | Where everything is stored | All persistence |

### [brain]
| Key | Default | Choices | Meaning |
|---|---|---|---|
| backend | subscription | subscription, api | How the model is accessed |
| model | claude-opus-5-5 | — | Model for API backend |
| cli_model | "" | — | Model for subscription ("" = plan default) |
| effort | low | low…max | Reasoning effort for replies |
| web_search | true | — | Allow web search |

### [hearing]
| Key | Default | Range/choices | Meaning |
|---|---|---|---|
| mic_device | "" | name or index | Input device |
| vad_threshold | 0.5 | 0–1 | Speech sensitivity |
| silence_ms | 700 | 100–5000 | Pause that ends an utterance |
| max_utterance_s ★ | 20 | 1–120 | Longest utterance |
| stt_engine | auto | auto, local, groq | Transcription engine |
| stt_model | auto | auto/tiny.en/base.en/small.en/large-v3-turbo | Local model |
| compute_type ★ | int8 | — | Local model precision |
| recognize_voices | true | — | Voice recognition |
| speaker_model ★ | models/…titanet_large | — | Voice model (small for Pi 4) |
| sounds | true | — | Ambient sound awareness |
| sound_model ★ | models/…ced-tiny… | — | Sound model |
| sound_threshold | 0.6 | 0–1 | Sound confidence |
| sound_classes | Music, Dog, Doorbell, Alarm, Smoke detector, Laughter, Applause, Baby cry, Knock | — | Sounds to notice |

### [voice]
| Key | Default | Range | Meaning |
|---|---|---|---|
| piper_voice | voices/en_US-lessac-medium.onnx | — | TTS voice |
| speech_rate | 1.05 | 0.3–3 | Speaking speed |
| chime | true | — | Chime when called |
| barge_in | true | — | Listen while talking |

### [conversation]
| Key | Default | Range/choices | Meaning |
|---|---|---|---|
| chattiness | normal | off, quiet, normal, chatty | Join-in frequency (dashboard override persisted) |
| quiet_hours | 23:00-07:00 | "HH:MM-HH:MM" or "" | No joining or greeting |
| follow_up_s | 8 | 0–120 | Follow-up window |
| turn_pause_s | 1.2 | 0.2–10 | Pause before considering a join |
| transcript_lines ★ | 40 | 5–500 | Conversation lines sent to the model |

### [presence]
| Key | Default | Range | Meaning |
|---|---|---|---|
| doze_after_min | 10 | 0.5–1440 | Silence before dozing |
| motion_sensor_pin | 0 | — | GPIO pin of PIR (0 = none) |
| away_after_min | 15 | 0.5–1440 | No motion before away |

### [dashboard]
| Key | Default | Meaning |
|---|---|---|
| host | 127.0.0.1 | Bind address ("0.0.0.0" exposes to the network) |
| port | 8765 | Port |
| allowed_hosts | [] | Extra host names accepted |

### [plugins] and [plugin.<name>]
| Key | Default | Meaning |
|---|---|---|
| dir | plugins | Plugin folder |
| enabled | [] | Plugins to load |
| [plugin.<name>] | — | Free-form settings passed to that plugin |

### [self_edit]
| Key | Default | Range | Meaning |
|---|---|---|---|
| admins | [] | voice names | Who can okay changes ([] = feature off) |
| model | "" | — | Coding model |
| timeout_min | 30 | 1–240 | Unwatched time limit |
| restart | true | — | Restart to switch a change on |

### [texting]
| Key | Default | Range | Meaning |
|---|---|---|---|
| from_number | "" | E.164 | Sender ("" = off) |
| phones | {} | name → E.164 | Recipients |
| daily_limit | 10 | 1–100 | Per person per day |

### [images]
| Key | Default | Range | Meaning |
|---|---|---|---|
| effort | medium | low…max | Drawing quality vs time |
| daily_limit | 20 | 0–500 | Drawings/day |
| own_daily_limit | 5 | 0–100 | Unprompted drawings/day |
| search_daily_limit | 100 | 0–1000 | Photos/day |
| own_search_daily_limit | 30 | 0–500 | Unprompted photos/day |

### Secrets file (names only)
`groq_api_key`, `anthropic_api_key`, `claude_code_oauth_token`, `dashboard_password`, `twilio_account_sid`, `twilio_auth_token`. Environment variables of the corresponding names take precedence.

### Settings changed from the UI (persisted separately)
| Setting | Where changed |
|---|---|
| Chattiness | Dashboard dropdown (overrides file) |
| Ringtones per kind, loudness, ring minutes, speak-between-rings | Timers & calendar tab |
| TV current view, saved dashboards, own-pictures switch | Voice and dashboard |
| Memory kinds/about | Dashboard |

### Personality file
Plain text with `{name}` placeholder and HTML comments stripped; reloaded automatically.

### Command line
`ziggletron [settings-file] [--no-browser] [-v/--verbose]`.

---

## 5. Settings that affect multiple features

| Setting | Features affected |
|---|---|
| name / nicknames | Addressing, chime, stop logic, memory classification, STT hint, UI labels, greetings |
| location | TV weather and clothing, model context (local questions) |
| data_dir | Every persisted feature |
| recognize_voices | Speaker labels, targeted reminders, admin approval (self-change impossible without it), echo-by-voice, greeting by name |
| stt_engine | Privacy, accuracy, latency, quota meter |
| chattiness / quiet_hours / doze | Join-ins, greetings, model usage |
| barge_in | Interruption, echo handling, alarm responses |
| admins | Self-change enablement, voice-protection rules |
| dashboard host | Who can view and control everything |
| dashboard_password | All admin features and the code console (off when empty) |
| brain backend/effort | All replies, latency, cost; drawing and sorting backends |
| images limits | Pictures and photos, prompt context about allowances |
