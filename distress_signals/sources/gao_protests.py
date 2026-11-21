from . import court_records, native_source


SOURCE = native_source(
    "gao_protests", court_records, "GAO bid protests",
    publisher="U.S. Government Accountability Office. GovCon API indexes the dockets.",
    record="One bid-protest docket identified by its B-number.",
    names="Protester and contracting agency. Other firms may appear in the decision.",
    dates="Filed date, published due date, decision date where decided, and posting date.",
    search="A B-number opens the GAO docket. With GOVCON_API_KEY, search by name or solicitation, "
           "or leave the query blank. Optional agency and status (Open or Closed) default to any. "
           "page starts at 1, with 25 index results per page.",
    read="Official docket fields, including the solicitation number, case status or outcome, "
         "and links to the decision when published.",
    coverage="Public GAO dockets. Discovery reports the index's coverage period. A case may close before its due date.",
    noun="the bid-protest docket",
)
