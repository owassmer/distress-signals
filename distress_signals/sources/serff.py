from . import native_source, state_health

SOURCE = native_source(
    "serff", state_health, "SERFF health rate filings",
    publisher="New York Department of Financial Services and Oregon Division of Financial Regulation.",
    record="one published insurance rate filing, identified by SERFF tracking number.",
    names="the filing insurer and the affected insurance product or market.",
    dates="effective date, proposed while pending; submission, posting, comment and decision dates where published.",
    search="Query by insurer, tracking number or words in the published filing row, or leave blank. state is NY or OR (default both). "
           "status is an optional exact label. year selects NY benefit year (posting year for long-term care) "
           "or OR effective year. Default is the latest NY table per market and all OR years. "
           "offset defaults to 0 and max_results to 25 (maximum 200).",
    read="the regulator's filing rows, requested and approved changes where reported, and document links.",
    coverage="NY individual/small group, large-group HMO, Medicare supplement and long-term care; OR health rate requests.",
    noun="a SERFF rate filing",
)
