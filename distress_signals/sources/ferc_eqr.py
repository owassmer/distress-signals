from . import contract_data, native_source

SOURCE = native_source(
    "ferc_eqr", contract_data, "FERC Electric Quarterly Reports",
    publisher="Federal Energy Regulatory Commission (FERC).",
    record="A quarterly filing or one contract in that filing, with its product and rate lines.",
    names="The respondent, sellers and their customers.",
    dates="Reporting quarter, execution, commencement, contract termination, actual termination and product terms.",
    search="Search filings by respondent name or ID, contracts by their reported fields. quarter defaults to the latest "
           "(YYYYQn). records defaults to filings, or contracts with respondent_id or termination bounds end_from or "
           "end_until. Those three filters are optional. Contract scans start at filing_offset=0 with filings_per_page=5 "
           "(up to 25). page=1, limit=25 (up to 100). Finish each batch before using next_filing_offset with page=1.",
    read="Filing identity and all contract lines, or the selected contract's lines in that filing version.",
    coverage="FERC's quarterly CSV archives from 2013Q3 onward.",
    noun="an electricity sales filing",
)
