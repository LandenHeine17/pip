// A to-do list's box on the monitor (web/static/tv.html; module.toml [monitor.todos]). `data.lists` is what
// module.py tv() sent: each list on the screen, by the name the box was given ("" = the household's).
PipMonitor.box("todos", (box, section, data, h) => {
  const list = (data.lists || {})[section.list || ""] || { name: section.list || "To-dos", items: [] };
  const why = list.private ? "That list is private" : list.missing ? `No list called ${list.name}` : "All done";
  h.fill(box, list.name, `<div class="body">${list.items.map((t) => `<div class="item"><span class="what">○ ${h.esc(t)}</span></div>`).join("") || h.none(why)}</div>`);
});
