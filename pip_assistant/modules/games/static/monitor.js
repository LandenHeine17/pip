// The game box on the monitor (D-144; module.toml [monitor.game]). module.py tv() sends the game's box() (trivia.py,
// twenty.py, rather.py, song.py): {kind, …}, or {} when no game is on. The box covers the monitor while a game is
// on (the screen module's cover), with Pip's face small beside it.
// Guess the song: the clip (Deezer's 30-second preview) plays here, from one Audio element that outlives the box's
// redraws; it starts again whenever `plays` goes up (a new round, "play it again"), and plays at `duck` while Pip
// is listening, thinking or talking (h.state()), else at `volume`. The title is never shown until it's guessed.
(() => {
  const QUIET_WHEN = new Set(["listening", "thinking", "speaking", "interrupted", "ringing"]);
  const LETTERS = "ABCD";
  const audio = new Audio();
  let played = "", help = null, data = {};

  const style = document.createElement("style");
  style.textContent = `
    .game { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 1rem; }
    .game .top { display: flex; justify-content: space-between; color: var(--muted); font-size: 1.4rem; }
    .game .q { font-size: 2.4rem; font-weight: 600; line-height: 1.2; text-wrap: balance; }
    .game .choices { display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; }
    .game .choice { padding: .8rem 1rem; border-radius: 1rem; background: rgba(255,255,255,.07); font-size: 1.8rem; }
    .game .choice b { color: var(--muted); margin-right: .6rem; }
    .game .choice.right { background: rgba(95,224,176,.25); }
    .game .last { font-size: 1.6rem; min-height: 2rem; }
    .game .last.good { color: var(--good); } .game .last.bad { color: var(--critical); }
    .game .scores { display: flex; flex-wrap: wrap; gap: .6rem 1.6rem; font-size: 1.6rem; margin-top: auto; }
    .game .scores b { font-size: 2rem; margin-left: .4rem; }
    .game .big { font-size: 3.2rem; font-weight: 700; text-align: center; margin: auto 0; text-wrap: balance; }
    .game .two { display: grid; grid-template-columns: 1fr auto 1fr; gap: 1rem; align-items: center; flex: 1; }
    .game .side { padding: 1.4rem; border-radius: 1.2rem; background: rgba(255,255,255,.07); font-size: 2.2rem;
                  text-align: center; text-wrap: balance; }
    .game .side small { display: block; color: var(--muted); font-size: 1.4rem; margin-top: .6rem; }
    .game .or { color: var(--muted); font-size: 1.8rem; }
    .game ol { margin: 0; padding-left: 2rem; color: var(--muted); font-size: 1.4rem; }
    .game .eq { display: flex; gap: .5rem; align-items: flex-end; justify-content: center; height: 8rem; margin: auto 0; }
    .game .eq i { width: 1.4rem; background: var(--good); border-radius: .4rem; animation: eq 0.9s ease-in-out infinite; }
    .game .eq.still i { animation: none; height: 1rem; }
    @keyframes eq { 0%, 100% { height: 1.5rem; } 50% { height: 7.5rem; } }`;
  document.head.appendChild(style);

  const scores = (s, h) => {
    const list = Object.entries(s || {}).sort((a, b) => b[1] - a[1]);
    return list.length ? `<div class="scores">${list.map(([n, p]) => `<span>${h.esc(n)}<b>${p}</b></span>`).join("")}</div>` : "";
  };

  function trivia(d, h) {
    const last = d.last ? (d.last.right ? `<div class="last good">✓ ${h.esc(d.last.who && d.last.who !== "Guest" ? d.last.who + " got it: " : "Right: ")}${h.esc(d.last.answer)}, ${h.esc(d.last.text)}</div>`
                                        : `<div class="last bad">It was ${h.esc(d.last.answer)}, ${h.esc(d.last.text)}</div>`) : `<div class="last"></div>`;
    if (d.over) return `<div class="top"><span>Trivia${d.topic ? " · " + h.esc(d.topic) : ""}</span><span>Final scores</span></div>${last}
      <div class="big">That's the game!</div>${scores(d.scores, h)}`;
    return `<div class="top"><span>Trivia${d.topic ? " · " + h.esc(d.topic) : ""}</span><span>Question ${d.n} of ${d.of}</span></div>
      <div class="q">${h.esc(d.question)}</div>
      <div class="choices">${(d.choices || []).map((c, i) => `<div class="choice"><b>${LETTERS[i]}</b>${h.esc(c)}</div>`).join("")}</div>
      ${last}${scores(d.scores, h)}`;
  }

  function twenty(d, h) {
    const head = `<div class="top"><span>20 questions</span><span>${d.asked} of ${d.limit} asked</span></div>`;
    if (d.over) return head + `<div class="big">It was: ${h.esc(d.secret)}${d.found_by ? `<br><small>${h.esc(d.found_by === "Guest" ? "Guessed!" : d.found_by + " guessed it!")}</small>` : ""}</div>`;
    return head + `<div class="big">I'm thinking of ${h.esc(d.thing_kind)}…</div>` +
      (d.questions && d.questions.length ? `<ol start="${Math.max(1, d.asked - d.questions.length + 1)}">${d.questions.map((q) => `<li>${h.esc(q)}</li>`).join("")}</ol>` : "");
  }

  function rather(d, h) {
    const who = (side) => (d.votes || []).filter((v) => v[1] === side).map((v) => h.esc(v[0])).join(", ");
    return `<div class="top"><span>Would you rather…</span><span>Round ${d.round}</span></div>
      <div class="two"><div class="side">${h.esc(d.a)}${who(0) ? `<small>${who(0)}</small>` : ""}</div><div class="or">or</div>
      <div class="side">${h.esc(d.b)}${who(1) ? `<small>${who(1)}</small>` : ""}</div></div>`;
  }

  function song(d, h) {
    const last = d.last ? `<div class="last ${d.last.who ? "good" : "bad"}">${d.last.who ? "✓ " + h.esc(d.last.who !== "Guest" ? d.last.who + ": " : "") : "It was: "}${h.esc(d.last.title)} · ${h.esc(d.last.artist)}</div>` : `<div class="last"></div>`;
    return `<div class="top"><span>Guess the song${d.topic ? " · " + h.esc(d.topic) : ""}</span><span>${d.over ? "Final scores" : `Round ${d.round} of ${d.of}`}</span></div>
      ${d.over ? `<div class="big">That's the game!</div>` : `<div class="eq${audio.paused ? " still" : ""}">${"<i></i>".repeat(9).replace(/<i>/g, () => `<i style="animation-delay:-${(Math.random() * 0.9).toFixed(2)}s">`)}</div>`}
      ${last}${scores(d.scores, h)}`;
  }

  function volume() {
    const quiet = help && QUIET_WHEN.has(help.state());
    return Math.max(0, Math.min(100, quiet ? Math.min(data.volume ?? 80, data.duck ?? 15) : (data.volume ?? 80))) / 100;
  }

  function sound(d) {   // the clip: start it again on a new round or "play it again"; stop it when there's none
    const want = d.kind === "song" && d.clip && !d.over ? `${d.clip}#${d.plays}` : "";
    if (want === played) return;
    played = want;
    if (!want) { audio.pause(); return; }
    if (audio.src !== d.clip) audio.src = d.clip;
    audio.currentTime = 0;
    audio.volume = volume();
    audio.play().catch(() => { /* can't play (a bad link): the round can still be skipped */ });
  }

  PipMonitor.box("game", (box, section, d, h) => {
    help = h; data = d;
    sound(d);
    if (!d.kind) return h.fill(box, "Game", h.none("No game on. Say “Pip, let's play trivia”."));
    const draw = { trivia, twenty, rather, song }[d.kind];
    box.innerHTML = `<div class="game">${draw ? draw(d, h) : ""}</div>`;
    box.classList.toggle("keep-awake", true);   // the screen doesn't doze during a game
  });

  // The clip's volume follows Pip; once the game's box is gone (the game's over), the clip stops.
  setInterval(() => {
    if (audio.paused) return;
    if (!document.querySelector('.box[data-type="game"]')) { audio.pause(); played = ""; return; }
    audio.volume = volume();
  }, 250);
  audio.addEventListener("ended", () => document.querySelectorAll(".game .eq").forEach((e) => e.classList.add("still")));
  audio.addEventListener("play", () => document.querySelectorAll(".game .eq").forEach((e) => e.classList.remove("still")));
})();
