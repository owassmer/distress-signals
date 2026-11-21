from . import healthcare, native_source

SOURCE = native_source(
    "medicare_coverage", healthcare, "Medicare coverage analyses",
    publisher="Centers for Medicare & Medicaid Services (CMS).",
    record="a national coverage analysis (NCA) or coding analysis for labs (CAL) tracking sheet.",
    names="the requestor where recorded, including analyses initiated by CMS.",
    dates="formal request, expected completion, proposed memo due, memo issuance and public comment periods.",
    search="Query by title, CAG ID or numeric document ID, or leave blank. document_type selects NCA or CAL. "
           "status selects Open, Closed or Open for Public Comment. Both filters default to all. "
           "offset defaults to 0 and max_results to 25 (maximum 200).",
    read="the tracking sheet's requestor information, issue, decision timetable and comment periods.",
    coverage="the CMS Coverage API national NCA/CAL report and its published tracking sheets.",
    noun="a Medicare coverage tracking sheet",
)
