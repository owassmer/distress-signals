from . import native_source, policy_records

SOURCE = native_source(
    "regulations_gov", policy_records, "Regulations.gov",
    publisher="Federal agencies through Regulations.gov",
    record="A docket, a document in that docket, or a comment on a document.",
    names="Issuing agency, authors and commenting organization where disclosed.",
    dates="Posting, comment opening and closing, and rule effective or implementation dates where listed.",
    search="Keywords or ID. record_type defaults to documents, also comments or dockets. Optional agency. "
           "For documents, docket_id and comment_end_from/comment_end_until. For comments, comment_on is the "
           "parent document ID. since/until filter posting dates for documents and comments. limit 20, offset 0.",
    read="Document or comment text and metadata, links to attachments, parent document and docket where present.",
    coverage="Participating federal agencies' public records. Agencies determine which commenter fields appear. "
             "The public demo key has a shared quota.",
    noun="the docket record",
)
