from . import healthcare, native_source

SOURCE = native_source(
    "medicare_stars", healthcare, "Medicare plan star ratings",
    publisher="Centers for Medicare & Medicaid Services (CMS).",
    record="one contract in an annual Medicare Advantage and Part D star ratings table.",
    names="the contract, organization marketing name and parent organization.",
    dates="the contract's rating year and the date of its table edition when CMS dates that edition.",
    search="Query by contract number, contract name, marketing name or parent, or leave blank. year selects "
           "an edition (latest by default). offset defaults to 0 and max_results to 25 (maximum 200).",
    read="the contract row with organization type, Part C, Part D and overall ratings, and a link to the edition's "
         "fact sheet when available. CMS labels unrated contracts in the table.",
    coverage="the standalone annual summary ratings tables linked in CMS Part C and D performance data, including standalone drug plans.",
    noun="a Medicare contract rating",
)
