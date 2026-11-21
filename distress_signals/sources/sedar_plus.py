from . import issuer_disclosures, native_source

SOURCE = native_source(
    "sedar_plus", issuer_disclosures, "Canadian issuer disclosures",
    publisher="Canadian Securities Administrators (SEDAR+), Canadian Securities Exchange (CSE), and issuer websites",
    record="one filing in a licensed delivery or exchange listing, or one issuer document indexed by FREEDAR.",
    names="issuer named in filing metadata or on the original document.",
    dates="filing publication where supplied. FREEDAR discovery dates describe its index.",
    search="query matches filing metadata or indexed issuer text. Optional since and until bound filing dates "
           "(YYYY-MM-DD) for DDS and CSE. cse_listing selects a CSE listing slug. issuer_collection selects a "
           "FREEDAR collection. Continue FREEDAR pages with the returned epoch and generation. limit=30 "
           "(max 50 on FREEDAR, 200 elsewhere), offset=0.",
    read="filing PDF, or FREEDAR metadata with issuer_document_url for the original.",
    coverage="defaults to the configured licensed DDS catalog, otherwise FREEDAR’s independent issuer collections. "
             "CSE listing access requires a data-use agreement.",
    noun="the issuer disclosure",
)
