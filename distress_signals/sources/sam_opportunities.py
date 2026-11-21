"""SAM.gov contract opportunity notices."""

from . import federal_contracts, native_source

SOURCE = native_source(
    "sam_opportunities", federal_contracts, "SAM.gov contract opportunities",
    publisher="GSA (SAM.gov) and U.S. Treasury (USAspending)",
    record="a notice or a separately published federal award.",
    names="agency, published awardee or USAspending recipient.",
    dates="posting, response deadline, award and performance dates when given.",
    search="collection defaults to api with SAM_API_KEY (query searches title), else active_file (title, IDs, agency, "
           "awardee). since/until filter posting (api defaults to last 30 days), deadline_from/deadline_until the "
           "response date. notice_type, solicitation_number optional. For collection=award_lookup query an exact PIID. "
           "award_types=contracts (default) or idvs, optional agency_code and paired parent_award_number and "
           "parent_agency_code. page=1, limit=25 (max 100).",
    read="notice fields and text or award summary. Full USAspending reads fill awards, award_funding, award_modifications.",
    coverage="daily active file (~225 MB), SAM API notices, USAspending awards since October 2007.",
    noun="a procurement notice or award",
)
