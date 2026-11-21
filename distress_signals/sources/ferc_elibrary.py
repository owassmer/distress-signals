from . import energy, native_source

SOURCE = native_source(
    "ferc_elibrary", energy, "FERC eLibrary",
    publisher="Federal Energy Regulatory Commission (FERC).",
    record="A public filing or order under an accession number and its individual attachments.",
    names="Authors, agents and recipients where recorded, with their affiliations.",
    dates="Issued, filed and posted dates. Orders and notices can give hearing dates and procedural deadlines.",
    search="Search descriptions and full text. docket is optional. since and until filter posted dates "
           "(all dates by default, through today when only since is supplied). page=1, limit=30 (up to 100).",
    read="Accession metadata, dockets, affiliations and attachment links. Individual PDF, DOCX and text attachments "
         "return their text where extractable.",
    coverage="Public records indexed by eLibrary's General Search.",
    noun="a FERC filing",
)
