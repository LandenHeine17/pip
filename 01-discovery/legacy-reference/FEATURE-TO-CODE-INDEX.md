# Feature-to-Code Evidence Index (LEGACY, NON-AUTHORITATIVE)

> ⚠️ **This appendix exists only so readers can verify discovery claims against the legacy source.**
> It is **not** a design for Ziggletron 2.0. Module names, classes and structure listed here are
> historical facts about the old implementation and must not be treated as requirements.

Paths are relative to the repository root at `efc08c2`. `src/` means `src/ziggletron/`.

## 1. Feature → source

| Feature | Primary evidence | Tests |
|---|---|---|
| FEAT-001 Listening | `src/audio/microphone.py` (Segmenter, Microphone), `src/audio/vad.py`, `src/core/assistant.py` (`_listen`) | `test_config_and_audio.py` (segmenter) |
| FEAT-002 STT | `src/speech/stt.py` (`create_transcriber`, `GroqWhisper`, `LocalWhisper`, `WithFallback`, `auto_model`, `normalize`) | `test_stt.py` |
| FEAT-003 Called by name | `src/speech/text.py` (`calls_by_name`, `mentions_name`, `GREETINGS`), `src/conversation/turns.py` (`address`) | `test_text.py`, `test_conversation.py` |
| FEAT-004 Mentioned | `src/conversation/turns.py`, `src/core/answering.py` (`unsure`), `src/brain/prompts.py` (`ANSWER_IF_FOR_YOU`), `src/brain/parsing.py` (`is_pass`) | `test_assistant.py::being_talked_about…`, `test_parsing.py` |
| FEAT-005 Follow-up | `src/conversation/turns.py` (`spoke`, `follow_up_until`), `src/core/conductor.py` (`_greet` resets) | `test_conversation.py` |
| FEAT-006 Chime | `src/core/conductor.py` (`on_heard`), `src/audio/sounds.py` (`chime`) | `test_assistant.py` |
| FEAT-007 Echo rejection | `src/audio/echo.py`, `src/speech/text.py` (`is_echo`), `src/memory/voices.py` (`SELF`, `learn_own_voice`, `learn_echo`), `src/core/hearing.py`, `src/audio/speaker.py` (`said_since`, `ECHO_TAIL_S`) | `test_echo.py`, `test_text.py`, `test_voices.py` |
| FEAT-008 Hygiene | `src/speech/stt.py` (`HALLUCINATIONS`, `_only_the_name`) | `test_stt.py` |
| FEAT-009 Sounds | `src/audio/ambient.py`, `src/core/conductor.py` (`on_sound`), `src/conversation/transcript.py` (`add_sound`) | `test_config_and_audio.py`, `test_assistant.py` |
| FEAT-010–013 Voice ID & management | `src/memory/voices.py`, `src/core/hearing.py` (`introduce`, `correct`, `_relabel`), `src/core/assistant.py` (`name_voice`, `correct_line`, `rename_voice`), `src/core/commands.py` | `test_voices.py`, `test_voice_corrections.py`, `test_security.py` |
| FEAT-014 Admin protection | `src/core/assistant.py` (`name_voice`), `src/core/commands.py` (privileged voice commands) | `test_self_edit.py::admins_voices_cant_be_claimed…`, `test_security.py` |
| FEAT-015 Answering | `src/core/answering.py`, `src/brain/__init__.py` (`answer`), `src/brain/parsing.py` (`SpeechSplitter`) | `test_assistant.py`, `test_parsing.py` |
| FEAT-016 TTS | `src/audio/speaker.py`, `src/speech/text.py` (`speakable`), `src/core/speaking.py` | `test_config_and_audio.py`, `test_tv.py` (speaker level) |
| FEAT-017–018 Barge-in, cut-off | `src/core/conductor.py`, `src/core/activities.py`, `src/speech/text.py` (`is_stop_request`), `src/core/speaking.py` (`record`) | `test_assistant.py`, `test_text.py` |
| FEAT-019–020 Joining, Ask | `src/core/joining.py`, `src/conversation/turns.py` (`may_join`, `JOIN_INTERVAL_S`), `src/brain/prompts.py` (`JOIN`, `MOODS`), `src/brain/parsing.py` (`parse_join_reply`) | `test_assistant.py`, `test_conversation.py` |
| FEAT-021 Greeting | `src/core/conductor.py` (`GREET_AFTER_QUIET_S`, `_may_greet`) | `test_assistant.py::welcomes_people_back…` |
| FEAT-022 Personality | `behavior.md`, `src/brain/prompts.py` (`BehaviorFile`, `SYSTEM`) | `test_assistant.py` |
| FEAT-023 Context | `src/core/assistant.py` (`situation`), `src/memory/household.py` (`describe`), `src/core/alarms.py` (`situation`), `src/core/self_edit.py` (`situation`), `src/texting.py` (`situation`), `src/core/screen.py` (`situation`) | `test_assistant.py`, `test_screen.py` |
| FEAT-024 Web search | `src/brain/backends.py` (tools, `--tools WebSearch`) | — |
| FEAT-025 Actions/outcomes | `src/skills/__init__.py` (`Skills.run`, `Failed`), `src/core/acting.py`, `src/core/answering.py` (follow-up), `src/brain/prompts.py` (`FOLLOW_UP`) | `test_assistant.py::claude_finds_out…`, `test_skills.py` |
| FEAT-026 Failure speech | `src/core/answering.py`, `src/brain/__init__.py` (`Refused`) | `test_backends.py` |
| FEAT-027 Typing | `src/core/commands.py` (`say`), `src/core/conductor.py` (`typed`), `src/web/static/index.html` (composer) | `test_assistant.py` |
| FEAT-028–032 Memory | `src/memory/household.py` (memories), `src/memory/kinds.py`, `src/core/assistant.py` (`_sort_memories`), `src/brain/__init__.py` (`sort_memories`), `src/brain/prompts.py` (`SORT_MEMORIES`), `src/skills/builtin.py` (`remember`, `forget`), dashboard memory rows in `index.html` | `test_household.py`, `test_memory_kinds.py`, `test_assistant.py`, `test_dashboard_browser.py` |
| FEAT-033–036 Timers, reminders, calendar | `src/memory/household.py`, `src/skills/builtin.py` (`timer`, `reminder`, `add_event`, `remove_event`, `cancel`), `src/core/commands.py` (`add_timer`, `save_event`, `remove`), `index.html` (calendar) | `test_household.py`, `test_timers_end_to_end.py`, `test_skills.py` |
| FEAT-037–041 Alarms | `src/core/alarms.py`, `src/memory/household.py` (`run_alerts`, `_ring_again_after_restart`, `snooze`, `finished`), `src/speech/text.py` (`is_acknowledgement`), `src/audio/sounds.py` (`RINGTONES`, `LOUDNESS`) | `test_assistant.py`, `test_timers_end_to_end.py`, `test_household.py` |
| FEAT-042 Cancel/remove | `src/memory/household.py` (`cancel`, `cancel_event`, `remove`, `_best_match`) | `test_household.py` |
| FEAT-043 Texting | `src/texting.py`, `src/skills/builtin.py` (`text`, `reminder.text_to`), `src/core/alarms.py` (`_text`) | `test_skills.py`, `test_assistant.py` |
| FEAT-044 Volume | `src/audio/sounds.py` (`set_volume`, `get_volume`), `src/skills/builtin.py` (`volume`) | — |
| FEAT-045–046 Sleep, mic | `src/core/monitor.py` (`sleep`, `wake`, `apply_presence`), `src/core/commands.py` (`set`, `wake`), `src/skills/builtin.py` (`sleep`) | `test_skills.py` |
| FEAT-047 Speaker test | `src/core/commands.py` (`test_speaker`) | — |
| FEAT-048–050 Presence | `src/conversation/presence.py`, `src/core/monitor.py` | `test_conversation.py` (quiet hours) |
| FEAT-051 Muted-mic warning | `src/core/monitor.py` (`_check_silent_mic`) | — |
| FEAT-052–054 Face, weather clothing, six-seven | `src/web/static/tv.html` (FACES, `wearing`, `sixSeven`), `src/core/weather.py` (`dress`) | `test_tv.py`, `test_screen.py::the_face_dresses…` |
| FEAT-055–058 TV dashboards & layout | `src/core/screen.py`, `src/core/layout.py`, `src/skills/display.py`, `tv.html` (SECTIONS, `showDashboard`) | `test_screen.py` |
| FEAT-059–061 Pictures & photos | `src/core/pictures.py`, `src/core/photos.py`, `src/core/screen.py` (`draw`, `find_photo`), `src/brain/__init__.py` (`draw`), `src/brain/prompts.py` (`DRAW`), `src/web/server.py` (`_picture`) | `test_screen.py` |
| FEAT-062–063 TV privacy, resilience | `tv.html` (`handle`, CAPTION_S, `lost`, `backAgain`, `creep`), `scripts/tv-kiosk.sh`, `systemd/ziggletron-tv.service` | `test_tv.py` |
| FEAT-064 Weather | `src/core/weather.py` | `test_screen.py` |
| FEAT-065 Vitals | `src/core/vitals.py`, `src/core/monitor.py` (`vitals`) | `test_tv.py` |
| FEAT-066–070 Dashboard | `src/web/static/index.html`, `src/web/server.py`, `src/events.py`, `src/core/monitor.py` (`publish_*`), `src/core/answering.py` (`_Timing`) | `test_dashboard_browser.py`, `test_security.py` |
| FEAT-071 Admin unlock | `src/core/commands.py` (`password_ok`, lockout), `index.html` (unlock) | `test_security.py`, `test_dashboard_browser.py` |
| FEAT-072 Access protection | `src/web/server.py` (`_only_from_here`, `host_allowed`) | `test_security.py` |
| FEAT-073–076 Self-change | `src/core/self_edit.py`, `src/claude_code.py`, `src/core/terminal.py`, `src/core/repo.py` | `test_self_edit.py` |
| FEAT-077 Console | `src/core/console.py`, `src/core/terminal.py`, `src/web/server.py` (`_terminal`) | `test_console.py`, `test_dashboard_browser.py` |
| FEAT-078 Restart | `src/core/assistant.py` (`restart`), `src/core/console.py` (`restart_now`) | `test_dashboard_browser.py` |
| FEAT-079 Plugins | `src/plugins.py`, `plugins/example.py` | `test_plugins.py`, `test_security.py` |
| FEAT-080–081 Config, secrets | `src/config.py`, `config.example.toml`, `secrets.example.toml` | `test_config_and_audio.py`, `test_security.py` |
| FEAT-082–083 Startup, self-healing | `src/__main__.py`, `src/core/assistant.py` (`load`, `run`, `_supervised`) | `test_assistant.py::a_background_job_that_fails…` |
| FEAT-084 Storage | `src/storage.py` | `test_storage.py`, `test_security.py` |
| FEAT-085 Model access | `src/brain/backends.py`, `src/claude_code.py` (`find_claude`, `claude_code_env`) | `test_backends.py` |
| FEAT-086 Install & services | `scripts/*.sh`, `systemd/*.service`, `constraints.txt`, `pyproject.toml` | — |
| FEAT-087 Mic recovery | `src/audio/microphone.py` (`utterances`) | — |
| FEAT-088 Music (unmerged) | branch `ziggletron/20261028-1537-add-the-ability-for-ziggletron` (13 files, +235 lines) | branch tests (not run) |
| FEAT-089 Explain own code (unmerged) | branch `ziggletron/20261103-0024-add-the-ability-for-ziggletron` (adds a `core/explaining.py`; 4 files, +206 lines); working copy under `.worktrees/` | branch tests (not run) |

## 2. Dashboard command → feature

| Command | Privileged | Feature |
|---|---|---|
| say | no | FEAT-027 |
| join_now | no | FEAT-020 |
| stop | no | FEAT-017 |
| wake | no | FEAT-045 |
| set (mic, chattiness) | no | FEAT-046, 019 |
| test_speaker | no | FEAT-047 |
| dismiss, snooze | no | FEAT-039 |
| add_timer | no | FEAT-033 |
| save_event | no | FEAT-036 |
| remove | no | FEAT-032, 042 |
| sort_memory | no | FEAT-032 |
| ringing, preview_ringtone | no | FEAT-040 |
| tv_show, tv_own_pictures | no | FEAT-055, 060 |
| label_line, rename_voice, forget_voice | **yes** | FEAT-012, 013 |
| check_password | **yes** | FEAT-071 |
| restart | **yes** | FEAT-078 |
| console_open/close/commit/set_aside | **yes** | FEAT-077 |
| change_approve/reject/stop/finish/retry/remove/clear/delete_branch/undo | **yes** | FEAT-074–076 |
| *(plugin-defined)* | no | FEAT-079 |

## 3. Model action → feature

| Action | Unprompted allowed | Feature |
|---|---|---|
| timer, reminder, cancel | no | FEAT-033, 034, 042 |
| add_event, remove_event | no | FEAT-036, 042 |
| text | no | FEAT-043 |
| dismiss | no | FEAT-038 |
| remember | **yes** | FEAT-028 |
| forget | no | FEAT-029 |
| name_voice | **yes** | FEAT-011 |
| volume, sleep | no | FEAT-044, 045 |
| show_dashboard, make_dashboard, delete_dashboard | no | FEAT-055, 056 |
| add_box, remove_box, move_box, color_box, show_widgets | no | FEAT-057 |
| draw, find_photo | no | FEAT-059, 061 |
| draw_on_your_own, find_photo_on_your_own | **yes** | FEAT-060, 061 |
| propose_change, approve_change (only with admins configured) | no | FEAT-073 |

## 4. HTTP surface

| Route | Purpose | Auth |
|---|---|---|
| `GET /` | Dashboard page (snapshot at startup) | Host/Origin checks only |
| `GET /tv` | TV page | Host/Origin checks only |
| `GET /pictures/<12-hex>.<ext>` | Stored pictures (restrictive CSP, nosniff) | Host/Origin checks only |
| `GET /events` | Server-Sent Events stream (all events) | Host/Origin checks only |
| `POST /command` | JSON commands | Password for privileged ones |
| `GET /terminal/{console,change}` | WebSocket terminal attach | Password in first message |
| `GET /static/*` | Bundled terminal library | Host/Origin checks only |

## 5. Persisted files (data folder)

`household.json` (memories, alerts, events) · `voices.json` (+ `voices.old.json` on model change) · `heard.json` · `ringing.json` · `preferences.json` · `screen.json` · `pictures.json` + `pictures/` (+ `pictures/failed/`) · `self_edits.json` · `console.json` · `texts.json` · `terminal/` (tmux socket, config, `*.events`) · `*.bak` backups · `*.damaged-<timestamp>` quarantined files.
