from . import native_source, policy_records

SOURCE = native_source(
    "oira", policy_records, "OIRA",
    publisher="Office of Information and Regulatory Affairs, Reginfo.gov",
    record="A pending regulatory review or an Executive Order 12866 meeting about a rule.",
    names="Reviewing and submitting agencies. A meeting can name its requester, the requester's client and attendees.",
    dates="Date received for review, any stated legal deadline, and the scheduled or completed meeting date and time.",
    search="Words in the index, including rule title or agency. record_type defaults to reviews, also meetings. "
           "Optional rin (Regulation Identifier Number) and stage. For meetings, since defaults to 30 days ago "
           "and until to 60 days ahead. limit 20, offset 0.",
    read="Review stage and details, or meeting status, requester, client, listed participants and filed materials.",
    coverage="Current OIRA reviews and public EO 12866 meeting records in the selected date range.",
    noun="the OIRA record",
)
