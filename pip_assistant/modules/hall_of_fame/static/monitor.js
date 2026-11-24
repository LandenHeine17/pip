// The Hall of Fame box on the monitor (D-147; module.toml [monitor.moment]). module.py tv() sends one moment:
// {title, lines: ["Sam: …", "Pip: …"], at, count}, or {} when the Hall of Fame is empty. "Pip, show the hall of
// fame" covers the monitor with it for a minute (the screen module's cover), Pip's face small beside it; added to
// a screen as an ordinary box, it shows a moment that changes each day.
(() => {
  const style = document.createElement("style");
  style.textContent = `
    .moment { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: .8rem; overflow: hidden; }
    .moment .top { display: flex; justify-content: space-between; color: var(--muted); font-size: 1.3rem; }
    .moment .title { font-size: 2.2rem; font-weight: 700; line-height: 1.15; text-wrap: balance; }
    .moment .said { font-size: 1.7rem; line-height: 1.3; }
    .moment .said b { color: var(--good); margin-right: .5rem; }
    .moment .said.pip b { color: var(--muted); }`;
  document.head.appendChild(style);

  PipMonitor.box("moment", (box, section, d, h) => {
    if (!d || !d.title) return h.fill(box, "Hall of Fame", h.none("Nothing yet. Say “Pip, save that to the hall of fame”."));
    const day = new Date(d.at * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
    const lines = (d.lines || []).map((x) => {
      const m = /^([^:]{1,30}):\s([\s\S]*)$/.exec(x);
      return m ? `<div class="said${m[1].toLowerCase() === "pip" ? " pip" : ""}"><b>${h.esc(m[1])}</b>${h.esc(m[2])}</div>`
               : `<div class="said">${h.esc(x)}</div>`;
    }).join("");
    box.innerHTML = `<div class="moment"><div class="top"><span>★ Hall of Fame</span><span>${h.esc(day)}</span></div>
      <div class="title">${h.esc(d.title)}</div>${lines}</div>`;
  });
})();
