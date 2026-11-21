from . import issuer_disclosures, native_source

SOURCE = native_source(
    "emma", issuer_disclosures, "municipal disclosures",
    publisher="Municipal Securities Rulemaking Board (EMMA) and California DebtWatch",
    record="one continuing disclosure file, or one municipal debt issue reported to California.",
    names="issuer and any named obligor in EMMA. State issue records can name underwriter, trustee or lender.",
    dates="EMMA posting date and reporting period. State records can give sale, settlement, first call and maturity dates.",
    search="query matches filing metadata or searches state issue records. collection=msrb is the default with a "
           "configured MSRB delivery, otherwise collection=california. Optional since and until bound MSRB posting "
           "dates (YYYY-MM-DD). limit=30 (1–200), offset=0.",
    read="EMMA filing metadata and supplied PDF text. State issue details link to available original documents.",
    coverage="current published files in the licensed EMMA delivery. The public state collection covers California issues.",
    noun="the municipal record",
)
