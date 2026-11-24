// The video box on the monitor (D-141; module.toml [monitor.video]). module.py tv() sends what's up:
// {id, title, channel, playing, start, volume, duck, commands: [{n, op, s}]}. The video plays in YouTube's own player
// (its IFrame API, loaded from YouTube the first time a video goes up). The box is drawn again whenever anything on
// the monitor changes, so the player is made only when a new video comes; after that, only the controls numbered
// higher than the last one done are carried out, in order: "pause", "play", "seek_by" (seconds forwards or back),
// "seek_to". The volume follows Pip: `duck` while he's listening, thinking or talking (h.state()), else `volume`.
// While it plays, the box is marked keep-awake, so the screen doesn't doze (dim) during a long video.
(() => {
  const QUIET_WHEN = new Set(["listening", "thinking", "speaking", "interrupted", "ringing"]);
  const WONT_PLAY = { 2: "That video link is broken", 5: "The video player had a problem", 100: "That video is gone",
                      101: "That video won't play outside YouTube", 150: "That video won't play outside YouTube",
                      153: "YouTube refused to play it here" };
  let player = null, ready = false, shownId = "", done = 0, waiting = [], data = {}, help = null, loading = null;

  // Make sure the box's video fills it, whatever its size (the box's own padding and title stay).
  const style = document.createElement("style");
  style.textContent = `
    .yt { flex: 1; min-height: 0; border-radius: 1rem; overflow: hidden; background: #000; }
    .yt iframe { width: 100%; height: 100%; border: 0; display: block; }
    .yt-title { margin-top: .7rem; font-size: 1.5rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .yt-title small { color: var(--muted); margin-left: .6rem; }
    .yt-said { flex: 1; display: flex; align-items: center; justify-content: center; text-align: center; font-size: 2rem; color: var(--muted); }`;
  document.head.appendChild(style);

  function youtube() {   // YouTube's player library, loaded once (tried again next time if it couldn't load)
    if (window.YT && window.YT.Player) return Promise.resolve();
    loading = loading || new Promise((done, failed) => {
      const before = window.onYouTubeIframeAPIReady;
      window.onYouTubeIframeAPIReady = () => { if (before) before(); done(); };
      const tag = document.createElement("script");
      tag.src = "https://www.youtube.com/iframe_api";
      tag.onerror = () => { tag.remove(); loading = null; failed(new Error("no YouTube")); };
      document.head.appendChild(tag);
    });
    return loading;
  }

  function say(box, h, words) {   // a message in place of the video
    const spot = box.querySelector(".yt, .yt-said");
    if (spot) spot.outerHTML = `<div class="yt-said">${h.esc(words)}</div>`;
  }

  function volume() {
    const quiet = help && QUIET_WHEN.has(help.state());
    return quiet ? Math.min(data.volume ?? 70, data.duck ?? 15) : (data.volume ?? 70);
  }

  function run(c) {
    if (!ready) return waiting.push(c);
    if (c.op === "pause") player.pauseVideo();
    else if (c.op === "play") player.playVideo();
    else if (c.op === "seek_by") player.seekTo(Math.max(0, player.getCurrentTime() + c.s), true);
    else if (c.op === "seek_to") player.seekTo(c.s, true);
  }

  function forget() {
    try { if (player) player.destroy(); } catch { /* its box is already gone */ }
    document.querySelectorAll('.box[data-type="video"].keep-awake').forEach((b) => b.classList.remove("keep-awake"));
    player = null; ready = false; shownId = ""; waiting = [];
  }

  function make(box, d, h) {
    forget();
    shownId = d.id;
    done = Math.max(0, ...(d.commands || []).map((c) => c.n));   // what came before is in d.start and d.playing
    box.innerHTML = `<div class="yt"><div></div></div><div class="yt-title">${h.esc(d.title)}<small>${h.esc(d.channel)}</small></div>`;
    const spot = box.querySelector(".yt div");
    youtube().then(() => {
      if (shownId !== d.id || !spot.isConnected) return;   // a different video, or taken down, while YouTube loaded
      player = new YT.Player(spot, {
        videoId: d.id, width: "100%", height: "100%",
        playerVars: { autoplay: d.playing ? 1 : 0, start: Math.floor(d.start || 0), controls: 0, rel: 0, fs: 0,
                      iv_load_policy: 3, disablekb: 1, playsinline: 1 },
        events: {
          onReady: (e) => {
            ready = true;
            e.target.setVolume(volume());
            if (d.playing) e.target.playVideo();
            for (const c of waiting.splice(0)) run(c);
          },
          // While it plays, the screen doesn't doze (dozing dims it): tv.html's shown() looks for .keep-awake.
          onStateChange: (e) => box.classList.toggle("keep-awake", e.data === YT.PlayerState.PLAYING),
          onError: (e) => {
            forget();
            say(box, h, `${WONT_PLAY[e.data] || "The video won't play"}. Say “Pip, a different one”.`);
            shownId = d.id;   // don't try the same one again until another comes
          },
        },
      });
    }).catch(() => {
      if (shownId !== d.id) return;
      say(box, h, "Couldn't load YouTube's player. Is the internet down?");
      shownId = "";   // the next time the box is drawn, it tries again
    });
  }

  PipMonitor.box("video", (box, section, d, h) => {
    help = h; data = d;
    if (!d.id) {
      forget();
      return h.fill(box, "Video", h.none("No video up"));
    }
    if (d.id !== shownId || !box.querySelector(".yt, .yt-said")) return make(box, d, h);
    for (const c of d.commands || []) if (c.n > done) { run(c); done = c.n; }
  });

  // The volume follows what Pip is doing (several times a second: it's only a number). Once the video's box is gone
  // (taken down), the player is let go of.
  setInterval(() => {
    if (!player) return;
    const frame = player.getIframe && player.getIframe();
    if (frame && !frame.isConnected) return forget();
    if (ready && player.getVolume() !== volume()) player.setVolume(volume());
  }, 250);
})();
