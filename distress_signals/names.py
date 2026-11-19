"""Mechanical name handling. Which spellings are the same company is decided by the researcher;
this module only normalizes strings."""

import re

LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "l", "c", "corp", "corporation", "co", "company", "ltd",
                  "limited", "lp", "llp", "plc", "pllc", "pc", "dba", "usa", "us", "na"}


def _camel_split(s):
    # some ledgers store names without spaces, e.g. "AcmeServicesInc"
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s or "")


def tokens(s):
    s = _camel_split(s).lower().replace("&", " and ")
    return re.findall(r"[a-z0-9]+", s)


def core_tokens(s):
    t = tokens(s)
    if t and t[0] == "the":
        t = t[1:]
    while t and t[-1] in LEGAL_SUFFIXES:
        t = t[:-1]
    return t


def vendor_key(s):
    """Groups trivially different spellings ('ACME SERVICES LLC' / 'Acme Services, LLC') before the agent
    resolves the harder cases."""
    return "".join(core_tokens(s))


def matches(text, name):
    """Whole-word sequence match ('Acme' does not match 'PACMEN SUPPLY')."""
    a, t = core_tokens(name), tokens(text)
    n = len(a)
    return bool(a) and any(t[i:i + n] == a for i in range(len(t) - n + 1))


def clean_unit(raw):
    s = re.sub(r"\(\s*[\d\-\s]+\)", "", raw or "")
    return re.sub(r"\s+", " ", s).strip()


def domain_key(v):
    """'https://www.ICF.com/about' -> 'icf.com'; '' when there is no domain."""
    v = (v or "").strip().lower()
    v = re.sub(r"^[a-z]+://", "", v).split("/")[0].split("?")[0].split(":")[0]
    return v[4:] if v.startswith("www.") else v
