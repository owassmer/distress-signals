# Data sources

Each source in `search_records` returns record IDs for `read_record`. Reading a record stores the evidence
and its source URL so the researcher can cite it. [The design specification](../SPEC.md#6-sources) also
describes the SEC, Federal Register and USAspending searches.

## Public record search

Use the source name below in `search_records`. The tool description lists the filters for that
source. When a search returns a continuation, pass it back with the same query and filters to read
the next page. A search hit identifies a record to investigate. Read the filing, notice or table
row to establish who is named, what the date means, and which revenue stream depends on the outcome.

| Source | What the researcher can read | Access and coverage |
|---|---|---|
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
