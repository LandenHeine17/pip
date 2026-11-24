// The phone's part of /phone (D-149): one button that makes this phone Pip's microphone and speaker. The rest of the
// page is the monitor's screen (tv.html), laid out for a phone held upright.
//   Tap "Talk to Pip here": the microphone is asked for, and a websocket (/api/phone) carries what it hears to Pip
//   (16 kHz, 80 ms at a time, made by phone-audio.js) and brings back what he says, played straight away.
//   While it has Pip the room isn't listened to and he speaks only here; videos and song clips play here too
//   (window.PipPhone.active, tv.html's soundHere()). Alarms ring in both places.
//   He goes back to the room when you tap again, close the page or lock the phone (the page is hidden), after
//   10 minutes with nobody talking, or when another phone takes over (Pip says why: {"bye": …}).
// Phones only allow the microphone on an https page: on plain http the button leads to Pip's https address.
(() => {
  "use strict";
  const WHY = {
    moved: "Pip moved to another phone.",
    idle: "Nobody talked for a while, so Pip went back to the room.",
    no_audio: "This phone stopped sending sound, so Pip went back to the room.",
    listening_off: "Listening is switched off in the room, so Pip can't come to the phone.",
    unavailable: "Pip's ears aren't running right now. Try again in a minute.",
  };
  window.PipPhone = { active: false };
  let ws = null, ctx = null, stream = null, capture = null, wake = null, playAt = 0, playing = [], why = "";

  const style = document.createElement("style");
  style.textContent = `
    #phone-bar { position: fixed; left: 0; right: 0; bottom: 0; z-index: 5; padding: 1rem 1.2rem 1.4rem;
      display: flex; flex-direction: column; gap: .7rem; align-items: stretch;
      background: linear-gradient(to top, rgba(9,9,12,.97) 70%, rgba(9,9,12,0)); font-family: system-ui, sans-serif; }
    #phone-go { font: inherit; font-size: 2rem; font-weight: 700; padding: 1.2rem; border: 0; border-radius: 1.6rem;
      background: var(--good); color: #0b1a14; cursor: pointer; }
    #phone-go.on { background: #2a2a31; color: var(--ink); }
    #phone-row { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; font-size: 1.3rem; color: var(--muted); }
    #phone-row a { color: var(--muted); text-decoration: none; white-space: nowrap; }
    @media (orientation: landscape) { #phone-bar { left: auto; width: 36rem; } }`;
  document.head.appendChild(style);
  const bar = document.createElement("div");
  bar.id = "phone-bar";
  bar.innerHTML = `<button id="phone-go">🎙 Talk to Pip here</button>
    <div id="phone-row"><span id="phone-note">The room hears and answers as usual until you tap.</span><a href="/">‹ Dashboard</a></div>`;
  document.body.appendChild(bar);
  const btn = document.getElementById("phone-go"), noteEl = document.getElementById("phone-note");
  const note = (text) => { noteEl.textContent = text; };

  function show() {
    const on = window.PipPhone.active;
    btn.classList.toggle("on", on);
    btn.textContent = on ? "Give Pip back to the room" : ws ? "Connecting…" : "🎙 Talk to Pip here";
  }

  async function httpsAddress() {
    try { return (await (await fetch("/api/phone/address", { cache: "no-store" })).json()).url || ""; } catch { return ""; }
  }

  async function start() {
    if (!window.isSecureContext || !navigator.mediaDevices) {   // plain http: go to the https address
      const url = await httpsAddress();
      if (url) { location.href = url + "/phone"; return; }
      return note("Phones only let a page use the microphone on https. Set up Pip's https address first (deploy/install-https.sh).");
    }
    why = "";
    note("Asking for the microphone…");
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: {
        echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    } catch {
      return note("No microphone: allow it for this page (the lock icon by the address), then tap again.");
    }
    try {
      ctx = new AudioContext({ latencyHint: "interactive" });
      await ctx.audioWorklet.addModule("/phone-audio.js");
      capture = new AudioWorkletNode(ctx, "pip-capture", { processorOptions: { rate: 16000, frame: 1280 } });
      ctx.createMediaStreamSource(stream).connect(capture);
    } catch {
      return stop("This browser can't send the microphone's sound to Pip (try Chrome).");
    }
    const sock = ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/phone`);
    capture.port.onmessage = (e) => { if (sock.readyState === 1 && window.PipPhone.active) sock.send(e.data); };
    sock.binaryType = "arraybuffer";
    sock.onmessage = (m) => (typeof m.data === "string" ? said(JSON.parse(m.data)) : play(m.data));
    sock.onclose = () => {   // (not when you gave him back yourself: that's already done)
      if (ws === sock) stop(why || (window.PipPhone.active ? "The connection to Pip dropped, so he's back in the room." : "Couldn't reach Pip."));
    };
    show();
    note("Connecting to Pip…");
    try { wake = await navigator.wakeLock.request("screen"); } catch { /* the screen may lock: Pip goes back then */ }
  }

  function said(msg) {
    if (msg.hello) {
      window.PipPhone.active = true;
      note("Pip is on this phone: talk to him like in the room. The room isn't listened to.");
      show();
    } else if (msg.flush) {
      quiet();
    } else if (msg.bye) {
      why = WHY[msg.bye] || "Pip went back to the room.";
    }
  }

  function play(buf) {   // 4 bytes sample rate, then 16-bit sound: queued right after what's already playing
    if (!ctx) return;
    const rate = new DataView(buf).getUint32(0, true), pcm = new Int16Array(buf, 4);
    if (!pcm.length) return;
    const audio = ctx.createBuffer(1, pcm.length, rate), ch = audio.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;
    const node = ctx.createBufferSource();
    node.buffer = audio;
    node.connect(ctx.destination);
    playAt = Math.max(playAt, ctx.currentTime + 0.05);
    node.start(playAt);
    playAt += audio.duration;
    playing.push(node);
    node.onended = () => { playing = playing.filter((n) => n !== node); };
  }

  function quiet() {   // he was stopped: drop what's queued
    for (const node of playing) { try { node.stop(); } catch { /* already done */ } }
    playing = [];
    playAt = 0;
  }

  function stop(text) {
    const had = ws;
    ws = null;
    window.PipPhone.active = false;
    if (had && had.readyState <= 1) had.close();
    quiet();
    if (stream) stream.getTracks().forEach((t) => t.stop());
    if (ctx) ctx.close();
    if (wake) wake.release().catch(() => {});
    stream = ctx = capture = wake = null;
    show();
    note(text || "Pip is back in the room.");
  }

  btn.onclick = () => (ws ? stop("You gave Pip back to the room.") : start());
  // Locked, switched away or closed: Pip goes back to the room straight away (owner, D-149)
  document.addEventListener("visibilitychange", () => { if (document.hidden && ws) stop("Pip went back to the room when the phone was put away."); });
  addEventListener("pagehide", () => { if (ws) stop(); });
  show();
})();
