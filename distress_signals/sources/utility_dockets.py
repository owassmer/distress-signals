from . import energy, native_source

SOURCE = native_source(
    "utility_dockets", energy, "state utility dockets",
    publisher="South Dakota Public Utilities Commission and Illinois Commerce Commission.",
    record="A South Dakota electric or gas docket, or a scheduled Illinois hearing.",
    names="Utilities and other parties named in docket titles, filings or hearing descriptions.",
    dates="Filing dates, stated deadlines and orders in South Dakota. Scheduled hearing dates and times in Illinois.",
    search="With state=SD (default), search docket titles and numbers. year defaults to the current year and sector "
           "defaults to both electric and gas. With state=IL, search the hearing title, docket or case description in "
           "the seven days starting on week_start (default today). page=1, limit=30 (up to 100).",
    read="The commission's docket or hearing page, with links to the case, filings or orders where listed.",
    coverage="South Dakota's electric and natural-gas filing indexes and Illinois's hearing calendar.",
    noun="a utility proceeding",
)
