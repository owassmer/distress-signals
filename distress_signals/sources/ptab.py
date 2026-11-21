from . import drug_patent, native_source

SOURCE = native_source(
    "ptab", drug_patent, "PTAB records",
    publisher="U.S. Patent and Trademark Office, Patent Trial and Appeal Board",
    record="one trial proceeding, docket document or scheduled hearing.",
    names="petitioner, patent owner or respondent as reported. The hearing schedule lists the parties.",
    dates="proceeding and filing dates, deadlines stated in orders, or a scheduled hearing date and time.",
    search="record_type defaults to proceedings. For proceedings or documents, query accepts USPTO search terms or "
           "field syntax. For hearings, query matches a party, proceeding number or location. Optional trial_number "
           "selects a proceeding. Optional since and until bound hearing dates (YYYY-MM-DD). limit=20 (1–99), offset=0.",
    read="proceeding metadata, filing details with OCR text where supplied, or a hearing entry with its schedule page.",
    coverage="Open Data Portal proceedings and documents require USPTO_API_KEY. Public schedules cover the trials "
             "and appeals currently listed by the Board.",
    noun="the Board record",
)
