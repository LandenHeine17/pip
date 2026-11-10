// The calendar's box on the monitor (web/static/tv.html; module.toml [monitor.calendar]). `data` is what
// module.py tv() sent: today's and tomorrow's events Pip may show the room.
PipMonitor.box("calendar", (box, section, data, h) => {
  const now = h.serverNow(), today = new Date().getDate();
  const rows = (data.events || []).filter((e) => e.all_day ? e.at + 86400 > now : e.at > now - 1800).slice(0, 8).map((e) => {
    const tomorrow = new Date(e.at * 1000).getDate() !== today;
    const when = (tomorrow ? "Tomorrow " : "") + (e.all_day ? "all day" : h.at(e.at));
    const soon = !e.all_day && e.at - now < 3600;
    return `<div class="item${soon ? " soon" : ""}"><span class="what">${h.esc(e.title)}</span><span class="when">${h.esc(when)}</span></div>`;
  });
  h.fill(box, "Coming up", `<div class="body">${rows.join("") || h.none("Nothing today or tomorrow")}</div>`);
  box.classList.toggle("empty", !rows.length);
});
