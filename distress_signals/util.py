import datetime as dt
import hashlib
import os
import re
import threading
import time
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config"
OUT = ROOT / "out"

_local = threading.local()


class SourceError(Exception):
    """A source couldn't answer; the message says what to do instead. A plain SourceError is a failure to get an
    answer (the request failed, or the publisher refused it), and a read that meets one stores nothing."""


class Missing(SourceError):
    """The publisher answered that nothing is at the address (404 or 410): a fact about the record."""


class Unreadable(SourceError):
    """The file arrived and has no text a read can take (an image, a type the reader does not know, or past the size
    a read takes): a fact about the file."""


class Throttled(SourceError):
    """The publisher refused the request for the rate it was asked at; the same request can be made later."""
UA = "distress-signals (+https://github.com/owassmer/distress-signals)"


def claude_token():
    """The Claude sign-in for a run, read fresh each time so an account switch takes effect on the next run:
    the macOS keychain item named in CLAUDE_TOKEN_KEYCHAIN (its password is the OAuth token), else
    CLAUDE_CODE_OAUTH_TOKEN, else None (the claude CLI's own sign-in)."""
    item = os.environ.get("CLAUDE_TOKEN_KEYCHAIN")
    if item:
        import getpass
        import subprocess
        r = subprocess.run(["security", "find-generic-password", "-w", "-a", getpass.getuser(), "-s", item],
                           capture_output=True, text=True, timeout=20)
        token = r.stdout.strip()
        if r.returncode != 0 or not token:
            raise RuntimeError(f"couldn't read the Claude token from the keychain item {item!r}")
        return token
    return os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or None


def load_env():
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def config(name):
    return yaml.safe_load((CONFIG / name).read_text())


def exclusions():
    """[(name, reason)] from config/exclude.yaml and the uncommitted config/exclude.local.yaml."""
    out = []
    for f in ("exclude.yaml", "exclude.local.yaml"):
        p = CONFIG / f
        if p.exists():
            for reason, lst in ((yaml.safe_load(p.read_text()) or {}).get("exclude") or {}).items():
                out += [(n, reason) for n in lst or []]
    return out


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers["User-Agent"] = UA
    return _local.s


def http_json(url, *, params=None, method="GET", json_body=None, headers=None, tries=5, timeout=120):
    """JSON request with retry/backoff on throttling and transient errors."""
    last = None
    for i in range(tries):
        try:
            r = session().request(method, url, params=params, json=json_body, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(f"{r.status_code} {r.text[:200]}", response=r)
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code in (400, 401, 403, 404):
                raise
            time.sleep(min(30, 2 ** i))
    raise last


def found_line(company_ids):
    """How a run's results are counted, everywhere they are reported: one revenue exposure per case (each is
    a deal), across the distinct companies they belong to. A company with two exposed revenue streams is two
    exposures at one company. company_ids has one entry per active case."""
    n, k = len(company_ids), len(set(company_ids))
    if not n:
        return "no revenue exposures"
    return f"{n} revenue exposure{'s' if n != 1 else ''} {'at' if k == 1 else 'across'} {k} compan{'y' if k == 1 else 'ies'}"


def today():
    return dt.date.today()


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


_MDY = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")


def to_date(s):
    """'2026-08-13T00:00:00.000' | '2026-08-13' | '08/13/2026' -> date, else None."""
    if not s:
        return None
    if isinstance(s, dt.date):
        return s
    s = str(s).strip()
    try:
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        m = _MDY.match(s)
        if m:
            return dt.date(int(m[3]), int(m[1]), int(m[2]))
    return None


def to_float(x):
    try:
        return float(str(x).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        return None


def stable_id(*parts):
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:20]


def money(x):
    if x is None:
        return ""
    return f"${x:,.0f}" if abs(x) >= 1 else f"${x:,.2f}"
