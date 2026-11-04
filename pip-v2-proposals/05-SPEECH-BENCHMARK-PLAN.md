# Pip 2.0 — Speech Benchmark Plan (PROPOSAL)

> Purpose: choose Pip's microphone setup, segmentation settings and STT provider from measurements in the real room, not from vendor claims. Related: [04-SPEECH-RECOGNITION-RESEARCH.md](04-SPEECH-RECOGNITION-RESEARCH.md).

## 1. Principles

- **Same audio for every provider.** Record once, then replay identical files to each provider and each local model.
- **Test the pipeline, not only the model.** Each recording is captured through each candidate microphone setup, so mic and AEC choices are measured too.
- **Consent first, and temporary by default** (§2).
- **Representative of real use:** the living room at real distances, with the real TV, real voices and real background.

## 2. Consent and data handling for test recordings

| Rule | Detail |
|---|---|
| Opt-in only | A **Benchmark recording** mode is switched on from the dashboard by an admin. The TV shows a red "Recording for speech tests" banner and Pip says so out loud when it starts |
| Scripted participants | Every speaker in a recording has agreed. Recording stops automatically if an unrecognised voice is detected and was not added as a participant |
| Scope | Only during a scheduled session. Never left on |
| Storage | Encrypted, inside Pip's data folder, in a dedicated `benchmark/` area. Never in version control, never in logs |
| Expiry | Deleted automatically after 30 days unless an admin marks individual clips "keep for regression tests". Each person can delete any clip they appear in |
| Sending to providers | Clips are sent only to providers enabled for the test run. The run screen lists them before starting |
| Participant view | Each participant can list and play back clips they are in |

## 3. Test set

Aim for about 300 utterances (around 25 minutes of speech), from at least 3 speakers, including a child's voice if the household has children and they consent. Each utterance gets a human-checked reference transcript and labels.

| Category | Examples | Count |
|---|---|---|
| Clear, near (1 m) | "Pip, what's the weather tomorrow?" | 30 |
| Quiet | Same phrases spoken softly | 25 |
| Distant (3–5 m, other side of room) | Mixed commands | 30 |
| Background media | Commands while a phone or laptop plays a video or podcast at normal volume a few feet away (no TV in the house, D-46) | 40 |
| Multiple speakers | Two people talking; one addresses Pip | 25 |
| Names and vocabulary | Household names, device names, project words | 30 |
| Short commands | "Stop", "Pip, lights off", "Snooze" | 30 |
| Interruptions (over Pip's speech) | Pip plays a long reply; speaker says "wait", "actually…", a new question | 25 |
| Pauses and corrections | "Set a timer for… um… twelve, no, fifteen minutes" | 25 |
| **Negatives (should not trigger)** | Speech that never addresses Pip, words like *tip, pick, peep*, TV dialogue | 40 + a 2-hour room soak (§3.1) |

### 3.1 Soak test for false activations
Two hours or more of ordinary household audio, captured with consent, including TV and music. The **local name spotter runs live on it, and the audio is not stored**: only spotter events are kept (time, score), and a participant reviews each event within the session ("was Pip called?"). This measures false activations per hour without archiving private audio.

## 4. Configurations under test

1. **Microphone:** current mic · **owner's speakerphone over USB, playing Pip's audio through it** · (fallback) far-field array with AEC.
2. **Segmentation:** fixed 700 ms (Ziggletron baseline) · adaptive rule · provider end-of-turn (streaming providers only).
3. **Pre-processing:** array AGC only · plus software boost (Ziggletron's +30 dB rule).
4. **Wake form:** "Pip" · "Hey Pip".
5. **STT:** each provider from the [comparison](04-SPEECH-RECOGNITION-RESEARCH.md#3-provider-comparison-2026-11-03), with and without the vocabulary list.

The full cross-product is too large. Run it in stages, carrying the best option forward each time:
mic (fixed provider: Groq Turbo baseline) → segmentation → wake form → providers × vocabulary.

## 5. Metrics

| Metric | Definition | How measured |
|---|---|---|
| **WER** | Word error rate against the reference, after normalising numbers and punctuation | Automatic |
| **Entity accuracy** | Names, devices and numbers transcribed exactly | Automatic, using labelled entities |
| **Command interpretation accuracy** | Pip's intent parser (local or LLM) produces the intended action from the transcript | Run the transcript through Pip's interpreter; compare with the labelled intent |
| **Wake-name recall** | Share of utterances that addressed Pip and were detected as addressed | Automatic |
| **False activations** | Per hour (soak) and per negative clip | §3.1 |
| **Missed utterances** | Speech present but nothing produced | Automatic |
| **Truncated utterances** | Reference words missing at the start or end of the transcript | Automatic (alignment) |
| **Vocabulary insertions** | Vocabulary words appearing where not spoken | Automatic |
| **Latency** | End of speech → final transcript; for streaming, also end of speech → end-of-turn event | Replay in real time; P50 and P90 |
| **Interruption stop time** | Speech onset over Pip → Pip's audio stops | Live test, measured on a loopback recording |
| **Cost** | Estimated $/month at Private-mode and Social-mode usage profiles | From billed audio, including per-request minimums |
| **Fallback reliability** | Network cut and provider-error injection during replay: time to fall back, lost utterances | Scripted fault injection |
| **Pi resource use** (local models) | CPU %, RAM, temperature over a 1-hour continuous run | Vitals log |

## 6. Decision rule

1. Discard configurations with **truncation above 2%** or **false activations above 1 per 8 hours** (target; owner to confirm).
2. Rank the rest by **command interpretation accuracy** first, then **entity accuracy**, then **P90 latency**, then cost.
3. A provider becomes the default only if it beats the Groq Turbo baseline on command accuracy by a margin larger than the run-to-run variation (repeat runs ×3).
4. Publish the results table on the dashboard's Speech panel, so the choice can be reviewed and re-run later.

## 7. Re-running

- The benchmark is a repeatable tool. Re-run it when a provider releases a new model, when the microphone changes, or quarterly.
- Clips kept for regression tests (§2) form a small permanent set, held only with participants' consent.
