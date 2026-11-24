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
.finding-source{margin-top:5px}.finding-source+.finding-memo{margin-top:1px}.finding-memo a{font-weight:600}.finding-source a{color:var(--mute);text-decoration:underline;text-decoration-color:#bdc8d7;text-underline-offset:3px}
.finding-measure{display:flex;align-items:baseline;flex-wrap:wrap;gap:0 7px}
.finding-amount{color:var(--ink);font-size:21px;font-weight:700;line-height:1.2;letter-spacing:-.035em;font-variant-numeric:tabular-nums}
.finding-period{white-space:normal}.finding-condition,.finding-role{margin-top:3px}
.finding-contact{font-size:14px;line-height:1.4;font-weight:580;color:var(--ink)}
.prospect-table .mobile-label{display:none}
.prospect-table .stream-continuation :is(th,td){border-top-color:var(--line2)}
.prospect-queue col:nth-child(1){width:32%}.prospect-queue col:nth-child(2){width:26%}.prospect-queue col:nth-child(3){width:auto}.prospect-queue col:nth-child(4){width:146px}
.deal-toggle{display:inline-flex;align-items:center;gap:8px;max-width:100%;border:1px solid #cfd9e7;border-radius:6px;padding:5px 9px;background:var(--card);color:var(--soft);font:inherit;font-size:12px;font-weight:600;white-space:nowrap;cursor:pointer;line-height:1.4}
.deal-toggle:hover,.deal-toggle[aria-expanded=true]{border-color:#a5bbdc;background:#f3f7ff;color:var(--blue)}
.deal-toggle::after{content:"";flex:0 0 5px;width:5px;height:5px;border:solid currentColor;border-width:0 1.5px 1.5px 0;transform:translateY(-1px) rotate(45deg)}
.deal-toggle[aria-expanded=true]::after{transform:translateY(1px) rotate(225deg)}
.prospect-table .deal-cell{padding:15px 18px;background:#f6f9fd}
.deal-body{scroll-margin-top:calc(var(--shell-height,54px) + 16px);max-width:800px;color:var(--soft);font-size:14px;line-height:1.45}
.deal-head{display:flex;align-items:baseline;justify-content:space-between;flex-wrap:wrap;gap:4px 16px;margin-bottom:12px}
.deal-head small{display:block;color:var(--mute);font-size:12px}
.deal-head h4{margin:2px 0 0;color:var(--ink);font-size:16px;font-weight:650;line-height:1.3}
.deal-head a{font-size:13px;font-weight:600;white-space:nowrap}
.deal-head a:hover{text-decoration:underline}.deal-links{display:inline-flex;gap:16px;align-items:baseline}
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
  .prospect-table tbody .deal-row{display:block;padding:0}
  .prospect-table .deal-cell{padding:14px}
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
@media(min-width:661px) and (max-width:800px){
  .prospect-queue col:nth-child(1){width:31%}.prospect-queue col:nth-child(2){width:24%}
  .prospect-queue col:nth-child(3){width:auto}.prospect-queue col:nth-child(4){width:136px}
}
@media(max-width:660px){
  .prospect-queue tbody tr:not(.deal-row){grid-template-columns:minmax(0,1fr) auto;grid-template-areas:"company company" "stream action" "person action";gap:4px 12px}
  .prospect-queue tbody tr.stream-continuation{grid-template-areas:"stream action" "person action"}
  .prospect-queue .company-event{grid-area:company}
  .prospect-queue .finding-stream{grid-area:stream}
  .prospect-queue .finding-person{grid-area:person}
  .prospect-queue .deal-action{grid-area:action;align-self:center}
  .deal-body dl{gap:8px 20px}
}

"""

HTML = """<section class="view" id="v-findings" hidden>
  <header class="finding-intro" id="overview"><h1>__TITLE__</h1><p class="lede" __PURPOSEHIDDEN__>__PURPOSE__</p>
    <p class="requester" id="requester" hidden></p></header>
  <section id="section-findings" aria-labelledby="findings-title">
    __RESULTSHEADER__
    <div id="cards"></div>
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

function memoLinks(c) {
  return c.memo ? `<span class="finding-source finding-memo"><a href="${esc(c.memo.page)}" target="_blank" rel="noopener">Memo</a> · <a href="${esc(c.memo.pdf)}" download="${esc(c.memo.name)}">PDF</a></span>` : "";
}

function exposureCell(c) {
  const worst = (c.scenarios || []).filter(s => s.shortfall != null).reduce(
    (max, next) => !max || next.shortfall > max.shortfall ? next : max, null);
  const scenario = worst?.summary || worst?.description || worst?.label;
  const measure = c.stake == null ? "" : `<span class="finding-measure"><b class="finding-amount">${esc(money(c.stake,c.currency))}</b><span class="mobile-label">Largest modeled shortfall</span><span class="finding-period">${esc(periodLabel(c))}</span></span>
    ${scenario ? `<span class="finding-condition">${esc(scenario)}</span>` : ""}`;
  return `<td class="finding-stake">${measure}</td>`;
}

function prospectRow(c, eventCell, source, dealId) {
  const url = "#case/" + encodeURIComponent(c.oid);
  const contact = c.who || "", comma = contact.indexOf(", ");
  const name = comma === -1 ? contact : contact.slice(0, comma);
  const role = comma === -1 ? "" : contact.slice(comma + 2);
  return `<tr${eventCell ? "" : ' class="stream-continuation"'}>${eventCell}
    <th class="finding-stream" scope="row"><a class="stream-link" href="${esc(url)}">${esc(c.stream || "View case")}</a>${source}${memoLinks(c)}</th>
    ${dealId ? "" : exposureCell(c)}
    <td class="finding-person">${name ? `<span class="mobile-label">Contact · </span><span class="finding-contact">${esc(name)}</span>` : ""}
      ${role ? `<span class="finding-role">${esc(role)}</span>` : ""}</td>
    ${dealId ? `<td class="deal-action"><button type="button" class="deal-toggle" aria-expanded="false" aria-controls="${esc(dealId)}" aria-label="Preview deal for ${esc([c.company, c.stream].filter(Boolean).join(", "))}">Preview deal</button></td>` : ""}</tr>`;
}

function exampleDealRow(c, id) {
  const fields = [["Deal stage", "Proposed"], ["Company", c.company], ["Suggested contact", c.who]];
  return `<tr class="deal-row" hidden><td class="deal-cell" colspan="4">
    <section class="deal-body" id="${esc(id)}" aria-labelledby="${esc(id)}-title" tabindex="-1">
      <div class="deal-head"><div><small>HubSpot deal preview</small>
        <h4 id="${esc(id)}-title">${esc(c.stream || c.company || "Proposed deal")}</h4></div>
        <span class="deal-links">${c.memo ? `<a href="${esc(c.memo.page)}" target="_blank" rel="noopener">Memo</a><a href="${esc(c.memo.pdf)}" download="${esc(c.memo.name)}">PDF</a>` : ""}<a href="#case/${esc(encodeURIComponent(c.oid))}">Open case</a></span></div>
      <dl>${fields.filter(([, value]) => value).map(([name, value]) =>
        `<div><dt>${esc(name)}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
      ${c.opening ? `<span class="draft-label">Draft outreach</span><blockquote>${esc(c.opening)}</blockquote>` : ""}
    </section></td></tr>`;
}

function setExampleDealOpen(open, button = $('#cards .deal-toggle[aria-controls="hubspot"]')) {
  if (!button) return;
  const panel = document.getElementById(button.getAttribute("aria-controls"));
  const previous = $("#cards .deal-toggle[aria-expanded=true]");
  if (open && previous && previous !== button) setExampleDealOpen(false, previous);
  button.setAttribute("aria-expanded", String(open));
  panel.closest("tr").hidden = !open;
  return panel;
}

function companyRows(cases, dealIds) {
  const byEvent = new Map();
  for (const c of cases) {
    const key = JSON.stringify([c.event, c.event_date, c.date_label]);
    if (!byEvent.has(key)) byEvent.set(key, []);
    byEvent.get(key).push(c);
  }
  return Array.from(byEvent.values(), (streams, i) => {
    const c = streams[0], sources = dealIds ? [] : streams.map(findingSource);
    const commonSource = sources.length > 1 && sources.every(source => source === sources[0]) ? sources[0] : "";
    const eventDate = !dealIds && c.event_date && new Date(c.event_date + "T12:00:00Z").toLocaleDateString("en-US",
      {month:"short", day:"numeric", year:"numeric", timeZone:"UTC"});
    const cell = `<th class="company-event" rowspan="${streams.length}" scope="rowgroup">
      ${i === 0 ? `<h3 class="finding-company">${esc(c.company || "Company unspecified")}</h3>` : ""}
      ${c.event ? `<div class="finding-event">${esc(c.event)}</div>` : ""}
      ${eventDate ? `<span class="finding-date">${esc(c.date_label || "Event reference date")} · ${esc(eventDate)}</span>` : ""}${commonSource}</th>`;
    const rows = streams.map((s, j) => prospectRow(s, j === 0 ? cell : "", commonSource ? "" : sources[j] || "", dealIds?.get(s.oid)));
    const previews = dealIds ? streams.map(s => exampleDealRow(s, dealIds.get(s.oid))).join("") : "";
    return `<tbody aria-label="${esc(c.company || "Company findings")}">${rows.join("")}${previews}</tbody>`;
  }).join("");
}

function findings() {
  const cases = M.found.map(f => f.c);
  const dealIds = DATA?.example_deal ? new Map(cases.map((c, i) => [c.oid, i ? "hubspot-" + c.oid : "hubspot"])) : null;
  const headers = dealIds ? ["Company and event", "Revenue stream", "Suggested contact", "HubSpot"]
                          : ["Company and event", "Revenue stream", "Largest modeled shortfall", "Suggested contact"];
  $("#cards").innerHTML = cases.length ? `<div class="prospect-sheet"><table class="prospect-table${dealIds ? " prospect-queue" : ""}" aria-labelledby="findings-title">
    <colgroup><col><col><col><col></colgroup>
    <thead><tr>${headers.map(header => `<th scope="col">${header}</th>`).join("")}</tr></thead>
    ${groupByCompany(cases).map(group => companyRows(group, dealIds)).join("")}</table></div>` : `<p class="sub">${emptyFindings()}</p>`;
  const rq = status.requester, requester = $("#requester");
  requester.hidden = !rq;
  if (rq) requester.innerHTML = `Requested from <a href="${esc(rq.url)}">${esc(rq.name)}</a> in HubSpot.`;
}

$("#cards").addEventListener("click", e => {
  const button = e.target.closest("button.deal-toggle");
  if (!button) return;
  const open = button.getAttribute("aria-expanded") === "false";
  const panel = setExampleDealOpen(open, button);
  if (open) panel.scrollIntoView({block:"nearest"});
});
"""
