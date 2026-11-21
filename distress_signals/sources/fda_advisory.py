from . import drug_patent, native_source

SOURCE = native_source(
    "fda_advisory", drug_patent, "FDA advisory meetings and drug applications",
    publisher="U.S. Food and Drug Administration.",
    record="an advisory committee meeting announcement or a Drugs@FDA application.",
    names="the committee, center or office and any sponsor in the notice; the sponsor and products on an application.",
    dates="meeting start and end where listed; recorded submission action dates on applications.",
    search="record_type defaults to meetings: query by title, center or office. since and until optionally bound "
           "meeting start dates (YYYY-MM-DD). For record_type=approvals, query by sponsor, product, ingredient or application "
           "number. A blank query browses either collection. limit defaults to 20 (maximum 99); offset defaults to 0.",
    read="the meeting notice and agenda, or FDA's application record, products and submission history.",
    coverage="FDA's published advisory committee calendar and the Drugs@FDA data available through openFDA.",
    noun="an FDA meeting or application record",
)
