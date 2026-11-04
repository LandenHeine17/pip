# Technical Audit Notes (LEGACY, NON-AUTHORITATIVE)

> ⚠️ Historical observations about the existing implementation, kept for verification only.
> Nothing here is a recommendation to reproduce the legacy architecture in Ziggletron 2.0.

## 1. Architectural observations (as found)

- **Single process, single asynchronous event loop.** Heavy work (STT, TTS, speaker embeddings) runs on worker threads; the ambient classifier runs on its own low-priority thread; GPIO callbacks on the GPIO library's thread.
- **One ordered inbox** for utterances, typed text, sounds and "join now"; alarms bypass it via a direct callback. Transcription happens *inside* the decision loop, so a slow STT call blocks later inbox items (P/R-01).
- **One cancellable "activity"** models what the assistant is doing; interruption equals task cancellation. Several other subsystems track "in progress" separately (self-change job, console state, alarm "nag" state), a source of past bugs noted in `CODE_REVIEW.md` §5.3.
- **Shared mutable state** object passed to components (dependency-injection style) rather than explicit messages.
- **Event bus** with sticky state events, a bounded history, and separate histories for model requests and logs; the browser receives everything over SSE. The dashboard and TV pages are single large HTML files (~1,540 and ~1,100 lines) with no build step.
- **Model protocol:** text with `[[json]]` action tags instead of native tool calls, so the CLI and API paths behave the same. Actions run *after* their words are generated; outcomes are fed back as transcript lines; one follow-up request on failure.
- **Subscription mode** drives the coding CLI headlessly in streaming-JSON mode with a pre-spawned "warm" process per backend instance; drawing and memory sorting create their own short-lived backends.
- **Self-modification** uses git worktrees, a tmux-hosted interactive coding agent, hook-written JSON event files for agent state, an import check and a pytest gate, fast-forward merge, then `execv` restart.
- **Persistence**: multiple JSON files with atomic write + `.bak` + quarantine-on-corruption; voice embeddings as base64 float16.
- **Configuration**: dataclass-backed TOML with strict validation; secrets copied into `os.environ`.

## 2. Source evidence for unclear or problematic behaviours

### P-01 reply collision
Log 2026-11-02: `RuntimeError: readuntil() called while another coroutine is already waiting for incoming data`, raised while iterating the CLI process's stdout in `brain/backends.py` `ClaudeCodeBackend.stream`, called from `Brain.answer`. In `stream`, the warm process is taken from `self._warm` and only replaced after `await self._spawn(...)`. A second `stream` call that starts during that `await` (e.g. a replacement question, or the failed-action follow-up) can take the same process, giving two readers on one stdout.

### P-02 stale worktrees
`SelfEditing._recover()` marks working records "stopped" after a restart and runs `git worktree prune`, which does not delete existing worktree directories. Normal completion removes the worktree in a `finally`. Live: `.worktrees/change-c2rx3qwh` and `.worktrees/change-kxc9ey3c` remain; branches `ziggletron/20261028-…`, `…20261102-1211…`, `…20261102-1342…`, `…20261103-0024…` remain.

### P-03 silent no-match
`skills/builtin.py`: `forget`, `cancel`, `remove_event` return `None` when nothing matches, rather than a `Failed` note, so no `[didn't work: …]` line is added and no follow-up request occurs.

### P-04 typed "to the room"
`core/commands.py` `say` passes `addressed=False`; `Conductor.on_heard` only computes addressing when `heard.addressed is None`. The UI never sends `None`.

### P-06 targeted reminder wording
`skills/builtin.py` `reminder` description: "it only goes off if their voice has been heard recently", while `core/alarms.py` `went_off` always rings, prefixing "Is X home?".

### P-11 dead UI string
`index.html` handles `join.outcome === "busy"`; `core/joining.py` never emits it.

### P-12 volume
`audio/sounds.py` `set_volume` returns the requested percentage whether or not `wpctl`/`pactl` exist.

### Hidden settings
`max_utterance_s`, `compute_type`, `speaker_model`, `sound_model`, `transcript_lines` exist in `config.py` but not in `config.example.toml`.

### Dashboard snapshot
`web/server.py` reads `index.html` and `tv.html` once at startup, so page edits need a restart.

### Restart semantics
`Assistant.restart(when_quiet=True)` polls every 2 s for no ringing, no nag, no activity, console not running; then saves heard times and voices and `os.execv`s. The dashboard Restart calls it without `when_quiet`.

### Secrets in child environments
`claude_code_env()` passes a minimal allow-list plus `CLAUDE_CODE_OAUTH_TOKEN` to the coding agent and tmux; `_check()` passes `login=False` for test runs; `git`/`wpctl`/`pactl` get `env_without_secrets()`.

### Personal data in commit history
Commit `9c55b4f` subject (a console request) contains a personal phone number verbatim. Not reproduced here.

## 3. Technical limitations

- No tests for live microphone capture, VAD, the presence monitor, or motion sensing.
- About 25 tests rely on fixed sleeps (per `CODE_REVIEW.md`); the suite took ~69 s on the Pi.
- Dependencies are pinned in a constraints file; major versions are capped.
- Event dicts and records are untyped dictionaries with keys spread across modules.
- Several polling loops (`say_when_free` 0.25 s, restart 2 s, monitor 1 s, console 2 s, self-change 1 s).
- Local Whisper is always instantiated as the fallback, even when Groq is used.
- `project_root()` is cached; `running_commit()` is read once at startup.

## 4. Repository hygiene observations

- Working branch `claude-code-terminal` is ahead of `main`; remotes `origin/main` and a local `github-main` exist.
- Files outside version control in the working tree: `config.toml`, `secrets.toml`, `data/`, `voices/`, `models/`, `.worktrees/`, `STATUS.txt`.
- `temp.txt` (tracked) contains the brief for this discovery task.
- `CODE_REVIEW.md` (2026-10-29/11-02) is a prior audit with a fix-status table; most 🔴 items there are marked fixed and were confirmed by tests in this pass (cross-site checks, voice-gate passwords, alarm persistence, empty-audio crash, supervised tasks, corrupt-file recovery).
