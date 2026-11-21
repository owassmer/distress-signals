"""Defense Department contract announcements."""

from . import federal_contracts, native_source

SOURCE = native_source(
    "dod_contracts", federal_contracts, "Defense Department contract announcements",
    publisher="U.S. Department of Defense (GovDelivery)",
    record="one daily contract announcement, which can cover multiple awards and modifications.",
    names="contractors and contracting offices in each entry.",
    dates="the bulletin's publication date and each entry's completion date or estimate where stated.",
    search="query matches all words in the title and release text. Optional since and until filter publication dates "
           "(YYYY-MM-DD). bulletin_id selects one known release. Without filters, searches contract releases in the "
           "department's recent feed.",
    read="the full release, including award amounts, contract numbers and completion terms where reported.",
    coverage="contract releases in the department's latest 25 GovDelivery bulletins across all topics, plus older "
             "contract releases by bulletin_id.",
    noun="a Defense Department contract announcement",
)
