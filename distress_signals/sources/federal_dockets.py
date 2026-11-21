from . import court_records, native_source


SOURCE = native_source(
    "federal_dockets", court_records, "federal court dockets",
    publisher="Federal courts, through Free Law Project's RECAP archive.",
    record="A contributed docket sheet, docket entry or collection of filed documents.",
    names="Parties as captioned and their roles where recorded.",
    dates="Case and entry filing dates, termination, archive updates and dates stated in orders.",
    search="Give an archive identifier alone, or search captions by phrase (blank browses recent additions). "
           "Optional court code. Archive page defaults to 1. With COURTLISTENER_TOKEN, the query searches "
           "live RECAP filings. Use the returned cursor for more. A docket_id with an empty query lists entries. "
           "court, cursor and docket_id default to unset.",
    read="Docket entries and available document links. For an archive item with filings only, the highest-numbered "
         "available PDF is read. Live docket reads return the first page of entries.",
    coverage="Contributed federal records. The latest available entry and snapshot date vary by case.",
    noun="the federal court record",
)
