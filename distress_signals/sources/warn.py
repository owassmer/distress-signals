from . import local_notices, native_source


SOURCE = native_source(
    "warn", local_notices, "WARN notices",
    publisher="State labor agencies. WARN Index compiles their notices nationally.",
    record="An employer's notice of a planned layoff or closure.",
    names="Employer and affected locations, with affected worker counts where reported.",
    dates="Notice or state receipt date and planned layoff start date. The national index separately dates its retrieval.",
    search="Employer name or blank. state defaults to all indexed jurisdictions. state=WA searches Washington's own "
           "directory. Optional effective_from and effective_until bound the layoff start date on each page; "
           "both default to unrestricted. page starts at 1. The national index checks 50 notices per page.",
    read="Indexed notice fields and links to state sources, or Washington's directory entry with the filed "
         "letter text when readable.",
    coverage="State-published notices. National coverage and document availability vary by jurisdiction.",
    noun="the WARN notice",
)
