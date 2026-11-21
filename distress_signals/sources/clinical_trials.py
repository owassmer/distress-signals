from . import healthcare, native_source

SOURCE = native_source(
    "clinical_trials", healthcare, "ClinicalTrials.gov",
    publisher="U.S. National Library of Medicine, ClinicalTrials.gov.",
    record="a registered clinical study identified by its NCT number.",
    names="the lead sponsor and any named collaborators.",
    dates="first and last posting, primary completion and study completion, with actual or estimated status as reported.",
    search="Query by study terms or NCT number, or leave blank. sponsor and status filter organization or study status. "
           "completion_from and completion_until bound either completion date (YYYY-MM-DD). "
           "These filters default to all. max_results defaults to 25 (maximum 200); page_token continues a search.",
    read="the full public study record, including its protocol, design, status, sponsors and results when available.",
    coverage="clinical studies registered in ClinicalTrials.gov by sponsors and investigators worldwide.",
    noun="a clinical study record",
)
