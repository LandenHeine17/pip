// The dice box on the monitor (web/static/tv.html; module.toml [monitor.dice]). `data` is what module.py tv()
// sent; h has the monitor's helpers: h.fill(box, title, html), h.esc(text), h.none(text), h.at(timestamp).
PipMonitor.box("dice", (box, section, data, h) => {
  h.fill(box, "Dice", data.last ? `<div class="body"><div class="clock">${h.esc(data.last)}</div><div class="feels">d${h.esc(data.sides)}</div></div>`
                                : h.none("No rolls yet"));
});
