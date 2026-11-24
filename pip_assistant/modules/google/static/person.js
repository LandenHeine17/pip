// Google's part of a person's profile page (web/static/person.html; module.toml person_panel, D-148): their Google
// account, whether Pip may mention their events and to-dos to everyone, disconnecting, and below it each module's
// part of a Google account's settings (module.toml account_panel: the calendar's calendars, the to-do lists), drawn
// by those modules' own scripts. Not connected: a button to the Google page, which connects it.
(() => {
  let el = null, person = null, acc = null, loaded = false, jumped = false;
  // the People page's "Google connected" chip and the Google page's Settings link come here as /person?id=…#google
  const jump = () => { if (location.hash === "#google" && !jumped) { jumped = true; el.scrollIntoView({block: "start"}); } };
  // Each module's part of the account's settings: its static/account.js calls PipAccount.panel(module, draw), and
  // draw(el, acc) fills its own element (acc: this Google account; acc.id is its id).
  const drawers = {};
  window.PipAccount = { panel(module, draw) { drawers[module] = draw; if (acc) draw(slot(module), acc); } };
  function slot(module) {
    let s = el.querySelector(`[data-panel="${module}"]`);
    if (!s) { s = document.createElement("div"); s.dataset.panel = module; el.querySelector(".panels").appendChild(s); }
    return s;
  }
  async function draw(where, who) {
    el = where; person = who;
    let g = null;
    try { g = await pip.get("status", {}, "google"); } catch (e) { g = null; }
    const accs = g ? g.accounts.filter(a => a.person_id === person.id) : [];
    acc = accs[accs.length - 1] || null;
    if (!g || !g.configured || !acc) {
      el.innerHTML = `<section id="google"><div class="head"><h2>Google account</h2></div>
        <div class="sub" style="margin-bottom:10px">${!g ? "Couldn't reach Pip." : !g.configured
          ? "Google isn't set up for Pip yet (once, about 10 minutes)."
          : `Not connected. Connecting lets Pip read and add to ${esc(person.name)}'s Google Calendar and Tasks.`}</div>
        <a href="/m/google/?person=${encodeURIComponent(person.id)}"><button class="primary">${g && g.configured ? "Connect Google" : "Set up Google"}</button></a></section>`;
      jump();
      return;
    }
    el.innerHTML = `<section id="google"><div class="head"><h2>Google account</h2><span class="sub">${acc.status === "ok"
        ? (acc.last_sync ? "last synced " + new Date(acc.last_sync * 1000).toLocaleTimeString([], {hour: "numeric", minute: "2-digit"}) : "connected")
        : `<span style="color:var(--bad)">${acc.status === "missing" ? esc(acc.error) : "needs reconnecting"}</span>`}</span></div>
      <div class="item"><div><b>${esc(acc.email)}</b><div class="sub">used for ${esc(person.name)}'s calendars and to-dos</div></div>
        <button class="ghost" id="g-disconnect">Disconnect</button></div>
      <div class="row"><span class="what">Pip may mention their events and to-dos</span>
        <select id="g-sharing"><option value="everyone">to everyone</option><option value="me">only to ${esc(person.name)}</option></select></div>
      ${acc.status === "ok" && !acc.can_manage ? `<div class="sub">To let Pip make, rename or delete calendars: <a href="/m/google/?person=${encodeURIComponent(person.id)}">connect Google</a> again and tick Select all (no need to disconnect).</div>` : ""}
      </section><div class="panels" style="display:flex;flex-direction:column;gap:18px;margin-top:18px"></div>`;
    jump();
    el.querySelector("#g-sharing").value = acc.sharing;
    el.querySelector("#g-sharing").onchange = async ev => { if (await pip.act(pip.post("sharing", {id: acc.id, value: ev.target.value}, "google"))) draw(el, person); };
    el.querySelector("#g-disconnect").onclick = async () => {
      if (!confirm(`Disconnect ${acc.email}? Pip forgets its calendar and to-dos here (nothing is deleted in Google).`)) return;
      if (await pip.act(pip.post("disconnect", {id: acc.id}, "google"))) draw(el, person);
    };
    if (!loaded) {   // the modules' scripts, once (each calls PipAccount.panel when it has loaded)
      loaded = true;
      for (const m of (await pip.modules()).filter(m => m.account)) {
        slot(m.name);
        const tag = document.createElement("script"); tag.src = m.account; document.body.appendChild(tag);
      }
    }
    for (const [module, d] of Object.entries(drawers)) d(slot(module), acc);
  }
  PipPerson.panel("google", draw);
})();
