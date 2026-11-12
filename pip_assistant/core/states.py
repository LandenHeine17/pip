"""Pip's states: what his face, the dashboard's state circle and the Conversation's state bars show.

This file is the whole "face state machine" on pip-core's side. See docs/HOW-PIP-WORKS.md for the full
picture, including the monitor's side (web/static/tv.html).

Two levels:

    state    one of 8, each with its own colour on the face and the dashboard (D-108):
             idle (grey), listening (green), thinking (purple), speaking (blue), interrupted (orange),
             ringing (red), mic_off (dark grey), offline (slate)
    detail   what exactly he's doing within that state (D-112), e.g. listening → "turn" = he's holding
             what you said, waiting to see if you're done. Every detail has plain words, shown under
             the face. They're words about Pip only, never what anyone said, so they're safe to log.

`situation()` works out the current (state, detail) from the Conversation's fields. It's a list of
questions asked in order; the first one that's true wins. pip-core calls it again whenever one of
those fields changes (Conversation.refresh_state), and publishes `pip.state` if the answer changed.

To change what Pip shows:
    the words         edit SITUATIONS below
    a new situation   add it to SITUATIONS, return it from situation(), and (optionally) give it an
                      expression in tv.html's FACES, keyed "state:detail"
    the face          web/static/tv.html: FACES (expressions), COLORS, WORDS (the big state word)
"""
import time

# Every situation, grouped by state, with the words shown for it. Order within a state doesn't matter.
SITUATIONS = {
    "idle": {
        "quiet":          "Say my name if you need me",              # nothing going on
        "following":      "Following the conversation",              # people are talking, not to him
        "mentioned":      "Heard my name mentioned: is it for me?",  # Claude decides whether to answer
        "joining":        "Thinking about joining in",               # Claude decides whether to join in
    },
    "listening": {
        "called":         "Heard my name: go on",                    # a wake word or his name, waiting for the rest
        "hearing":        "Working out what you said",               # speech-to-text on what was said to him
        "turn":           "Waiting to see if you're done",           # holding your words for the end of your turn (D-106)
        "follow_up":      "Listening for your reply",                # after answering, in case you reply
        "learning_voice": "Learning a voice",                        # reading the sentences on the dashboard
    },
    "thinking": {
        "asking":         "Working on an answer",                    # Claude is working on it
        "searching":      "Searching the web",
        "looking_up":     "Looking it up",                           # the second round after a look-up (D-102)
        "voice":          "Getting the words out",                   # the answer is being turned into speech
    },
    "speaking": {
        "answer":         "Answering",
        "joining_in":     "Joining in",                              # joining a conversation nobody asked him into
        "welcome":        "Saying hi",                               # "Hey Landen, welcome back"
        "confirming":     "Saying what I did",                       # "Timer set for ten minutes"
        "trouble":        "Saying something went wrong",             # "Sorry, I didn't catch that"
        "coaching":       "Guiding voice learning",
    },
    "interrupted": {
        "cut_in":         "Someone talked over me",
    },
    "ringing": {
        "ring":           "Say “stop” or “snooze”",
    },
    "mic_off": {
        "claps_off":      "Clap twice to turn me back on",
        "switched_off":   "Switched off on the dashboard",
    },
    "offline": {
        "no_internet":    "No internet: timers and alarms still work",
    },
}

# The same words looked up by detail alone (every detail name is used in only one state).
DETAILS = {detail: words for details in SITUATIONS.values() for detail, words in details.items()}

# Why he's talking (the `reason` passed to Conversation.say) → the speaking detail.
SPEAKING = {"answer": "answer", "searching": "answer", "welcome": "welcome", "command": "confirming",
            "alarm answered": "confirming", "error": "trouble", "not caught": "trouble", "offline": "trouble",
            "enrol": "coaching"}


# States where restarting Pip's programs cuts nothing off (with nothing else going on: see quiet()).
QUIET_STATES = {"idle", "mic_off", "offline"}


def quiet(c) -> bool:
    """Is now a good moment to restart Pip's programs (a settings change, an install)? Nothing he's doing or
    about to do would be cut off: not talking, ringing, hearing or holding what someone said, and Claude isn't
    working on anything. The one rule for both: core/settingsapi.py and release.py (via `pip.state`)."""
    return (getattr(c, "state", "idle") in QUIET_STATES and getattr(c, "answer_id", None) is None
            and not getattr(c, "pending", "") and not getattr(c, "transcribing", False)
            and not getattr(c, "ringing", False) and not getattr(c, "speaking", False))


def situation(c) -> tuple[str, str, float]:
    """(state, detail, ends_at) for the Conversation `c`. ends_at: when a wait he's in runs out (a
    time.monotonic() value; 0 if he isn't waiting a known time). The face shows it as a countdown."""
    now = time.monotonic()
    working = c.answer_id is not None            # Claude is working on something for him
    if c.ringing:                                 # an alarm, timer or reminder is going off
        return "ringing", "ring", 0.0
    if c.speaking:                                # his voice is playing
        if c.say_reason == "answer" and c.voice_kind == "join":
            return "speaking", "joining_in", 0.0
        return "speaking", SPEAKING.get(c.say_reason, "answer"), 0.0
    if c.mic is not None and not c.mic.listening:   # listening switched off (D-91)
        return "mic_off", "switched_off" if not c.mic.dashboard else "claps_off", 0.0
    if now < c.cut_until:                         # someone just talked over him (shown for 1.5 s)
        return "interrupted", "cut_in", 0.0
    if working and c.answer_kind == "direct" and c.filler_said in (None, "searching"):   # until his first words
        if c.filler_said == "searching":
            return "thinking", "searching", 0.0
        return "thinking", "looking_up" if c.looking_up else "asking", 0.0
    if now < c.say_pending_until:                 # words sent to pip-ears, not playing yet
        return "thinking", "voice", 0.0
    if c.transcribing:                            # speech-to-text on something said to him
        return "listening", "hearing", 0.0
    if c.pending:                                 # holding your words until your turn is over (D-106)
        return "listening", "turn", c.turn_until
    if now < c.listen_until:                      # a listening window: called / follow_up / hearing / learning_voice
        return "listening", c.listen_why, c.listen_until
    if now < c.offline_until:                     # speech-to-text fell back to the Pi in the last 2 minutes
        return "offline", "no_internet", 0.0
    if working and c.answer_kind in ("mentioned", "follow_up"):   # "is that for me?" (D-124)
        return "idle", "mentioned", 0.0
    if working and c.answer_kind == "join":
        return "idle", "joining", 0.0
    if now < c.room_until:                        # room talk being heard, or a room line in the last 4 s
        return "idle", "following", 0.0
    return "idle", "quiet", 0.0
