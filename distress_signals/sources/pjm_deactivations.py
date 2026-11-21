from . import energy, native_source

SOURCE = native_source(
    "pjm_deactivations", energy, "PJM generator deactivations",
    publisher="PJM Interconnection.",
    record="A generating unit's deactivation entries, or an individual notice, revision or PJM response.",
    names="Generating unit, state and transmission owner zone. Notices may name the plant owner or its agent.",
    dates="Original and revised request letters, requested and projected deactivation, actual deactivation and withdrawal "
          "where reported.",
    search="records=units (default) searches unit name, state, zone or fuel. Optional status uses PJM's label "
           "(Future Deactivation, Withdrawn Deactivation or Deactivated Generator). records=notices searches unit "
           "and document titles. page=1, limit=30 (up to 100).",
    read="All entries for the selected unit and state, including withdrawn requests, or the individual notice or response.",
    coverage="Generator deactivations reported to PJM and letters listed in its public notice index.",
    noun="a PJM deactivation record",
)
