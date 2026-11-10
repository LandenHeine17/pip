// The calendar's part of a Google account's settings page (modules/google/static/account.html; module.toml
// account_panel). This account's calendars: use or ignore, on the calendar page, in the two-week list, heads-ups;
// the household calendar; no heads-ups at night (D-109). Its requests go to this module (module.py
// _account_settings).
(() => {
  const HEADS = [["off","Off"],["own","Only ones you set"],["all","Every event (calendar's reminder)"],["5","5 min before every event"],
    ["10","10 min before"],["15","15 min before"],["30","30 min before"],["60","1 hour before"],["120","2 hours before"],["1440","1 day before"]];
  let el = null, acc = null, cal = {calendars: []};
  async function draw(where, account) {
    el = where; acc = account;
    try { cal = await pip.get("account_settings", {account: acc.id}, "calendar"); } catch (e) { cal = {calendars: []}; }
    render();
  }
  function render() {
    const writable = cal.calendars.filter(c => c.writable && !c.ignored);
    el.innerHTML = `<section>
      <div class="head"><h2>Calendar</h2><span class="sub">${cal.heads_ups != null ? `${cal.heads_ups} heads-up${cal.heads_ups===1?"":"s"} waiting (all calendars)` : ""}</span></div>
      <div class="row"><span class="what">Household calendar</span>
        <select data-pref="household"><option value="">${cal.household ? "Not this account" : "Automatic"}</option>
        ${writable.map(c => `<option value="${esc(c.id)}" ${c.id===cal.household?"selected":""}>${esc(c.primary?"Main calendar":c.name)}</option>`).join("")}</select>
        <span class="sub">Events go here when Pip doesn't know whose they are.</span></div>
      <label class="row"><span class="what">No heads-ups at night</span>
        <input type="checkbox" data-pref="night" ${cal.night?"checked":""}> <span class="sub">Nothing between 11 PM and 7 AM (alarms still ring).</span></label>
      <div class="sub">${acc.can_manage ? "" : "To let Pip make, rename or delete calendars: on the Google page press Connect Google again for this person and tick Select all."}</div>
      <table style="margin-top:10px"><thead><tr><th>Calendar</th><th>Use</th><th class="c">On the calendar page</th><th class="c hide">In the two-week list</th><th>Heads-ups</th></tr></thead><tbody>
      ${cal.calendars.map(c => `<tr class="${c.ignored?"off":""}">
        <td><span class="sw" style="background:${esc(c.color)}"></span><span class="cname">${esc(c.primary?"Main calendar":c.name)}</span>${c.writable?"":` <span class="sub">(read-only)</span>`}</td>
        <td><select data-cal="${esc(c.id)}" data-field="ignored"><option value="use">Use</option><option value="ignore" ${c.ignored?"selected":""}>Ignore</option></select></td>
        <td class="c"><input type="checkbox" data-cal="${esc(c.id)}" data-field="ticked" ${c.ticked?"checked":""} ${c.ignored?"disabled":""}></td>
        <td class="c hide"><input type="checkbox" data-cal="${esc(c.id)}" data-field="in_list" ${c.in_list?"checked":""} ${c.ignored?"disabled":""}></td>
        <td><select data-cal="${esc(c.id)}" data-field="heads_up" ${c.ignored?"disabled":""}>${HEADS.map(([v,l]) => `<option value="${v}" ${v===c.heads_up?"selected":""}>${l}</option>`).join("")}</select></td></tr>`).join("")
        || `<tr><td colspan="5" class="sub">No calendars yet (they arrive with the next sync).</td></tr>`}
      </tbody></table>
      <div class="sub" style="margin-top:10px"><b>Ignore</b>: Pip doesn't fetch it, it's not on the calendar page, and he never hears about it.
        <b>Heads-ups</b>: "Only ones you set" means events where you added a reminder yourself in Google, not the calendar's
        automatic one, so classes and the like stay quiet. <b>Two-week list</b>: the list on Pip's main dashboard.</div></section>`;
    el.querySelectorAll("[data-pref]").forEach(x => x.onchange = () =>
      save(pip.post("set_pref", {account: acc.id, key: x.dataset.pref, value: x.type === "checkbox" ? x.checked : x.value}, "calendar")));
    el.querySelectorAll("[data-cal]").forEach(x => x.onchange = () => {
      const f = x.dataset.field;
      const value = f === "ignored" ? x.value === "ignore" : x.type === "checkbox" ? x.checked : x.value;
      save(pip.post("set_calendar", {id: x.dataset.cal, field: f, value}, "calendar"));
    });
  }
  async function save(request) { const r = await pip.act(request); if (r) { cal = r; render(); } }
  PipAccount.panel("calendar", draw);
})();
