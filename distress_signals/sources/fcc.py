"""Federal Communications Commission filings and auction reports."""

from . import communications, native_source

SOURCE = native_source(
    "fcc", communications, "FCC filings and auction reports",
    publisher="Federal Communications Commission (ECFS and Public Reporting System)",
    record="a docket filing, an auction announcement or one bidder's status in one auction round.",
    names="filers, and bidders with FCC registration numbers in bidder status reports.",
    dates="receipt and dissemination, comment and reply deadlines when supplied, or an auction announcement's timestamp.",
    search="report=filings (default) searches ECFS by query words, optional docket, filer and auction_number. For "
           "report=announcements or bidder_status, give auction_number. Query words match across the report row. "
           "limit=25 (max 99), offset=0.",
    read="filing metadata, available text and document links, or all fields in the auction report row.",
    coverage="public ECFS filings using the shared demo key or FCC_API_KEY. Auction reports are available for auctions "
             "that publish the requested CSV, up to 32 MiB per report.",
    noun="an FCC filing or auction record",
)
