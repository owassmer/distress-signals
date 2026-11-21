from . import native_source, trade

SOURCE = native_source(
    "usitc", trade, "USITC investigations",
    publisher="U.S. International Trade Commission (IDS and EDIS)",
    record="one investigation phase in IDS, or one public docket filing in EDIS.",
    names="participants and their roles, including named complainants and respondents. EDIS identifies the filing party.",
    dates="publication of the notice of institution, issuance of the target-date order and the target date where "
          "reported. EDIS provides filing dates.",
    search="query matches words in the title, investigation number or participant names. Optional target_from and "
           "target_until bound IDS target dates (YYYY-MM-DD). limit=50 (1–250), offset=0. For EDIS use the "
           "investigation number as query and filing_page (starting at 1) in place of IDS filters.",
    read="IDS phase record, dates, participants and linked documents, or EDIS filing metadata and attachment references.",
    coverage="current IDS export and public EDIS filing metadata. Reading EDIS attachments requires EDIS_API_TOKEN.",
    noun="the investigation record",
)
