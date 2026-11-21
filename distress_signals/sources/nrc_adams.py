"""Nuclear Regulatory Commission documents and notices."""

from . import communications, native_source

SOURCE = native_source(
    "nrc_adams", communications, "Nuclear Regulatory Commission records",
    publisher="Nuclear Regulatory Commission (ADAMS and Federal Register notices)",
    record="an ADAMS document or an NRC notice published in the Federal Register.",
    names="ADAMS author and addressee organizations, and applicants or licensees named in a notice.",
    dates="ADAMS document date and date added. Notices give publication date and any deadlines stated in the text.",
    search="collection=adams (default) searches by text, optional docket, document_type and organization (author or "
           "addressee). since/until filter date added. collection=notices searches by words and since/until filter "
           "publication date. Date filters are optional (YYYY-MM-DD). limit=25 (max 99), offset=0.",
    read="document metadata and available text, or the GPO notice text with links to cited ADAMS accession numbers.",
    coverage="ADAMS main and legacy public libraries require NRC_ADAMS_API_KEY. NRC Federal Register notices are "
             "keyless, with up to 10,000 matches per search.",
    noun="an NRC document or notice",
)
