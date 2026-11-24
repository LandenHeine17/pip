// Shared helpers for Pip's dashboard pages (/, /talk, /people, /settings…) and module pages (/m/<name>/). Used with
// /pip.css. (The monitor's page, tv.html, stands alone.) Every page with a <main> also gets the dashboard's frame
// (D-148): the menu (a sidebar on a laptop, tabs at the bottom on a phone), and the status bar along the top with
// Pip's state, the Listening switch, a ringing alarm's Shut off / Snooze, and the sentences to read while Pip
// learns a voice. A page doesn't do anything for it: loading this script is enough.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
// A value inside an onclick="…": the browser turns esc()'s &#39; back into ' before the script runs, so a name
// like D'Andre would end a '…' string early. JSON gives a "…" string instead (no quotes around ${js(x)}).
const js = v => esc(JSON.stringify(v ?? ""));
// Any of Pip's /api/… routes
async function getJSON(url){ const r = await fetch(url, {cache: "no-store"}); return r.json(); }
async function postJSON(url, body){
  const r = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)});
  return r.json(); }

// Talk to this page's module in pip-core: pip.get("range", {from, to}) · pip.post("add", {...})
// (or another module, last argument: pip.get("status", {}, "<its name>"))
const pip = {
  module: location.pathname.split("/")[2],
  async get(action, args = {}, module = pip.module) {
    const q = new URLSearchParams({action, ...args});
    const r = await (await fetch(`/api/m/${module}?${q}`, {cache: "no-store"})).json();
    if (r.error) throw new Error(r.error);
    return r;
  },
  async post(action, body = {}, module = pip.module) {
    const r = await (await fetch(`/api/m/${module}`, {method: "POST", headers: {"Content-Type": "application/json"},
                                                          body: JSON.stringify({action, ...body})})).json();
    if (r.error) throw new Error(r.error);
    return r;
  },
  // Pip's live news (/api/stream), one connection per page however many parts listen:
  // pip.on("alerts", data => …) calls the function with each event of that kind (its data already read).
  _es: null,
  on(kind, fn) {
    if (location.search.includes("nolive")) return;
    if (!pip._es) pip._es = new EventSource("/api/stream");
    pip._es.addEventListener(kind, ev => { let d = {}; try { d = JSON.parse(ev.data); } catch (e) {} fn(d); });
  },
  // Call fn whenever this module (or the timers/alarms) says something changed
  live(fn) {
    pip.on("module", d => { if (d.module === pip.module) fn(); });
    pip.on("alerts", () => fn());
  },
  async act(promise) { try { return await promise; } catch (e) { alert(e.message || "Couldn't reach Pip"); } },
  // Every module that's on (/api/modules: its page, menu group and icon, what it does), fetched once per page
  _mods: null,
  modules() { return pip._mods || (pip._mods = getJSON("/api/modules").catch(() => [])); },
  // Shown in the status bar by every page: Pip's states, in words
  STATES: {idle: "Idle", listening: "Listening", thinking: "Thinking", speaking: "Speaking", interrupted: "Interrupted",
           ringing: "Ringing", offline: "Offline", mic_off: "Not listening"},
};

// ---------------------------------------------------------------- the frame: menu and status bar (D-148)
(() => {
  // The menu. Modules aren't named here: each says in its module.toml which group it's in, its icon, and whether
  // it's also a tab on a phone (group, icon, tab under [dashboard]).
  const TOP = [["/", "✦", "Overview"], ["/talk", "🗨️", "Talk"], ["/people", "👥", "People"], ["/memory", "🧠", "Memory"]];
  const GROUPS = [["house", "Around the house"], ["fun", "Fun"], ["pip", "Pip himself"], ["", "More"]];
  const SYSTEM = [["/system", "🩺", "Health & trace"], ["/prompts", "✳️", "Claude"], ["/changes", "🛠️", "Changes"], ["/guide", "📖", "Guide"]];
  const END = [["/settings", "⚙️", "Settings"]];
  let mods = [], waiting = 0;

  // The page this is, as the menu knows it: a person's profile is under People, a module's other files under it
  function here() {
    const p = location.pathname;
    if (p === "/person") return "/people";
    if (p.startsWith("/m/")) return `/m/${p.split("/")[2]}/`;
    return p;
  }
  function link([href, icon, name], on) {
    const badge = href === "/changes" && waiting ? `<span class="pip-badge">${waiting}</span>` : "";
    return `<a href="${esc(href)}" class="${href === on ? "on" : ""}"><span class="pip-ic">${esc(icon || "•")}</span><span class="pip-nm">${esc(name)}</span>${badge}</a>`;
  }
  function navHTML() {
    const on = here();
    let html = TOP.map(l => link(l, on)).join("");
    for (const [g, title] of GROUPS) {
      const ms = mods.filter(m => (m.group || "") === g);
      if (ms.length) html += `<div class="pip-grp">${title}</div>` + ms.map(m => link([m.page, m.icon, m.label], on)).join("");
    }
    html += `<div class="pip-grp">System</div>` + SYSTEM.map(l => link(l, on)).join("");
    return html + `<div class="pip-grp"></div>` + END.map(l => link(l, on)).join("");
  }
  function renderNav() {
    $("pip-nav").innerHTML = navHTML();
    $("pip-sheetnav").innerHTML = navHTML();
    const on = here();
    const tabs = [TOP[0], TOP[1], ...mods.filter(m => m.tab).slice(0, 1).map(m => [m.page, m.icon, m.label]), TOP[2]];
    const inTabs = tabs.some(t => t[0] === on);
    $("pip-tabs").innerHTML = tabs.map(([href, icon, name]) => `<a href="${esc(href)}" class="${href === on ? "on" : ""}"><span class="pip-ti">${esc(icon)}</span>${esc(name)}</a>`).join("")
      + `<a href="#" id="pip-more" class="${inTabs ? "" : "on"}"><span class="pip-ti">☰</span>More${waiting ? `<span class="pip-badge">${waiting}</span>` : ""}</a>`;
    $("pip-more").onclick = ev => { ev.preventDefault(); $("pip-sheet").classList.add("open"); };
  }

  // Pip's state, and within it what exactly he's doing (D-112), with a countdown while he waits a known time
  let tick = null;
  function setState(s, d = {}) {
    $("pip-state").className = "face st-" + s;
    clearInterval(tick);
    const ends = d.ends_in != null ? Date.now() + d.ends_in * 1000 : null;
    const show = () => { const left = ends ? Math.max(0, (ends - Date.now()) / 1000) : null;
      $("pip-statetext").textContent = (pip.STATES[s] || s) + (d.words ? " · " + d.words : "") + (left != null ? ` · ${left.toFixed(1)}s` : ""); };
    show();
    if (ends) tick = setInterval(show, 100);
  }
  // The Listening switch (D-91)
  let mic = null;
  function renderMic(m) {
    if (!m || m.dashboard === undefined) return;
    mic = m; const b = $("pip-mic");
    b.className = "micbtn" + (m.listening ? "" : " off");
    b.innerHTML = m.listening ? "Listening" : !m.dashboard ? "Mic off <small>· tap to turn on</small>" : "Mic off <small>(clapped)</small>";
    b.title = m.listening ? "Switch listening off" : "Switch listening back on (or clap twice, if a clap microphone is chosen in Settings)";
  }
  async function toggleMic() {
    try { renderMic(await postJSON("/api/mic", {value: !(mic && mic.listening)})); } catch (e) { alert("Couldn't reach Pip"); }
  }
  // The running release (versioned installs, PIP-NEW-080): a short commit id, or an object with one
  function renderRelease(r) {
    const sha = !r ? "" : typeof r === "string" ? r : (r.sha || r.id || r.commit || "");
    const at = r && typeof r === "object" && r.time ? new Date(typeof r.time === "number" ? r.time * 1000 : r.time) : null;
    $("pip-release").textContent = sha === "dev" ? "Running from the repo (no release)" : sha
      ? `Release ${String(sha).slice(0, 7)}${at && !isNaN(at) ? " · " + at.toLocaleDateString([], {month: "short", day: "numeric"}) : ""}` : "";
  }
  async function loadHealth() {
    try {
      const h = await getJSON("/api/health");
      if (h.state) setState(h.state, h.state_detail || {});
      if (h.online !== undefined) $("pip-netoff").hidden = h.online;
      $("pip-onphone").hidden = h.route !== "phone";
      $("pip-conn").textContent = h.connected ? "" : "pip-core not reachable";
      renderRelease(((h.programs || {}).core || {}).release);
    } catch (e) { $("pip-conn").textContent = "Dashboard unreachable"; }
  }

  // A ringing alarm, on every page: Shut off, Snooze, other snooze lengths
  const KIND = {timer: "Timer", alarm: "Alarm", reminder: "Reminder", event: "Heads-up"};
  async function alertAct(body) {
    try { const r = await postJSON("/api/alerts/action", body); if (r.error) alert(r.error); }
    catch (e) { alert("Couldn't reach Pip"); } finally { loadRing(); }
  }
  async function loadRing() {
    let r = {};
    try { r = await getJSON("/api/alerts"); } catch (e) { return; }
    const ring = (r.alerts || []).filter(a => a.state === "ringing"), d = r.snooze_minutes || 5;
    const key = ring.map(a => a.id).join(), el = $("pip-ring");
    if (el.dataset.key === key && ring.length) return;   // keep the open snooze menu while it rings
    el.dataset.key = key;
    el.innerHTML = ring.length ? `<div class="ring"><div><div class="what">🔔 ${ring.map(a => esc(a.label && !["timer", "alarm"].includes(a.label) ? a.label : KIND[a.kind])).join(", ")}</div>
        <div class="sub">Say "okay" or "snooze", or use the buttons</div></div>
      <div class="btns"><button class="danger" data-ring='{"action":"stop"}'>Shut off</button>
        <span class="split"><button data-ring='{"action":"snooze","minutes":${d}}'>Snooze ${d} min</button><button id="pip-snoozemore" aria-label="Other snooze lengths">▾</button></span>
        <div class="menu" id="pip-snoozemenu">${[1, 5, 10, 15, 30].filter(m => m !== d).map(m => `<button data-ring='{"action":"snooze","minutes":${m}}'>Snooze ${m} min</button>`).join("")}
          <form id="pip-snoozeform"><input name="m" type="number" min="1" max="120" placeholder="min" style="width:5em" required><button class="primary">Snooze</button></form></div></div></div>` : "";
    el.querySelectorAll("[data-ring]").forEach(b => b.onclick = () => alertAct(JSON.parse(b.dataset.ring)));
    if (ring.length) {
      $("pip-snoozemore").onclick = ev => { ev.stopPropagation(); $("pip-snoozemenu").classList.toggle("open"); };
      $("pip-snoozeform").onsubmit = ev => { ev.preventDefault(); alertAct({action: "snooze", minutes: +ev.target.m.value}); };
    }
  }
  document.addEventListener("click", ev => { const m = $("pip-snoozemenu"); if (m && !m.contains(ev.target)) m.classList.remove("open"); });

  // Learning a voice ("Pip, I'm Sam"): the sentence to read, on whatever page is open
  let enrolling = false, poll = null;
  async function loadEnrol() {
    let r = {};
    try { r = await getJSON("/api/people"); } catch (e) { return; }
    const e = r.enrolling, el = $("pip-enrol");
    el.innerHTML = e ? `<div class="pip-enrol"><div class="sub">Learning ${esc(e.name || "a")}'s voice · sentence ${Math.min(e.done + 1, e.of)} of ${e.of}</div>
       <div class="pip-say">"${esc(e.phrases[Math.min(e.done, e.of - 1)])}"</div>
       <div class="sub">${e.retry ? "Pip didn't catch that: read it again after the beep." : "After Pip's beep, read this out loud in your normal voice."}</div>
       <div class="pip-steps">${e.phrases.map((_, i) => `<span class="${i < e.done ? "pip-done" : ""}"></span>`).join("")}</div>
       <button class="ghost" id="pip-enrolstop">Stop</button></div>` : "";
    if (e) $("pip-enrolstop").onclick = () => postJSON("/api/people/action", {action: "enrol_cancel"}).finally(loadEnrol);
    if (e && !enrolling) window.scrollTo({top: 0, behavior: "smooth"});
    enrolling = !!e;
    clearInterval(poll);
    if (enrolling) poll = setInterval(loadEnrol, 1500);
  }

  // Changes waiting for the owner (questions, plans to approve, finished changes): a badge on Changes
  async function loadChanges() {
    try { const r = await getJSON("/api/changes"); waiting = (r.changes || []).filter(c => c.waiting).length; }
    catch (e) { waiting = 0; }
    renderNav();
  }

  function frame() {
    const main = document.querySelector("body > main");
    if (!main || $("pip-nav")) return;
    document.body.classList.add("framed");
    const app = document.createElement("div");
    app.className = "pip-app";
    app.innerHTML = `
      <aside class="pip-side"><a class="pip-brand" href="/"><span class="logo">P</span><b>Pip</b></a><nav class="pip-nav" id="pip-nav"></nav>
        <div class="sub pip-foot" id="pip-release"></div></aside>
      <div class="pip-mainwrap">
        <div class="pip-strip">
          <a class="pip-phonebrand" href="/" title="Overview"><span class="logo">P</span></a>
          <span class="face" id="pip-state" title="Pip's state: grey idle · green listening · purple thinking · blue speaking · orange interrupted · red ringing · dark mic off"><span class="orb"></span><span id="pip-statetext">…</span></span>
          <span class="netoff" id="pip-netoff" hidden title="Pip can't reach the internet: only timers, alarms and other basics work until it's back">offline</span>
          <span class="netoff" id="pip-onphone" hidden title="A phone has Pip's microphone and speaker (D-149): the room isn't listened to until it gives him back">📱 on a phone</span>
          <span class="pip-grow"></span><span class="sub" id="pip-conn"></span>
          <button id="pip-mic" class="micbtn">Listening…</button>
        </div>
        <div id="pip-ring"></div><div id="pip-enrol"></div>
      </div>`;
    document.body.insertBefore(app, main);
    app.querySelector(".pip-mainwrap").appendChild(main);
    document.body.insertAdjacentHTML("beforeend", `<nav class="pip-tabs" id="pip-tabs"></nav>
      <div class="pip-sheet" id="pip-sheet"><div class="pip-inner"><div class="label">Every page</div><nav class="pip-nav" id="pip-sheetnav"></nav></div></div>`);
    $("pip-sheet").onclick = ev => { if (ev.target.id === "pip-sheet") ev.target.classList.remove("open"); };
    $("pip-mic").onclick = toggleMic;
    renderNav();
    pip.modules().then(m => { mods = m || []; renderNav(); });
    loadHealth(); loadRing(); loadEnrol(); loadChanges();
    getJSON("/api/mic").then(renderMic).catch(() => {});
    pip.on("state", d => setState(d.state, d));
    pip.on("mic", renderMic);
    pip.on("net", d => { $("pip-netoff").hidden = d.online; });
    pip.on("route", d => { $("pip-onphone").hidden = d.route !== "phone"; });
    pip.on("health", p => { $("pip-conn").textContent = ""; renderRelease((p.core || {}).release); });
    pip.on("alerts", loadRing);
    pip.on("people", loadEnrol);
    if (pip._es) { pip._es.onerror = () => { $("pip-conn").textContent = "Reconnecting…"; };
                   pip._es.onopen = () => { $("pip-conn").textContent = ""; }; }
    setInterval(loadChanges, 30000);
  }
  if (document.querySelector("body > main")) frame();
  else document.addEventListener("DOMContentLoaded", frame);
})();
