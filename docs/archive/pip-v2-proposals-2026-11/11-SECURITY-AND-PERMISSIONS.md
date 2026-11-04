# Pip 2.0 — Security and Permissions (PROPOSAL)

> Ziggletron today: no login for viewing or most controls (S-01); one shared admin password over plain HTTP (S-02); unsandboxed self-changes (S-03); voice-approved auto-merge (S-04); injection through room audio, TV and web (S-05); fully trusted plugins (S-07); phone numbers in settings (S-08); secrets in the process environment (S-09). See [KNOWN-PROBLEMS §2](../ziggletron-discovery/KNOWN-PROBLEMS-AND-LIMITATIONS.md#2-security).
> Requirement IDs: PIP-NEW-027, 054–059, 083. **Owner decision:** the dashboard must be reachable away from home.

## 1. Principles
1. **Least privilege** for every client, integration, plugin and agent.
2. **Voice is an identity hint, never proof** of administrative authority.
3. **Every sensitive action is permission-checked on the server**, regardless of UI.
4. **Clients get only the data they need**, including on event streams.
5. **Secrets never appear** in documentation, git, logs, events, prompts or sandboxes.

## 2. Roles and permissions

| Principal | How identified | Can | Cannot |
|---|---|---|---|
| **Admin** | Dashboard account (password + optional passkey/TOTP) | Everything, including settings, accounts, credentials, change approval, deployment, deletion, and **reading all memories** (owner decision D-15; audited) | Approve their own security-sensitive deployments without the second confirmation |
| **Member** | Dashboard account; voice profile for spoken use | Converse; manage own memories, consent and tasks; household calendar, lists and timers; approve memories about themselves; request changes | Settings, credentials, deployments, other members' private data |
| **Read-only viewer** | Dashboard account | View Home, calendar and lists | Change anything; see conversation or memories |
| **Guest (voice)** | Unrecognised voice | Ask questions; timers; class A devices (if enabled) | Memories, messaging, class B/C devices, changes, private information |
| **TV / display client** | Device token, issued once at pairing | Receive display data for its widgets | Commands; full event stream; transcripts beyond captions |
| **Devices (MQTT / ESPHome / Tuya)** | Per-device MQTT credentials; Tuya local keys in the vault | Publish their own state and discovery topics; receive commands for themselves only | Publish to other devices' topics, or reach Pip's dashboard |
| **External integrations** (SMS, STT, model) | Their credentials in the vault | Receive only the data their function needs | — |
| **Plugins** | Declared manifest | Only granted permissions (actions, events, context, network hosts, data scopes) | Anything undeclared; credentials not granted |
| **Coding agent** | Sandbox | Workspace only (see [10 §5](10-SAFE-SELF-MODIFICATION.md#5-isolated-development-pip-new-072)) | Production data, credentials, live code, network beyond the allow-list |

### 2.1 Confirmation tiers (voice)

| Action | Requirement |
|---|---|
| Questions, timers, lists, class A devices, own reminders | Addressed; any voice (guests limited as above) |
| Personal data read-out, own private memories | Voice "sure" and no unconsented/unknown voices present, else "I'll show you on your phone" |
| Texting, class B devices, deleting things, routine edits | Voice "sure" + spoken confirmation |
| Class C devices, unlocking, disabling safety alerts | Voice "sure" + confirmation on the member's signed-in device |
| Settings, accounts, credentials, deployments, forgetting a person | Signed-in admin on the dashboard only |

## 3. Authentication and remote access
- Accounts with hashed passwords, optional TOTP/passkey (recommended for admins), session expiry (30 days on trusted devices, re-authentication for admin actions after 15 minutes), lock-out with back-off (as today, per account), and an **owner recovery path** (a local console command on the Pi).
- **Remote access:** a private overlay network (Tailscale-class; Ziggletron already accepts `*.ts.net` host names) with HTTPS certificates. **No public port forwarding.** Binding to all interfaces is not offered without HTTPS and accounts.
- Host/origin checks kept from Ziggletron (FEAT-072). CSRF protection; JSON commands only.

## 4. Data scoping per client (PIP-NEW-056)
- The event stream is **filtered on the server per client and role**: the TV receives widget data and captions only; viewers receive no conversation; members receive the conversation feed minus unconsented people's lines; admins also receive diagnostics.
- Prompts (the Prompts inspector) are visible to admins only, with memory contents redacted unless that admin may see them.

## 5. Logs and audit (PIP-NEW-057, 059)

| Log | Content | Retention | Who sees it |
|---|---|---|---|
| Operational events | Structured: time, component, event type, error category, latency, IDs. **No speech text, memory text or message bodies** | 30 days (setting 7–90), rotated, size-capped | Admins |
| Audit trail | Who (account, voice label), what action, when, from where (device), result | 1 year (setting) | Admins; members see their own entries |
| Decision trace | [09 §4](09-SELF-KNOWLEDGE-AND-DIAGNOSTICS.md#4-explaining-recent-behaviour-pip-new-066) | 48 hours | Members (own), admins |
| Content debug | Text included, explicitly enabled | ≤24 h, auto-deleted; visible indicator | Admins |

- All logs pass a **redaction filter** (credentials, phone numbers, emails, card-like numbers) before writing.
- Pip's own logs do not go to the system journal with content. Only start/stop and fatal errors go there.
- Logs are searchable in Developer → Logs.

## 6. Migration from Ziggletron (credentials and data) (PIP-NEW-058, 083)

The owner does not want to re-enter API keys. Requirements:

1. **Read-only on the source.** The migration reads Ziggletron's installation at **`~/ziggletron`** (settings file, secrets file and data folder). Pip's project lives at `~/pip`. It **never modifies, moves or deletes** them. Ziggletron stays runnable (rollback option).
2. **Explicit start.** An admin starts the migration on the dashboard (or a console command), and sees a list of the **credential names** found (`groq_api_key`, `claude_code_oauth_token`, `twilio_account_sid`, …), never the values. The admin selects which to import.
3. **Destination: Pip's credential vault.** Encrypted at rest with a device-bound key; file permissions owner-only; never in git; never in events or logs; values never displayed after entry (only "set / not set" and the last 4 characters on request).
4. **Validation.** Each imported credential is optionally tested with a harmless call ("Groq key works", "Twilio credentials accepted"), showing the result only.
5. **Phone numbers** move from Ziggletron's settings into the vault (fixes S-08).
6. **Credentials Pip doesn't use** are not imported (e.g. the dashboard password; Pip uses accounts instead).
7. **Data imported:** memories → Memory Inbox (pending review); reminders, calendar events, TV layouts → directly, with a summary. **Not imported:** voice profiles, conversation history, logs, self-change history (summary only, if wanted), pictures (optional).
8. **Report:** a migration report listing what was imported, skipped and why, with no values.
9. **No secret reaches the coding agent or plugins** unless explicitly granted.

## 7. Integration boundaries
- **Model provider:** receives prompts with only the context the mode, consent and visibility rules allow. Prompts carry device names, sensor values, web results and synced calendar data marked as data, not instructions.
- **Prompt-injection limits** (S-05): unaddressed speech can't trigger actions; media speech is gated; web and calendar text can't trigger class B/C actions, messaging or memory saves without the confirmation tiers.
- **STT providers:** only addressed audio (plus benchmark runs).
- **Plugins:** written only by the owner or by Pip's own self-change (owner decision D-27). They still declare permissions in a manifest, but the model is lighter: no third-party isolation. Disabled on load failure; their dashboard commands are permission-checked (fixes S-07).
- **Devices:** the MQTT broker accepts only the local network, with per-device credentials; a device can publish only its own topics. Tuya local keys and the Google Calendar OAuth token live in the vault.
- **Phone notifications (ntfy, PIP-NEW-086):** prefer a self-hosted ntfy server on the Pi, reached over the same private network as the dashboard. If the public ntfy.sh is used, topics are access-protected and messages carry **only a generic title** ("Reminder from Pip") unless a member enables full content for themselves.
- **Coding agent:** per [10 §5](10-SAFE-SELF-MODIFICATION.md#5-isolated-development-pip-new-072).

## 8. Sensitive git history
- Pip's repository never receives raw requests or personal data (PIP-NEW-076).
- **Ziggletron's existing history** contains at least one commit with a phone number (V-06). **Owner decision D-26: leave the history as is.** Recommendation kept: do not push Ziggletron's history to any public remote, and start Pip in its own repository.
