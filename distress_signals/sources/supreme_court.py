from . import court_records, native_source


SOURCE = native_source(
    "supreme_court", court_records, "Supreme Court dockets",
    publisher="Supreme Court of the United States.",
    record="The docket report for a case or application.",
    names="Petitioners, respondents and other named parties.",
    dates="Docketing date, entry dates and dates stated in filings or orders, including briefing, "
          "conference and argument dates.",
    search="A docket number, or at least three characters from a case name or other docket text. "
           "page starts at 1. The Court reports limits on broad searches.",
    read="Parties, lower court, dated proceedings and orders, and links to filed documents.",
    coverage="Pending and decided cases. The Court describes docket status coverage as complete "
             "from its 2001 Term onward. Linked documents vary by case.",
    noun="the Supreme Court docket",
)
