from . import local_notices, native_source


SOURCE = native_source(
    "local_agendas", local_notices, "local legislative agendas",
    publisher="Participating city and county legislative bodies through Legistar.",
    record="A meeting and its public agenda items, including matter numbers and notes.",
    names="The legislative body and any entities named in its agenda text.",
    dates="Meeting date, meeting time, and the agenda's last publication timestamp where supplied.",
    search="Give the jurisdiction's client slug (required). Query matches text in item titles, matter names, "
           "file numbers or agenda notes. Blank includes all items. since defaults to today, until to 30 days "
           "after since. page starts at 1 and checks five meetings. Results preview five matching items per meeting.",
    read="Meeting details, the agenda PDF link where available, and all public agenda-item text.",
    coverage="A single Legistar jurisdiction per search. Some jurisdictions require LEGISTAR_TOKEN_<CLIENT>.",
    noun="the meeting agenda",
)
