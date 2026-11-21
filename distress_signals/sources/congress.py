from . import native_source, policy_records

SOURCE = native_source(
    "congress", policy_records, "Congress.gov and GovInfo",
    publisher="Library of Congress and U.S. Government Publishing Office",
    record="A committee meeting, printed hearing, committee report or bill version linked to its bill's action history.",
    names="Committees, hearing witnesses and their stated affiliations, and entities named in the legislative text.",
    dates="Scheduled meeting date, issue date of a printed version, and dates of recorded bill actions.",
    search="record_type defaults to meetings. Search schedule text by week_of (YYYY-MM-DD, default this week "
           "and next), offset 0. reports, transcripts and bills search GovInfo full text with cursor (default "
           "first page). limit 20 for all types.",
    read="Meeting details and witness materials, report or transcript text and PDF, or bill actions.",
    coverage="Congress.gov committee schedules and bill histories. GovInfo covers published congressional "
             "documents and counts each printed bill version separately.",
    noun="the congressional record",
)
