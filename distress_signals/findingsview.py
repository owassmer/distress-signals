"""The prospecting overview, grouped by company and event."""

STYLE = r"""
#overview{margin-bottom:26px;scroll-margin-top:calc(var(--shell-height,64px) + 20px)}
#overview h1{max-width:930px;margin:0 0 9px;font-size:clamp(30px,3.5vw,46px);font-weight:750;letter-spacing:-.045em;line-height:1.09}
#overview .lede{max-width:990px;margin:0;color:var(--soft);text-wrap:pretty;font-size:clamp(16px,1.5vw,18px);line-height:1.5}
#overview .requester{margin:9px 0 0;color:var(--mute);font-size:14px}
#section-findings,#hubspot{scroll-margin-top:calc(var(--shell-height,64px) + 16px)}
#section-findings h2{margin:0 0 12px;font-size:22px;letter-spacing:-.025em}
#section-findings .prospect-sheet{border:1px solid var(--line);border-radius:12px;background:var(--card);overflow:hidden}
.prospect-table{width:100%;table-layout:fixed;border-collapse:collapse;font-size:14px;line-height:1.4;color:var(--soft)}
.prospect-table col:nth-child(1){width:31%}.prospect-table col:nth-child(2){width:23%}.prospect-table col:nth-child(3){width:25%}.prospect-table col:nth-child(4){width:21%}
.prospect-table thead th{text-align:left;vertical-align:bottom;padding:9px 15px;background:#f1f4f7;color:var(--mute);font-size:12px;font-weight:630}
.prospect-table tbody :is(th,td){text-align:left;vertical-align:top;padding:12px 15px;border-top:1px solid var(--line);overflow-wrap:anywhere}
.prospect-table .finding-company{margin:0 0 4px;font-size:17px;line-height:1.3;letter-spacing:-.02em;color:var(--ink)}
.prospect-table .finding-event{font-weight:500;color:var(--soft)}
.finding-date,.finding-source,.finding-period,.finding-condition,.finding-role{display:block;color:var(--mute);font-size:12px;line-height:1.4;font-weight:450}
.finding-date{margin-top:4px}
.prospect-table .stream-link{color:var(--blue);font-weight:650;text-decoration:none}.prospect-table .stream-link:hover{text-decoration:underline}
.finding-source{margin-top:5px}.finding-source a{color:var(--mute);text-decoration:underline;text-decoration-color:#bdc8d7;text-underline-offset:3px}
.finding-measure{display:flex;align-items:baseline;flex-wrap:wrap;gap:0 7px}
.finding-amount{color:var(--ink);font-size:21px;font-weight:700;line-height:1.2;letter-spacing:-.035em;font-variant-numeric:tabular-nums}
.finding-period{white-space:normal}.finding-condition,.finding-role{margin-top:3px}
.finding-contact{font-size:14px;line-height:1.4;font-weight:580;color:var(--ink)}
.prospect-table .mobile-label{display:none}
.prospect-table .stream-continuation :is(th,td){border-top-color:var(--line2)}
.finding-deal{background:var(--card);border:1px solid var(--line);border-top:0;border-radius:0 0 12px 12px;color:var(--soft);font-size:14px}
#section-findings:has(.finding-deal:not([hidden])) .prospect-sheet{border-radius:12px 12px 0 0}
.finding-deal summary{padding:9px 15px;color:var(--blue);font-size:13px;font-weight:600;cursor:pointer}
.finding-deal summary::marker{color:var(--blue)}
.deal-subject{margin-left:6px;color:var(--mute);font-weight:450}
.deal-body{padding:3px 16px 15px}
.deal-body dl{display:flex;flex-wrap:wrap;gap:8px 28px;margin:0}
.deal-body dt{font-size:12px;color:var(--mute)}.deal-body dd{margin:2px 0 0}
.deal-body blockquote{max-width:780px;margin:5px 0 0;padding-left:12px;border-left:2px solid #bdd1ef;color:var(--ink)}
.deal-body .draft-label{display:block;margin-top:12px;font-size:12px;font-weight:600;color:var(--mute)}
@media(max-width:800px) and (min-width:661px){
  .prospect-table :is(thead,tbody) :is(th,td){padding-left:10px;padding-right:10px}
  .prospect-table col:nth-child(1){width:29%}.prospect-table col:nth-child(2){width:23%}.prospect-table col:nth-child(3){width:26%}.prospect-table col:nth-child(4){width:22%}
}
@media(max-width:660px){
  #overview{margin-bottom:22px}#overview h1{max-width:560px;font-size:32px}#section-findings h2{font-size:20px}
  .prospect-table,.prospect-table tbody{display:block}.prospect-table colgroup{display:none}
  .prospect-table thead{position:absolute;width:1px;height:1px;padding:0;overflow:hidden;clip-path:inset(50%)}
  .prospect-table tbody tr{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);column-gap:14px;row-gap:5px;padding:12px 14px;border-top:1px solid var(--line)}
  .prospect-table tbody:first-of-type tr:first-child{border-top:0}
  .prospect-table tbody :is(th,td){display:block;border:0;padding:0}
  .prospect-table .company-event{grid-column:1/-1;margin:0 0 4px;padding-bottom:9px;border-bottom:1px solid var(--line2)}
  .prospect-table .finding-company{font-size:17px;margin:0 0 2px}
  .prospect-table .finding-event{font-size:13px}.prospect-table .finding-date{margin-top:1px}
  .prospect-table .finding-stream{grid-column:1;grid-row:auto}
  .prospect-table .finding-stake{grid-column:2;grid-row:span 2}
  .prospect-table .finding-person{grid-column:1}
  .prospect-table .finding-source{margin-top:3px;font-size:11px}
  .prospect-table .finding-person .mobile-label{display:inline;color:var(--mute);font-size:12px}
  .prospect-table .finding-stake .mobile-label{display:block;flex-basis:100%;order:-1;color:var(--mute);font-size:11px;margin-bottom:1px}
  .prospect-table .finding-contact{font-weight:500;font-size:12px}
  .prospect-table .finding-role{display:none}
  .finding-deal summary{padding:10px 14px}.deal-subject{margin-left:0;display:block}
}
@media(max-width:480px){
  .prospect-table tbody tr{grid-template-columns:minmax(0,1fr);gap:4px}
  .prospect-table .finding-stream,.prospect-table .finding-stake,.prospect-table .finding-person{grid-column:1;grid-row:auto}
  .prospect-table .finding-stream{display:flex;align-items:baseline;flex-wrap:wrap;gap:0 10px}
  .prospect-table .finding-stream .finding-source{margin:0}
  .prospect-table .finding-stake .mobile-label{flex-basis:auto;order:0;margin:0}
  .prospect-table .finding-amount{font-size:19px}
  .prospect-table .finding-condition{margin:2px 0 0}
}

"""

HTML = """<section class="view" id="v-findings" hidden>
  <header class="finding-intro" id="overview"><h1>__TITLE__</h1><p class="lede" __PURPOSEHIDDEN__>__PURPOSE__</p>
    <p class="requester" id="requester" hidden></p></header>
  <section id="section-findings" aria-labelledby="findings-title">
    <h2 id="findings-title">__FINDINGSTITLE__</h2>
    <div id="cards"></div>
    <details id="hubspot" class="finding-deal" hidden><summary>Example HubSpot deal <span class="deal-subject" id="deal-subject"></span></summary><div class="deal-body" id="deal"></div></details>
  </section>
  __ACTIVITYHTML__
  __LEADSHTML__
</section>"""

JS = r"""
function findingSource(c) {
  const source = (c.evidence || []).find(e => /^https?:\/\//i.test(e.url));
  if (!source) return "";
  try {
    const url = new URL(source.url);
    const filing = (url.hostname === "sec.gov" || url.hostname.endsWith(".sec.gov")) && url.pathname.startsWith("/Archives/edgar/");
    const label = filing ? "SEC filing" : url.hostname.replace(/^www\./,"");
    return `<span class="finding-source"><a href="${esc(url.href)}">${esc(label)}</a></span>`;
  } catch { return ""; }
}

function prospectRow(c, eventCell, source) {
  const worst = (c.scenarios || []).filter(s => s.shortfall != null).reduce(
    (max, next) => !max || next.shortfall > max.shortfall ? next : max, null);
  const scenario = worst?.summary || worst?.description || worst?.label;
  const period = periodLabel(c), url = "#case/" + encodeURIComponent(c.oid);
  const contact = c.who || "", comma = contact.indexOf(", ");
  const name = comma === -1 ? contact : contact.slice(0, comma);
  const role = comma === -1 ? "" : contact.slice(comma + 2);
  const measure = c.stake == null ? "" : `<span class="finding-measure"><b class="finding-amount">${esc(money(c.stake,c.currency))}</b><span class="mobile-label">Largest modeled shortfall</span><span class="finding-period">${esc(period)}</span></span>
    ${scenario ? `<span class="finding-condition">${esc(scenario)}</span>` : ""}`;
  return `<tr${eventCell ? "" : ' class="stream-continuation"'}>${eventCell}
    <th class="finding-stream" scope="row"><a class="stream-link" href="${esc(url)}">${esc(c.stream || "View case")}</a>${source}</th>
    <td class="finding-stake">${measure}</td>
    <td class="finding-person">${name ? `<span class="mobile-label">Contact · </span><span class="finding-contact">${esc(name)}</span>` : ""}
      ${role ? `<span class="finding-role">${esc(role)}</span>` : ""}</td></tr>`;
}

function companyRows(cases) {
  const byEvent = new Map();
  for (const c of cases) {
    const key = JSON.stringify([c.event, c.event_date, c.date_label]);
    if (!byEvent.has(key)) byEvent.set(key, []);
    byEvent.get(key).push(c);
  }
  return Array.from(byEvent.values(), (streams, i) => {
    const c = streams[0], sources = streams.map(findingSource);
    const commonSource = streams.length > 1 && sources.every(source => source === sources[0]) ? sources[0] : "";
    const eventDate = c.event_date && new Date(c.event_date + "T12:00:00Z").toLocaleDateString("en-US",
      {month:"short", day:"numeric", year:"numeric", timeZone:"UTC"});
    const cell = `<th class="company-event" rowspan="${streams.length}" scope="rowgroup">
      ${i === 0 ? `<h3 class="finding-company">${esc(c.company || "Company unspecified")}</h3>` : ""}
      ${c.event ? `<div class="finding-event">${esc(c.event)}</div>` : ""}
      ${eventDate ? `<span class="finding-date">${esc(c.date_label || "Event reference date")} · ${esc(eventDate)}</span>` : ""}${commonSource}</th>`;
    return `<tbody aria-label="${esc(c.company || "Company findings")}">${streams.map((s, j) => prospectRow(s, j === 0 ? cell : "", commonSource ? "" : sources[j])).join("")}</tbody>`;
  }).join("");
}

function findings() {
  const cases = M.found.map(f => f.c);
  $("#cards").innerHTML = cases.length ? `<div class="prospect-sheet"><table class="prospect-table" aria-labelledby="findings-title">
    <colgroup><col><col><col><col></colgroup>
    <thead><tr><th scope="col">Company and event</th><th scope="col">Revenue stream</th>
    <th scope="col">Largest modeled shortfall</th><th scope="col">Suggested contact</th></tr></thead>
    ${groupByCompany(cases).map(companyRows).join("")}</table></div>` : `<p class="sub">${emptyFindings()}</p>`;
  const rq = status.requester, requester = $("#requester");
  requester.hidden = !rq;
  if (rq) requester.innerHTML = `Requested from <a href="${esc(rq.url)}">${esc(rq.name)}</a> in HubSpot.`;
  const first = cases[0], preview = $("#hubspot");
  preview.hidden = !(DATA?.example_deal && first);
  if (preview.hidden) return;
  $("#deal-subject").textContent = first.company || "";
  const fields = [["Revenue stream", first.stream], ["Suggested contact", first.who], ["Deal stage", "Proposed"]];
  $("#deal").innerHTML = "<dl>" + fields.filter(([,value]) => value).map(([name,value]) =>
    `<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd></div>`).join("") + "</dl>"
    + (first.opening ? `<span class="draft-label">Draft outreach</span><blockquote>${esc(first.opening)}</blockquote>` : "");
}
"""
