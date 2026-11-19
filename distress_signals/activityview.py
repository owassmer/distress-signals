"""Recorded research activity and its playback controls for the run page."""

STYLE = r"""
.activity-panel{margin:0;padding:22px 24px 15px;border:1px solid var(--line);border-radius:16px;background:var(--card)}
.activity-caption{font-size:17px;font-weight:680}
.activity-legend{display:flex;align-items:center;flex-wrap:wrap;gap:5px 18px;margin:8px 0 6px;color:var(--soft);font-size:13px}
.activity-legend span{display:inline-flex;align-items:center;gap:5px}
.activity-legend i{display:inline-block;width:9px;height:9px;border-radius:2px}
.activity-legend b,.activity-time,.activity-case-time{font-variant-numeric:tabular-nums}
#activity-chart{display:block;width:100%;height:200px}
#activity-chart[data-seekable]{cursor:crosshair}
.activity-controls{display:grid;grid-template-columns:auto auto minmax(50px,1fr) auto;align-items:center;gap:14px;margin-top:12px}
.activity-toggle{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:8px 13px;border:0;border-radius:99px;background:var(--ink);color:#fff;font:inherit;font-size:13px;font-weight:600;line-height:1.4;cursor:pointer}
.activity-toggle svg{width:15px;height:15px}.activity-toggle:hover{background:var(--soft)}
.activity-time{color:var(--mute);font-size:13px;white-space:nowrap}.activity-time b{color:var(--ink);font-weight:650}
#activity-seek{width:100%;min-width:0;height:26px;margin:0;accent-color:var(--ink);cursor:pointer}
#activity-speeds{display:inline-flex;gap:1px;padding:3px;border-radius:99px;background:#f1f3f6}
#activity-speeds button{border:0;border-radius:99px;background:transparent;color:var(--faint);cursor:pointer;font:inherit;font-size:12px;font-weight:650;min-width:32px;min-height:28px}
#activity-speeds button[aria-pressed="true"]{color:var(--ink);background:var(--card);box-shadow:0 1px 3px #0f172a24}
.activity-entry{margin:13px 0 0;padding-top:12px;display:flex;align-items:baseline;gap:5px 20px;justify-content:space-between;flex-wrap:wrap;border-top:1px solid var(--line2);font-size:13px;color:var(--soft);overflow-wrap:anywhere}
.activity-entry a{white-space:nowrap}
#activity-cases{margin-top:28px}#activity-cases h2{font-size:18px;margin:0 0 8px}
#activity-case-list{list-style:none;margin:0;padding:0;background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden}
#activity-case-list li+li{border-top:1px solid var(--line2)}
#activity-case-list a{display:grid;align-items:baseline;grid-template-columns:20px 48px 1fr auto;gap:10px;padding:11px 16px;color:var(--ink);line-height:1.45}
#activity-case-list a:hover{background:#f8fafc;text-decoration:none}#activity-case-list a:focus-visible{outline-offset:-3px}
.activity-case-dot{width:18px;height:18px;text-align:center;border-radius:50%;align-self:start;font-size:11px;line-height:18px;background:var(--greenink);color:#fff;font-weight:700}
.activity-case-time,.activity-case-company{color:var(--faint);font-size:13px}.activity-case-name{font-size:14px;font-weight:600}
.activity-case-arrow{color:var(--blue);font-size:14px;align-self:center}
@media(max-width:620px){.activity-panel{padding:16px 14px}.activity-controls{grid-template-columns:auto 1fr auto;gap:4px 10px}
#activity-seek{grid-column:1/-1;grid-row:2}#activity-speeds{grid-column:3;grid-row:1}
#activity-case-list a{grid-template-columns:18px 40px 1fr auto;gap:7px;padding:11px 12px}
.activity-case-company{display:block}.activity-case-sep{display:none}}
@media(max-width:380px){.activity-toggle{width:34px;height:34px;padding:0}.activity-toggle span{display:none}}
"""

HTML = r"""
<section class="view" id="v-activity" aria-labelledby="activity-heading" hidden>
  <h1 id="activity-heading">Research activity</h1>
  <p class="empty" id="activity-empty" hidden></p>
  <figure class="activity-panel" id="activity-figure">
    <figcaption class="activity-caption">Completed actions per minute</figcaption>
    <div class="activity-legend">
      <span><i style="background:#2563eb"></i>Searches <b id="activity-search">0</b></span>
      <span><i style="background:#94a3b8"></i>Source reads <b id="activity-read">0</b></span>
      <span><i style="background:#7c3aed"></i>Queries &amp; calculations <b id="activity-calc">0</b></span>
    </div>
    <svg id="activity-chart" role="img" aria-label="Completed actions per minute. Numbered markers show when cases were saved."></svg>
    <div class="activity-controls" id="activity-controls">
      <button type="button" class="activity-toggle" id="activity-toggle"></button>
      <span class="activity-time" aria-label="Elapsed run time"><b id="activity-time"></b> / <span id="activity-duration"></span></span>
      <input type="range" id="activity-seek" min="0" step="1" aria-label="Elapsed run time">
      <div id="activity-speeds" role="group" aria-label="Activity playback speed">
        <button type="button" data-rate="1" aria-pressed="true">1×</button>
        <button type="button" data-rate="2" aria-pressed="false">2×</button>
        <button type="button" data-rate="4" aria-pressed="false">4×</button>
      </div>
    </div>
    <div class="activity-entry" id="activity-entry" hidden>
      <span id="activity-entry-label"></span><a id="activity-entry-link"></a>
    </div>
  </figure>
  <section id="activity-cases" hidden>
    <h2>Cases saved</h2>
    <ol id="activity-case-list"></ol>
  </section>
</section>
"""

JS = r"""
function activityKind(item) {
  if (item.k !== "act" || item.status !== "ok") return null;
  return {search:"search", read:"read", web:"read", calc:"calc"}[item.icon] || null;
}

const activity = {
  model: null, events: [], flags: [], end: 0, time: 0, rate: 1, timer: null, lastTick: 0,
  drawnEvents: -1, drawnFlags: -1
};

function activityPrepare() {
  const atEnd = !activity.model || activity.time >= activity.end;
  activity.model = M;
  activity.events = [];
  for (const step of M.steps) {
    for (const item of step.acts) {
      const kind = activityKind(item);
      if (kind) activity.events.push({t:item.el || 0, step:step.i, kind, label:item.label});
    }
  }
  activity.events.sort((a, b) => a.t - b.t);
  activity.flags = M.found.map(f => ({t:f.el, n:f.n, label:[f.c.company, f.c.stream].filter(Boolean).join(" · ")}));
  activity.end = Math.max(0, M.secs, ...activity.flags.map(f => f.t), activity.events.at(-1)?.t || 0);
  activity.time = atEnd || live() ? activity.end : Math.min(activity.time, activity.end);
  $("#activity-seek").max = Math.ceil(activity.end);
  $("#activity-duration").textContent = clock(activity.end);
  $("#activity-case-list").innerHTML = M.found.map(({oid, c, el, n}) => {
    const href = "#found/" + encodeURIComponent(oid);
    return `<li><a href="${esc(href)}" aria-label="${esc(`Research log at ${clock(el)} · ${[c.company,c.stream].filter(Boolean).join(" · ")}`)}">
      <span class="activity-case-dot" aria-hidden="true">${n}</span><span class="activity-case-time">${clock(el)}</span>
      <span><span class="activity-case-name">${esc(c.stream || c.company || "Case saved")}</span>
        ${c.stream && c.company ? `<span class="activity-case-company"><span class="activity-case-sep"> · </span>${esc(c.company)}</span>` : ""}</span>
      <span class="activity-case-arrow" aria-hidden="true">→</span></a></li>`;
  }).join("");
  $("#activity-cases").hidden = !M.found.length;
}

function activityPaint(force = false) {
  const t = activity.time, events = activity.events, flags = activity.flags;
  const next = events.findIndex(event => event.t > t);
  const count = next < 0 ? events.length : next;
  const seen = flags.filter(f => f.t <= t).length;
  $("#activity-entry").hidden = !count || (!live() && t >= activity.end);
  if (force || count !== activity.drawnEvents || seen !== activity.drawnFlags) {
    const bins = M.bins.map(() => ({search:0, read:0, calc:0}));
    const totals = {search:0, read:0, calc:0};
    for (let i = 0; i < count; i++) {
      const {t, kind} = events[i];
      bins[Math.max(0, Math.min(bins.length - 1, Math.floor(t / 60)))][kind]++;
      totals[kind]++;
    }
    for (const kind of Object.keys(totals)) $("#activity-" + kind).textContent = totals[kind];
    drawChart($("#activity-chart"), M.bins, flags, t, 200, bins);
    activity.drawnEvents = count;
    activity.drawnFlags = seen;
    const latest = events[count - 1], link = $("#activity-entry-link");
    if (latest) {
      $("#activity-entry-label").textContent = latest.label || "";
      link.textContent = "Research log at " + clock(M.steps[latest.step].el) + " →";
      link.href = "#step/" + latest.step;
    }
  }
  const playing = activity.timer !== null;
  const action = playing ? "Pause" : t >= activity.end ? "Replay" : "Play";
  const toggle = $("#activity-toggle");
  if (toggle.dataset.action !== action) {
    toggle.dataset.action = action;
    toggle.setAttribute("aria-label", action + " activity");
    toggle.innerHTML = (playing ? ICONS.pause : ICONS.play) + "<span>" + action + "</span>";
  }
  $("#activity-time").textContent = clock(t);
  const seek = $("#activity-seek");
  seek.value = t >= activity.end ? seek.max : Math.floor(t);
  seek.setAttribute("aria-valuetext", clock(t) + " of " + clock(activity.end));
}

function activityStop() {
  clearInterval(activity.timer);
  activity.timer = null;
}

function activityAdvance() {
  const now = performance.now(), seconds = (now - activity.lastTick) / 1000;
  activity.lastTick = now;
  // At the base speed one minute of research takes two seconds in the replay.
  const replaySeconds = Math.min(activity.end, Math.max(40, Math.min(100, activity.end / 30)));
  if (replaySeconds) activity.time = Math.min(activity.end, activity.time + seconds * activity.end / replaySeconds * activity.rate);
  if (activity.time >= activity.end) activityStop();
}

function activityTick() {
  activityAdvance();
  activityPaint();
}

function activityView() {
  if (!M) return;
  if (activity.model !== M) activityPrepare();
  const empty = !activity.events.length && !activity.flags.length;
  $("#activity-empty").hidden = !empty;
  $("#activity-empty").textContent = live() ? "Waiting for completed actions." : "No completed searches, source reads, queries, or calculations.";
  $("#activity-figure").hidden = empty;
  $("#activity-controls").hidden = live();
  $("#activity-chart").toggleAttribute("data-seekable", !live() && !empty && activity.end > 0);
  if (!empty) activityPaint(true);
}

function activityLeave() {
  if (activity.timer !== null) {
    activityAdvance();
    activityStop();
  }
}

function activityResize() {
  if (M && !$("#v-activity").hidden) activityPaint(true);
}

$("#activity-toggle").onclick = () => {
  if (live() || !activity.end) return;
  if (activity.timer !== null) activityLeave();
  else {
    if (activity.time >= activity.end) activity.time = 0;
    activity.lastTick = performance.now();
    activity.timer = setInterval(activityTick, 100);
  }
  activityPaint();
};
$("#activity-speeds").onclick = e => {
  const button = e.target.closest("button[data-rate]");
  if (!button) return;
  if (activity.timer !== null) activityTick();
  activity.rate = Number(button.dataset.rate);
  for (const option of $$("#activity-speeds button")) option.setAttribute("aria-pressed", String(option === button));
};
function activitySeek(seconds) {
  if (!activity.model || live() || !activity.end) return;
  activity.time = Math.max(0, Math.min(activity.end, seconds));
  activity.lastTick = performance.now();
  if (activity.time >= activity.end) activityStop();
  activityPaint();
}
$("#activity-seek").oninput = e => activitySeek(Number(e.target.value));
$("#activity-chart").onclick = e => {
  const chart = e.currentTarget;
  if (!chart.hasAttribute("data-seekable")) return;
  const bounds = chart.getBoundingClientRect(), width = chart.viewBox.baseVal.width;
  const x = (e.clientX - bounds.left) * width / bounds.width;
  const left = Number(chart.dataset.plotLeft);
  activitySeek((x - left) / (width - left) * M.bins.length * 60);
};
document.addEventListener("visibilitychange", () => {
  if (document.hidden && activity.timer !== null) {
    activityLeave();
    activityPaint();
  }
});
"""
