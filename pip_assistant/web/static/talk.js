// The conversation feed and the chat box (D-108, D-131), shared by the Overview (the last few lines said to and by
// Pip) and the Talk page (everything heard). Load after /pip.js. The feed is live and never saved.
//   Talk.mount({feed: <element>, chat: <element>, limit: 60, speechOnly: false, all: () => true/false,
//               empty: "…", onPip: () => … (Pip said something: memories and people may have changed)})
const Talk = (() => {
  const EVENTS = {
    chime: () => "Pip chimed: he heard his name",
    state: e => `Pip: ${pip.STATES[e.state] || e.state}${e.words ? " · " + e.words : ""}${e.ends_in != null ? ` (${e.ends_in}s)` : ""}`,
    waiting: e => `Waiting for you to finish (${e.seconds}s)`,
    passed: e => e.about === "mentioned" ? "Pip heard his name and stayed out of it" : "Pip decided not to answer",
    interrupted: () => "Pip was interrupted",
    joined: () => "Pip joined in",
    claps_trial: () => "Heard two claps (trial: the clap switch isn't on, so nothing changed)",
    alarm_mic_on: () => "Mic on for the alarm (listening is off: only \"stop\" or \"snooze\" is heard)",
    alarm_mic_off: () => "Alarm done: mic back off",
    mic_off: e => `Mic off${e.how === "claps" ? " (two claps)" : e.how === "dashboard" ? " (dashboard)" : ""}`,
    mic_on: e => `Mic back on${e.how === "claps" ? " (two claps)" : e.how === "dashboard" ? " (dashboard)" : ""} · was off for ${dur(e.off_for)}`,
    alert: e => `${({timer: "Timer", alarm: "Alarm", reminder: "Reminder", event: "Calendar heads-up"})[e.alert] || "Alarm"} ${e.what === "ringing" ? "ringing" : e.what === "snoozed" ? `snoozed${e.minutes ? ` ${e.minutes} min` : ""}` : "stopped"}`,
    offline: () => "Pip went offline (only the basics work)",
    online: () => "Pip is back online",
    offline_quiet: () => "Offline: that needs the internet, so Pip stayed quiet",
    stt_limited: () => "Speech-to-text's free daily limit is used up: Pip hears on the Pi for now (slower, more mistakes)",
    stt_back: () => "Speech-to-text is back to normal",
  };
  function dur(s) { if (s == null) return "–"; s = Math.round(s); return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : `${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`; }
  // One line: everything heard (grey = not for Pip, green = for Pip), Pip's lines, and event bars
  function line(l) {
    const t = new Date(l.ts * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit", second: "2-digit"});
    if (l.who === "event") return `<div class="evbar"><span>${esc((EVENTS[l.kind] || (() => l.kind))(l))} · ${t}</span></div>`;
    if (l.who === "pip") return `<div class="line pip"><div class="meta">Pip · ${t}${l.first_s != null ? ` · first words after ${l.first_s}s` : ""}${l.local ? " · Pip's own command" : ""}</div>${esc(l.text)}</div>`;
    const forPip = l.who === "you" || l.how === "direct";
    const how = l.how === "mentioned" ? `<span class="how">mentioned Pip</span>` : forPip ? `<span class="how" style="color:var(--good)">to Pip</span>` : "";
    return `<div class="line ${forPip ? "forpip" : "room"}"><div class="meta">${esc(l.speaker || "Someone")} · ${t}${l.stt_s != null ? ` · heard in ${l.stt_s}s` : ""}${l.offline ? " · offline" : ""}${how}</div>${esc(l.text)}</div>`;
  }
  let conv = [], o = {};
  function render() {
    if (!o.feed) return;
    const all = o.all ? o.all() : false;
    const shown = conv.filter(l => (all || l.who === "pip" || l.who === "you" || l.how === "direct") && (!o.speechOnly || l.who !== "event"))
      .slice(0, o.limit || 60);
    o.feed.innerHTML = shown.length ? shown.map(line).join("") : `<div class="empty">${o.empty || "Say \"Pip\" and ask something, or type to him."}</div>`;
  }

  // The chat box (D-131): typed to Pip, answered like the same words said to him, by the person picked
  const store = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } };
  function chatHTML() {
    return `<form class="chat" style="margin:0 0 10px"><input name="text" placeholder="Message Pip…" style="flex:1;min-width:12em" maxlength="500" autocomplete="off">
      <select name="from" title="Who's typing"><option value="">Someone</option></select>
      <label class="sub" style="display:inline-flex;gap:5px;align-items:center"><input type="checkbox" name="aloud"> say it out loud</label>
      <button class="primary">Send</button></form><div class="sub reply" style="margin:-4px 0 10px;white-space:pre-wrap"></div>`;
  }
  async function people(f) {
    let r = {};
    try { r = await getJSON("/api/people"); } catch (e) { return; }
    const keep = f.from.value || store("pip.chat.from") || "";
    f.from.innerHTML = `<option value="">Someone</option>` + (r.people || []).filter(p => !p.guest).map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join("");
    f.from.value = [...f.from.options].some(x => x.value === keep) ? keep : "";
  }
  function chat(el) {
    el.innerHTML = chatHTML();
    const f = el.querySelector("form"), reply = el.querySelector(".reply"), send = f.querySelector("button");
    f.aloud.checked = store("pip.chat.aloud") === "1";
    f.aloud.onchange = () => store("pip.chat.aloud", f.aloud.checked ? "1" : "0");
    f.from.onchange = () => store("pip.chat.from", f.from.value);
    people(f);
    pip.on("people", () => people(f));
    f.onsubmit = async ev => {
      ev.preventDefault();
      const text = f.text.value.trim(); if (!text) return;
      send.disabled = true; reply.textContent = "Pip is thinking…";
      try {
        const r = await postJSON("/api/chat", {text, person: f.from.value, aloud: f.aloud.checked});
        if (r.error) { reply.textContent = r.error; return; }
        reply.textContent = r.reply ? "Pip: " + r.reply : "Pip didn't say anything (he decided it wasn't for him).";
        f.text.value = "";
      } catch (e) { reply.textContent = "Couldn't reach Pip."; }
      finally { send.disabled = false; f.text.focus(); }
    };
  }

  async function mount(opts) {
    o = opts;
    if (o.chat) chat(o.chat);
    try { conv = await getJSON("/api/conversation"); } catch (e) { conv = []; }
    render();
    pip.on("conversation", l => { conv.unshift(l); conv = conv.slice(0, 200); render(); if (o.onPip && l.who === "pip") o.onPip(); });
  }
  return {mount, render};
})();
