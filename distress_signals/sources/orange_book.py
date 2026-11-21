from . import drug_patent, native_source

SOURCE = native_source(
    "orange_book", drug_patent, "FDA Orange Book",
    publisher="U.S. Food and Drug Administration (Orange Book via openFDA)",
    record="one drug product under an approved application, with its listed patents and exclusivities.",
    names="applicant and product trade name, with active ingredients.",
    dates="product approval and listed patent and exclusivity expiration dates where provided. The dataset "
          "update date applies to the release.",
    search="query matches product, ingredient, applicant, application number or patent number. Optional expires_from "
           "and expires_until (YYYY-MM-DD) select products with a listed expiration in the window. limit=20 (1–99), offset=0.",
    read="the product record with the full patent and exclusivity entries under its application and product numbers.",
    coverage="the public openFDA Orange Book dataset of approved drug products.",
    noun="the product record",
)
