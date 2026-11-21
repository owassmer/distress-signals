# Data sources

Each source in `search_records` returns record IDs for `read_record`. Reading a record stores the evidence
and its source URL so the researcher can cite it. [The design specification](../SPEC.md#6-sources) also
describes the SEC, Federal Register and USAspending searches.

## Public record search

Use the source name below in `search_records`. The tool description lists the filters for that
source. When a search returns a continuation, pass it back with the same query and filters to read
the next page. The same proceeding can produce several records. A solicitation can give a response
deadline, while an earlier award names the supplier. The contract or a filing can then establish what
that supplier earns. Keep the records' identifiers, party roles, publication dates and event dates
with the evidence for each claim.

| Source | What the researcher can read | Access and coverage |
|---|---|---|
| `emma` | MSRB continuing disclosures identify the securities, issuer and any reported obligor. The filing itself can describe the event and its terms. California issuance reports also give repayment sources, optional call and maturity dates, and links to official statements. | Use an authorized [MSRB](https://www.msrb.org/Market-Data-and-Research/Continuing-Disclosure-Subscription) XML/PDF delivery for EMMA. [California DebtWatch](https://debtwatch.treasurer.ca.gov/issue-level-detail/issues) is a separate state-published collection available without a key. |
| `sedar_plus` | Canadian issuers' disclosure PDFs and publication dates. The original filing supplies the terms, dates and economic relationship. | Without a national catalog, [FREEDAR](https://freedar.ca/) points to original documents on the Canadian issuers' websites it indexes. Search an authorized [CSA Data Distribution Services](https://www.securities-administrators.ca/national-systems/data-distribution-services/) catalog, or one issuer's [Canadian Securities Exchange](https://thecse.com/site-terms/) filings with permission for commercial reuse. See the filing setup below. |
| `ferc_eqr` | Electric Quarterly Report contract records identify sellers, buyers and contract terms, including the reported termination date. | [FERC Electric Quarterly Reports](https://www.ferc.gov/power-sales-and-markets/electric-quarterly-reports-eqr). The public [report viewer](https://eqrreportviewer.ferc.gov/) supplies the quarter and filing versions. Search by respondent or page through the filings for contracts. |
| `sam_opportunities` | Solicitation posting dates and response deadlines, plus award numbers, dates, amounts and named recipients where reported. A known award number can be checked separately in USAspending. | [GSA active opportunities extract](https://sam.gov/data-services/Contract%20Opportunities/datagov?privacy=Public) is public. Its first download is large. Set `SAM_API_KEY` for the [notices API](https://open.gsa.gov/api/get-opportunities-public-api/). |
| `dod_contracts` | Dated Defense Department contract bulletins publish the award text, contractor names, amounts, and completion terms. A single bulletin can contain several awards. | [Department bulletin feed](https://public.govdelivery.com/accounts/USDOD/feed.rss) for recent announcements. Individual older bulletins can be read by ID. |
| `erate` | USAC Form 471 funding requests identify applicants, service providers, contract dates and amounts where supplied. | [USAC funding request data](https://opendata.usac.org/d/qdmp-ygft). Public Socrata data. |
| `municipal_contracts` | Local purchasing records name vendors and give jurisdiction-specific dates and amounts. | [Chicago](https://data.cityofchicago.org/d/rsxa-ify5), [Cook County](https://datacatalog.cookcountyil.gov/d/qh8j-6k63), [Montgomery County, Maryland](https://data.montgomerycountymd.gov/d/vmu2-pnrc), and [San Francisco](https://data.sfgov.org/d/cqi5-hm2d). Search one or all four jurisdictions. |
| `medicaid_contracts` | Iowa publishes named managed-care plan contracts and amendments. Texas publishes procurement timelines and links to solicitations with response deadlines and anticipated awards. | [Iowa HHS contracts](https://hhs.iowa.gov/medicaid/about-medicaid/policies-rules-regulations/contracts-rates) and [Texas HHSC procurements](https://www.hhs.texas.gov/business/contracting-hhs/procurement-opportunities). Select `state=IA` or `TX`. |
| `serff` | State rate filings identify the insurer, tracking number, status and proposed or approved terms where published. | [New York DFS](https://myportal.dfs.ny.gov/web/prior-approval/) and [Oregon DFR](https://www4.cbs.state.or.us/exs/ins/rates2/index.cfm?fuseaction=home.show_table). Select `state=NY` or `OR`. Coverage varies by insurance market. |
| `regulations_gov` | Dockets, agency documents and public comments. Read the posted date, comment deadline, any effective date and the organization where the agency identifies it. | [Regulations.gov API](https://open.gsa.gov/api/regulationsgov/). Shared public key or an individual key for a separate quota. |
| `oira` | Pending regulatory reviews and EO 12866 meetings. A review gives the date received. A meeting can give a scheduled date, requestor, client, attendees and written materials. | [OIRA pending reviews](https://www.reginfo.gov/public/do/eoReviewSearch) and [meeting records](https://www.reginfo.gov/public/do/eom12866Search). Public search. |
| `congress` | Scheduled committee hearings, published witnesses and affiliations, bills and their dated actions, hearing transcripts, and committee reports that can identify spending recipients. | [Congress.gov committee calendar](https://www.congress.gov/committee-schedule) and [GovInfo](https://www.govinfo.gov/). GovInfo supports a shared public key or a separate quota. |
| `federal_dockets` | Case captions, named parties, docket entries and contributed court documents. Orders can set or change a deadline. | [RECAP public collection](https://archive.org/details/usfederalcourts) for caption discovery and deposited files. Set `COURTLISTENER_TOKEN` for the [live RECAP index](https://www.courtlistener.com/help/api/). Coverage and currency depend on contributions. |
| `supreme_court` | Case dockets identify parties and preserve dated entries, scheduling orders, conference distributions and document links. | [Supreme Court docket](https://www.supremecourt.gov/docket/docket.aspx). Public case and party search. |
| `gao_protests` | GAO's protest docket gives the protester, filing date, due date and current status or outcome. | [GAO](https://www.gao.gov/legal/bid-protests) records are public by B-number. Set `GOVCON_API_KEY` for [broader protest discovery](https://govconapi.com/api-guide/protests). |
| `ptab` | Patent trial proceedings, petitioners, patent owners and filed documents. Scheduling orders can set or revise deadlines. The current public hearing calendar lists parties and scheduled hearings. | Use `record_type=hearings` for the [public calendar](https://www.uspto.gov/patents/ptab/hearings). Set `USPTO_API_KEY` for [Open Data Portal](https://data.uspto.gov/apis/ptab-trials/search-proceedings) proceedings and filed documents. |
| `orange_book` | Drug products and applicants, with listed patents, exclusivities and their expiry dates. Search by product, holder or expiration window. | [FDA Orange Book](https://open.fda.gov/apis/drug/orangebook/). Public API. |
| `fda_advisory` | Committee meetings and announcements, including agenda and sponsor information where disclosed. `record_type=approvals` searches Drugs@FDA for sponsors and decisions already recorded. | [FDA meeting calendar](https://www.fda.gov/advisory-committees/advisory-committee-calendar) and [Drugs@FDA](https://open.fda.gov/apis/drug/drugsfda/). |
| `clinical_trials` | Study sponsor, collaborators, posting dates and expected or actual completion. The record retains the sponsor's date precision and status. | [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/api). |
| `medicare_coverage` | National coverage and lab coding analyses. Tracking sheets identify the requestor and give expected completion, proposed memo deadlines, and actual decisions where available. | [CMS Coverage API](https://api.coverage.cms.gov/docs/v1/coverage-api.json). Public report and tracking sheets. |
| `medicare_stars` | Annual Medicare Advantage and Part D contract ratings, plan and parent names, table edition and published rating values. The edition and the year in which MA bonus payments are affected can differ. | [CMS annual tables](https://www.cms.gov/medicare/health-drug-plans/part-c-d-performance-data). Uses the latest standalone annual table by default. |
| `usitc` | Investigation participants and their roles, target dates, order dates and status. An investigation number also opens its public EDIS filing index. | [USITC investigations](https://ids.usitc.gov/) and [EDIS](https://edis.usitc.gov/). Investigation and filing metadata are public. Set `EDIS_API_TOKEN` for EDIS attachment PDFs. |
| `ofac` | General licenses and their revisions. Read the license for the named parties, authorized transactions, conditions and expiry. | [Treasury's selected licenses](https://ofac.treasury.gov/selected-general-licenses-issued-ofac) and [File Finder](https://ofac.treasury.gov/file-finder). |
| `ferc_elibrary` | Filings and orders by text, docket or posting date. An accession identifies the filing and lists its attachments. PDF, text and DOCX attachments have their own readable IDs. | [FERC eLibrary](https://elibrary.ferc.gov/eLibrary/search). Public records. |
| `utility_dockets` | South Dakota electric and gas dockets by year. Illinois hearings by calendar week, with docket and hearing records. | [South Dakota PUC](https://puc.sd.gov/dockets/) and [Illinois Commerce Commission](https://icc.illinois.gov/meetings). Select `state=SD` or `state=IL`. |
| `pjm_deactivations` | Unit requests, projected and actual dates, and changes in status. Owner letters and PJM responses can identify the owner. The transmission-owner zone identifies the grid area. | [PJM unit data](https://www.pjm.com/pjmfiles/media/planning/gen-deactivation-data/GenDeactivationUnits.xml) and [deactivation notices](https://www.pjm.com/planning/service-requests/gen-deactivations/generator-deactivation-notices). |
| `fcc` | Docket filings, named filers, deadlines and attachments. Auction reports provide announcements and round-level bidder records where published. | [FCC ECFS](https://www.fcc.gov/ecfs/) uses a shared public key by default. Set `FCC_API_KEY` for a separate quota. [Auction reports](https://auctiondata.fcc.gov/) are public. |
| `nrc_adams` | Licensing documents by text, docket or organization. Records distinguish the document date from its addition to the public library. | Set `NRC_ADAMS_API_KEY` for the [ADAMS library](https://adams-api-developer.nrc.gov/). `collection=notices` reads NRC's [Federal Register notices](https://www.federalregister.gov/agencies/nuclear-regulatory-commission) without a key, including references to ADAMS documents. |
| `warn` | Employer layoff notices, notice dates and effective dates. A supplier's dependence on the employer requires separate evidence. | National discovery uses [WARN Index](https://warnindex.com/) and retains state source and original document links where available. `state=WA` searches [Washington's notice filings](https://fortress.wa.gov/esd/file/WARN/Public/SearchWARN.aspx) directly. |
| `local_agendas` | Meeting dates, agenda publication times, individual matters and agenda notes. Search a Legistar jurisdiction by its client slug. | [Legistar Web API](https://webapi.legistar.com/Help). Supply a client slug. Set `LEGISTAR_TOKEN_<CLIENT>` for a jurisdiction that requires a token. |

### Filing deliveries

For MSRB records, set `EMMA_DISCLOSURE_DIR` to a directory containing the unpacked continuing-disclosure XML
and accompanying PDFs. The reader uses the latest transaction for each submission. If the publisher
supplied only index data, the observation identifies it as such. Without a configured delivery, the
municipal search opens California DebtWatch. Each California record names the state as publisher.

For SEDAR+, set `SEDAR_DDS_INDEX` to a current UTF-8 CSV from an authorized delivery. The columns are
`document_id,issuer,title,published,url,file`. Supply the publisher's stable document identifier,
publication date in ISO format, public document permalink, and original PDF path relative to the CSV.
Replace the catalog after applying amendments or withdrawals. An authorized CSE deployment can set
`CSE_SEDAR_LICENSED=1` and pass the issuer's listing slug as `cse_listing`.

## Payment ledgers

All 19 ledgers are published through the Socrata open data API, so one client reads all of them.
Each government names its columns differently. `config/ledgers.yaml` maps each ledger's columns to
one schema.

| Field | Meaning |
|---|---|
| `vendor` | Payee name as the government records it |
| `paid` | Date the payment left the government |
| `amount` | Dollar amount of the payment line |
| `unit` | Paying department |
| `invoice_date` | Invoice date, only where verified (see below) |
| `invoice_date_meaning` | `issued` if the date is when the vendor issued the invoice, `received` if it's when the government received it |
| `due_date`, `invoice_id`, `description` | Mapped where the ledger has them |

### Ledgers with a verified invoice date

These five ledgers support payment-delay measurement.

| ID | Government | Invoice date column | Meaning |
|---|---|---|---|
| `los_angeles` | Los Angeles, CA | `inv_date` | Received |
| `fulton_county` | Fulton County, GA | `vendor_invoice_date` | Received |
| `baton_rouge` | Baton Rouge / East Baton Rouge Parish, LA | `invoice_date` | Issued |
| `montgomery_county_md` | Montgomery County, MD | `inv_date` | Issued |
| `moreno_valley` | Moreno Valley, CA | `invoice_date` | Issued |

### Ledgers with payment dates only

These 14 ledgers show which governments and departments pay a vendor, and when.

| ID | Government | Notes |
|---|---|---|
| `dallas` | Dallas, TX | The dataset holds the current fiscal year only. |
| `kansas_city` | Kansas City, MO | Three yearly datasets (2024, 2025, 2026). Amount uses `sum_amount`, the per-voucher amount, because `payment_amount` is the whole check. |
| `cincinnati` | Cincinnati, OH | |
| `mesa` | Mesa, AZ | Payee names are stored without spaces. |
| `atlanta` | Atlanta, GA | |
| `riverside_county` | Riverside County, CA | |
| `ramsey_county` | Ramsey County, MN | |
| `providence` | Providence, RI | |
| `college_station` | College Station, TX | Some payee names are masked, such as `Lexip...#5401`. These are never matched to a company. |
| `mesquite` | Mesquite, TX | |
| `corona` | Corona, CA | |
| `cook_county` | Cook County, IL | |
| `connecticut` | State of Connecticut | The agency name is in `program`. The `invoice_date` column is an accounting date, so it isn't mapped. |
| `delaware` | State of Delaware | |

## Invoice date verification

A column is mapped as `invoice_date` only if it behaves like an invoice date in the live data. The
check takes the latest 5,000 payments in the ledger, computes payment date minus invoice date for
each, and requires all of the following:

- Fewer than 5% of gaps are negative.
- Fewer than 50% of gaps are zero.
- The median gap is greater than zero.

Run `python -m distress_signals ledgers --verify` to repeat the check. Connecticut fails it because its
column is an accounting date.

## Federal contract awards

`collect` queries the USAspending `spending_by_award` endpoint for each target. It requests contract
award types A through D from the last three years. USAspending's recipient search is fuzzy, so an
award is kept only if the recipient name contains one of the target's known names as whole words.
All federal awards are stored under one payer, `U.S. Federal government`, with the awarding
sub-agency as the department.

## Adding a ledger

1. Find the dataset ID on the government's Socrata portal.
2. Add an entry to `config/ledgers.yaml` with the column mapping. Map `invoice_date` only after
   running `ledgers --verify` and confirming it passes.
3. Run `discover` to classify the new government's departments and screen its payees, then
   `collect`.
