# Models shipped with Pip (public-safe)

| File | What | Origin |
|---|---|---|
| (none now) | The wake-word detectors that were here were removed with D-134: Pip is called by his name in the transcript, like Ziggletron. They're in git history (before 2026-11-16), as are the `m0/` tools that trained them (removed in D-154). | |

Models trained on a household's own voices (D-72) live in `~/pip-data/models/` and are never committed.
Pip's voice (Piper `en_US-lessac-medium`, ~63 MB) is downloaded into `~/pip-data/models/voices/` by `deploy/install.sh`.
