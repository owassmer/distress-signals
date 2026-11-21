from . import native_source, state_health

SOURCE = native_source(
    "medicaid_contracts", state_health, "Medicaid managed-care contracts",
    publisher="Iowa Department of Health and Human Services and Texas Health and Human Services Commission.",
    record="an Iowa contract or amendment, or a Texas managed-care program's procurement record.",
    names="the contracted plan in Iowa; respondents or awardees in Texas when the state names them.",
    dates="Iowa contract terms and extensions; Texas bid deadlines, award and operating dates, including forecasts.",
    search="Query by plan, program, solicitation number or words in the index, or leave blank. "
           "state selects IA or TX (default both). offset defaults to 0 and max_results to 25 (maximum 200).",
    read="Iowa's contract PDF, or the Texas program notice with linked solicitation details when published.",
    coverage="Iowa's current managed-care contract directory and Texas's Medicaid managed-care procurement timetable.",
    noun="a Medicaid managed-care contract or procurement record",
)
