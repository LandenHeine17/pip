# Pip 2.0 — Self-Knowledge and Diagnostics (PROPOSAL)

> **One of the owner's top priorities.** Ziggletron cannot explain itself beyond its prompts tab. An unmerged branch tried "explain your own code" by having the model read source (FEAT-089). It was never merged.
> Requirement IDs: PIP-NEW-064–068. Acceptance criteria: [14 §I](14-ACCEPTANCE-CRITERIA.md#i-self-knowledge-and-diagnostics).

## 1. What Pip should be able to answer

| Kind | Examples | Primary evidence |
|---|---|---|
| **Capabilities** | "What can you control?", "Can you text people?" | Capability registry |
| **How it works** | "How does your memory work?", "What happens when I interrupt you?" | Registry + architecture docs |
| **Current configuration** | "What speech recognition are you using?", "What mode are you in?" | Live configuration metadata (sanitised) |
| **Code structure** | "Which part of your software handles alarms?", "How are your plugins organised?" | Read-only source inspection |
| **Recent behaviour** | "Why didn't you answer?", "Why did you think I was talking to you?", "Why did that take so long?" | Decision trace + diagnostic events |
| **Failures** | "Why did the timer fail?", "Why did the mic disconnect?" | Diagnostic events, health history |
| **Changes** | "What changed yesterday?", "Can you undo that?" | Change history ([10 §9](10-SAFE-SELF-MODIFICATION.md#9-change-history-pip-new-076)) |

## 2. Knowledge sources (PIP-NEW-064)

In order of authority:

1. **Live state and decision trace.** What actually happened, from structured events.
2. **Capability registry.** One record per feature (ID, what it does, triggers, settings, limits, dependencies, privacy notes, related code areas, status). It is **part of the codebase**, updated with every change, and checked by the verification pipeline: a change that adds a setting or action without a registry entry fails the "architecture compliance" check.
3. **Sanitised configuration metadata.** Setting names and values, never credentials; provider names, never keys.
4. **Architecture and user documentation.** Versioned with the code.
5. **Read-only source inspection.** For detail beyond the docs.
6. **The model's general training knowledge.** Only for general concepts ("what is a word error rate"), and labelled as such.

## 3. Self-knowledge answers (PIP-NEW-065)
- Answers cite their source in the dashboard view ("from the capability registry, v2.3.1") and, briefly, out loud when asked "how do you know?".
- **Discrepancy reporting:** when docs, registry and code disagree, Pip says so explicitly. For example: "My documentation says reminders for a person only ring if they're home, but the code always rings and asks 'is Alex home?'." (This is P-06, the kind of case Ziggletron had.) Each discrepancy found is logged as a documentation bug and offered as a change request.
- Spoken answers are short. "Show me" puts a longer explanation, or a diagram from the docs, on the dashboard or TV.
- Answers are generated on demand, using the read-only inspection tools (§5) through the model. They are not a static FAQ.

## 4. Explaining recent behaviour (PIP-NEW-066)

### 4.1 The decision trace
Every turn records structured, **content-free by default** decision events, kept for **48 hours**:

| Stage | Example recorded facts |
|---|---|
| Hearing | Speech start/end times; spotter score; AEC/echo verdict; media-speech score |
| Addressing | Verdict (called / mentioned / follow-up / not addressed) and the rule that decided it |
| Mode and consent | Mode; consent state of the people present; gates passed or failed |
| STT | Provider; latency; fallback used; timeout |
| Speaker | Label and confidence level |
| Model | Request kind; latency to first sentence; success/failure class |
| Actions | Lifecycle state per action (12 §1); verification result |
| Speech output | Start, interrupted, stop latency |

Text content (what was said) is held only in temporary conversation context. A "why" question about the **current conversation** can reference it. After the context expires, Pip can explain the **mechanics** but not the words ("At 3:12 someone spoke, but the name detector scored it low, so I treated it as not for me").

### 4.2 Rules for explanations
- **Only observable evidence.** Pip never invents hidden model reasoning. For model-driven decisions (e.g. joining), it can report the inputs and the outcome ("I was asked whether to join; the model replied PASS"), not "what it was thinking".
- **State uncertainty:** "The transcription took 6 seconds. Groq responded slowly, and I can't tell why from here."
- Examples:
  - "Why didn't you answer my last question?" → "I didn't hear my name. The detector scored 'Pip' at 0.41 against a threshold of 0.6. Did you say it at the end? Saying 'Hey Pip' works better from across the room."
  - "Why did you interrupt us?" → "Social mode, nobody had spoken for 1.4 seconds, and the question 'who won in 1998' looked like something I could answer. If that was unwelcome, say 'Pip, not now'."

## 5. Read-only self-inspection (PIP-NEW-067)

A fixed set of **read-only tools** available to the model when answering self-knowledge questions:

| Tool | Returns | Never returns |
|---|---|---|
| `status` | Component health, modes, provider status | — |
| `capabilities` | Registry entries | — |
| `config` | Setting names/values with secrets masked | Credential values, phone numbers |
| `docs.search` / `docs.read` | Architecture and user docs | — |
| `code.search` / `code.read` | Source files in the **application code tree** | Anything outside it: data folder, secrets, home directory, `.git` internals, environment |
| `events.query` | Redacted decision and diagnostic events (time range, type) | Transcript text of non-consenting people; memory contents |
| `changes.list` | Change history summaries | Private request text |

Safeguards:
- An allow-listed root directory; symlinks resolved and rejected if outside; size limits.
- **Output redaction** pass for anything that looks like a credential, phone number or email.
- Tool results are **data, not instructions** (injection through code comments or logs is ignored).
- Tool calls are visible in the Developer section ("Pip read `alarms/…` to answer Alex at 18:40").
- Inspection is available to members. Code-level detail can be limited to admins (setting).

## 6. Proactive self-diagnostics (PIP-NEW-068)

| Severity | Examples | Spoken? | Dashboard |
|---|---|---|---|
| **Critical** (core function lost) | Mic disconnected; speaker missing; alarms can't sound; storage almost full | Yes, once, at the next opportunity (immediately if an alarm is due) | Banner until resolved |
| **Degraded** | Cloud STT failing (using local); model unreachable (offline mode); a background task stalled and restarted | Only if it affects what the person is doing right now ("I'm using offline speech for now, so I might mishear") | Notice |
| **Informational** | An update failed its tests and wasn't deployed; repeated layout failures | No | Notice (plus a self-improvement suggestion, M4) |

Proportionality rules:
- Each problem is announced once per occurrence. Recovery is noted on the dashboard, not spoken unless the person was told about the problem.
- At most 1 unprompted diagnostic utterance per hour, except Critical.
- Never during quiet hours, except Critical problems that affect alarms.
- "Pip, how are you doing?" gives a spoken health summary on demand.
