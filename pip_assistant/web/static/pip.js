// Shared helpers for module pages on Pip's dashboard (/m/<name>/). Used with /pip.css.
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

// Talk to this page's module in pip-core: pip.get("range", {from, to}) · pip.post("add", {...})
const pip = {
  module: location.pathname.split("/")[2],
  async get(action, args = {}) {
    const q = new URLSearchParams({action, ...args});
    const r = await (await fetch(`/api/m/${pip.module}?${q}`, {cache: "no-store"})).json();
    if (r.error) throw new Error(r.error);
    return r;
  },
  async post(action, body = {}) {
    const r = await (await fetch(`/api/m/${pip.module}`, {method: "POST", headers: {"Content-Type": "application/json"},
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
