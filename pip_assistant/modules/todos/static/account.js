// The to-dos' part of a Google account's settings page (modules/google/static/account.html; module.toml
// account_panel): this account's to-do lists, each used or ignored. Its requests go to this module (module.py).
(() => {
  let el = null, lists = [];
  async function draw(where, acc) {
    el = where;
    try { lists = (await pip.get("account_lists", {account: acc.id}, "todos")).lists; } catch (e) { lists = []; }
    render();
  }
  function render() {
    el.innerHTML = `<section><div class="head"><h2>To-do lists</h2></div>
      ${lists.map(l => `<div class="row"><select data-list="${esc(l.id)}"><option value="use">Use</option><option value="ignore" ${l.ignored?"selected":""}>Ignore</option></select>
        <span style="${l.ignored?"text-decoration:line-through;opacity:.6":""}">${esc(l.name)}</span></div>`).join("") || `<div class="empty">No to-do lists yet.</div>`}
      <div class="sub" style="margin-top:8px">Ignored lists aren't fetched, shown on the To-dos page, or told to Pip.</div></section>`;
    el.querySelectorAll("[data-list]").forEach(x => x.onchange = async () => {
      const r = await pip.act(pip.post("set_list", {id: x.dataset.list, ignored: x.value === "ignore"}, "todos"));
      if (r) { lists = r.lists; render(); }
    });
  }
  PipAccount.panel("todos", draw);
})();
