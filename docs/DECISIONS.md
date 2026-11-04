# Owner decisions

Every decision the owner has made about Pip, numbered `D-nn`. **Decisions override everything else in the docs.** New decisions get the next number and go at the end; a changed decision is edited in place with a note of what changed and when.

78 decisions. Last updated 2026-11-04.

| # | Topic | Decision |
|---|---|---|
| D-01 | Calling Pip | Test "Pip" and "Hey Pip" in the benchmark, then decide |
| D-02 | Default listening mode | **Social** |
| D-03 | Household | Owner + other adults; occasional guests; no children |
| D-04 | Microphone | "rayBit" conference speakerphone |
| D-05 | Model access | Claude subscription via Claude Code, plus a local model fallback |
| D-06 | Cloud budget | Under $10/month |
| D-07 | Migration | Memories (reviewed first), reminders, calendar, TV layouts. Not voice profiles |
| D-08 | First release | Parity with Ziggletron first |
| D-09 | Self-change in parity | Yes: sandboxed, with diff review |
| D-10 | Dashboard | Reachable away from home (secure remote access) |
| D-11 | Voice | Local only |
| D-12 | Speakerphone connection | **USB, used as both mic and speaker** |
| D-13 | Consent for Social mode | **Opt-out** for household adults; everyone is told how; guests are never included **Changed by D-77:** guests and unknown voices *are* included unless they opt out |
| D-14 | "Stop listening" | Only the local name spotter keeps running ("Pip, start listening" works); the mic switch and mute button are the hard off |
| D-15 | Admin access to memories | **Full read** (audited; members are told) |
| D-16 | Default memory visibility | **Household** (private optional per memory) |
| D-17 | Plan limits | **Subscription only**, no API key. On plan limit or outage → local fallback |
| D-18 | Home Assistant | **Not used.** Everything runs on the Pi; Pip is the device hub |
| D-19 | Calendar sync | Google Calendar (M3) |
| D-20 | Music | Local music files on Pip's speaker (M3) |
| D-21 | Unprompted drawings | Kept, off by default |
| D-22 | Mic switch | Persists across restarts |
| D-23 | Coding agent location | On the Pi, resource-limited |
| D-24 | Adopted ideas | Phone notifications, household messages, daily briefing, usage meter (PIP-NEW-086…089). The mute button + LED and the NVMe SSD were later replaced or withdrawn (D-38, D-39). Not adopted: simulator, backups |
| D-25 | Acceptance targets | Accepted as starting values (≤1 false wake/8 h, ≤2% truncation, stop ≤100 ms, first audio ≤2.5 s typical) |
| D-26 | Ziggletron git history | Leave as is (never push it publicly; Pip gets its own repository) |
| D-27 | Plugins | Written only by the owner or Pip's self-change → lighter permission model |
| D-28 | Language | Python |
| D-29 | Milestone 1 | Split into M1a and M1b |
| D-30 | Next step | M0 measurements, then the architecture phase |
| D-31 | Development setup | Build on the Pi; run side by side with Ziggletron |
| D-32 | Locations | Ziggletron at `~/ziggletron` (read-only migration source); Pip project at `~/pip` |
| D-33 | If the Pi can't transcribe the room | Social scales back to local triggers |
| D-34 | Retention | Logs 30 d, audit 1 y, decision trace 48 h, voice review after 180 d unrecognised |
| D-35 | Smaller defaults | Spoken diagnostics critical only; six-seven and reactions kept; 2-factor sign-in for admins |
| D-36 | Device connection | MQTT broker on the Pi + ESPHome devices; Eightree smart outlets via the local Tuya protocol |
| D-37 | Phone notifications | ntfy |
| D-38 | Mute | The speakerphone's built-in mute button; no GPIO button or LED. Detection refined 2026-11-04: long digital silence = "muted or very quiet" (a silent room can also read as zeros) |
| D-39 | Storage | No NVMe SSD; stay on the SD card |
| D-40 | Benchmark providers | Groq only for now (other providers untested until accounts exist) |
| D-41 | Benchmark speakers | The owner only for now |
| D-42 | Repository | New **private** GitHub repo `pip` (account LandenHeine17) holding everything in `~/pip`; secrets, recordings and results excluded |
| D-43 | M0 dashboard | A local page on the Pi, in a separate folder, reachable over Tailscale only |
| D-44 | During M0 tests | Ziggletron may be stopped temporarily and is restarted afterwards |
| D-45 | Tooling | uv and Python packages in a project venv; apt packages only after asking |
| D-46 | Background noise for tests | No TV in the house. "Media" tests use a phone or laptop playing a video or podcast |
| D-47 | Recording phrases | Scripted phrases written for the owner. **No personal names**: Pip's names (Pip, Hey Pip, Ziggletron), generic device names and technical words only |
| D-48 | Speech-to-text for addressed speech | **Groq Whisper Large V3 Turbo with a vocabulary hint**: best result in the M0 benchmark (1–7% words wrong close up; 93% names and terms; P90 0.41 s). Other cloud providers not tested (no accounts) |
| D-49 | Microphone | No hardware upgrade. Pip is tuned for talking from nearer, and asks people to repeat when unsure. Across-the-room accuracy (35–40% words wrong) is accepted for now |
| D-50 | Social mode on the Pi | On-device models tested (Whisper base/small, Moonshine tiny/base) aren't accurate or fast enough for continuous room transcription, so Social mode uses local triggers only (D-33). Newer local models can be re-tested later |
| D-51 | Cloud transcription of the room (f001) | **Keep Ziggletron's way:** in Social mode, everything heard is transcribed by Groq (not only addressed speech). Private mode still sends nothing. The local wake-word detector is still used to decide when Pip is addressed, because Whisper often drops "Pip". Estimated cost $1–4/month; uses the Groq free-tier daily request allowance |
| D-52 | Joining in (f019) | **Ziggletron's way plus a local question check** (owner's idea): a fast on-device check spots questions and invitations in the Groq transcript, and only then is Claude asked whether to chime in; at most every ~3 min (D-58). No per-hour budget or back-off. Interpretation confirmed by the owner |
| D-53 | Conversation feed (f067) | **Keep Ziggletron's way:** no login needed to view it. The dashboard is still reachable only on the owner's devices (Tailscale/localhost) |
| D-54 | Approved behaviour changes | Yes to: person-bound follow-up (f005), guest voices only with consent (f011), admin powers from dashboard login (f014), wait/actually/ducking (f017), recognised-only greetings (f021), memory approval and no silent loss (f028), dashboard approval of code changes (f073), sandboxed coding terminal (f077), plugin permissions (f079) |
| D-55 | Personality tone | Playful and jokey |
| D-56 | Answer length | Medium (a few sentences) |
| D-57 | Jokes | Sometimes |
| D-58 | Joining frequency (Social) | Often: at most every ~3 minutes |
| D-59 | Pronoun | He |
| D-60 | Extra names | "Pippy", "Pips", "Pippletron" (plus "Pip", "Hey Pip") |
| D-61 | Opt-out notice | Owner's wording: "Hello I am Pip, I will transcribe audio to be temporarily saved for the session, if you would like to be left out, just let me know" (confirmed: owner's wording) |
| D-62 | Screen | A monitor is connected for the animated face (the TV-face features apply to it) |
| D-63 | Room and location | Pip's room is "the dorm"; location Lincoln, Nebraska (US units) |
| D-64 | Household | Owner + 1 other adult (roommate), occasional guests |
| D-65 | Claude plan | **Pro.** Tightest usage limits: the usage meter, the local question check and quota-saving rules are essential |
| D-66 | Quiet hours, notifications, Eightree | Quiet hours 23:00–07:00; ntfy self-hosted on the Pi; Eightree plugs not set up yet (later) |
| D-67 | Telling the roommate | The owner tells the roommate about Pip personally (Pip doesn't need to introduce itself first) |
| D-68 | Second wake word | Also train a "Pippletron" detector (after "Hey Pip") |
| D-69 | Running low on Claude usage | Stop joining in **and** pause self-changes together; replies to people always come first |
| D-70 | Open source | The owner **may publish Pip as open source later**. Keep the repo publishable at all times: no secrets or personal data in code or history, an open-source licence ready (MIT recommended, to confirm before publishing), docs written for outsiders |
| D-71 | How the owner works with the code | Understands Pip **from docs and diagrams**, edits **configuration** directly, and may add simple modules only if that's easy without understanding everything. The architecture must therefore offer a documented module template, plain-language settings, and diagrams kept up to date |
| D-72 | Personal data on the Pi | The owner's recordings and anything learned from his voice (e.g. wake-word models trained on his clips) **may be kept on the Pi indefinitely** and used for training. The only hard rule: **nothing personal is uploaded to GitHub**. Replaces the 30-day expiry for the owner's M0 recordings |
| D-73 | Pip's voice (for now) | **Piper "lessac"** (Ziggletron's voice): starts speaking in ~0.3 s. The owner's blind favourite was Kokoro "puck" (4/10 vs 3/10), but it adds ~2–3 s before every answer on the Pi. Swappable later (PIP-NEW-097), e.g. if faster hardware or a Kokoro speed-up appears |

| D-74 | Wake words | **Both "Pippletron" and "Hey Pip"**: say either. Each detector uses ~20% of one CPU core. Sensitivity finalised from the overnight false-wake checks |
| D-75 | Offline fallback AI | **Qwen 2.5 1.5B** (local). Only simple questions, starts with "I'm offline, so this is my best guess", never claims to have done anything; actions always use Pip's offline commands |
| D-76 | Architecture phase | Start now (2026-11-04), while the overnight false-wake checks finish; they only tune wake-word sensitivity |
| D-77 | Room speech sent to Groq (no wake word) | Sent for **everyone**: household members, guests and unknown voices, **except anyone who has opted out**, whose speech never leaves the Pi. Anyone (guests too) can say "Pip, leave me out"; a guest's voice is remembered only for that visit, just to keep them left out. Replaces the guest part of D-13 |
| D-78 | Architecture | **Approved** (2026-11-04) with the D-77 change. M1a building can start |

## Still open (answered by M0 tests)


| # | Question | How it gets resolved |
|---|---|---|
| R-01 | Speakerphone (Anhui LISTENAI "USB Speaker Phone"). **Mute: answered (2026-11-03).** Mute sends no button events, but the audio becomes exact digital silence (all zeros). A quiet room is near-silent but not zero, so Pip can detect mute from the audio. **Still open:** does echo cancellation remove Pip's voice, and is quiet or distant speech removed? | Echo and distance tests (M0) |
| R-02 | Are the Eightree outlets Tuya-based, and can their local keys be obtained? | M0 check |
| R-03 | Self-hosted ntfy on the Pi, or the public ntfy.sh with protected topics? | Recommended: self-hosted; confirm when M3 starts |
| R-04 | Which local LLM for the fallback? | M0 speed test on the Pi |
| R-05 | ~~Kokoro or Piper voice?~~ Answered by D-73 | — |
| R-06 | "Pip" or "Hey Pip"? | Benchmark false-activation numbers |
| R-07 | ~~Which cloud STT for addressed speech?~~ Answered by D-48 | — |
| R-08 | Does the Claude subscription's usage allowance and terms suit an always-on household service plus the coding agent? | Owner checks the plan; usage meter data from side-by-side running |
