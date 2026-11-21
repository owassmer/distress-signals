from . import contract_data, native_source

SOURCE = native_source(
    "municipal_contracts", contract_data, "city and county contracts",
    publisher="Chicago, Cook County, Montgomery County (Maryland) and San Francisco.",
    record="A contract, amendment or supplier row in a local procurement dataset.",
    names="Vendor and contracting department. San Francisco identifies prime contractors and project team suppliers.",
    dates="Term end and, where reported, start, approval, system release or possible extension.",
    search="Search vendor, description or contract number. jurisdiction accepts chicago, cook_county, "
           "montgomery_county_md or san_francisco (default all four). Optional end_from and end_until filter term ends. "
           "page=1, limit=25 per jurisdiction (up to 100).",
    read="All published rows for the jurisdiction and contract number, including revisions and document links where present.",
    coverage="Chicago contracts and modifications, Cook County awards and amendments, Montgomery County contracts and "
             "San Francisco expenditure contracts.",
    noun="a local government contract",
)
