# Data sources

The researcher searches *series* of public records with `search_records` and reads them with `read_record`. A
series is one kind of record (every Federal Register document, every WARN notice, every federal award), described
to the researcher by one entry in a catalog: what a record is, who creates and publishes it, its jurisdiction,
the dates it covers and its fields. [The specification](../SPEC.md#6-sources) describes the design. This page
lists the 63 series, how each is reached, and the keys some of them need.

A series that needs a key is in a run's catalog only when the key is set in `.env`. 50 series need none.

## Keys

| `.env` | What it is | Series it adds |
|---|---|---|
| `DATA_GOV_API_KEY` | An [api.data.gov](https://api.data.gov/signup/) key, free; one key serves Regulations.gov and the FCC's ECFS. The shared `DEMO_KEY` allows 10 requests an hour | `fcc_ecfs_filings`, `regulatory_dockets`, `regulatory_documents`, `public_comments` |
| `USPTO_API_KEY` | A [USPTO Open Data Portal](https://data.uspto.gov/) key, free | `ptab_proceedings`, `ptab_documents` |
| `NRC_ADAMS_API_KEY` | A subscription key from the [NRC's developer portal](https://adams-api-developer.nrc.gov/) | `nrc_adams_documents` |
| `GOVCON_API_KEY` | A [GovCon API](https://govconapi.com/api-guide/protests) key (a third party's index of GAO's docket) | `gao_bid_protests` |
| `MSRB_CONTINUING_DISCLOSURE_DIR` | The directory a [Continuing Disclosure subscription](https://www.msrb.org/Market-Data-and-Research/Continuing-Disclosure-Subscription) is delivered to | `emma_continuing_disclosures` |
| `MSRB_PRIMARY_MARKET_DIR` | The directory a [Primary Market subscription](https://www.msrb.org/Market-Data-and-Research/Primary-Market-Subscription) is delivered to | `emma_official_statements`, `emma_advance_refunding_documents` |
| `SEDAR_DDS_INDEX` | The catalog CSV of a [CSA Data Distribution Services](https://www.securities-administrators.ca/national-systems/data-distribution-services/) delivery | `sedar_filings` |
| `CSE_SEDAR_LICENSED` | `1` where the deployment has the Canadian Securities Exchange's permission for commercial reuse ([site terms](https://thecse.com/site-terms/)) | `cse_sedar_filings` |

Four more settings change how a series is reached without adding one. `COURTLISTENER_TOKEN` reads the RECAP archive
live through CourtListener, which also searches by docket number, filing date and the text of filings. Since May
2026 a CourtListener account without a [Free Law Project membership](https://free.law/membership) may make 5 requests
a minute and 125 a day; at those limits the Internet Archive's copy of the archive serves a run better. `EDIS_API_TOKEN`
adds the text of attachments to `usitc_filings`. `LEGISTAR_TOKEN_<CLIENT>` adds a council whose Legistar asks for a
token (`config/legistar.yaml`) to `local_legislation`. `SOCRATA_APP_TOKEN` raises the request limits of the payment
ledgers' portals. `SEC_USER_AGENT`, a name and contact email, is required by the SEC for every request.

An MSRB delivery is zip files of XML transactions ("Index Data") and the PDFs they describe ("Doc"). It is read where
it lies, zipped or unpacked, and the latest transaction for a submission stands. To see the format, MSRB publishes a
sample of each subscription: **Historical Data Sample** on the Continuing Disclosure page, and **Historical Data Sample
(Primary Market)** on the Primary Market page. Put one in its directory and the series read it; the samples date from
2009 and 2018 and are the format, not the market. The SEDAR+ catalog is a UTF-8 CSV with the columns `document_id,issuer,title,published,url,file`:
the publisher's document identifier, the filing date, the document's public address, and the PDF's path beside the CSV.

## The series

### The Federal Register

`sources/federal_register.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `federal_register` | A rule, proposed rule, notice or presidential document | Federal agencies and the President; Office of the Federal Register |  |

How it is reached:

```
The Federal Register (federalregister.gov): every rule, proposed rule, notice and presidential document
since 1994.

The Register returns, and counts, at most 10,000 documents for one query. A search by words comes back by
relevance and stops there. A search without words, and any search read to its end, comes back newest first
and continues by publication date, which has no such limit; its count then comes from the Register's count
of documents by type, which is not capped.
```

### SEC EDGAR

`sources/sec.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `edgar_filers` | A company, fund or person that files with the SEC, under its Central Index Key (CIK) | U.S. Securities and Exchange Commission |  |
| `edgar_filing_documents` | A document of a filing made with the SEC: the form itself, or one of its exhibits | SEC filers; U.S. Securities and Exchange Commission (EDGAR full-text search) |  |
| `edgar_xbrl_facts` | A value a filer reported in its financial statements for one period, as XBRL data | SEC filers; U.S. Securities and Exchange Commission |  |

How it is reached:

```
SEC EDGAR, three series: filers, the documents of their filings, and the figures they report as data.

edgar_filers            every company, fund or person with a CIK. Names come from two SEC files, each kept
                        for a day in out/: company_tickers.json (the companies with a ticker) and
                        cik-lookup-data.txt (every name EDGAR holds for every filer, current and former, about a
                        million), indexed with SQLite full-text search in out/sec_names.db. A search by SIC
                        code or state goes through EDGAR's company browser. Each filer's fields come from its
                        submissions record.
edgar_filing_documents  EDGAR full-text search: the documents of filings since 2001, by words, filer, form,
                        date, SIC code and state. It returns 100 at a time and at most 10,000 for one search,
                        so a search read to its end is split into date windows that each come back whole.
edgar_xbrl_facts        the values a filer reported in its financial statements (XBRL company facts), read
                        for one filer at a time.
```

### USAspending

`sources/usaspending.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `usaspending_awards` | A federal prime award: a contract, indefinite delivery vehicle (IDV), grant, loan, direct payment or other assistance | Federal agencies; U.S. Department of the Treasury (USAspending.gov) |  |
| `usaspending_transactions` | An action on a federal award: the award itself, or a modification to it, with the amount it obligates | Federal agencies; U.S. Department of the Treasury (USAspending.gov) |  |
| `usaspending_award_funding` | The obligations and outlays on one federal award from one federal account in one reporting period | Federal agencies; U.S. Department of the Treasury (USAspending.gov) |  |

How it is reached:

```
USAspending.gov, three series: federal awards, the transactions on them, and their funding by federal
account.

  usaspending_awards         prime awards of every type. The search API takes one group of award types at a
                             time, so a search across types asks each group in turn. It continues by the last
                             record's sort value, which has no limit on how far it reads.
  usaspending_transactions   each action on an award: the award itself and every modification. Read for one
                             award from its own history, or across awards by recipient, agency and action date.
  usaspending_award_funding  an award's obligations and outlays by federal account and reporting period, read
                             for one award at a time.

A range on an award's end date or start date has no filter in the API. The search then sorts by that date,
newest first, and reads until it passes the range, which reaches every award in it.
```

### State and local payment ledgers

`sources/ledgers.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `government_payments` | A payment by a state or local government to a payee: one line of its published checkbook | State and local governments |  |

How it is reached:

```
State and local government payment ledgers (Socrata SODA API): who a government pays, from which
department, how much and when. config/ledgers.yaml maps each government's published checkbook onto the same
fields; a search asks every ledger at once and merges their payments by date.
```

### Federal contract notices and announcements

`sources/federal_contracts.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `sam_contract_opportunities` | A federal contract opportunity notice active on SAM.gov: a presolicitation, solicitation, sources-sought or award notice, or another notice of a procurement | Federal contracting offices; General Services Administration (SAM.gov) |  |
| `dod_contract_announcements` | An announcement of one contract action valued at $7.5 million or more, from the Department of War's daily release | U.S. Department of War |  |

How it is reached:

```
Two series: contract opportunity notices active on SAM.gov, and the Department of War's daily contract
announcements.

  sam_contract_opportunities   GSA publishes every active notice in one daily file (about 225 MB). It is downloaded
                               when it changes and indexed in out/sources/sam_notices.db: each notice's fields, and
                               its title and description for full-text search. A search is answered from the index.
  dod_contract_announcements   The Department lists its daily releases in a feed (the last 500), but its release
                               pages refuse scripted readers. Each release's text is read from the Internet
                               Archive's copy of the page, kept in out/sources/dod_contracts/, and split into its
                               announcements: one paragraph for each contract action, under its military
                               department's heading.
```

### Contracts with end dates

`sources/contract_data.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `erate_funding_requests` | A funding request (FRN) on an FCC Form 471: a school's or library's request for E-Rate support for services from one service provider under one contract, as first filed or as it now stands | E-Rate applicants; Universal Service Administrative Company |  |
| `municipal_contracts` | A row of a city's or county's published contracts: a contract, or an amendment or purchase order under it, with its vendor and term | City and county governments |  |
| `eqr_contracts` | A contract for the sale of electricity or transmission that a seller reported to FERC in one quarter's Electric Quarterly Report | Public utilities and power marketers under FERC's jurisdiction; Federal Energy Regulatory Commission |  |

How it is reached:

```
Three series of contracts with end dates: E-Rate funding requests, city and county contracts, and the
contracts electricity sellers report to FERC.

  erate_funding_requests   USAC's open data (Socrata), which applies every filter and counts the matches.
  municipal_contracts      four city and county portals (Socrata) with their own column names, mapped onto the
                           same fields; a search asks them all and merges their rows by end date.
  eqr_contracts            FERC publishes each quarter's Electric Quarterly Reports as one archive of about
                           4,000 filings and 4 GB, one zip per respondent. A search by respondent reads that
                           respondent's filing. Any other search reads the filings in turn, a few at a time, and
                           returns a cursor: it reaches every contract, slowly.
```

### Rulemaking

`sources/rulemaking.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `regulatory_dockets` | A docket on Regulations.gov: an agency's file for one rulemaking or other action, holding its documents and the comments on them | Federal agencies; General Services Administration (Regulations.gov) | `DATA_GOV_API_KEY` |
| `regulatory_documents` | A document an agency posted to a docket on Regulations.gov: a proposed rule, a rule, a notice, or material supporting one | Federal agencies; General Services Administration (Regulations.gov) | `DATA_GOV_API_KEY` |
| `public_comments` | A comment submitted to an agency on a document it posted for comment on Regulations.gov, with the submitter and organization the agency shows | Commenters: companies, associations, governments and individuals; General Services Administration (Regulations.gov) | `DATA_GOV_API_KEY` |
| `eo12866_reviews` | A review of a regulatory action by OIRA under Executive Order 12866, before the agency may publish it: under review now, or concluded with OIRA's decision | Office of Information and Regulatory Affairs, Office of Management and Budget |  |
| `eo12866_meetings` | A meeting OIRA held or has scheduled with outside parties about a regulatory action under Executive Order 12866 review, with who asked for it and the client they act for | Office of Information and Regulatory Affairs, Office of Management and Budget |  |

How it is reached:

```
Five series of the federal rulemaking record: Regulations.gov's dockets, documents and comments, and OIRA's
reviews of regulatory actions under Executive Order 12866 and its meetings with outside parties about them.

  regulatory_dockets, regulatory_documents, public_comments
        Regulations.gov's API, with an api.data.gov key. One search returns at most 5,000 records, so a search
        that runs past them goes on from the last record's modification time, as the API's documentation says to.
        The API's times are UTC and its dates are Eastern: a comment period that ends 2027-01-12T04:59:59Z ends
        on January 11.
  eo12866_reviews
        OIRA publishes its reviews as files: the actions now under review, and each year's concluded reviews.
        They are kept in out/sources/eo12866_reviews/.
  eo12866_meetings
        OIRA's meeting search lists meetings by date. Who asked for a meeting, and for which client, is on the
        meeting's own page, which is read for each meeting listed and kept in out/sources/eo12866_meetings/.
```

### Congress

`sources/congress.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `bills` | A bill or resolution of the current Congress, with its sponsor, committees and latest action; its status file adds every action, its summary, cost estimates and related bills | U.S. Congress; Government Publishing Office |  |
| `bill_text_versions` | A version of the text of a bill or resolution: as introduced, reported, engrossed, enrolled or another stage | U.S. Congress; Government Publishing Office |  |
| `committee_reports` | A report of a House, Senate or conference committee to its chamber, most often on a bill it has reported | Committees of Congress; Government Publishing Office |  |
| `congressional_hearings` | The published transcript of a hearing before a committee of Congress, with its witnesses and their affiliations | Committees of Congress; Government Publishing Office |  |
| `house_committee_meetings` | A hearing, markup or other meeting of a House committee or subcommittee, held or scheduled, with its witnesses and the documents filed for it | Committees of the House of Representatives; Clerk of the House (Committee Repository) |  |
| `senate_committee_meetings` | A hearing or meeting of a Senate committee or subcommittee now on the Senate's schedule | Committees of the Senate; Secretary of the Senate |  |

How it is reached:

```
Six series of the record of Congress.

bills                     GPO publishes every bill's status as a file, and each kind of bill's files as one archive.
                          The current Congress's archives are downloaded when they change and indexed in
                          out/sources/bills.db: each bill's fields, and its titles, summary, subjects and actions for
                          full-text search. A search is answered from the index; a read fetches the bill's own file.
bill_text_versions,       GovInfo's own search finds the packages; each package's fields are read from its
committee_reports,        descriptive metadata (MODS), kept in out/sources/govinfo/.
congressional_hearings
house_committee_meetings  The Clerk's Committee Repository lists meetings by week; a meeting's title, witnesses and
                          documents are on its own page, kept in out/sources/house_meetings/.
senate_committee_meetings The Senate's file of the committee meetings now scheduled.
```

### Courts and bid protests

`sources/court_records.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `federal_court_dockets` | The docket of a case in a U.S. district, bankruptcy or appellate court, as far as RECAP's contributors have bought it from PACER | Federal courts; Free Law Project's RECAP archive, as deposited at the Internet Archive |  |
| `federal_court_docket_entries` | An entry on a federal court docket: a filing or an order, with the date it was filed | Federal courts; Free Law Project's RECAP archive, as deposited at the Internet Archive |  |
| `supreme_court_dockets` | The docket of a case or an application before the Supreme Court of the United States | Supreme Court of the United States |  |
| `gao_bid_protests` | A bid protest filed at the Government Accountability Office against a federal contract award or solicitation | U.S. Government Accountability Office; GovCon API (govconapi.com), which indexes GAO's docket | `GOVCON_API_KEY` |

How it is reached:

```
Four series: federal court dockets and their entries (the RECAP archive), the Supreme Court's docket, and
GAO bid protests.

  federal_court_dockets,          RECAP holds the dockets and filings its users have bought from PACER. Free Law
  federal_court_docket_entries    Project deposits them at the Internet Archive, where they are searched by
                                  caption and court; a docket's entries are read from its deposited docket sheet.
                                  With a CourtListener token (COURTLISTENER_TOKEN) the same archive is read live
                                  through CourtListener's API instead, which also searches by docket number, filing
                                  dates and the text of filings.
  supreme_court_dockets           the Court publishes each docket as a JSON file. Its own search form finds cases by
                                  words. An open search walks the current term's three sequences of case numbers
                                  (paid, in forma pauperis, applications) downward and merges them by docketed date.
  gao_bid_protests                GAO's own docket pages refuse scripted readers, so protests are read from GovCon
                                  API's index of them, which needs a key (GOVCON_API_KEY).
```

### Drugs and patents

`sources/drug_patent.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `orange_book_products` | A drug product approved under a new drug application (NDA) or an abbreviated one (ANDA), as the Orange Book lists it | U.S. Food and Drug Administration |  |
| `orange_book_patents` | A patent listed in the Orange Book for an approved drug product, with its expiration date | U.S. Food and Drug Administration |  |
| `orange_book_exclusivities` | A period of marketing exclusivity the Orange Book lists for an approved drug product, with its code and expiration date | U.S. Food and Drug Administration |  |
| `drugsfda_applications` | An application to market a drug in the United States (NDA, ANDA or BLA), with its products and FDA's actions on it | U.S. Food and Drug Administration |  |
| `fda_advisory_committee_meetings` | A meeting of an FDA advisory committee, as announced on FDA's calendar | U.S. Food and Drug Administration |  |
| `ptab_hearings` | An oral hearing on the Patent Trial and Appeal Board's published schedule | U.S. Patent and Trademark Office, Patent Trial and Appeal Board |  |
| `ptab_proceedings` | A trial before the Patent Trial and Appeal Board challenging a patent: an inter partes review, post-grant review, covered business method review or derivation | U.S. Patent and Trademark Office, Patent Trial and Appeal Board | `USPTO_API_KEY` |
| `ptab_documents` | A document filed in a Patent Trial and Appeal Board trial: a petition, response, order or decision | Parties to PTAB trials and the Board; U.S. Patent and Trademark Office, Patent Trial and Appeal Board | `USPTO_API_KEY` |

How it is reached:

```
Eight series on drug approvals and patents: the Orange Book's products, patents and exclusivities; Drugs@FDA
applications; FDA advisory committee meetings; and the Patent Trial and Appeal Board's proceedings, documents
and hearings.

  orange_book_products,        FDA publishes the Orange Book as three data files in one small zip, updated
  orange_book_patents,         monthly. They are read whole; each patent and exclusivity carries its product's
  orange_book_exclusivities    applicant, trade name and ingredient.
  drugsfda_applications        openFDA's copy of Drugs@FDA, which applies names, numbers and action dates, and
                               reads no further than 25,000 records into one search.
  fda_advisory_committee_meetings   FDA's advisory committee calendar, read whole.
  ptab_hearings                the Board's published hearing schedules (PDF tables), read whole.
  ptab_proceedings,            the USPTO Open Data Portal, which needs a key (USPTO_API_KEY).
  ptab_documents
```

### Health care

`sources/healthcare.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `clinicaltrials_studies` | A clinical study registered on ClinicalTrials.gov, under its NCT number | Study sponsors and investigators; U.S. National Library of Medicine |  |
| `medicare_coverage_analyses` | A national coverage analysis (NCA) or coding analysis for labs (CAL): CMS's review of whether and how Medicare covers an item or service, with its tracking sheet | Centers for Medicare & Medicaid Services |  |
| `medicare_star_ratings` | A Medicare Advantage or Part D contract's summary star ratings for one rating year | Centers for Medicare & Medicaid Services |  |

How it is reached:

```
Three series: clinical studies registered on ClinicalTrials.gov, Medicare's national coverage analyses, and
Medicare Advantage and Part D star ratings.

  clinicaltrials_studies       the registry's API applies every filter here, and continues by page token.
  medicare_coverage_analyses   CMS lists every analysis in one report without their dates; each one's tracking
                               sheet has them. The sheets are kept in out/ and read again only when CMS's
                               last-updated stamp for the analysis changes, so a search has every date to filter.
  medicare_star_ratings        CMS publishes one table a year, in a zip file. A search reads the years it
                               needs, newest first.
```

### State health agencies

`sources/state_health.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `medicaid_managed_care_contracts` | A contract, or an amendment to one, between a state Medicaid agency and a managed-care plan | State Medicaid agencies |  |
| `medicaid_managed_care_procurements` | A state's procurement of managed-care plans for one Medicaid program, with its timeline and solicitation | State Medicaid agencies |  |
| `insurance_rate_filings` | A health insurer's rate filing with its state regulator, under its SERFF tracking number | State insurance regulators |  |

How it is reached:

```
Three series from state health agencies' own pages: Medicaid managed-care contracts (Iowa), Medicaid
managed-care procurements (Texas), and health insurers' rate filings (New York and Oregon).

Each page is read whole and its records filtered here. Rate filings are published a year at a time; a search
reads the years it needs, newest first.
```

### Trade and sanctions

`sources/trade.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `usitc_investigations` | A U.S. International Trade Commission investigation in one phase: an import-injury (antidumping or countervailing duty) case, a Section 337 unfair-import case, or a fact-finding study | U.S. International Trade Commission |  |
| `usitc_filings` | A public document filed in a USITC investigation, in the Commission's docket system (EDIS) | Parties to USITC investigations and the Commission; U.S. International Trade Commission |  |
| `ofac_general_licenses` | A general license issued by the Office of Foreign Assets Control under a sanctions program, in each version posted, with related notices | U.S. Department of the Treasury, Office of Foreign Assets Control |  |

How it is reached:

```
Three series: USITC investigations, the public documents filed in them (EDIS), and OFAC's general licenses.

usitc_investigations     the Commission's daily export of every investigation, read whole and filtered here.
usitc_filings            EDIS lists public filings newest first, 20 at a time, by investigation, document type
                         and firm. An attachment's text needs an EDIS account token (EDIS_API_TOKEN); without
                         one a read returns the filing's details and lists its attachments.
ofac_general_licenses    Treasury's File Finder, restricted to general licenses: every version it has posted,
                         searched in full text.
```

### Energy

`sources/energy.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `ferc_elibrary` | A filing made with the Federal Energy Regulatory Commission, or an order or notice it issued, under its accession number | FERC and the parties to its proceedings; Federal Energy Regulatory Commission |  |
| `utility_dockets` | A docket opened at a state public utilities commission: a rate case, tariff filing, siting permit or other proceeding | State public utilities commissions |  |
| `utility_commission_hearings` | A hearing scheduled in a docket before a state utility commission | State utility commissions |  |
| `pjm_generator_deactivations` | A generating unit's request to deactivate, with its dates and status, as PJM lists it | PJM Interconnection |  |
| `pjm_deactivation_notices` | A letter in PJM's index of deactivation notices: a generator owner's notice, a revision of it, or PJM's response | Generator owners and PJM Interconnection; PJM Interconnection |  |

How it is reached:

```
Five series: FERC's eLibrary, state utility commission dockets (South Dakota) and hearings (Illinois), and
PJM's generator deactivations and the notice letters behind them.

  ferc_elibrary                  eLibrary's own search applies the words, a docket and one date range, and
                                 pages by number.
  utility_dockets                South Dakota publishes one index page per sector and year; a search reads the
                                 years it needs, newest first.
  utility_commission_hearings    Illinois publishes its hearing calendar a week at a time; a search reads the
                                 weeks it needs, latest first.
  pjm_generator_deactivations    PJM's export of every deactivation request, read whole.
  pjm_deactivation_notices       PJM's index of the letters: owners' notices, revisions and PJM's responses.
```

### Communications and nuclear licensing

`sources/communications.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `fcc_ecfs_filings` | A filing in an FCC proceeding: a comment, reply, ex parte notice, petition or other submission | Parties to FCC proceedings and the Commission; Federal Communications Commission (Electronic Comment Filing System) | `DATA_GOV_API_KEY` |
| `fcc_auction_announcements` | An announcement the FCC made to bidders during a spectrum auction | Federal Communications Commission |  |
| `fcc_auction_bidder_status` | A bidder's eligibility, activity and commitments in one round of an FCC spectrum auction | Federal Communications Commission |  |
| `nrc_adams_documents` | A document in the Nuclear Regulatory Commission's public library: a licensing application, inspection report, order, letter or other record of a licensee's docket | The NRC and its licensees and applicants; U.S. Nuclear Regulatory Commission | `NRC_ADAMS_API_KEY` |

How it is reached:

```
Four series: filings in FCC proceedings (ECFS), the announcements and bidder status of FCC spectrum auctions,
and documents in the NRC's ADAMS library.

  fcc_ecfs_filings            ECFS applies the words, a proceeding, a filer and a range of received dates. It
                              reads no further than 10,000 records into one query, so a search continues from
                              the last record's received time, which has no such limit. It takes an api.data.gov
                              key (DATA_GOV_API_KEY); the shared demo key allows ten requests a day.
  fcc_auction_announcements,  the FCC's public reporting system publishes each auction's reports as one file,
  fcc_auction_bidder_status   read for one auction at a time.
  nrc_adams_documents         the ADAMS public search API, which needs a subscription key (NRC_ADAMS_API_KEY).
```

### Layoff notices and local legislation

`sources/local_notices.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `warn_notices` | An employer's notice of a plant closing or mass layoff, given to its state under the WARN Act | State dislocated worker units; WARN Index (warnindex.com), which compiles the states' postings |  |
| `local_legislation` | A matter before a city or county legislative body: an ordinance, resolution, contract approval or other file, with the dates it was introduced, placed on an agenda and passed | City and county legislative bodies; Granicus (Legistar) |  |

How it is reached:

```
Two series: WARN notices of plant closings and mass layoffs, and legislation before city and county councils.

warn_notices        WARN Index (warnindex.com) compiles the notices each state posts. Its API applies the
                    state, the employer and a range of notice dates, and counts the matches. A read returns
                    the notice's fields and, where the state posts the employer's letter, the letter's text.
local_legislation   Legistar's web API, one client per council (config/legistar.yaml). Each client applies
                    every filter itself; a search asks them all at once and merges their matters by the time
                    each was last modified.
```

### Issuers outside the SEC and EMMA

`sources/issuers.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `california_debt_issues` | A debt issue a California state or local public agency reported to the California Debt and Investment Advisory Commission, proposed or sold | California public agencies that issue debt; California Debt and Investment Advisory Commission |  |
| `california_debt_documents` | A document filed with the California Debt and Investment Advisory Commission for a public agency's debt issue: its official statement, indenture, resolution, lease or another of the issue's documents | California public agencies that issue debt; California Debt and Investment Advisory Commission |  |
| `sedar_filings` | A document an issuer filed with Canadian securities regulators on SEDAR+ | Issuers of securities in Canada; Canadian Securities Administrators | `SEDAR_DDS_INDEX` |
| `cse_sedar_filings` | A document a company listed on the Canadian Securities Exchange filed on SEDAR+, as the Exchange republishes it | Companies listed on the Canadian Securities Exchange; Canadian Securities Exchange | `CSE_SEDAR_LICENSED` |

How it is reached:

```
Four series of what issuers of securities file outside the SEC and EMMA.

california_debt_issues,       The California Debt and Investment Advisory Commission's DebtWatch: every debt issue a
california_debt_documents     California public agency reported, and the documents filed for each (official
                              statements, indentures, resolutions and others). Its API applies identifiers, the values
                              a column lists, and the ranges a column accepts, all read from its own column metadata,
                              and finds one word or phrase; the registry applies the rest.
sedar_filings                 A catalog of SEDAR+ documents from the Canadian Securities Administrators' data
                              distribution service: the CSV at SEDAR_DDS_INDEX, with the PDFs beside it.
cse_sedar_filings             The SEDAR+ filings the Canadian Securities Exchange republishes for each company it
                              lists, read by listing. The Exchange's terms ask permission for commercial reuse;
                              CSE_SEDAR_LICENSED says the deployment has it.
```

### EMMA

`sources/msrb.py`

| Series | One record is | Creator; publisher | Needs |
|---|---|---|---|
| `emma_continuing_disclosures` | A file of a continuing disclosure an issuer or obligated person submitted to EMMA: annual financial information, audited statements, or notice of an event affecting the bonds | Issuers of municipal securities and the obligated persons behind them; Municipal Securities Rulemaking Board (EMMA) | `MSRB_CONTINUING_DISCLOSURE_DIR` |
| `emma_official_statements` | An official statement for a new issue of municipal securities, as submitted to EMMA, with the issue's securities and their maturities | Issuers of municipal securities and their underwriters; Municipal Securities Rulemaking Board (EMMA) | `MSRB_PRIMARY_MARKET_DIR` |
| `emma_advance_refunding_documents` | A document submitted to EMMA for an advance refunding: the escrow for outstanding municipal securities to be paid off before they can be called, with the securities refunded | Issuers of municipal securities and their agents; Municipal Securities Rulemaking Board (EMMA) | `MSRB_PRIMARY_MARKET_DIR` |

How it is reached:

```
Three series from the MSRB's EMMA subscriptions: continuing disclosures, and the official statements and advance
refunding documents of the primary market.

The MSRB delivers each subscription as zip files of XML transactions ("Index Data") and the PDFs they describe
("Doc"). MSRB_CONTINUING_DISCLOSURE_DIR and MSRB_PRIMARY_MARKET_DIR are the directories the deliveries are put
in, zipped as delivered or unpacked; they are read where they lie, and read again when a file in them changes.
Each transaction replaces its submission whole, so the latest transaction for a submission stands: a withdrawn
submission, or a document or file no longer current in it, is no longer a record.
```

## Payment ledgers

`government_payments` is 19 governments' published checkbooks. All are on the Socrata open data API, so one
client reads all of them. Each government names its columns differently; `config/ledgers.yaml` maps each ledger's
columns to one schema.

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

## Adding a ledger

1. Find the dataset ID on the government's Socrata portal.
2. Add an entry to `config/ledgers.yaml` with the column mapping. Map `invoice_date` only after
   `python -m distress_signals ledgers --verify` passes for it.
