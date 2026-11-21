from . import native_source, trade

SOURCE = native_source(
    "ofac", trade, "OFAC general licenses",
    publisher="U.S. Treasury, Office of Foreign Assets Control",
    record="one general license or related notice listed by Treasury for a sanctions program.",
    names="entities named in the license or notice. Some authorizations cover classes of persons.",
    dates="issuance, effective and expiration dates as stated in the document.",
    search="query matches words in the selected index’s program or license title and description. limit=50 (1–250), "
           "offset=0. Use file_page (starting at 1) in place of limit and offset to search Treasury’s File Finder.",
    read="the document text from the Treasury file URL, including operative terms and any amendments or revocations it states.",
    coverage="OFAC’s selected licenses by default. File Finder searches Treasury’s broader general-license collection "
             "and includes earlier versions.",
    noun="the Treasury document",
)
