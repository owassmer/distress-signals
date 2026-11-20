"""Recorded research activity and its playback controls for the run page."""

STYLE = r"""
#activity{min-width:0;margin-top:24px;padding:17px 20px;border:1px solid var(--line);border-radius:14px;background:var(--card);scroll-margin-top:calc(var(--shell-height,58px) + 16px)}
.activity-head{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:4px 12px}
#activity-heading{margin:0;font-size:20px;line-height:1.3}
.activity-head a{font-size:13px;font-weight:650}
.activity-panel{margin:0}
.activity-plot{position:relative}
.activity-unit{margin:0 0 -6px;font-size:11px;color:var(--faint)}
.activity-marker{position:absolute;top:20px;width:24px;height:26px;transform:translate(-50%,-50%);display:grid;place-items:center;z-index:1;border-radius:5px}
.activity-marker svg{width:17px;height:17px;padding:2px;background:var(--greenink);color:#fff;border-radius:50%}
.activity-marker:hover,.activity-marker:focus-visible{z-index:2}
.activity-marker:hover::after,.activity-marker:focus-visible::after{content:attr(data-label);position:absolute;bottom:100%;left:var(--tip-offset);width:max-content;max-width:var(--tip-width);padding:5px 8px;border-radius:6px;font-size:12px;line-height:1.4;font-weight:550;color:white;background:var(--ink);box-shadow:0 2px 8px #0f172a1c;pointer-events:none}
#activity-chart g :is(circle,text){visibility:hidden}
#activity-cases-key i{border-radius:50%;background:var(--greenink)}
.activity-legend{display:flex;align-items:center;flex-wrap:wrap;gap:5px 18px;margin:8px 0 6px;color:var(--soft);font-size:13px}
.activity-legend span{display:inline-flex;align-items:center;gap:5px}
.activity-legend i{display:inline-block;width:9px;height:9px;border-radius:2px}
.activity-legend b,.activity-time{font-variant-numeric:tabular-nums}
#activity-chart{display:block;width:100%;height:158px}
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
@media(max-width:620px){#activity{padding:15px 14px}.activity-controls{grid-template-columns:auto 1fr auto;gap:4px 10px}
#activity-seek{grid-column:1/-1;grid-row:2}#activity-speeds{grid-column:3;grid-row:1}}
@media(max-width:480px){.activity-controls{grid-template-columns:minmax(0,1fr) auto auto;gap:6px}
.activity-toggle{grid-column:1/3;grid-row:1;justify-self:start;padding:8px 10px;white-space:nowrap}
.activity-time{grid-column:3;grid-row:1;justify-self:end}
#activity-seek{grid-column:1;grid-row:2}#activity-speeds{grid-column:2/4;grid-row:2;justify-self:end}}
"""

HTML = r"""
<section id="activity" aria-labelledby="activity-heading">
  <div class="activity-head"><h2 id="activity-heading">Research activity</h2><a id="activity-open-log" href="#research">Open research log <span aria-hidden="true">→</span></a></div>
  <p class="empty" id="activity-empty" hidden></p>
  <figure class="activity-panel" id="activity-figure">
    <div class="activity-legend">
      <span><i style="background:#2563eb"></i>Searches <b id="activity-search">0</b></span>
      <span><i style="background:#94a3b8"></i>Source reads <b id="activity-read">0</b></span>
      <span><i style="background:#7c3aed"></i>Queries &amp; calculations <b id="activity-calc">0</b></span>
      <span id="activity-cases-key" hidden><i></i>Case saved</span>
    </div>
    <div class="activity-unit">Actions per minute</div>
    <div class="activity-plot">
      <svg id="activity-chart" role="img" aria-label="Completed research actions per minute"></svg>
      <div id="activity-markers" role="group" aria-label="Saved cases in the research log"></div>
    </div>
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
const demoReplay = $("#demo-replay"), demoReplayDuration = $("#demo-replay-duration");

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
  $("#activity-markers").innerHTML = M.found.map(({oid, c, el}) => {
    const label = [clock(el), c.company, c.stream].filter(Boolean).join(" · ");
    return `<a class="activity-marker" href="#found/${esc(encodeURIComponent(oid))}" aria-label="Open research log at ${esc(label)}" data-label="${esc(label)}" hidden>${ICONS.finding}</a>`;
  }).join("");
  $("#activity-cases-key").hidden = !M.found.length;
}

// Keep links outside the SVG so a chart redraw leaves keyboard focus intact.
function activityMarkers(chart, t) {
  const circles = $$("g circle", chart), width = chart.viewBox.baseVal.width;
  const tipWidth = Math.min(235, width);
  let visible = 0;
  for (const [i, link] of $$("#activity-markers a").entries()) {
    link.hidden = activity.flags[i].t > t;
    if (link.hidden) continue;
    const x = Number(circles[visible++].getAttribute("cx"));
    link.style.left = x / width * 100 + "%";
    link.style.setProperty("--tip-width", tipWidth + "px");
    link.style.setProperty("--tip-offset", Math.min(12, width - tipWidth - x + 12) + "px");
  }
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
    const chart = $("#activity-chart");
    drawChart(chart, M.bins, flags, t, 158, bins);
    activityMarkers(chart, t);
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
  let action = "Play";
  if (playing) action = "Pause";
  else if (t >= activity.end) action = "Replay";
  else if (t > 0) action = "Resume";
  const showDuration = action === "Replay" || action === "Play";
  const seconds = Math.ceil(activityReplaySeconds());
  if (demoReplayDuration && demoReplayDuration.textContent !== clock(seconds)) demoReplayDuration.textContent = clock(seconds);
  const label = action + " activity" + (showDuration ? " · " + clock(seconds) : "");
  const toggle = $("#activity-toggle");
  if (toggle.dataset.label !== label) {
    toggle.dataset.label = label;
    toggle.setAttribute("aria-label", action + " activity" + (showDuration ? ", " + seconds + " seconds of playback" : ""));
    toggle.innerHTML = (playing ? ICONS.pause : ICONS.play) + "<span>" + label + "</span>";
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

function activityReplaySeconds() {
  // At the base speed one minute of research takes two seconds in the replay.
  return Math.min(activity.end, Math.max(40, Math.min(100, activity.end / 30))) / activity.rate;
}

function activityAdvance() {
  const now = performance.now(), seconds = (now - activity.lastTick) / 1000;
  activity.lastTick = now;
  const replaySeconds = activityReplaySeconds();
  if (replaySeconds) activity.time = Math.min(activity.end, activity.time + seconds * activity.end / replaySeconds);
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
  if (demoReplay) demoReplay.hidden = live() || empty || !activity.end;
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
  if (activity.model === M && M && $("#activity-chart").getBoundingClientRect().width) activityPaint(true);
}

function activityPlay(fromStart = false) {
  if (fromStart || activity.time >= activity.end) activity.time = 0;
  activity.lastTick = performance.now();
  if (activity.timer === null) activity.timer = setInterval(activityTick, 100);
}

$("#activity-toggle").onclick = () => {
  if (live() || !activity.end) return;
  if (activity.timer !== null) activityLeave();
  else activityPlay();
  activityPaint();
};
if (demoReplay) demoReplay.onclick = () => {
  activityPlay(true);
  activityPaint();
  const toggle = $("#activity-toggle");
  $("#activity").scrollIntoView({block:"start"});
  toggle.focus({preventScroll:true});
  toggle.scrollIntoView({block:"nearest"});
};
$("#activity-speeds").onclick = e => {
  const button = e.target.closest("button[data-rate]");
  if (!button) return;
  if (activity.timer !== null) activityAdvance();
  activity.rate = Number(button.dataset.rate);
  for (const option of $$("#activity-speeds button")) option.setAttribute("aria-pressed", String(option === button));
  activityPaint();
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
