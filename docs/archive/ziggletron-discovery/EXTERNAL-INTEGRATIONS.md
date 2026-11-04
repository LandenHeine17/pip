# External Integrations

Every outside service or external program the current product communicates with, what it is used for, what crosses the boundary, how failure appears to users, and whether it is required.

## 1. Network services

| # | Service | Used for | Required? | Credentials (names only) | Data sent | Failure behaviour (user-visible) | Evidence |
|---|---|---|---|---|---|---|---|
| I1 | **Anthropic Claude via Claude Code CLI** ("subscription") | All replies, join-in decisions, drawing pictures, memory sorting | Yes, one of I1/I2 | Interactive CLI login, or `claude_code_oauth_token` for running as a service | See DATA §4 | Short spoken apology on direct requests; silent otherwise; dashboard error. Startup error if the CLI is missing | [R][T] |
| I2 | **Anthropic Claude API** ("api") | Same as I1 | Alternative to I1 | `anthropic_api_key` | Same | "Can't reach the Claude API – is the internet up?" / "Claude API error N: …" | [T] |
| I3 | **Provider web search** | Current facts | Optional (on by default) | via I1/I2 | Model-chosen queries | Part of the model's reply | [S] |
| I4 | **Groq speech-to-text** (hosted Whisper large-v3-turbo) | Accurate fast transcription | Optional | `groq_api_key` | Every utterance's audio (WAV), name hint | Automatic fallback to on-device STT with one notice; recovery notice; quota meter shows remaining requests and reset time | [R][T] |
| I5 | **Open-Meteo** geocoding + forecast | TV weather box and face clothing | Optional (TV only) | none | Place name; coordinates | "Can't get the weather right now." on the TV; retries every 5 min | [R][T] |
| I6 | **Wikimedia Commons** | Real photos for the TV | Optional | none (identifies itself with a descriptive user agent) | Search words; image downloads only from Wikimedia hosts | "Couldn't find that picture: …"; spoken apology if asked | [R][T] |
| I7 | **Twilio SMS** | Text messages and texted reminders | Optional | `twilio_account_sid`, `twilio_auth_token`; sender number and recipient map in settings | Numbers, message text | "Twilio refused it (error N)" / "couldn't reach Twilio" → failed action, spoken correction | [T]; configured live, delivery not observed |
| I8 | **Model/voice downloads** (Hugging Face for TTS voice; GitHub releases for speaker and sound models) | Install time only | Install | none | — | Install script fails | [S] |
| I9 | **Claude Code installer** | Install time (Pi) | Install | — | — | — | [S] |

**Integration contract with the model (observable):** the product sends one system instruction and one user prompt per request and receives streamed text. The reply contains spoken sentences plus trailing action tags of the form `[[{"do": "<action>", …}]]`; join checks reply `PASS` or `SAY: …`; name-mentioned replies may be `PASS`; drawings reply with a single SVG; memory sorting replies with a JSON array. This tag-based protocol was chosen so both access modes behave identically. It is not native tool-calling.

## 2. Local programs and OS services the product drives

| # | Program/service | Used for | Required? |
|---|---|---|---|
| L1 | Audio capture/playback stack (PortAudio over ALSA/PipeWire/PulseAudio) | Mic input, speech and ringtone output; Bluetooth speakers via PipeWire | Yes |
| L2 | `wpctl` (PipeWire) or `pactl` (PulseAudio) | Reading and setting system volume | For volume control |
| L3 | `git` | Self-changes (branches, isolated copies, commits, merge, revert), console commit/stash, "running version" display | For self-change/console |
| L4 | `claude` CLI (Claude Code) | Model access (subscription) and the coding agent (self-changes, console) | Subscription mode; self-change |
| L5 | `tmux` | Persistent, attachable terminals for the coding agent | Self-change/console |
| L6 | Python test runner | Self-change test gate | Optional (else startup check only) |
| L7 | GPIO (gpiozero) | PIR motion sensor | Optional (Pi only) |
| L8 | systemd (user service + system kiosk service), loginctl linger | Start at boot, restart on failure | Deployment |
| L9 | Chromium in `cage` (Wayland kiosk) on tty1 | TV display | TV only |
| L10 | Linux `/proc` and `/sys` | Machine vitals | TV vitals |
| L11 | System journal | Log persistence | Deployment |
| L12 | Web browser (any) | Dashboard | Yes, for dashboard |

## 3. On-device models (no network at runtime)

| Model | Purpose |
|---|---|
| Voice-activity detector | Finding speech |
| Whisper (local; tiny/base/small/large-v3-turbo) | Offline STT and fallback |
| Speaker-embedding model (large or small) | Voice recognition, own-voice detection |
| Audio-tagging model (AudioSet classes) | Ambient sound awareness |
| Piper TTS voice | Speech output |

## 4. Remote access methods (documented)

| Method | Status |
|---|---|
| Local only (default, 127.0.0.1) | Default |
| SSH tunnel to port 8765 | Recommended in docs |
| Tailscale (machine's `*.ts.net` name accepted automatically) | Supported by host check |
| Binding to all interfaces on the LAN | Supported, warned against (no login for viewing, no HTTPS) |
| Custom host names | Via allowed-hosts setting |

## 5. Extension integrations

Plugins (trusted local code) can integrate anything, e.g. the documented "lights via a smart-home bridge" pattern. No smart-home, music, or calendar-sync integrations exist in the running product.

## 6. Integration risks to carry into redesign

- **Single model vendor**, accessed through a CLI designed for coding. Subscription mode depends on the CLI's output stream format and flags.
- **Cloud STT sees all room audio** when enabled.
- **Twilio numbers in a plain settings file**, not the secrets file.
- **Coding agent integration has user-level privileges** (tests are ordinary code).
- **No offline fallback** for the reasoning step.
- Weather shown on the TV (Open-Meteo) and weather spoken (model web search) come from different sources.
