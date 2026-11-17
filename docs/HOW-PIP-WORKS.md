# How Pip works: a guide for reading the code

This guide is for someone who wants to open Pip's code and change it. It explains how the pieces fit, follows one request from start to finish, and then explains the **face state machine** in detail: how Pip decides what he's doing and how the monitor shows it.

What Pip *does* is in [`PIP-SPEC.md`](PIP-SPEC.md), why it was designed this way is in [`ARCHITECTURE.md`](ARCHITECTURE.md), and the owner's decisions are in [`DECISIONS.md`](DECISIONS.md). [`PROJECT-MAP.md`](PROJECT-MAP.md) lists every file.


> **Interactive version:** the dashboard's **Guide** button (`/guide`) walks through all of this in the real code: chapters with diagrams you can click, and step-by-step tours (a request from the wake word to the timer ringing, the face, modules, making a module…) that show each function live from the version that's running. Its code links are checked by `tests/web/test_guide.py`: if you rename a function the guide points at, that test tells you which link to update in `web/static/guide.html`.

## 1. The five programs

Pip is five small Python programs. Each runs as its own service on the Pi, so if one crashes the others keep going and it gets restarted.

```mermaid
flowchart LR
  ears["pip-ears<br/>microphone and speaker<br/>(ears/main.py)"]
  core["pip-core<br/>the hub and the brain<br/>(core/main.py)"]
  thinker["pip-thinker<br/>talks to Claude<br/>(thinker/main.py)"]
  web["pip-web<br/>dashboard and monitor pages<br/>(web/main.py)"]
  builder["pip-builder<br/>changes to Pip's own code<br/>(builder/main.py)"]
  ears <-->|messages| core
  builder <-->|messages| core
  thinker <-->|messages| core
  web <-->|messages| core
  web -->|"/ dashboard<br/>/tv the face"| browser["your phone,<br/>the monitor"]
```

| Program | Folder | What it does |
|---|---|---|
| pip-ears | `pip_assistant/ears/` | Listens to the microphone, hears two claps, cuts speech into pieces and sends them all on (pip-core listens for Pip's name in the words, D-134). Plays the chimes and speaks Pip's answers with the Piper voice. Doesn't know what any of it means. |
| pip-core | `pip_assistant/core/` | The hub every message goes through, and the brain. It turns speech into text, decides whether something was said to Pip, waits for you to finish, asks pip-thinker for an answer, and does the actions. It also runs timers and alarms, memory, people and voices, the modules, and **Pip's state**. |
| pip-thinker | `pip_assistant/thinker/` | Builds the prompt and asks Claude. It streams the answer back one sentence at a time, or an `ACTION` line when Claude wants Pip to do something. |
| pip-web | `pip_assistant/web/` | Serves the dashboard (`static/index.html`), the Changes page (`static/changes.html`) and the monitor's face page (`static/tv.html`). It stores nothing itself and asks pip-core (or, for the Changes page, pip-builder) for everything. |
| pip-builder | `pip_assistant/builder/` | Pip changing his own code (§6): keeps the record of each change, starts the sandboxed Claude Code and test jobs, and puts an approved change live. Does nothing until someone asks. |

`Conversation` (in `core/conversation.py`) is big, so its parts live in their own files and it takes them all in: `hearing.py` (speech-to-text, whose voice), `micctl.py` (the Listening switches, claps, prefs), `turns.py` (waiting for you to finish), `enrolment.py` (learning a voice), `context.py` (what Claude is told), `acting.py` (doing actions and Pip's own commands), `feed.py` (the dashboard's conversation feed). `conversation.py` keeps the state, `say()` and the main path from hearing to answering; its docstring has the full list.

Code shared by all five is in `pip_assistant/common/`, and features that plug in (calendar, to-dos, Google, the screen, web search, conversation notes, six seven, smart plugs) are in `pip_assistant/modules/`. Each program starts in its `main.py`.

### Messages

The programs talk by sending small JSON messages through a socket that pip-core owns (`core/bus.py`). Every message has:

- a **type**, such as `heard.utterance` or `pip.state`;
- **`data`**, which is written to the decision trace you can read on the dashboard;
- **`private`**, which is never written anywhere. What people said and Pip's answers always travel here.

There are three kinds of message:

- An **event** goes to everyone who subscribed to its type ("I heard something").
- A **command** goes to one program ("say this").
- A **request** gets a reply ("think of an answer").

`common/messages.py` defines the message; `common/client.py` (`BusClient`) is how ears, thinker, web and builder connect. Inside pip-core, parts react to events with `bus.core_listen(...)`. pip-core answers each request sent to it in its own task, so one slow answer (a typed message waiting for Claude) doesn't hold up the rest; if a program goes away, requests to or from it are answered or forgotten (`_drop_waiting` in `core/bus.py`).

**Staying alive.** Each program starts through `serve()` in `common/program.py`. If one of its main loops (listening, speaking, …) ends, the program exits so systemd restarts it, rather than sending heartbeats while deaf. While pip-core is away (restarting), `BusClient.emit` and `command` drop the message instead of failing. pip-core's watchdog (`core/health.py`) restarts a program whose heartbeats stop, without waiting for systemd (`--no-block`); if pip-core itself was held up, it skips that check, since every heartbeat looks old then.

## 2. One request, start to finish

Say someone says **"Hey Pip, what time is it?"** Here's what happens, and where to find it:

1. **pip-ears hears someone talking** and captures it until they pause (`ears/segmenter.py`). There's no wake-word detector (D-134): everything said in the room is captured, like Ziggletron.
2. **pip-ears sends the audio** as `heard.utterance` (reason `room`), with the audio in `private` and how long it waited in silence (`quiet_s`).
3. `Conversation.queue_utterance` puts it in a queue, so nothing else from pip-ears waits behind speech-to-text; one worker takes each in order. Room speech goes to `Conversation.on_room`, which checks there's a voice in it (not a cough), works out who's talking (`core/people.py`) and turns it into text with Groq (`core/stt.py`).
4. **Was it said to him?** `calls_pip` (`core/names.py`) looks for his name in the words: "Hey Pip, …", "…, Pip?", "thanks, Pip" (Pip, Pippy, Pippletron and close mishearings; never Ziggletron). Yes, so `on_room` cancels anything he was doing and passes it on. Just his name mentioned ("ask Pip later") goes to Claude, which may stay quiet. Neither: it's room talk.
5. `Conversation.addressed` **holds your words** in case you're not finished (`turn_kind` and `turn_wait`, D-106, D-122). The wait is a budget **counted from when you stopped talking** (`speech_ended`), so the pause and speech-to-text already count: a question gets 1.0 s in all, a long statement more, a half-finished sentence 3 s. *Face: "Waiting to see if you're done", with a countdown.*
   **Just his name, on its own** ("what's the weather… Pip?", or "Hey Pip" again because he missed it, D-134): `said_before_name` counts what anyone said in the 30 s before it (since his own last line, `Transcript.recent`). If something was, he chimes and holds the name for `name_alone_wait` (2.5 s from when they stopped; "Hey Pip" is often followed by a pause and then the question) with `ask="name_after"`; and Claude is told the last few lines are probably the question: it answers the most recent one meant for him, or says "Yes?". If they go on talking ("Pip… what time is it"), the pieces are joined and it's an ordinary request. If nothing was said before, he chimes and listens 5 s for the rest. A joined turn waits by its newest piece.
6. Nothing more came, so `respond` takes it. The chime plays (he was called by name). **A clearly worded command** ("what time is it", "set a timer for 10 minutes", "stop": `commands.sure`, D-123) is done at once on the Pi. Anything else goes to `answer`, which sends a `thinker.ask` request with the room transcript, the schedule, who's talking and their memories. A soft thinking tone plays. *Face: thinking, "Working on an answer".* Each step is traced as a `turn.decision` (`decided`), which `tools/latency.py` reads to show where the seconds went.
7. **pip-thinker** (`thinker/main.py`) asks Claude and streams each sentence back as `answer.sentence`.
8. `Conversation.on_sentence` sends each sentence to pip-ears with `ears.say`. *Face: "Getting the words out", then once the voice starts (`speech.started`), speaking, "Answering".*
9. When the voice finishes (`speech.finished`), `_maybe_follow_up` opens a short window to reply without saying his name. It's for **the person he answered** (`follow_up_for` compares voices; anyone if he asked a question, D-124). *Face: listening, "Listening for your reply", with a countdown.*
10. Nobody replies, so the window runs out. *Face: idle, "Say my name if you need me".*

If Claude needs to **look something up** first (the calendar beyond tomorrow, or the web through the `websearch` module, D-125), it replies `ACTION {"do": "look", …}`; `Acting.look_up` asks the module, says "Let me look that up." if that takes over a second, and asks Claude again with what was found. If "stop" or a new request comes meanwhile, the second round is dropped (`requests` counts them; `fetching` keeps the face on "Looking it up"). If Claude says a line first ("Let me check.") and then the look ACTION, it's still looked up. Being called by name sends `thinker.cancel` with no answer id, which stops answers only (not conversation notes or Settings previews), and pip-ears drops a sentence Piper was still making (`Ears.stops`). If Claude replies with an **ACTION** ("set a timer for 10 minutes"), `claude_action` checks all of it with `core/actions.py`, does it (or none of it, if any part is wrong), and says the confirmation (D-84). Each answer keeps **who asked** (`asker`), so if someone else talks while Claude works, the action is still done as the person who asked. Room talk that isn't for Pip goes through `Conversation.on_room` and `room_talk`. It's kept in the room transcript (`core/transcript.py`) and might lead Pip to join in (`core/joining.py`). The transcript Claude reads marks a quiet spell of 5 minutes or more ("(25 minutes later)"), so Claude doesn't bring back a topic that's long over. Claude's answer is cut into spoken sentences as it streams in (`SentenceSplitter` in `thinker/claude.py`); if it starts with notes to itself ("Caleb's asking about…", "Keep it short and cheeky.", "Honest answer, short:"), those aren't said (`NARRATION`, `SELF_NOTE`, `unnarrate`). If the whole reply was only notes, pip-thinker asks Claude once more for just the words to say (`AGAIN` in `thinker/main.py`).

**Talked over** (D-122): pip-ears stops him only if the sound over him has a voice in it (`Ears.heard_over`). pip-core's `on_interrupted` then **pauses** the answer instead of cancelling it. If what was said turns out to be only "yeah", a laugh or nothing (`only_listening`, `listening_only` in `core/turns.py`), `resume_paused` says the rest, starting from the sentence he was cut off in; real words cancel it (`drop_paused`) and become the next turn. A laugh has a voice in it, so it does stop him; speech-to-text then writes it as "haha", "(laughs)" or its phantom "Thank you.", and `listening_only` counts all of those as a laugh. The answer stays paused until what they said comes back, however long they laugh.

**Lowered first, then stopped** (D-128): with `duck_before_stop` on, `Ears.heard_over` first only turns his voice down (`Speaker.gain`, `DUCK_GAIN`). If the voice over him keeps going `DUCK_S` (0.6 s) longer, `talked_over` stops him, keeping what they'd said meanwhile in the recording; if the room goes quiet again (`UNDUCK_AFTER_S`), `unduck` brings him back up and nothing is cut. **"Hold on"** (`pause_word` in `core/turns.py`) keeps the paused answer for up to 30 s with a listening window open (`hold_paused`); "go on" or "okay" resumes it. **Long answers** (D-130) are said in full: `on_sentence` never cuts one short. A spoken answer may end with an ACTION line (`trailing_action` in `thinker/claude.py`, which also stops `SentenceSplitter` there); `answer` does it after he's said everything, with `claude_action(..., quiet=True)`, so a recipe can go in a notes box without "Added the notes box".

**Typed to Pip** (D-131): the front page's chat box posts to `/api/chat` → `core.chat` (`chat` in `core/dashapi.py`) → `Conversation.typed`. That puts the words in the room transcript as the person picked on the page, then runs `respond`, the same path as a finished spoken turn, so Pip's own commands and Claude both work. The request carries a context variable, `TYPED` (who typed it, out loud or not, and what he said), so it affects that request only: Pip talking to the room meanwhile is unaffected. `say` collects what he says for it as the reply, and skips pip-ears unless "out loud" is ticked; Claude's sentences for it are matched by answer id (`typed_answers`). Actions are done as the person picked, never with the room's last voice (`asker_now`). It waits for a spoken answer in progress to finish, opens no follow-up window, and a ringing alarm is answered first, as if it were said (`while_ringing`: "snooze" snoozes, "okay" stops it, anything else goes on to Claude).

**Explaining himself** (D-128): Claude's fixed instructions include what Pip can do (`common/capabilities.py`: the built-in list plus each module's summary). For "why didn't you answer?" Claude replies `ACTION {"do": "look", "kind": "why"}`; `Acting.look_up` sees it's one of `selfknow.KINDS` and answers it in pip-core (`core/selfknow.py`): `why` turns the last 10 minutes of the decision trace into plain lines ("22:46:00 speech-to-text wrote only "Thank you." … ignored"), and `settings`, `health`, `memory_why`, `docs` (his own docs, by section) and `code` (read-only search of his code) work the same way. Then Claude is asked again with that, like any look-up.

**"That was Sam, not me"** (D-128): `Feed.hear` keeps the last few lines' audio in memory (`recent_heard`, never saved). Claude's `correct_speaker` action (`Acting.correct_speaker`) relabels the line before the correction and, if the person correcting is recognised for sure, adds that line's audio to Sam's voice (`People.add_samples`).

**Your data** (D-129): Settings → Your data asks pip-core (`core/yourdata.py`, `/api/data`). Download gathers people, memories, each module's `export_data()`, the settings file and the personality (never voiceprints or secrets.toml). Delete everything needs "DELETE": memories, voices, each module's `wipe_data()`, the transcript. Removing one person calls every module's `forget_person`.

**The face's extras** (D-129): `face_status` (core/dashapi.py) sends the monitor yes/no flags for its corner icons; tv.html refreshes them every minute (`showIcons`). New reactions (`confused`, `amused`, `surprised`, `sleepy` in `FACES`) are set off by `reactIf` from captions and states. `[face]` settings reach the page through `/api/tv` (`live.look`): `eyes()` changes the eye shape in `eyePath`, `colorOf()` the colours.

**Remembering conversations** (D-128): the `recall` module gets every line through the `room_heard` hook. When the room has been quiet 10 minutes, it calls `ctx.summarize(lines)` (a module-neutral call in `core/modules.py`, allowed by its `claude:summarize` permission), which asks pip-thinker (`Thinker.on_summarize`, a separate one-off Claude) for a few lines of notes. Only the notes are kept (`recall_notes`, 30 days); the lines are dropped. Its `summary` hook adds the last day's notes to each request; `look:recall` finds older ones.

**Listening off, alarm ringing** (D-127): `MicControl.ears_mode` tells pip-ears "on" while something rings even if listening is switched off, and `ringing_changed` (a `schedule.watchers` callback) switches it back when the ringing stops. Meanwhile `alarm_only` is true: speech goes only to `commands.while_ringing` (stop, snooze); nothing else is kept in the transcript, shown or sent to Claude.

**"Hey Pip" while it rings:** everything said goes through `commands.while_ringing` first. His name alone calls `Schedule.hush`: the ringing goes quiet, but the alarm isn't answered yet. What's said next decides (`hushed_until`): "snooze" snoozes, anything else stops it and is answered as usual. "Hey Pip, snooze" in one go snoozes. If nothing is said within `hush_s` (20 s), saying his name stopped it, as it always has.

**Phantom "Thank you."** (D-85): sounds with no voice never reach speech-to-text (`has_voice`). But a cough, a laugh or a chair can sound enough like a voice to get through, and Whisper then writes "Thank you." (or "you", "Bye."). So after speech-to-text, `unless_phantom` treats exactly those phrases as noise unless the voice is someone Pip recognises (`is_phantom` in `core/turns.py`); a known voice saying "thank you" still gets an answer. Each one is traced as `heard.phantom`.

## 3. The face state machine

### The idea

Pip is always in **one of 8 states**, each with its own colour:

| State | Colour | Means |
|---|---|---|
| idle | grey | nothing is being asked of him |
| listening | green | listening for or to someone talking to him |
| thinking | purple | working on an answer |
| speaking | blue | his voice is playing |
| interrupted | orange | someone just talked over him |
| ringing | red | an alarm, timer or reminder is going off |
| mic_off | dark grey | listening switched off (claps or the dashboard) |
| offline | slate | no internet: just the basics |

Within each state there's a **detail**, which says what exactly he's doing. Every detail has **words**, which show under the face. Both live in one table, `SITUATIONS`, in [`pip_assistant/core/states.py`](../pip_assistant/core/states.py), which is the file to read first.

### How a change reaches the monitor

```mermaid
flowchart TD
  ev["Something happens<br/>(a sentence heard, the voice stops…)"] --> h["A Conversation method changes a field<br/>(speaking, pending, listen_until…)"]
  h --> r["refresh_state()<br/>core/conversation.py"]
  r --> s["situation()<br/>core/states.py:<br/>the first rule that's true wins"]
  s -->|"(state, detail) changed?"| p["publish pip.state<br/>{state, detail, words, ends_in, quiet}"]
  p --> w["pip-web listen() → on_state<br/>web/main.py"]
  w --> d["dashboard: words by the circle<br/>(index.html setState)"]
  w --> tv["monitor stream /api/tv/stream<br/>tv.html handle('state')"]
  tv --> e["expression() picks a face:<br/>FACES['listening:turn'], or FACES['listening']"]
  e --> a["step() eases the face towards it<br/>30 times a second"]
```

### Side 1: pip-core decides (`core/states.py`)

`situation()` reads fields on the `Conversation` object (grouped under "What the face shows" in `Conversation.__init__`) and asks questions **in this order**. The first one that's true wins, so a higher rule always beats a lower one: an alarm ringing beats everything.

| # | Question | State, detail | Words | Set by (`Conversation`; the file is `core/conversation.py` unless noted) |
|---|---|---|---|---|
| 1 | Is an alarm or timer ringing? | ringing, ring | Say "stop" or "snooze" | `core/schedule.py` |
| 2 | Is his voice playing? | speaking, by why he's talking (`say_reason`) | Answering / Joining in / Saying hi / Saying what I did / Saying something went wrong / Guiding voice learning | `on_speech_started`, `say()` |
| 3 | Is listening switched off? | mic_off, claps_off / switched_off | Clap twice to turn me back on / Switched off on the dashboard | `core/mic.py`, `apply_mic` (`core/micctl.py`) |
| 4 | Did someone talk over him in the last 1.5 s? | interrupted, cut_in | Someone talked over me | `on_interrupted` (`cut_until`) |
| 5 | Is Claude (or a look-up) working on an answer for someone, with nothing said yet? | thinking, asking / searching / looking_up | Working on an answer / Searching the web / Looking it up | `answer`, `on_searching` |
| 6 | Have words been sent to be spoken but not started yet? | thinking, voice | Getting the words out | `say()` (`say_pending_until`, 3 s) |
| 7 | Is speech said to him being turned into text? | listening, hearing | Working out what you said | `on_utterance` (`transcribing`) |
| 8 | Is he holding your words until you're done? | listening, turn (countdown) | Waiting to see if you're done | `addressed` (`core/turns.py`; `pending`, `turn_until`) |
| 9 | Is a listening window open? | listening, called / follow_up / hearing / learning_voice (countdown) | Heard my name: go on / Listening for your reply / … | `listening_for(seconds, why)` |
| 10 | Did the internet drop in the last 2 minutes? | offline, no_internet | No internet: timers and alarms still work | `transcribe` (`core/hearing.py`; `offline_until`) |
| 11 | Is Claude deciding whether a mention was for him? | idle, mentioned | Heard my name mentioned: is it for me? | `on_room` → `answer(kind="mentioned")` |
| 12 | Is Claude deciding whether to join in? | idle, joining | Thinking about joining in | `core/joining.py` → `join_in` |
| 13 | Has there been room talk in the last 4 s? | idle, following | Following the conversation | `on_started`, `on_room` (`room_until`) |
| 14 | None of the above | idle, quiet | Say my name if you need me | |

Some of the waits are settings, in the `[conversation]` section of `pip_assistant/defaults.toml`. You can override them in `~/pip-data/settings.toml`:

- `wait_after_name_seconds`: how long he listens after hearing only his name;
- `follow_up_seconds`: the follow-up window, half as long again if he asked a question;
- `turn_wait_*` and `unfinished_wait_seconds`: how long he waits to see if you're done, counted from when you stop talking (D-122);
- `follow_up_anyone`: off means only the person he answered can follow up without his name (D-124). Their follow-up is asked as `follow_up_likely`: Claude takes it as for Pip but may PASS if it clearly isn't (D-127);
- `own_commands`: "sure" means clearly worded commands are done without Claude (D-123);
- `carry_on_after_backchannel`: off means anything said over him ends his answer (D-126).

Two more are elsewhere: `[social] step_back_minutes` (how long he stays out of the conversation after "not now") and `[stt] drop_phantoms` (a lone "Thank you." from a voice he doesn't know is noise). All of these are on the Settings page, most under **Taking turns**, with a live preview of what they do.

`refresh_state()` is called whenever one of the fields changes. It publishes only when the (state, detail) pair is different, and sets a timer to check again when the next deadline (`listen_until`, `cut_until`, …) passes. When the dashboard reconnects (pip-web restarting), `on_program_connected` sends it the current state, so it never starts blank.

If you tick **"Show what Pip is doing in the Conversation"** on the dashboard, every change also appears in the conversation feed. This is the best way to watch the state machine at work.

### Side 2: the monitor shows it (`web/static/tv.html`)

The face is drawn on a canvas, about 30 times a second. The important parts of the page are:

- **`live`** holds what the page knows: `live.state`, `live.detail` and `live.words` from pip-core, plus reactions.
- **`handle(kind, d)`** is where everything from the stream arrives. A `"state"` message sets `live.state` and calls `setDetail(d)`.
- **`shown()`** gives the state the screen is in. It's usually `live.state`, except for three states of the screen's own:
  - **loading**: the page has just opened;
  - **lost**: it can't reach Pip;
  - **dozing**: idle with nobody talking for 10 minutes. He moves less, as screen care.
- **`expression()`** picks the face. A short **reaction** comes first if there is one: pleased when thanked, perked, or the six-seven freak-out. Otherwise it's `"state:detail"` if `FACES` has that entry (for example `"listening:turn"`), or else the plain state's face.
- **`FACES`** gives each expression as a few numbers the face eases towards. Anything left out uses `REST`. The numbers are:

  | Number | What it does |
  |---|---|
  | `w`, `h` | eye width and height |
  | `open` | how open the eyes are |
  | `happy` | cheeks pushing the eyes into arcs |
  | `slant` | + worried, − focused |
  | `lookX`, `lookY` | where he's looking |
  | `smile` | −1 frown to 1 grin |
  | `mouth` | mouth width |
  | `gape` | mouth open |
  | `lean` | as if leaning in |

- **`COLORS`** sets the colour of each state. A detail uses its state's colour.
- **`WORDS`** is the big word ("Listening"). The smaller words come from pip-core. `SCREEN_WORDS` covers loading, lost and dozing.
- **`step(now)`** is the animation loop:
  - eases every face number towards the target;
  - blinks;
  - glances about: more often when following a conversation, scanning side to side when searching;
  - moves the mouth with his real voice (`speech.level`);
  - drains the countdown bar (`showCountdown`).
- **`draw(now)`** paints it. The hats and sunglasses for the weather, the thinking dots and the floating "zZz" are separate `draw…` functions.

## 4. Making changes

Two cautions before you start:

- Keep the rules in [`CLAUDE.md`](../CLAUDE.md).
- Read [`CONTRIBUTING.md`](../CONTRIBUTING.md). The ones that matter most here:
  - **Never put what anyone said into `data`**, logs or the repo. Only words *about Pip* go in the state.
  - **Owner decisions win.** The colours and states come from D-108 and D-112.

### Common changes

- **Change the words for a situation:** edit `SITUATIONS` in `core/states.py`.
- **Change how a situation looks:** edit its entry in `FACES` in `tv.html` (or add one, keyed `"state:detail"`). Nudge one number at a time.
- **Change a colour:**
  - `COLORS` in `tv.html` (the monitor);
  - `.st-…` in `web/static/pip.css` (the dashboard circle).
- **Add a new situation:**
  1. Add it to `SITUATIONS` with its words, under the state it belongs to. The detail name must not be used by any other state.
  2. In `situation()`, return it from the right place in the order. If it needs new information, add a field in `Conversation.__init__` under "What the face shows", set it where it happens, and call `self.refresh_state()` after.
  3. If it's a wait with a deadline, add that field to the `nxt` list in `refresh_state()`, so the state is checked again when it runs out.
  4. Optionally, give it a face in `FACES` as `"state:detail"`.
  5. Add a line to `tests/core/test_states.py`.

### Trying it

- **Run the tests**, with a memory cap, from the `pip` folder:
  ```
  systemd-run --user --same-dir -p MemoryMax=2000M --wait --pipe --collect uv run pytest -q tests/core/test_states.py tests/web/test_tv.py
  ```
  `tests/web/test_tv.py` checks that every face in `FACES` is a real situation and that every situation has words.
- **See it live:**
  - Pip's services run from `~/pip-app/current`, a separate copy (§7), **not** from this folder. Restarting a service (`systemctl --user restart pip-core`) no longer picks up your edits in `~/pip`.
  - To try a change: **commit it, then run `sh deploy/install.sh`**. It installs the last commit as a new version, restarts Pip at a quiet moment and checks he's healthy; if he isn't, he goes back to the version before by itself. pip-web reads `tv.html` and the dashboard once, when it starts; the monitor reloads itself when pip-web comes back.
  - Never edit `~/pip-app` by hand.
  - Restarting doesn't make a sound, but testing by talking to Pip does: not between 23:00 and 07:00.
- **Watch it:** tick the state bars option on the dashboard, and open `http://<pi-tailscale-ip>:8790/tv` in a browser to see the face next to the feed.

### Making a module

Every new feature is a module: a folder in `pip_assistant/modules/`. Pip finds it at startup; nothing else in Pip changes (`tools/check_map.py` and `tests/core/test_plugins.py` fail if pip-core or pip-web ever name one). ARCHITECTURE §7 explains the design; this is the recipe.

1. **Copy the template:** `cp -r pip_assistant/modules/_template pip_assistant/modules/shopping` (a lowercase name). Folders starting with `_` are never loaded, so the template stays an example.
2. **`module.toml`:** set `name` to the folder's name, a one-line `summary`, `says_examples`, whether it works `offline`, any internet hosts it may reach (`permissions`), and the modules it `needs`. Each setting has a `default` and an `explain` line (they appear on the Settings page). `[claude] instructions` tells Claude its ACTION lines: keep it short.
3. **`module.py`:** `setup(ctx)` returns your Module. Keep the parts you need and delete the rest:
   - `actions`: what Claude can do (`check` first, raising `Invalid` if it doesn't make sense; then `do`, returning what Pip says).
   - `summary(call)`: what Claude should know with every request. It goes with *every* request, so keep it to a line, or nothing.
   - `offline()`: its own phrases when the internet is down (`OfflineItems`; the template's dice, the calendar's `offline.py`).
   - `home()`: a card on the dashboard's front page: a list of dated items (the calendar), or a row of On/Off buttons (the smart plugs, `modules/tuya/`).
   - `tv(shown)` with a `[monitor.<kind>]` table and `static/monitor.js`: a box on the monitor ("put the shopping list up").
   - `dashboard(action, args)` with `static/index.html`: its own page, with a button on the dashboard.
   - `room_heard(text, call)`: hears everything (room talk, and what's said to or about Pip: `call.kind`) and may return something for him to say; for speech to Pip that replaces asking Claude (`modules/six_seven/` is just this). `modules_answer` in `core/conversation.py` offers it at any hour; Pip says the answer only when he's free to talk. To make the monitor's face react, `ctx.publish("reaction", {"reaction": "pleased"})`. Read the words, keep nothing.
   - `tasks()`: background jobs (syncing, checking a website). One that fails is started again after 30 s, then longer, up to 10 minutes (`Registry._guard`).
   - Its data goes in its own tables (`ctx.db()`, named `<name>_…`): it can't read or change any other table, nor reach one through a trigger, a view or an attached database. Its secrets are in its own section of `secrets.toml` (`ctx.secrets`).
4. **Tests:** `test_<name>.py` in the folder, like the template's. `tests/helpers.py` has `registry()`, which loads real modules the way pip-core does, and the other shared fakes; `tests/fakes/` has a pretend Google.
5. **Try it:** run its tests (`uv run pytest pip_assistant/modules/shopping`, memory-capped), add it to `docs/PROJECT-MAP.md` if it's a new kind of thing, commit, then `sh deploy/install.sh`. To switch it off without deleting it: `enabled = false` under `[modules.shopping]` in `~/pip-data/settings.toml`.

Or ask Pip ("Pip, I'd like a shopping list"): his self-changes are told to follow this recipe (§6).

## 5. Settings and the personality

- **Settings** start in `pip_assistant/defaults.toml`; your changes are in `~/pip-data/settings.toml`; `common/settings.py` loads both when a program starts (`load()`).
- The Settings page (`web/static/settings.html`) asks pip-core (`core/settingsapi.py`) through `/api/settings`. `SCHEMA` in `core/settings_schema.py` says, for every setting: its group, label, explanation, allowed values, and **which programs read it**. Saving checks every change (`validate`), writes only what differs from the defaults (`save`), then restarts just those programs at a quiet moment (`quiet()` in `core/states.py`).
- **Seeing what a setting does** (D-126): `PREVIEW` in `settings.html` has a small function per setting that draws what it means from the values on the page, saved or not ("Someone laughs → stops for a moment, then carries on"). `turnViz` draws the turn-taking timeline, and `DEPENDS` greys out a setting that another one switches off. They're only illustrations: the real rules stay in `core/`. After saving, `settle` marks each saved row "applying…" and then "✓ applied".
- **To add a setting:** add it to `defaults.toml` with a comment, read it where it's used, and add a `SCHEMA` entry. `tests/core/test_settings.py` fails if the two don't match.
- **The personality** (who he is, how he talks) is `common/personality.py`: `~/pip-data/personality.md` if you've edited it, else the built-in text, with `{name}` and `{location}` filled in. pip-thinker reads it fresh whenever the file changes, then adds `RULES` (the fixed part: actions, memory, privacy) from `thinker/main.py`.

## 6. How Pip changes himself

Someone asks for a change; Claude Code plans it and, after your first approval, builds it in a sandbox; pip-builder checks it; after your second approval it's committed and installed as a new version (§7). The owner's choices are D-118. Everything is in `pip_assistant/builder/`:

| File | What it does |
|---|---|
| `main.py` | The program: answers `builder.ask` (a new request) and `builder.page` (the Changes page), reads the `[builder]` settings fresh for each job, and pauses on `usage.limited` |
| `store.py` | The record: one row per change in the `builder_changes` table of `~/pip-data/pip.db`. `STATUSES` lists them in order. `request` (the asker's words) is private, and `forget_old` blanks it after `keep_days` (90) |
| `pipeline.py` | The steps, in `Builder`: `_plan`, `_build`, `_check`, `_deploy`, `_watch`/`_finish_release`, `_undo`, `start` (after a restart) |
| `api.py` | What the Changes page can do (`ChangesPage`, part of `Builder`): `ask`, `approve_plan`, `answer`, `approve`, `reject`, `stop`, `undo`, and the lists |
| `checks.py` | `FORBIDDEN` and `SECURITY` (which files may not change, and which make a change security-risk), the diff, the secret scan, and the test run in the strict sandbox |
| `workspace.py` | `Workspaces`: the plain copy Claude edits, and its `.venv` |
| `deploy.py` | `Deployer` (git: branch, commit, revert, fast-forward main; never pushes) and `commit_message` |
| `releasejob.py` | `ReleaseRunner`: runs `release.py install` as its own job and reads the outcome through `release.py` itself |
| `agent.py` | Claude Code: `RULES` (what it's told), `PLAN_TASK` and `BUILD_TASK`, `claude_args` (planning gets read-only tools; building may also edit and run only `tools/sandbox-test`), `StreamParser` (Claude's output → the live steps on the Changes page), `parse_plan` |
| `sandbox.py` | The two sandboxes as `systemd-run` command lines: `agent_command` and `check_command` |

### The steps and statuses

```mermaid
stateDiagram-v2
    [*] --> asked
    asked --> planning
    planning --> questions: Claude has questions
    questions --> asked: you answer
    planning --> plan_ready
    plan_ready --> building: approval 1
    building --> checking
    checking --> ready: checks pass
    checking --> failed: checks fail
    ready --> deploying: approval 2
    deploying --> live: healthy
    deploying --> rolled_back: not healthy
    live --> undone: Undo
    plan_ready --> rejected
    ready --> rejected
```

(Also `stopped`: the Stop button while planning, building or checking.)

1. **Asking.** By voice: Claude answers with `ACTION {"do": "request_change", "summary": …}` (the rule is in `RULES`, `thinker/main.py`); `Conversation.request_change` (`core/acting.py`) passes it on only for a speaker Pip is **sure** of who isn't a guest, and says it'll be on the dashboard. On the dashboard: the request box on the Changes page. Either way pip-builder gets `builder.ask`, with the words in `private`.
2. **Planning** (`_plan`): a fresh workspace (`Workspaces.prepare`): `~/pip-data/builder/work/<id>/src` is a plain export of `main` with **no `.git`**, `base/` an untouched copy to diff against, and `src/.venv` made before Claude starts. Claude Code reads the code and returns a JSON plan. `risk_for` makes it `security` if it touches a `SECURITY` path, whatever Claude said.
3. **Building** (`_build`, after "Build it"): Claude Code edits `src/` and runs the tests with `tools/sandbox-test`.
4. **Checking** (`_check`): `compute_diff` (text files only), `forbidden`, `secret_problems` (`tools/check_secrets.py`), then `strict_check`: the whole suite plus an import check in the strict sandbox.
5. **Going live** (`approve` → `_deploy`): `Deployer.commit_change` copies the changed files into a git worktree on branch `pip/change-<id>`, commits with `commit_message` (from the approved summary, never the request; the normal pre-commit check runs), then `ReleaseRunner.start` runs `release.py install`. That restarts pip-builder too, so the result is read from an exit file in `~/pip-data/builder/releases/` (or `release-state.json`) when it comes back (`_recover_deploy`). When live, `fast_forward_main` moves `main` if `~/pip` is clean; otherwise the page says to merge the branch.
6. **Undo** (`undo` → `_undo`): `Deployer.revert` makes a new commit on `pip/undo-<id>` and installs it the same way.

One change at a time (`kick`). After a restart, anything that was planning, building or checking is marked failed and its workspace deleted (`start`).

### The sandboxes

| | Agent sandbox (`agent_command`) | `tools/sandbox-test` | Strict sandbox (`check_command`) |
|---|---|---|---|
| Runs | Claude Code | The tests, when Claude runs them | The builder's own test and import check |
| Home folder | Empty, except the change's folder and the Claude login file (mounted, never copied) | Empty (no secrets, no login, no `~/.ssh`, no `~/pip`) | Empty |
| Network | Yes (Claude needs it) | **No** (`unshare`: only loopback) | **No** (`PrivateNetwork`) |
| Can change | The workspace (`tools/sandbox-test` and `.venv` are read-only) | The workspace | The workspace |
| Limits | 2 GB, 2 CPUs, 10 min to plan / 30 to build | Inside the agent's | 2 GB, 15 min |

`tests/builder/test_sandbox.py` checks the real sandboxes on the Pi: inside them the secrets file, the Claude login, `~/.ssh`, `~/pip` and the internet are all out of reach.

### Where to look

- **The Changes page** (`/changes`, the Changes button on the dashboard; a badge counts changes waiting for you): ask, answer questions, approve, read the diff and test report, watch the live log, Stop, Undo. pip-web forwards `/api/changes` to `builder.page`.
- **The dashboard:** "Claude today: N requests" (the usage meter, `common/usage.py`) and a notice when Claude's usage limit has paused joining in and self-changes.
- **Settings → `[builder]`:** on/off, model, effort, time limits, how long requests are kept.

## 7. How an update goes live (and comes back)

Pip runs from a separate live copy, never from `~/pip`. Every update, yours or a self-change, goes through `pip_assistant/release.py` (only the standard library, run with the system `python3`, so it works whatever state the versions are in).

```
~/pip-app/
├── releases/<id>/        one folder per version: a git worktree of ~/pip at that commit, its own .venv, a RELEASE file
├── current -> releases/<id>   the running version; the services start from here
├── release-state.json    what's current, what was before, every install and rollback
└── release.log           every progress line
```

**`sh deploy/install.sh`:**

1. Refuses if tracked files in `~/pip` have uncommitted changes (`release.py dirty`). `--allow-dirty` installs the last commit anyway, without them; `--now` skips the wait for a quiet moment.
2. Downloads the models Pip needs into `~/pip-data` (once). If a download fails, it stops and says so.
3. Runs `python3 -m pip_assistant.release install HEAD --install-units` as its own systemd job, so the restarts can't cut it off.

**`Releases.install`** (in `release.py`):

1. `make`: `git worktree add` the commit into `releases/<id>` (the id is the first 10 characters of the commit), `uv sync --frozen --offline`, write `RELEASE`.
2. `wait_quiet`: waits, at most 2 minutes, until pip-core says it's a quiet moment (`quiet` in `pip.state`, read from `/api/health`). That's `quiet()` in `core/states.py`: idle, mic off or offline, with nothing heard, held, ringing, spoken or being answered. Settings restarts (`core/settingsapi.py`) use the same rule.
3. `switch`: copies the service files (with `--install-units`), points `current` at the new version, restarts the programs.
4. `check_health`: up to 2 minutes for every expected program to be `ok` **on this version** (each heartbeat carries `release`, read from `RELEASE` by `common/program.py`), and for `/` and `/api/tv` to answer; then 30 s in which nothing may restart.
5. Healthy: recorded as live; `prune` keeps the newest 3 plus the current and previous. Not healthy: `_recover` switches back to the version before and checks again. On the very first install, it puts back the old service files that ran Pip from `~/pip`.

Exit codes: **0** live, **1** rolled back (healthy again), **2** stopped before anything changed, **3** needs the owner (look at `journalctl --user -u 'pip-*' -n 100`). By hand, from `~/pip`: `python3 -m pip_assistant.release list`, `status` (`status --json` for programs: pip-builder reads it this way), or `rollback`. The exit codes are named in `release.py` (`EXIT_LIVE`…). The tests (`tests/builder/test_release.py`) fake git, uv, systemd and the health check.

## 8. Where to read next

| To understand… | Read |
|---|---|
| everything said in the room, and when Pip answers | `core/conversation.py`: `on_room`, `answer`; `core/turns.py`: `addressed` |
| how fast he answers, and where the time goes | `tools/latency.py` (run it: `uv run tools/latency.py`), `turn.decision` in the trace, `core/turns.py` (the waits), `commands.sure` (instant commands) |
| what Claude is told about the room, people and what's set | `core/context.py` (`for_claude`) |
| when he joins in by himself | `core/joining.py` |
| knowing who's talking | `core/people.py`, `core/peoplecmds.py` |
| timers, alarms, reminders | `core/schedule.py` (saving and ringing), `core/alerts.py` (making and changing them), `common/when.py`, `core/commands.py` (offline phrasings; modules add theirs, e.g. `modules/calendar/offline.py`) |
| what Claude is told, and the ACTION lines | `common/personality.py` (his personality, editable on the Settings page), `thinker/main.py` (`RULES`: the fixed part), `core/actions.py` |
| how a module plugs in | "Making a module" in §4, `modules/_template/`, `core/modules.py` (every hook), `tests/core/test_plugins.py` |
| the boxes on the monitor | `modules/screen/` (the layout), `BOXES` in `tv.html` (the screen's own boxes) and each module's `static/monitor.js` (its boxes) |
| his name in the words, claps, cutting up speech | `core/names.py`, `ears/clap.py`, `ears/segmenter.py` |
| self-changes | `builder/pipeline.py` (`Builder`), `builder/checks.py`, `builder/sandbox.py`, `tools/sandbox-test` |
| installs and rollback | `release.py`, `deploy/install.sh` |
| the usage meter | `common/usage.py`, `thinker/main.py` |
