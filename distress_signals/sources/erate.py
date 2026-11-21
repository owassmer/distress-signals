from . import contract_data, native_source

SOURCE = native_source(
    "erate", contract_data, "USAC E-Rate funding requests",
    publisher="Universal Service Administrative Company (USAC).",
    record="A funding request number (FRN) on FCC Form 471, in its original or current version.",
    names="The applicant organization and service provider, including the provider's identifying number.",
    dates="Contract award and expiration, possible extensions and service dates. For tariffs and month-to-month service, "
          "the expiration field is the service end.",
    search="Search the provider, applicant, contract number or FRN. version=Current or Original (default Current). "
           "Optional funding_year, state, contract_type (Contract, Tariff or Month-to-Month), end_from and end_until "
           "(expiration field). page=1, limit=25 (up to 100).",
    read="Funding request, amounts and status, application certification, and links to the original PDF where available.",
    coverage="USAC's rolling ten years of public FRN status data.",
    noun="a funding request",
)
