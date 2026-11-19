"""Company findings and the public guide’s example HubSpot deal."""

STYLE = r"""
#v-findings .finding-intro{margin:0 0 24px}
#v-findings .finding-intro .lede{margin:0;max-width:780px}
#v-findings .finding-intro .requester{font-size:14px;color:var(--mute);margin:8px 0}
#v-findings .cards{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
#v-findings .company, #v-findings .crm{min-width:0;border:1px solid var(--line);border-radius:14px;background:var(--card)}
#v-findings .company{padding:20px 22px;display:flex;flex-direction:column;gap:13px}
#v-findings .company.wide{grid-column:1/-1}#v-findings .company h2{font-size:22px}
#v-findings .event{padding-top:12px;border-top:1px solid var(--line);display:flex;flex-direction:column;flex:1}
#v-findings .event-meta{color:var(--soft);font-size:15px}
#v-findings .event-meta strong{display:block;font-weight:650}#v-findings .event-meta small{display:block;margin-top:2px;font-size:13px;color:var(--mute)}
#v-findings .streams{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:24px;flex:1;margin-top:14px}
#v-findings .streams.single{grid-template-columns:1fr}#v-findings .stream{min-width:0;display:flex;flex-direction:column;gap:8px}
#v-findings .stream + .stream{border-left:1px solid var(--line);padding-left:24px}#v-findings .stream h3{font-size:16px}
#v-findings .measure{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 9px}
#v-findings .amount{font-weight:760;font-size:29px;letter-spacing:-.04em;line-height:1.1;font-variant-numeric:tabular-nums}
#v-findings .metric, #v-findings .basis, #v-findings .reference{font-size:13px;line-height:1.4;color:var(--mute)}#v-findings .reference{margin-top:5px}
#v-findings .reference b{font-weight:650;color:var(--soft)}#v-findings .scenario{font-size:14px;color:var(--soft)}#v-findings .scenario b{color:var(--ink);font-weight:650}
#v-findings .contact{font-size:14px;color:var(--soft);margin-top:auto;padding-top:3px}#v-findings .contact b{font-size:12px;font-weight:650;display:block;color:var(--mute)}
#v-findings .links{display:flex;flex-wrap:wrap;gap:3px 15px;font-size:13px;line-height:1.8;font-weight:600}#v-findings .links a{padding:2px 0}
#v-findings .stream details{margin-top:-2px;font-size:13px}#v-findings .stream summary{display:list-item;width:fit-content;color:var(--blue);font-weight:600}
#v-findings .stream blockquote{margin:7px 0 4px;border-left:2px solid #c5d6ef;padding:1px 0 1px 12px;color:var(--soft);font-size:14px}
#v-findings .crm{padding:18px 20px}#v-findings .crm h2{font-size:20px}
#v-findings .crm dl{margin:12px 0 0;display:grid;grid-template-columns:1.2fr 1.5fr 1.5fr .7fr;gap:8px 20px}#v-findings .crm dt{font-size:12px;color:var(--mute);font-weight:650}#v-findings .crm dd{margin:3px 0 0;font-size:14px;color:var(--soft)}#v-findings .crm dd.stage{color:var(--greenink);font-weight:650}

#v-findings .crm{margin-top:26px;scroll-margin-top:calc(var(--shell-height,58px) + 16px)}
#v-findings .company :is(h2,h3,p), #v-findings .crm h2{margin:0}#v-findings .company .reference{margin-top:5px}#v-findings .company .contact{margin-top:auto}
@media(max-width:730px){#v-findings .cards{grid-template-columns:1fr}#v-findings .company.wide{grid-column:auto}#v-findings .company{padding:17px}#v-findings .streams{gap:15px}#v-findings .stream + .stream{padding-left:15px}#v-findings .crm dl{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:540px){#v-findings .streams{grid-template-columns:1fr}#v-findings .stream + .stream{border:0;border-top:1px solid var(--line);padding:13px 0 0}#v-findings .company h2, #v-findings .crm h2{font-size:20px}#v-findings .crm{padding:16px}#v-findings .crm dl{column-gap:10px}}
"""

HTML = """<section class="view" id="v-findings" hidden>
  <header class="finding-intro"><h1>__TITLE__</h1><p class="lede" __PURPOSEHIDDEN__>__PURPOSE__</p>
    <p class="requester" id="requester" hidden></p></header>
  <div class="cards" id="cards"></div>
  <section id="hubspot" class="crm" aria-labelledby="crm-title" hidden><h2 id="crm-title">Example HubSpot deal</h2><dl id="deal"></dl></section>
</section>"""

JS = r"""
function stream(c, sharedBasis) {
  const worst = (c.scenarios || []).filter(s => s.shortfall != null).reduce(
    (max, next) => !max || next.shortfall > max.shortfall ? next : max, null);
  const scenario = worst && (worst.description || worst.label);
  const oid = encodeURIComponent(c.oid), period = periodLabel(c);
  const measure = c.stake == null ? "" : `<div class="measure"><b class="amount">${esc(money(c.stake,c.currency))}</b><span class="metric">Largest modeled shortfall${period ? " · " + esc(period) : ""}</span></div>`;
  const share = c.share == null || !c.of ? "" : `<p class="basis">${esc((c.share * 100).toFixed(1))}% of ${sharedBasis ? "reference revenue" : esc(c.of)}</p>`;
  const contact = c.who ? `<p class="contact"><b>Suggested contact</b>${esc(c.who)}</p>` : "";
  let source = "";
  try {
    const url = new URL((c.evidence || []).find(e => e.url)?.url);
    if (["http:", "https:"].includes(url.protocol)) {
      const label = (url.hostname === "sec.gov" || url.hostname.endsWith(".sec.gov")) && url.pathname.startsWith("/Archives/edgar/") ? "SEC filing" : "Source document";
      source = `<a href="${esc(url.href)}">${label}</a>`;
    }
  } catch {}
  return `<div class="stream"><h3>${esc(c.stream)}</h3>${measure}${share}${scenario ? `<p class="scenario"><b>Scenario</b> · ${esc(scenario)}</p>` : ""}${contact}
    <div class="links"><a href="#case/${oid}">Full case</a>${source}<a href="#found/${oid}">Research log</a></div>
    ${c.opening ? `<details data-outreach="${esc(c.oid)}"><summary>Draft outreach</summary><blockquote>${esc(c.opening)}</blockquote></details>` : ""}</div>`;
}
function eventRecord(cases) {
  const first = cases[0];
  const sharedBasis = cases.length > 1 && first.of && cases.every(c => c.of === first.of && c.currency === first.currency && c.denominator_value === first.denominator_value);
  const date = first.event_date ? new Date(first.event_date + "T12:00:00Z").toLocaleDateString("en-US",{month:"short",day:"numeric",year:"numeric",timeZone:"UTC"}) : "";
  const basis = sharedBasis ? `<p class="reference"><b>Reference revenue</b> · ${first.denominator_value != null ? esc(money(first.denominator_value,first.currency)) + " · " : ""}${esc(first.of)}</p>` : "";
  return `<div class="event"><div class="event-meta"><strong>${esc(first.event)}</strong>${date ? `<small>${esc(first.date_label || "Event reference date")} · ${esc(date)}</small>` : ""}</div>${basis}
    <div class="streams${cases.length === 1 ? " single" : ""}">${cases.map(c => stream(c,sharedBasis)).join("")}</div></div>`;
}
function findings() {
  const cases = M.found.map(f => f.c);
  const expanded = new Set($$("#cards details[open]").map(d => d.dataset.outreach));
  $("#cards").innerHTML = groupByCompany(cases).map(cases => {
    const events = new Map();
    for (const c of cases) {
      const key = JSON.stringify([c.event, c.event_date, c.date_label]);
      if (!events.has(key)) events.set(key, []);
      events.get(key).push(c);
    }
    return `<article class="company${cases.length > 1 ? " wide" : ""}"><h2>${esc(cases[0].company)}</h2>${[...events.values()].map(eventRecord).join("")}</article>`;
  }).join("") || `<p class="sub">${emptyFindings()}</p>`;
  for (const d of $$("#cards details")) d.open = expanded.has(d.dataset.outreach);
  const requester = $("#requester"), rq = status.requester;
  requester.hidden = !rq;
  if (rq) requester.innerHTML = `Requested from <a href="${esc(rq.url)}">${esc(rq.name)}</a> in HubSpot.`;
  const first = cases[0];
  $("#hubspot").hidden = !(DATA?.example_deal && first);
  if ($("#hubspot").hidden) return;
  const fields = [["Company", first.company], ["Distress revenue stream", first.stream], ["Distress person", first.who], ["Deal stage", "Proposed"]];
  $("#deal").innerHTML = fields.filter(([,value]) => value).map(([name,value]) =>
    `<div><dt>${esc(name)}</dt><dd${name === "Deal stage" ? ' class="stage"' : ""}>${esc(value)}</dd></div>`).join("");
}
"""
