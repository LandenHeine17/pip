# Models shipped with Pip (public-safe)

| File | What | Origin |
|---|---|---|
| `wake/pippletron.onnx` | "Pippletron" wake-word detector | Trained in M0 on **synthetic voices only** (piper-sample-generator), so it contains no household data |
| `wake/hey_pip.onnx` | "Hey Pip" wake-word detector (v1) | Trained in M0 on **synthetic voices only** |
| `wake/melspectrogram.onnx`, `wake/embedding_model.onnx` | Audio feature models every openWakeWord detector uses | [openWakeWord](https://github.com/dscripka/openWakeWord) v0.5.1 release, Apache-2.0 |

Models trained on a household's own voices (D-72) live in `~/pip-data/models/` and are never committed.
Pip's voice (Piper `en_US-lessac-medium`, ~63 MB) is downloaded into `~/pip-data/models/voices/` by `deploy/install.sh`.
