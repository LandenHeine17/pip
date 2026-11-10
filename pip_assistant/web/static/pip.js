// Shared helpers for Pip's dashboard pages (/, /settings, /changes) and module pages (/m/<name>/). Used with
// /pip.css. (The monitor's page, tv.html, stands alone.)
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
  // Call fn whenever this module (or the timers/alarms) says something changed
  live(fn) {
    if (location.search.includes("nolive")) return;
    const es = new EventSource("/api/stream");
    es.addEventListener("module", ev => { if (JSON.parse(ev.data).module === pip.module) fn(); });
    es.addEventListener("alerts", () => fn());
  },
  async act(promise) { try { return await promise; } catch (e) { alert(e.message || "Couldn't reach Pip"); } },
};
