#!/usr/bin/env python3
"""
ats_scanner - given a list of company names, find out whether each company
officially uses one of 27 ATS (applicant tracking system) portals, and return
the direct URL of that company's career site / job listing on the portal.

Supported vendors
    Greenhouse, BambooHR, Workday, Workable, Recruitee (Tellent), Lever,
    Ashby, Personio, SmartRecruiters, Teamtailor, Homerun, SAP SuccessFactors,
    Oracle Taleo, iCIMS, softgarden, d.vinci, Flatchr, Taleez, Beetween,
    Pinpoint, Zoho Recruit, Eploy, JOIN, Coveto, rexx systems, Bullhorn,
    Avature

Two independent engines run for every company:

  A) PROBE       build the vendor-hosted URL from the company name
                 (`boards.greenhouse.io/<slug>`, `<slug>.teamtailor.com`, ...)
                 and prove the tenant exists through a public API, a redirect
                 rule, a status code or a DNS record.

  B) FINGERPRINT fetch the company's own careers page and look for links,
                 iframes, scripts and query parameters that point at an ATS.
                 This is the only engine that can find Zoho Recruit, Eploy,
                 Bullhorn and every customer hiding behind a custom domain -
                 and it is the corroborating evidence the tier-2 vendors need.

Read the tier model in vendors.py before trusting any single result: three
vendors (Workable, SmartRecruiters, Pinpoint) publish a branded page for ANY
recognisable company name, so a bare "HTTP 200" from them proves nothing.

Usage
-----
    python3 scan.py "Airbnb" "Stripe"
    python3 scan.py --csv companies.csv -o results
    python3 scan.py --list-vendors
    python3 scan.py --selftest
    python3 scan.py --csv companies.csv --vendors greenhouse lever ashby
    python3 scan.py --csv companies.csv --min-status VERIFIED

Input file: .csv/.tsv with headers `company,website,careers_url` (only
`company` is required), or a plain .txt list with one company per line.
Supplying website / careers_url makes the fingerprint engine far more
accurate; without them the scanner guesses a homepage from the company name.

Outputs (written next to the -o PREFIX):
    PREFIX_results.csv    one row per (company, ATS) finding
    PREFIX_results.json   full detail, including every probe that was tried
    PREFIX_hits.md        human-readable summary table
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import io
import json
import os
import re
import socket
import sys
import threading
import time
import unicodedata
from html import unescape as html_unescape
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpx

import vendors as V

VERSION = "2.3.0"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/126.0.0.0 Safari/537.36")

CAREER_WORDS = ("career", "careers", "job", "jobs", "vacanc", "vacature", "stellen",
                "stellenangebot", "emploi", "offres", "recruit", "hiring", "hire",
                "join", "opportunit", "position", "karriere", "arbeit", "werk",
                "empleo", "trabajo", "lavoro", "carriera", "lifeat", "workat",
                "werkenbij", "talent", "people", "team")

STATUS_RANK = {"VERIFIED": 5, "PROBABLE": 4, "UNVERIFIED": 3, "BLOCKED": 2,
               "MISS": 1, "NOT_FOUND": 0}

# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #

_dns_cache: Dict[str, Tuple[bool, Optional[str]]] = {}
_dns_lock = threading.Lock()


def dns_resolves(host: str) -> Tuple[bool, Optional[str]]:
    with _dns_lock:
        if host in _dns_cache:
            return _dns_cache[host]
    try:
        info = socket.getaddrinfo(host, None)
        ip = info[0][4][0] if info else None
        out = (True, ip)
    except Exception:
        out = (False, None)
    with _dns_lock:
        _dns_cache[host] = out
    return out


def ascii_fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(c))


# Transliterations that a real tenant slug would use.  These must be applied to
# the ORIGINAL unicode text: ascii_fold("ü") already returns "u", so folding
# first would destroy the information we need ("Südzucker" -> "suedzucker").
_UMLAUT = {
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
    "æ": "ae", "Æ": "Ae", "ø": "o", "Ø": "O", "å": "a", "Å": "A",
    "đ": "d", "Đ": "D", "ł": "l", "Ł": "L", "ı": "i", "ş": "s", "Ş": "S",
    "ğ": "g", "Ğ": "G", "ç": "c", "Ç": "C", "ñ": "n", "Ñ": "N",
}


def umlaut_fold(text: str) -> str:
    """Transliterate digraph-forming letters, then ASCII-fold.

    `ascii_fold("Südzucker")` gives "Sudzucker", but the tenant slug a German
    company actually registers is "suedzucker".  Without this alternative
    spelling the identity guard rejects correct matches for any company whose
    name contains an umlaut.
    """
    out = text
    for a, b in _UMLAUT.items():
        if a in out:
            out = out.replace(a, b)
    return ascii_fold(out)


def base_slug(company: str) -> str:
    s = ascii_fold(company).lower().strip()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


SUFFIXES = [
    " incorporated", " private limited", " limited company", " technologies",
    " technology", " solutions", " systems", " holding", " holdings",
    " corporation", " company", " inc", " llc", " ltd", " limited", " gmbh",
    " ag", " sa", " sas", " sasu", " sarl", " bv", " b.v.", " nv", " bvba",
    " cvba", " plc", " ltda", " s.a.", " s.r.l", " srl", " spa", " s.p.a",
    " oy", " ab", " asa", " hf", " ehf", " kft", " sp. z o.o.", " d.o.o",
    " ooo", " pty", " pvt", " corp", " group", " labs", " lab", " co",
]

EXTRA_SUFFIXES = ("-gruppe", "-group", "-gmbh", "-ag", "-sa", "-sas", "-bv", "-ltd",
                  "-inc", "-corp", "-uk", "-de", "-fr", "-jobs", "jobs", "-careers")

# Tenant-id vocabulary learned from company_ATS_seed.csv (81 real name -> portal
# pairs).  Each of these shapes is backed by at least one live board.
SLUG_TLDS = ("org", "com", "ai", "io", "de", "co", "net", "app")
SLUG_SUFFIXES = ("company", "space", "software", "hq", "group", "tech", "labs",
                 "team", "people", "recruiting", "talent")
SLUG_LEGAL = ("gmbh", "ag", "ltd", "inc", "bv", "ab", "se", "sas")
SLUG_LOCALES = ("en", "de", "global", "uk", "us", "emea")
SLUG_PREFIXES = ("workat", "join", "careersat")
# How many derived slugs to keep.  Was 10, which discarded every learned shape
# above; probe_vendor stops at the first hit, so the extra candidates only cost
# anything for a company that matches nothing.
SLUG_CAP = 44
SLUG_CAP_CORE = 10

# Vendors whose real tenant ids are not always the plain company name, per
# company_ATS_seed.csv: rowspace and ecosia.org and mistral.ai on Ashby,
# workatbackbase and bolcomen on Greenhouse, ScalableGmbH on SmartRecruiters,
# sonio-ag and quantum-diamond on Personio, pitch-software on Workable,
# leonardocompany and wk on Workday, innogames on Lever's EU host.  Every other
# vendor in the seed file uses the plain name, so they get the short list and
# the scan stays affordable.
EXTENDED_SLUG_VENDORS = {"ashby", "greenhouse", "lever", "workday", "personio",
                         "workable", "smartrecruiters"}

# Those seven vendors get a deeper probe budget than --probe-budget, because the
# learned shapes have to be *reachable*, not merely generated: with the default
# budget of 24, `mistral.ai` was produced at position 40 and never tried.  26 is
# the smallest budget that reaches every slug in the seed file (the deepest is
# `bolcomen` at 24); it was 40 at first, which cost 637 requests and 227 s for a
# single company.  The other 20 vendors keep the cheap core list.
#
# The budget counts SLUGS, not requests, and requests are capped at twice that:
# Personio needs two requests per slug (.de and .com), and counting requests
# meant it only ever reached slug 20 - Sonio's real tenant `sonio-ag` is 24.
EXTENDED_PROBE_BUDGET = 26


def slug_candidates(company: str,
                    extra: Iterable[str] = (),
                    extended: bool = True) -> List[Tuple[str, bool]]:
    """Ordered, de-duplicated `(slug, from_hint)` pairs.

    `from_hint` is True when the slug was harvested from the company's own
    careers page (Engine B) rather than derived from its name.  That distinction
    matters: a harvested slug is near-certain to be the right tenant, while a
    derived one can collide with an unrelated company's tenant, so only derived
    slugs are identity-checked.
    """
    out: List[Tuple[str, bool]] = []

    def add(s: Optional[str], hint: bool = False, keep_dots: bool = False) -> None:
        # keep_dots is only for domain-shaped slugs (jobs.ashbyhq.com/ecosia.org).
        # Everywhere else a dot must become a hyphen: "Cargo.one" is really
        # jobs.ashbyhq.com/cargo-one, not cargo.one.
        s = (s or "").strip()
        if not keep_dots:
            s = s.strip(".")
        s = re.sub(r"[^A-Za-z0-9\-.]" if keep_dots else r"[^A-Za-z0-9\-]", "-", s)
        s = re.sub(r"-{2,}", "-", s).strip("-")
        if not s or len(s) < 2:
            return
        low = s.lower()
        for i, (o, h) in enumerate(out):
            if o.lower() == low:
                if hint and not h:               # a hint upgrades a derived slug
                    out[i] = (o, True)
                return
        out.append((s, hint))

    raw = ascii_fold(company).strip()
    low = raw.lower()

    add(re.sub(r"\s+", "-", low))                    # acme-corp
    add(re.sub(r"[^a-z0-9]", "", low))               # acmecorp
    words_all = [w for w in re.split(r"[\s\-&,\.]+", low) if w]
    add(words_all[0] if words_all else low)          # acme

    stripped = low
    for suf in sorted(SUFFIXES, key=len, reverse=True):
        if stripped.endswith(suf) and len(stripped) - len(suf) >= 3:
            stripped = stripped[: -len(suf)]
            break
    add(re.sub(r"\s+", "-", stripped))
    add(re.sub(r"[^a-z0-9]", "", stripped))

    words = [w for w in re.split(r"[\s\-&,\.]+", stripped) if w]
    if len(words) >= 2:
        add("-".join(words[:2]))
        add("".join(words[:2]))
        # An acronym is only usable when it is long enough to be distinctive.
        # Two letters are hopelessly ambiguous: "Woonstad Rotterdam" -> "wr"
        # resolved to World Relief's Lever board and W+R GmbH's JOIN page, both
        # of which returned HTTP 200 and were reported as VERIFIED.
        if len(words) >= 3 and all(len(w) > 1 for w in words):
            acr = "".join(w[0] for w in words)
            if len(acr) >= 3:
                add(acr)                             # acm
    # ---- shapes learned from 81 real (company name, portal URL) pairs --------
    # Harvested hints outrank everything derived, so they go in first.
    for e in extra:
        add(e, hint=True)

    if not extended:
        # The core list only.  Every learned shape below costs one probe per
        # vendor, and 24 vendors x 34 slugs is ~780 requests for a single
        # company.  The seed file shows the unusual slugs occur on seven
        # vendors, so only those pay for the long tail (EXTENDED_SLUG_VENDORS).
        for o, h in list(out)[:5]:
            add(o.replace("-", "_"), h)
            add(o.replace("-", ""), h)
        for o, h in list(out)[:3]:
            for suf in EXTRA_SUFFIXES:
                add(o + suf, h)
        return out[:SLUG_CAP_CORE]

    sq = re.sub(r"[^a-z0-9]", "", low)
    first = words_all[0] if words_all else sq

    # Ordered by what the seed list actually contains, because --probe-budget
    # decides how far down this list gets tried.  Each block below is backed by
    # at least one live board in company_ATS_seed.csv:
    #   Rows -> rowspace        Leonardo -> leonardocompany   Pitch -> pitch-software
    #   Scalable Capital -> ScalableGmbH  Sonio -> sonio-ag   Bol.com -> bolcomen
    #   Ecosia -> ecosia.org    Mistral AI -> mistral.ai      Backbase -> workatbackbase
    # Plain and hyphenated together for the shapes that actually occur, so
    # neither `rowspace` nor `pitch-software` falls outside --probe-budget.
    if len(words_all) >= 2 and words_all[-1] in SLUG_TLDS:
        add(".".join(words_all), keep_dots=True)    # mistral.ai
    for tld in SLUG_TLDS[:4]:                       # ecosia.org
        add(f"{sq}.{tld}", keep_dots=True)
    for pre in SLUG_PREFIXES[:2]:                   # workatbackbase
        add(pre + sq)
    for suf in SLUG_SUFFIXES[:5]:                   # rowspace, leonardocompany,
        add(sq + suf)                               # pitch-software
        # Only "software" and "space" are ever hyphenated in the seed file; the
        # other three suffixes double the probe count for nothing.
        if suf in ("software", "space"):
            add(sq + "-" + suf)
        # English compounds collapse a doubled letter: "Rows" + "space" is
        # jobs.ashbyhq.com/rowspace, not rowsspace.
        if sq and sq[-1] == suf[0]:
            add(sq + suf[1:])
    for lf in SLUG_LEGAL[:3]:                       # ScalableGmbH
        add(first + lf)
    for lf in SLUG_LEGAL[:3]:                       # sonio-ag
        add(sq + "-" + lf)
    for loc in SLUG_LOCALES[:3]:                    # bolcomen
        add(sq + loc)
    # The rest of the shape space (tech/labs/team/people/recruiting/talent,
    # the .de/.co/.net/.app domains, careersat-) matches no row in the seed
    # file.  Each one is two HTTP requests per vendor, so they are not
    # generated: a candidate list nobody has ever seen pay off is just cost.

    for o, h in list(out)[:6]:
        add(o.replace("-", "_"), h)
        add(o.replace("-", ""), h)
    for o, h in list(out)[:4]:                       # entity suffixes
        for suf in EXTRA_SUFFIXES:
            add(o + suf, h)
    return out[:SLUG_CAP]


DOMAIN_TLDS = ("com", "co.uk", "de", "fr", "nl", "eu", "io", "net", "co", "se",
               "no", "dk", "fi", "es", "it", "pl", "at", "ch", "be", "in", "com.au")

# Conventional careers paths, tried when no careers_url is supplied.  Kept short
# on purpose: every entry is one HTTP request per company.
CAREERS_PATHS = ("/careers", "/careers/", "/jobs", "/jobs/", "/vacancies",
                 "/werken-bij", "/karriere", "/en/careers")

# Subdomains of the company's own domain that commonly front a vendor portal.
CAREERS_SUBDOMAINS = ("careers", "jobs", "career", "job", "werkenbij", "karriere")


# Interstitials served by Cloudflare / Akamai / PerimeterX when they decide to
# challenge a client.  These arrive with a real HTTP status and a few kB of
# plausible-looking HTML, so without this check a 403 challenge page gets
# treated as "the careers page" and the search stops there.
#
# Two lists, because the wording has to be unambiguous.  A bare substring test
# for "captcha" or "access denied" wrongly rejected real careers pages: both
# join.specsavers.com and moss.join.com embed reCAPTCHA site keys and serve a
# full 200 response with a proper <title>.
CHALLENGE_BODY_MARKERS = (
    "just a moment", "checking your browser", "verify you are human",
    "verifying you are human", "enable javascript and cookies to continue",
    "cf-browser-verification", "cf-chl", "challenge-platform",
    "ddos protection by", "protected by cloudflare", "attention required! | cloudflare",
    "are you a robot", "please wait while we check your browser",
    "px-captcha", "_incapsula_", "incapsula incident",
    "access to this page has been denied", "request blocked by",
    "verifying your browser", "security check by",
)
# Only trusted when the response also failed (4xx/5xx) - a real page can
# legitimately say "access denied" about something else entirely.
CHALLENGE_TITLE_MARKERS = (
    "just a moment", "access denied", "attention required", "request blocked",
    "are you a robot", "security check", "verify you are human", "captcha",
    "403 forbidden", "checking your browser",
)


# A redirect INTO a challenge is just as useless as being served one, and it
# shows up in the final URL rather than the body: Skyscanner's www.skyscanner.com
# bounced the client to
#   /sttc/px/captcha-v2/index.html?url=...&uuid=...
# which passed the body test (the captcha shell is tiny and generic) and was then
# returned as the company's website.
CHALLENGE_URL_MARKERS = ("captcha", "/px/", "challenge", "cf-chl", "_Incapsula_",
                         "humanchallenge", "/auth/deny", "recaptcha/api")


def is_challenge_url(url: str) -> bool:
    u = (url or "").lower()
    return any(m.lower() in u for m in CHALLENGE_URL_MARKERS)


def is_challenge_page(status, body: str) -> bool:
    """True when `body` is a bot-challenge interstitial rather than real content.

    A page served with HTTP 200 and real content is never a challenge, however
    many captcha widgets it embeds.
    """
    if not body:
        return False
    head = re.sub(r"\s+", " ", body[:8000]).lower()
    if any(m in head for m in CHALLENGE_BODY_MARKERS):
        return True
    if isinstance(status, int) and status >= 400:
        m = re.search(r"<title[^>]*>(.*?)</title>", body[:8000], re.S | re.I)
        title = re.sub(r"\s+", " ", m.group(1)).strip().lower() if m else ""
        if title and any(t in title for t in CHALLENGE_TITLE_MARKERS):
            return True
    return False


def guess_company_website(fetcher: "Fetcher", company: str,
                          tlds: Sequence[str] = DOMAIN_TLDS[:8],
                          max_dns: int = 14, max_fetch: int = 4) -> Optional[str]:
    """Derive a plausible company website from a bare company name.

    Name-only input previously left the fingerprint engine completely blind:
    `collect_careers_pages` had no URL to start from, so Engine B never ran and
    only name-derived tenant probes were tried.

    Cost is capped hard, because this runs for every company with no website -
    an unbounded version spent 150 s on Klarna alone while the same work takes
    0.5 s for Revolut.  DNS is resolved in parallel and only the first few
    hosts that actually resolve are fetched.

    Returns the first host that serves real content, or, if none does, the
    first host that merely resolved - that is still a usable base for the
    careers-path guesses, and an empty body simply means the site needs JS.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9]+", ascii_fold(company)) if w]
    if not words:
        return None
    stop = {x.strip() for x in SUFFIXES}
    legal = [w for w in words if w.lower() not in stop] or words
    bases = list(dict.fromkeys(b for b in (
        "".join(legal).lower(),                 # acmecorp
        "-".join(legal).lower(),                # acme-corp
        legal[0].lower(),                       # acme
        "".join(legal[:2]).lower(),             # acmetech
    ) if b and len(b) >= 3))

    hosts: List[str] = []
    for base in bases:
        for tld in tlds:
            hosts.append(f"{base}.{tld}")
    hosts = list(dict.fromkeys(hosts))[:max_dns]

    def resolves(h: str) -> bool:
        try:
            socket.gethostbyname(h)
            return True
        except OSError:
            return False

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        alive = [h for h, ok in zip(hosts, ex.map(resolves, hosts)) if ok]
    if not alive:
        return None

    fetched = 0
    fallback: Optional[str] = None
    for host in alive:
        if fetched >= max_fetch:
            break
        for url in (f"https://www.{host}/", f"https://{host}/"):
            if fetched >= max_fetch:
                break
            fetched += 1
            r = fetcher.request(url, max_bytes=60_000)
            st = r.get("status")
            if st is None or st in (404, 500, 502, 503):
                continue                        # nothing here, try the next
            body = r.get("body") or ""
            final = r.get("final_url") or url
            if (body.strip() and not is_challenge_page(st, body)
                    and not is_challenge_url(final)):
                return final
            # Resolved and answered, but served an empty shell (a JS-only site
            # such as Klarna) or a bot challenge.  Keep looking, but remember it
            # as a fallback.
            if fallback is None:
                fallback = url
    return fallback


def domain_from_url(url: str) -> Optional[str]:
    try:
        h = urlparse(url).hostname
        return h.lower() if h else None
    except Exception:
        return None


def registrable_domain(host: str) -> str:
    parts = (host or "").lower().split(".")
    two = {"co.uk", "com.au", "co.in", "com.br", "co.jp", "com.tr", "co.za",
           "com.mx", "co.nz", "co.id", "com.sg", "com.my", "com.cn", "co.kr"}
    if len(parts) >= 3 and ".".join(parts[-2:]) in two:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "")


def looks_like_careers_url(url: str) -> bool:
    u = (url or "").lower()
    return any(w in u for w in CAREER_WORDS)


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #

class Fetcher:
    """Thread-safe pooled fetcher with retries, per-host politeness and a cap
    on how many bytes are read."""

    def __init__(self, workers=12, timeout=20.0, max_bytes=900_000,
                 delay=0.12, retries=1, verbose=False):
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.delay = delay
        self.retries = retries
        self.verbose = verbose
        self._client = httpx.Client(
            headers={"User-Agent": UA,
                     "Accept-Language": "en-US,en;q=0.9,de;q=0.8,fr;q=0.7,nl;q=0.6"},
            follow_redirects=True, timeout=timeout, verify=True,
            limits=httpx.Limits(max_connections=workers * 2,
                                max_keepalive_connections=workers))
        self._last: Dict[str, float] = {}
        self._hl = threading.Lock()

    def _polite(self, host: str) -> None:
        if not self.delay:
            return
        with self._hl:
            wait = self.delay - (time.time() - self._last.get(host, 0.0))
            self._last[host] = time.time() + max(0.0, wait)
        if wait > 0:
            time.sleep(wait)

    def close(self) -> None:
        self._client.close()

    def request(self, url: str, method="GET", payload=None, headers=None,
                max_bytes: int = 0) -> Dict[str, Any]:
        out: Dict[str, Any] = {"url": url, "status": None, "body": "", "json": None,
                               "final_url": url, "history": [], "error": None,
                               "waf": None, "elapsed_ms": 0, "truncated": False,
                               "dns_ok": False, "dns_ip": None, "dns_error": False}
        if not url:
            out["error"] = "empty url"
            return out
        host = domain_from_url(url) or url
        has_proxy = bool(os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("ALL_PROXY"))
        ok, ip = dns_resolves(host)
        out.update(dns_ok=ok, dns_ip=ip, dns_error=not ok, host=host)
        if not ok and not has_proxy:
            is_static_api = any(host == vh or host.endswith("." + vh) for vh in (
                "greenhouse.io", "ashbyhq.com", "lever.co", "myworkdayjobs.com",
                "smartrecruiters.com", "workable.com", "icims.com", "avature.net"
            ))
            if not is_static_api:
                out["error"] = "NXDOMAIN"
                return out

        self._polite(host)
        cap = max_bytes or self.max_bytes
        attempt = 0
        while True:
            attempt += 1
            t0 = time.time()
            try:
                if method.upper() == "POST":
                    r = self._client.post(url, json=payload or {}, headers=headers or {})
                else:
                    r = self._client.get(url, headers=headers or {})
                if cap < self.max_bytes:
                    chunks, got = [], 0
                    for chunk in r.iter_text():
                        chunks.append(chunk)
                        got += len(chunk)
                        if got >= cap:
                            break
                    body = "".join(chunks)[:cap]
                    r.close()
                else:
                    body = r.text[: self.max_bytes]
                out.update(status=r.status_code, body=body, final_url=str(r.url),
                           history=[{"status": h.status_code, "url": str(h.url)}
                                    for h in r.history],
                           elapsed_ms=int((time.time() - t0) * 1000), error=None,
                           truncated=len(body) >= cap)
                out["waf"] = (r.headers.get("x-amzn-waf-action")
                              or ("captcha" if "Human Verification" in body[:3000] else None))
                stripped = body.lstrip()
                if "json" in r.headers.get("content-type", "") or stripped[:1] in ("{", "["):
                    try:
                        out["json"] = json.loads(body)
                    except Exception:
                        out["json"] = None
                if r.status_code in (429, 502, 503, 504) and attempt <= self.retries:
                    time.sleep(1.5 * attempt)
                    continue
                return out
            except Exception as e:
                out["error"] = f"{type(e).__name__}: {str(e)[:160]}"
                out["elapsed_ms"] = int((time.time() - t0) * 1000)
                if attempt <= self.retries and isinstance(
                        e, (httpx.ReadTimeout, httpx.ConnectTimeout,
                            httpx.RemoteProtocolError, httpx.ConnectError)):
                    time.sleep(1.0 * attempt)
                    continue
                return out


# --------------------------------------------------------------------------- #
# Engine A - probe vendor-hosted URLs
# --------------------------------------------------------------------------- #

# Former/parent brand names that still identify the vendor itself.
VENDOR_ALIASES = {"recruitee": ("tellent",)}

LEGAL_SUFFIX_TOKENS = ("gmbh", "ag", "inc", "ltd", "limited", "bv", "nv", "sas",
                       "sa", "sarl", "oy", "ab", "asa", "plc", "corp", "se",
                       "srl", "spa", "pty", "llc", "co", "group", "holding")


def is_vendor_itself(company: str, vid: str) -> bool:
    """True when the company being scanned IS this ATS vendor.

    Personio recruits on Personio, softgarden on softgarden and Flatchr on
    Flatchr.  Those rows are factually correct but useless in a list of
    "which ATS does this company use" - and worse, they make the output read
    like a list of ATS portals instead of a list of customers.  They are
    reported under a separate SELF status and excluded from the hits.
    """
    name = re.sub(r"[^a-z0-9]", "", ascii_fold(company).lower())
    v = re.sub(r"[^a-z0-9]", "", vid.lower())
    if not name or not v:
        return False
    if name == v:
        return True
    if name.startswith(v):
        rest = name[len(v):]
        # "personiose", "softgardenag", "greenhouseio" - a legal-form suffix or
        # the vendor's own brand, not a different company that happens to start
        # with the same letters.
        if rest in LEGAL_SUFFIX_TOKENS or len(rest) <= 2:
            return True
    if name.endswith(v):
        # "Tellent Recruitee" - Recruitee was renamed, and the old brand became
        # the prefix.  Only accept a prefix that is itself a known brand of this
        # vendor or a legal form, so "Global Workday Services" is not swallowed.
        head = name[: -len(v)]
        if head in LEGAL_SUFFIX_TOKENS or head in VENDOR_ALIASES.get(vid, ()):
            return True
    return False


def portal_url(f: Dict[str, Any]) -> Optional[str]:
    """The company's page inside the ATS, or None if there isn't one.

    A finding without this is not a finding. The whole point of the scan is to
    hand back a URL the user can open, so anything that cannot produce one -
    a marker with no harvestable slug, an Avature login redirect, a Workday
    tenant that answers 401 - collapses to NOT_FOUND rather than being reported
    as a weaker status.
    """
    u = f.get("job_list_url") or f.get("url")
    if not u or not re.match(r"^https?://", u):
        return None
    return u


def reportable(f: Dict[str, Any], statuses) -> bool:
    """True when this finding may appear in the output."""
    return f.get("status") in statuses and portal_url(f) is not None


def report_statuses(args) -> set:
    """Which statuses are allowed into the output.

    VERIFIED only, by default.  PROBABLE is opt-in because a PROBABLE row is by
    definition one step short of a usable answer, and the scan is meant to end
    at either a portal URL or NOT_FOUND.
    """
    # SELF is reportable: a company recruiting on its own product (Teamtailor on
    # career.teamtailor.com) still has an official portal, and that portal is the
    # answer.  It is labelled SELF rather than VERIFIED so nobody mistakes it for
    # a customer finding.
    st = {"VERIFIED"}
    if getattr(args, "include_self_matches", True):
        st.add("SELF")
    if getattr(args, "include_probable", False):
        st.add("PROBABLE")
    if getattr(args, "include_unverified", False):
        st.add("UNVERIFIED")
    return st


IDENTITY_MIN_TOKEN = 4


# Titles on vendor-hosted boards are branded: "Jobs at Babbel | JOIN",
# "Jobs at KONUX", "Careers at Sqills | ...".  That brand is the single most
# reliable statement of whose board it is - and it caught a false positive the
# token test could not: `teamtailor.join.com` serves "Jobs at InVisions", a
# different company, while the slug and the URL both contain "teamtailor".
RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
RE_BRAND = re.compile(r'(?:jobs?|careers?|stellenangebote?|offres?|vacancies|'
                      r'ledige stillinger|vacaturas)\s+(?:at|bei|chez|@|with|by|-|:)?\s+'
                      r'([A-Z0-9][^|<>]{1,58})', re.I)


def title_brand(body: str) -> Optional[str]:
    """Pull the company brand out of a board's <title>, if it states one."""
    m = RE_TITLE.search(body or "")
    if not m:
        return None
    title = re.sub(r"\s+", " ", html_unescape(m.group(1))).strip()
    b = RE_BRAND.search(title)
    if not b:
        return None
    brand = b.group(1).strip(" \t-–—,.")
    # drop the trailing site name and legal form: "KONUX GmbH" -> "konux"
    brand = re.split(r"\s*[|\-–—]\s*", brand)[0].strip()
    words = [w for w in re.split(r"[^\w]+", brand, flags=re.UNICODE)
             if w and w != "_"]
    words = [w for w in words if w.lower() not in
             {"gmbh", "ag", "inc", "ltd", "limited", "bv", "nv", "sas", "sa",
              "sarl", "oy", "ab", "asa", "plc", "corp", "corporation", "company",
              "co", "group", "gruppe", "se", "srl", "spa", "pty", "llc", "aps", "as"}]
    return " ".join(words) if words else None


def identity_mismatch(company: str, probe_url: str,
                      resp: Dict[str, Any]) -> Optional[str]:
    """Explain why a tenant page does not look like it belongs to `company`.

    A derived slug can land on an unrelated company's tenant, and every
    subdomain ATS answers HTTP 200 for any tenant that exists - so a 200 alone
    proves nothing about *whose* board it is.  Require at least one distinctive
    word of the company name to appear in the page (or in the probe URL).

    Returns None when the page looks right, otherwise a human-readable reason.
    """
    body = (resp.get("body") or "")[:250_000]
    # Two spellings of the page, because a German tenant registers "suedzucker"
    # while the company name folds to "sudzucker".
    hay = (ascii_fold(body).lower(), umlaut_fold(body).lower())

    # Tokenise the ORIGINAL name, then build both spellings of each token.
    # Folding first is fatal: ascii_fold("ü") is already "u", so the "ue"
    # variant could never be recovered and correct tenants would be rejected.
    raw_words = [w for w in re.split(r"[^\w]+", company, flags=re.UNICODE) if w]
    toks, variants = [], set()
    for w in raw_words:
        a = ascii_fold(w).lower()
        u = umlaut_fold(w).lower()
        if len(a) < IDENTITY_MIN_TOKEN and len(u) < IDENTITY_MIN_TOKEN:
            continue                                # not distinctive enough
        toks.append(a)
        variants.add(a)
        variants.add(u)
    if not variants:
        return None                                  # nothing distinctive to check

    # A branded title naming a DIFFERENT company outranks everything else: the
    # board may be full of our slug (it is in the URL, in every link) while
    # belonging to someone else entirely.
    brand = title_brand(body)
    if brand:
        btoks = set()
        for w in re.split(r"[^\w]+", brand, flags=re.UNICODE):
            if not w or w == "_":
                continue
            a, u = ascii_fold(w).lower(), umlaut_fold(w).lower()
            if len(a) >= IDENTITY_MIN_TOKEN or len(u) >= IDENTITY_MIN_TOKEN:
                btoks.add(a)
                btoks.add(u)
        if btoks and not (btoks & variants):
            # Compare against the COMPANY, never the body: the brand came out
            # of the <title>, so it is present in the body by construction and
            # would always "match".
            bsq = re.sub(r"[^a-z0-9]", "", ascii_fold(brand).lower())
            csq = re.sub(r"[^a-z0-9]", "", ascii_fold(company).lower())
            if bsq and bsq not in csq and csq not in bsq:
                return (f"identity check failed: the page is branded \"{brand}\", "
                        f"not {company} - this tenant belongs to a different company")

    if any(v and v in h for v in variants for h in hay):
        return None

    # Also accept the whole name written as one word: "Wo" + "onstadrotterdam".
    name_a = re.sub(r"[^a-z0-9]", "", ascii_fold(company).lower())
    name_u = re.sub(r"[^a-z0-9]", "", umlaut_fold(company).lower())
    if len(name_a) >= 5:
        squashed = [re.sub(r"[^a-z0-9]", "", h) for h in hay]
        if any(c in h for c in (name_a, name_u) for h in squashed):
            return None

    # A slug taken from the probe URL counts too: it was in the request we made.
    if probe_url:
        u = (ascii_fold(probe_url).lower(), umlaut_fold(probe_url).lower())
        if any(v and v in h for v in variants for h in u):
            return None
        if name_a and any(name_a in h for h in u):
            return None

    return (f"identity check failed: none of {toks[:4]} appear on the page, so "
            f"this tenant may belong to a different company")


def run_probe(fetcher: Fetcher, probe: V.Probe,
              validator: Optional[Callable[[Dict[str, Any], Dict[str, Any]],
                                           Dict[str, Any]]] = None) -> Dict[str, Any]:
    resp = fetcher.request(probe.request_url, probe.method, probe.payload,
                           probe.headers, max_bytes=probe.max_bytes)
    if probe.judge:
        try:
            st, ev = probe.judge(resp)
        except Exception as e:
            st, ev = "BLOCKED", f"judge crashed: {type(e).__name__}: {e}"
    else:
        st, ev = "BLOCKED", "no judge"
    cnt = (V.has_count(resp) or V.xml_items(resp)
           or V.count_jobs_in_text(probe.vid, resp.get("body") or ""))
    if probe.post_check and st not in ("MISS", "BLOCKED"):
        try:
            st2, ev2, cnt2 = probe.post_check(fetcher, probe, resp, st, ev)
            st, ev = st2, ev2
            if cnt2:
                cnt = cnt2
        except Exception as e:
            ev += f" (post-check failed: {type(e).__name__})"
    if st in ("VERIFIED", "PROBABLE") and is_challenge_page(resp.get("status"),
                                                            resp.get("body") or ""):
        st = "BLOCKED"
        ev = ("bot-challenge interstitial served instead of the tenant page "
              f"({resp.get('status')}) - retry later or from another IP")
    out = {
        "vendor_id": probe.vid, "vendor": probe.vendor,
        "url": probe.url or probe.request_url,
        "probe_url": probe.request_url, "api_url": probe.api_url,
        "kind": probe.kind, "status": st, "evidence": ev,
        "http_status": resp.get("status"), "final_url": resp.get("final_url"),
        "redirects": [h["status"] for h in resp.get("history") or []],
        "job_count": cnt, "error": resp.get("error"), "waf": resp.get("waf"),
        "truncated": resp.get("truncated"), "elapsed_ms": resp.get("elapsed_ms"),
        "note": probe.note,
    }
    if validator is not None:
        try:
            out = validator(resp, out) or out
        except Exception as e:
            out["evidence"] += f" (identity check failed: {type(e).__name__})"
    return out


def probe_vendor(fetcher: Fetcher, vid: str, slugs,
                 ctx: Dict[str, Any], budget: int) -> List[Dict[str, Any]]:
    """`slugs` is a list of plain strings or of `(slug, from_hint)` pairs."""
    spec = V.VENDORS[vid]
    norm = [(x if isinstance(x, str) else x[0],
             False if isinstance(x, str) else bool(x[1])) for x in slugs]
    company = ctx.get("company") or ""
    if vid == "workday":
        # Workday's search space is (tenant name x wd-number x site id).  Hand it
        # to the dedicated searcher so it does not consume the generic budget.
        res = None
        for slug, _hint in norm[:4]:
            res = V.run_workday_search(fetcher, slug, budget=max(budget, 48))
            if res["status"] in ("VERIFIED", "PROBABLE"):
                return [res]
        return [res] if res else []
    results, tried = [], set()
    blocked = 0
    # The budget is a *slug* budget.  It used to count requests, which silently
    # halved the coverage of every vendor that needs two requests per slug:
    # Personio probes jobs.personio.de and .com, so a budget of 40 reached only
    # slug 20 - and Sonio's real tenant, sonio-ag, is slug 24.  Vendors that
    # spend more than two requests per slug are held to twice the slug budget.
    req_cap = budget * 2
    slugs_used = 0
    for slug, from_hint in norm:
        if slugs_used >= budget or len(results) >= req_cap:
            return results
        slugs_used += 1
        try:
            probes = spec["build"](slug, company, ctx) or []
        except Exception as e:
            probes = []
            if fetcher.verbose:
                print(f"    ! build({vid},{slug}) failed: {e}", file=sys.stderr)

        # Only derived slugs are identity-checked.  A slug harvested from the
        # company's own careers page is theirs by construction, and demanding
        # that the company name also appear on the portal would wrongly demote
        # real boards that use a brand name or a product name instead.
        validator = None
        if not from_hint and company:
            def validator(resp, res, _slug=slug, _c=company):
                if res["status"] not in ("VERIFIED", "PROBABLE"):
                    return res
                reason = identity_mismatch(_c, res.get("probe_url") or "", resp)
                if reason:
                    res = dict(res)
                    res["status"] = "UNVERIFIED"
                    res["evidence"] = f"{res.get('evidence', '')} | {reason}".strip(" |")
                    res["note"] = f"identity mismatch for slug '{_slug}'"
                return res

        for p in probes:
            if not p.request_url:
                continue
            key = (p.request_url, p.method)
            if key in tried:
                continue
            tried.add(key)
            if len(results) >= req_cap:
                return results
            results.append(run_probe(fetcher, p, validator))
            st = results[-1]["status"]
            if st in ("VERIFIED", "PROBABLE"):
                return results                  # found it
            if st == "BLOCKED":
                blocked += 1
                if blocked >= 3:
                    return results              # this vendor will not answer us at all
    return results


# --------------------------------------------------------------------------- #
# Engine B - fingerprint the company's own careers page
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# Optional seed file: known (company -> portal) pairs
# --------------------------------------------------------------------------- #

def _seed_key(name: str) -> str:
    """Normalise a company name for seed lookup.

    The seed file spells things differently from any input list - `simcorp` vs
    SimCorp, `Moss` vs MOSS, `Cargo.one` vs Cargo One - so fold accents, drop
    punctuation, lowercase, and strip a trailing legal form.
    """
    n = ascii_fold(name or "").lower()
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    words = [w for w in n.split() if w]
    while len(words) > 1 and words[-1] in {"gmbh", "ag", "ltd", "limited", "inc",
                                           "incorporated", "bv", "nv", "sas", "sa",
                                           "sarl", "oy", "ab", "asa", "plc", "corp",
                                           "corporation", "company", "co", "llc",
                                           "srl", "spa", "pty", "llp", "se"}:
        words.pop()
    return "".join(words)


def load_seeds(path: str) -> Dict[str, Dict[str, Any]]:
    """Read a CSV of known company -> ATS portal rows.

    Expected columns (all but `name` optional): name, careers_url, provider,
    board_slug, notes.  Extra columns are ignored, so a wider spreadsheet can be
    handed over as-is.
    """
    if not path or not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        text = fh.read()
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    if not lines:
        return {}
    rdr = csv.DictReader(io.StringIO("\n".join(lines)))
    out: Dict[str, Dict[str, Any]] = {}
    for row in rdr:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        name = row.get("name") or row.get("company") or ""
        url = row.get("careers_url") or row.get("portal_url") or row.get("url") or ""
        if not name or not re.match(r"^https?://", url):
            continue
        k = _seed_key(name)
        if k and k not in out:
            out[k] = {"name": name, "careers_url": url,
                      "provider": (row.get("provider") or "").strip().lower(),
                      "board_slug": row.get("board_slug") or "",
                      "notes": row.get("notes") or ""}
    return out


def lookup_seed(company: str, seeds) -> Optional[Dict[str, Any]]:
    """The seed row for this company, or None.  `seeds` may be None."""
    if not seeds:
        return None
    return seeds.get(_seed_key(company))


def careers_path_candidates(base: str) -> List[str]:
    """Conventional careers URLs to try under a derived website.

    `base` is usually a redirect target rather than a site root, and appending a
    path to it verbatim is wrong two different ways:

        https://www.salesforce.com/in/?ir=1  ->  .../in/?ir=1/careers
        https://rows.com/product             ->  .../product/careers

    The first drops the path inside the query string, where it is not a path at
    all.  So the query and fragment are discarded, and each conventional path is
    tried both at the site root and under whatever locale prefix the redirect
    landed on - root first, because that is the URL a company advertises.
    """
    parts = urlsplit(base or "")
    if not parts.netloc:
        return []
    root = urlunsplit((parts.scheme or "https", parts.netloc, "", "", ""))
    prefix = (parts.path or "").rstrip("/")
    out: List[str] = []
    for path in CAREERS_PATHS:
        out.append(root + path)
        if prefix:
            out.append(root + prefix + path)
    seen, uniq = set(), []          # de-dup, keep order (root == prefixed when
    for u in out:                   # the derived site had no path of its own)
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def collect_careers_pages(fetcher: Fetcher, website, careers_url, limit=3):
    """Pages worth fingerprinting: the company's careers page, its home page,
    the conventional careers paths, and careers-looking links one hop away.

    Seeds and crawled links are budgeted separately.  They used to share one
    `limit + 2` cap, which silently stopped working once the conventional paths
    were added as seeds: the seed list alone exceeded the cap, so the fetch loop
    never ran and every page came back with status None.
    """
    seeds, crawled, seen = [], [], set()

    def add(url, origin, seed):
        if not url or url in seen or not re.match(r"^https?://", url):
            return
        seen.add(url)
        (seeds if seed else crawled).append({"url": url, "origin": origin})

    if careers_url:
        add(careers_url if re.match(r"^https?://", careers_url) else "https://" + careers_url,
            "input:careers_url", True)
    if website:
        base = website if re.match(r"^https?://", website) else "https://" + website
        add(base, "input:website", True)
        # No explicit careers page: try the conventional paths.  Revolut's
        # answers 403 but still serves 873 kB of real HTML, so guessing only the
        # homepage silently missed it.
        if not careers_url:
            for u in careers_path_candidates(base):
                add(u, "guess:careers-path", True)
            # Some companies put the portal on a subdomain of their OWN domain,
            # pointing at a vendor: career.spendesk.com is Teamtailor,
            # jobs.virtuagym.com is Recruitee.  No tenant slug exists to derive,
            # so the only way in is to try the conventional subdomains and let
            # Engine B fingerprint whatever is serving them.  These are added
            # last, so a company with a normal /careers page never pays for them.
            reg = registrable_domain(domain_from_url(base) or "")
            if reg:
                for sub in CAREERS_SUBDOMAINS:
                    add(f"https://{sub}.{reg}/", "guess:careers-path", True)

    # ---- fetch the seeds.  The conventional-path guesses are tried in order
    # ---- and abandoned as soon as one yields a real careers page, so a company
    # ---- whose careers live at /careers costs one request, not eight.
    have_careers = bool(careers_url)
    dead = 0                       # consecutive guessed paths with nothing readable
    for page in seeds:
        guessed = page["origin"] == "guess:careers-path"
        if guessed and have_careers:
            page.update(status=None, body="", skipped="an earlier careers page was readable")
            continue
        if guessed and dead >= 3:
            # Klarna answers 202 with zero bytes on every path; Specsavers serves
            # a Cloudflare challenge on every path.  Three such answers from the
            # same host is enough - stop spending requests on it.
            page.update(status=None, body="", skipped="host stopped answering usefully")
            continue
        r = fetcher.request(page["url"])
        page.update(status=r.get("status"), body=r.get("body") or "",
                    final_url=r.get("final_url"), dns_ok=r.get("dns_ok"),
                    error=r.get("error"))
        page["challenge"] = (is_challenge_page(page.get("status"), page["body"])
                             or is_challenge_url(page.get("final_url") or ""))
        # A 404 is not the careers page, however large its body: rows.com/careers
        # answers 404 with 30 kB of site chrome, and accepting it stopped the
        # remaining paths from being tried.  Bot-walled statuses (403/429) still
        # count - Revolut serves 873 kB of real HTML behind a 403.
        readable = (not page["challenge"] and len(page["body"]) >= 1500
                    and page.get("status") not in (404, 410))
        if guessed:
            dead = 0 if readable else dead + 1
        if guessed and readable and looks_like_careers_url(page["url"]):
            have_careers = True

    # ---- one hop: follow careers-looking links out of any page that actually
    # ---- has content.  Bot-protected pages (403/429/503) very often still
    # ---- carry the full document; an empty body (Klarna answers 202 with zero
    # ---- bytes) has nothing to read and is a dead end without a browser.
    for page in seeds:
        if len(crawled) >= limit * 2:
            break
        body = page.get("body") or ""
        if len(body) < 1500 or page.get("skipped") or page.get("challenge"):
            continue
        host = domain_from_url(page.get("final_url") or page["url"]) or ""
        reg = registrable_domain(host)
        cand = []
        for h in re.findall(r"""(?:href|src)\s*=\s*["']([^"'<> ]{4,300})["']""",
                            body, re.I):
            if h.startswith("//"):
                h = "https:" + h
            elif h.startswith("/"):
                h = f"https://{host}{h}" if host else ""
            elif not h.startswith("http"):
                h = urljoin(page.get("final_url") or page["url"], h)
            if not h or not re.match(r"^https?://", h):
                continue
            if re.search(r"\.(?:js|css|png|jpe?g|gif|svg|webp|ico|woff2?|ttf|map|mp4|pdf)(?:\?|$)",
                         h, re.I):
                continue                      # a static asset, not a careers page
            hh = domain_from_url(h) or ""
            same_site = hh and reg and registrable_domain(hh) == reg
            if (same_site and looks_like_careers_url(h)) or proof_url_in_text(h):
                cand.append(h)
        cand = list(dict.fromkeys(cand))
        cand.sort(key=lambda u: (0 if looks_like_careers_url(u) else 1, len(u)))
        for c in cand[:limit]:
            add(c, "crawl:" + (domain_from_url(page["url"]) or "?"), False)

    for page in crawled:
        r = fetcher.request(page["url"])
        page.update(status=r.get("status"), body=r.get("body") or "",
                    final_url=r.get("final_url"), dns_ok=r.get("dns_ok"),
                    error=r.get("error"))
        page["challenge"] = (is_challenge_page(page.get("status"), page["body"])
                             or is_challenge_url(page.get("final_url") or ""))
    return [p for p in seeds + crawled if not p.get("skipped")]


def proof_url_in_text(text: str) -> bool:
    """Does this snippet contain a URL that *proves* some ATS is in use?"""
    for vid, pat in V.PROOF_FINGERPRINTS:
        if pat.search(text):
            return True
    return False


def fingerprint_pages(pages, company: str = "") -> Dict[str, Dict[str, Any]]:
    """Scan fetched HTML.  Returns {vendor_id: {"proof": [...], "hints": [...],
    "urls": [...], "slugs": [...]}}.

    A match is discarded when the page it was found on is itself hosted by that
    vendor - otherwise every Greenhouse board would "prove" it runs on
    Greenhouse, and Avature's own careers page would credit itself.

    The one exception is when the company IS that vendor: Teamtailor recruits on
    career.teamtailor.com, and the link to it sits on teamtailor.com, so the
    self-confirmation guard would throw away the only correct answer.
    """
    out: Dict[str, Dict[str, Any]] = {}

    def bucket(vid):
        return out.setdefault(vid, {"proof": [], "hints": [], "urls": [],
                                    "slugs": [], "proof_count": 0})

    for page in pages:
        body = page.get("body") or ""
        if not body:
            continue
        if (page.get("challenge") or is_challenge_page(page.get("status"), body)
                or is_challenge_url(page.get("final_url") or "")):
            continue                      # a bot wall carries no ATS evidence
        src = page.get("final_url") or page.get("url") or ""
        page_reg = registrable_domain(domain_from_url(src) or "")
        for vid, pat in V.PROOF_FINGERPRINTS:
            for m in list(pat.finditer(body))[:40]:
                raw = m.group(0)
                try:
                    url = vendor_url_from_match(vid, raw, m.groups())
                except Exception:
                    url = None
                if url and not re.match(r"^https?://", url):
                    url = urljoin(src, url)          # resolve relative markers
                if url:
                    uhost = domain_from_url(url) or ""
                    ureg = registrable_domain(uhost)
                    if (page_reg and ureg and ureg == page_reg and vid not in ("eploy",)
                            and not (company and is_vendor_itself(company, vid))):
                        continue          # self-confirmation, ignore
                    b = bucket(vid)
                    b["proof_count"] += 1
                    sub = tenant_slug_for(vid, url, uhost)
                    infra = V.is_infra_host(uhost)
                    # always record the sample; only harvest the URL when it is a
                    # real tenant career site rather than a vendor CDN/asset host
                    b["proof"].append({"matched": raw[:180],
                                       "url": None if infra else url,
                                       "found_on": src, "infra": infra})
                    if not infra and url not in b["urls"]:
                        b["urls"].append(url)
                    if sub and sub not in b["slugs"]:
                        b["slugs"].append(sub)
                else:
                    b = bucket(vid)
                    b["proof_count"] += 1
                    b["proof"].append({"matched": raw[:180], "url": None, "found_on": src})
                    if m.groups() and m.group(1) and m.group(1) not in b["slugs"]:
                        b["slugs"].append(m.group(1))
        for vid, pat in V.HINT_FINGERPRINTS:
            for m in list(pat.finditer(body))[:3]:
                bucket(vid)["hints"].append({"matched": m.group(0)[:120], "found_on": src})
    return out


VENDOR_BASE_DOMAIN = {
    "greenhouse": "greenhouse.io", "lever": "lever.co", "ashby": "ashbyhq.com",
    "workday": "myworkdayjobs.com", "workable": "workable.com",
    "bamboohr": "bamboohr.com", "recruitee": "recruitee.com",
    "personio": "jobs.personio.de", "smartrecruiters": "smartrecruiters.com",
    "teamtailor": "teamtailor.com", "homerun": "homerun.co",
    "successfactors": "jobs.sap.com", "taleo": "taleo.net", "icims": "icims.com",
    "softgarden": "softgarden.io", "dvinci": "dvinci-easy.com",
    "flatchr": "flatchr.io", "taleez": "taleez.com", "beetween": "betheen.com",
    "pinpoint": "pinpointhq.com", "join": "join.com", "coveto": "coveto.fr",
    "rexx": "rexx-systems.com", "avature": "avature.net",
    "zoho": "zohopublic.com", "bullhorn": "bullhornstaffing.com", "eploy": "",
}


def vendor_base_domain(vid: str) -> str:
    return VENDOR_BASE_DOMAIN.get(vid, "")


def slug_lives_in_hostname(vid: str, company: str = "", ctx: Optional[Dict[str, Any]] = None) -> bool:
    """True when a vendor puts the tenant in the hostname, not the path.

    Detected by building one probe for a slug that cannot collide with anything
    real, so no vendor list has to be maintained by hand.  On such vendors a
    slug containing a dot is not a long shot, it is impossible: sonio.org
    .jobs.personio.de cannot resolve.  Skipping those is free accuracy - Sonio's
    real tenant moved from position 24 to 20.
    """
    spec = V.VENDORS.get(vid)
    if not spec or not spec.get("build"):
        return False
    probe_slug = "acmesampletenant"
    try:
        probes = spec["build"](probe_slug, company, ctx or {}) or []
    except Exception:
        return False
    for pr_ in probes:
        host = urlparse(pr_.request_url or "").hostname or ""
        if probe_slug in host:
            return True
    return False


def drop_impossible_slugs(vid: str, cands, company: str = "",
                          ctx: Optional[Dict[str, Any]] = None):
    if not slug_lives_in_hostname(vid, company, ctx):
        return cands
    kept = [c for c in cands if "." not in (c[0] if isinstance(c, tuple) else c)]
    return kept or cands


def proof_is_vendor_root(vid: str, url: str) -> bool:
    """True when a proof URL is just the vendor's own marketing site.

    Every hosted board ends with a "Powered by <Vendor>" link, so the vendor's
    bare root is the single most common false proof: it identifies the ATS but
    is not the company's portal.  career.spendesk.com is a Teamtailor board on
    Spendesk's own domain, and the only Teamtailor URL in its HTML is
    www.teamtailor.com - reporting that would send the user to the wrong place.
    """
    if not url:
        return False
    vbd = vendor_base_domain(vid)
    if not vbd:
        return False
    try:
        pr = urlparse(url)
    except ValueError:
        return False
    host = (pr.hostname or "").lower()
    if host not in (vbd, f"www.{vbd}"):
        return False                      # a real tenant, e.g. spendesk.teamtailor.com
    return pr.path.rstrip("/") == "" and not pr.query


def portal_from_proof(vid: str, entry: Dict[str, Any],
                      pages: List[Dict[str, Any]]) -> Optional[str]:
    """Swap a vendor-root proof for the page that actually carries it.

    Only when that page is a real job listing: a careers page with job links
    that says "Powered by <Vendor>" *is* that vendor's portal, on a custom
    domain.  A page that merely mentions the vendor is not.
    """
    url = entry.get("url")
    if not proof_is_vendor_root(vid, url or ""):
        return None
    found_on = entry.get("found_on") or ""
    if not found_on:
        return None
    fhost = registrable_domain(domain_from_url(found_on) or "")
    if not fhost or fhost == registrable_domain(vendor_base_domain(vid)):
        return None                       # found on the vendor's own site
    for pg in pages:
        if (pg.get("url") or pg.get("final_url")) != found_on:
            continue
        body = pg.get("body") or ""
        if not body or is_challenge_page(pg.get("http_status") or pg.get("status"), body):
            return None
        if V.page_confirms_listing(V.job_page_signals(body)):
            return found_on
        return None
    return None


# Vendors whose tenant id is the FIRST PATH SEGMENT rather than a subdomain.
PATH_SLUG_VENDORS = {
    "lever": r"jobs\.lever\.co/([A-Za-z0-9_\-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-]+)",
    "greenhouse": r"greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_\-]+)",
    "workable": r"apply\.workable\.com/([A-Za-z0-9_\-]+)",
    "smartrecruiters": r"careers\.smartrecruiters\.com/([A-Za-z0-9_\-]+)",
    "join": r"join\.com/companies/([A-Za-z0-9_\-]+)",
    "zoho": r"recruit\.zohopublic\.com/careers/([A-Za-z0-9_\-]+)",
    "beetween": r"app\.beetween\.com/([A-Za-z0-9_\-]+)",
    "taleo": r"taleo[^/]*/careersection/",
    "bullhorn": r"rest-services/([A-Za-z0-9]+)",
}
PATH_SLUG_SKIP = {"embed", "job_board", "o", "companies", "careers", "api", "v1",
                  "job-board", "posting-api", "widget", "assets", "jobs", "job"}


def tenant_slug_for(vid: str, url: str, host: str) -> Optional[str]:
    """Extract the tenant id from a fingerprinted ATS URL, handling both the
    subdomain form (`bupa.pinpointhq.com`) and the path form
    (`jobs.lever.co/palantir`)."""
    pat = PATH_SLUG_VENDORS.get(vid)
    if pat:
        m = re.search(pat, url or "", re.I)
        if m and m.groups():
            cand = m.group(1)
            if cand and cand.lower() not in PATH_SLUG_SKIP:
                return cand
    return V.tenant_slug_from_host(host or "", vendor_base_domain(vid))


SUB_RE = {
    "greenhouse": r"(?:job-boards|boards)\.greenhouse\.io/([A-Za-z0-9_\-]+)",
    "ashby": r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-]+)",
    "lever": r"jobs\.lever\.co/([A-Za-z0-9_\-]+)",
    "workable": r"apply\.workable\.com/([A-Za-z0-9_\-]+)",
    "smartrecruiters": r"careers\.smartrecruiters\.com/([A-Za-z0-9_\-]+)",
    "personio": r"([A-Za-z0-9\-]+)\.jobs\.personio\.(de|com)",
    "bamboohr": r"([A-Za-z0-9\-]+)\.bamboohr\.com",
    "recruitee": r"([A-Za-z0-9\-]+)\.recruitee\.com",
    "teamtailor": r"([A-Za-z0-9\-]+)\.teamtailor\.com",
    "homerun": r"([A-Za-z0-9\-]+)\.homerun\.co",
    "softgarden": r"([A-Za-z0-9\-]+)\.softgarden\.io",
    "dvinci": r"([A-Za-z0-9\-]+)\.dvinci-easy\.com",
    "flatchr": r"([A-Za-z0-9\-]+)\.flatchr\.io",
    "taleez": r"([A-Za-z0-9\-]+)\.taleez\.com",
    "pinpoint": r"([A-Za-z0-9\-]+)\.pinpointhq\.com",
    "join": r"([A-Za-z0-9\-]+)\.join\.com",
    "avature": r"([A-Za-z0-9\-]+)\.avature\.net",
    "icims": r"([A-Za-z0-9\-\.]+)\.icims\.com",
    "taleo": r"([A-Za-z0-9\-]+)\.(taleo\d*\.(?:net|com)|taleoenterprise\.com)",
    "beetween": r"([A-Za-z0-9\-]+)\.betheen\.com",
    "coveto": r"([A-Za-z0-9\-]+)\.coveto\.fr",
    "rexx": r"([A-Za-z0-9\-]+)\.rexx-systems\.com",
}


def vendor_url_from_match(vid: str, matched: str, groups) -> Optional[str]:
    """Turn a fingerprint match into the public career-site URL."""
    if vid == "workday":
        m = re.search(r"([A-Za-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com(/[\w\-/]*)?", matched, re.I)
        return f"https://{m.group(1)}.{m.group(2)}.myworkdayjobs.com{m.group(3) or ''}" if m else None
    if vid == "successfactors":
        # shared-host form first: the career_company query string is the career site
        m = re.search(r"https?://[^\s\"'<>]*career_company=[A-Za-z0-9_\-]+[^\s\"'<>]*",
                      matched, re.I)
        if m:
            return m.group(0).rstrip("&")
        m = re.search(r"https?://[A-Za-z0-9\-\.]*(?:jobs\.sap\.com|successfactors\.(?:com|eu)|sapsf\.com)[^\s\"'<>]*",
                      matched, re.I)
        return m.group(0) if m else None
    if vid == "zoho":
        m = re.search(r"https?://[A-Za-z0-9\-\.]*(?:recruit\.zohopublic\.com|zohorecruit\.com|recruit\.zoho\.com)[^\s\"'<>]*", matched, re.I)
        return m.group(0) if m else None
    if vid == "bullhorn":
        m = re.search(r"https?://[a-z0-9\-\.]*bullhornstaffing\.com/rest-services/[A-Za-z0-9]+[^\s\"'<>]*", matched, re.I)
        if m:
            return m.group(0)
        m = re.search(r"https?://[a-z0-9\-\.]*bhlts\.com[^\s\"'<>]*", matched, re.I)
        return m.group(0) if m else None
    if vid == "eploy":
        m = re.search(r"https?://[^\s\"'<>]+", matched, re.I)
        if m:
            return m.group(0)
        return matched            # relative marker; resolved against the page URL
    if vid == "beetween":
        m = re.search(r"app\.beetween\.com/([A-Za-z0-9_\-]+)", matched, re.I)
        if m:
            return f"https://app.beetween.com/{m.group(1)}/recrutement"
    if vid == "icims":
        m = re.search(r"([A-Za-z0-9\-\.]+)\.icims\.com", matched, re.I)
        return f"https://{m.group(1)}.icims.com/jobs" if m else None

    pat = SUB_RE.get(vid)
    if pat:
        m = re.search(pat, matched, re.I)
        if m:
            gs = [g for g in m.groups() if g]
            s = m.group(1)
            if vid == "personio":
                dom = m.group(2) if len(m.groups()) > 1 and m.group(2) else "de"
                return f"https://{s}.jobs.personio.{dom}/"
            if vid == "taleo":
                tld = m.group(2) if len(m.groups()) > 1 and m.group(2) else "taleo.net"
                return f"https://{s}.{tld}/"
            tmpl = {
                "greenhouse": "https://job-boards.greenhouse.io/{s}",
                "ashby": "https://jobs.ashbyhq.com/{s}",
                "lever": "https://jobs.lever.co/{s}",
                "workable": "https://apply.workable.com/{s}/",
                "smartrecruiters": "https://careers.smartrecruiters.com/{s}",
                "bamboohr": "https://{s}.bamboohr.com/careers",
                "recruitee": "https://{s}.recruitee.com/",
                "teamtailor": "https://{s}.teamtailor.com/",
                "homerun": "https://{s}.homerun.co/",
                "softgarden": "https://{s}.softgarden.io/",
                "dvinci": "https://{s}.dvinci-easy.com/",
                "flatchr": "https://{s}.flatchr.io/",
                "taleez": "https://{s}.taleez.com/",
                "pinpoint": "https://{s}.pinpointhq.com/",
                "join": "https://{s}.join.com/",
                "avature": "https://{s}.avature.net/",
                "beetween": "https://{s}.betheen.com/",
                "coveto": "https://{s}.coveto.fr/",
                "rexx": "https://{s}.rexx-systems.com/",
            }.get(vid)
            if tmpl and gs:
                return tmpl.format(s=s)
    m = re.search(r"https?://[^\s\"'<>]+", matched, re.I)
    return m.group(0) if m else None


# --------------------------------------------------------------------------- #
# per-company orchestration
# --------------------------------------------------------------------------- #

def apply_tier(spec, probe_status, probe, corroborated, proof_urls):
    """Enforce the proof tier: can this probe result be reported as-is?"""
    tier = spec["tier"]
    if tier == V.TIER_AUTHORITATIVE:
        return probe_status
    if tier == V.TIER_CORROBORATED:
        # UNVERIFIED from a probe means "a page exists under this name".
        # Promote it when the company's own careers page points at the ATS.
        if probe_status == "UNVERIFIED" and corroborated:
            return "VERIFIED"
        return probe_status
    return probe_status          # tier 3 vendors produce no probe results


def scan_company(fetcher: Fetcher, company: str, website, careers_url,
                 args) -> Dict[str, Any]:
    t0 = time.time()
    # keep what the user actually supplied separate from what we derived, so the
    # output can say which is which
    input_website, input_careers = website, careers_url
    guessed_site = False
    # A seed row is treated as knowledge, not as an answer: it supplies the real
    # portal URL (so Engine B starts from the right page instead of guessing) and
    # the real tenant slug (so Engine A probes it as a hint).  Everything still
    # has to verify live, so a stale seed cannot fabricate a result.
    seed = lookup_seed(company, getattr(args, "seeds_data", None))
    seed_url = (seed or {}).get("careers_url") or None
    if seed_url and not careers_url:
        careers_url = seed_url
    if not website and not careers_url and args.mode in ("all", "fingerprint"):
        website = guess_company_website(fetcher, company)
        guessed_site = bool(website)
        if guessed_site and fetcher.verbose:
            print(f"    ~ {company}: no website given, derived {website}", file=sys.stderr)
    ctx = {"company": company, "website": website, "careers_url": careers_url,
           "domain": domain_from_url(careers_url or website or "") or "",
           "website_guessed": guessed_site}
    vfilter = set(args.vendors) if args.vendors else None
    active = [v for v in V.VENDOR_ORDER if not vfilter or v in vfilter]

    # ---- Engine B first: cheap, finds custom domains, and the slugs it
    # ---- uncovers make Engine A far more likely to succeed.
    pages, fp = [], {}
    if args.mode in ("all", "fingerprint"):
        pages = collect_careers_pages(fetcher, website, careers_url,
                                      limit=args.max_career_pages)
        fp = fingerprint_pages(pages, company=company)

    # ---- Engine A
    probe_results: Dict[str, List[Dict[str, Any]]] = {}
    if args.mode in ("all", "probe"):
        for vid in active:
            spec = V.VENDORS[vid]
            hints = list(fp.get(vid, {}).get("slugs", []))
            if seed and seed.get("provider") == vid and seed.get("board_slug"):
                # Workday rows carry "tenant/site"; either part is a useful hint.
                for part in re.split(r"[/\s]+", seed["board_slug"]):
                    if part and part.lower() not in [h.lower() for h in hints]:
                        hints.insert(0, part)
            if not spec["guessable"] and not hints:
                probe_results[vid] = []
                continue
            ext = vid in EXTENDED_SLUG_VENDORS
            cands = drop_impossible_slugs(
                vid, slug_candidates(company, hints, extended=ext), company, ctx)
            probe_results[vid] = probe_vendor(
                fetcher, vid, cands, ctx,
                max(args.probe_budget, EXTENDED_PROBE_BUDGET) if ext
                else args.probe_budget)

    # ---- merge into one finding per vendor
    findings: List[Dict[str, Any]] = []
    for vid in active:
        spec = V.VENDORS[vid]
        pr = probe_results.get(vid, [])
        f = fp.get(vid, {})
        proof_urls = [p["url"] for p in f.get("proof", []) if p.get("url")]
        # a proof-pattern match without a resolvable URL (e.g. a relative
        # ?vacancyId= link) still corroborates a tier-2 probe
        # a "Powered by" link to the vendor's own root proves the ATS but is not
        # the portal; the portal is the page the marker was found on.
        for _e in f.get("proof", []) or []:
            _alt = portal_from_proof(vid, _e, pages)
            if _alt:
                proof_urls = [_alt] + [u for u in proof_urls if u != _alt]
                break
        corroborated = bool(proof_urls) or f.get("proof_count", 0) > 0

        best = None
        for r in sorted(pr, key=lambda r: -STATUS_RANK.get(r["status"], 0)):
            if r["status"] in ("VERIFIED", "PROBABLE", "UNVERIFIED"):
                best = dict(r)
                break
        if best is not None:
            tier_status = apply_tier(spec, best["status"], best, corroborated, proof_urls)
            if tier_status != best["status"] and tier_status == "VERIFIED":
                best["evidence"] += ("  ||  corroborated: the company's own careers page "
                                     "links to " + (proof_urls[0] if proof_urls else "this ATS"))
            best["status"] = tier_status

        # a proof pattern matched but yielded no usable URL (e.g. a relative
        # ?vacancyId= link, or a vendor CDN host) - still worth reporting
        if corroborated and not proof_urls:
            rank_best = STATUS_RANK.get(best["status"], 0) if best else 0
            sample = (f.get("proof") or f.get("hints") or [{}])[0]
            if rank_best < STATUS_RANK["VERIFIED"]:
                surl = portal_from_proof(vid, sample, pages) or sample.get("url")
                best = {"vendor_id": vid, "vendor": spec["name"],
                        "status": "VERIFIED" if spec["tier"] == V.TIER_FINGERPRINT
                                  else "PROBABLE",
                        "url": surl, "job_list_url": surl,
                        "final_url": surl, "job_count": None,
                        "http_status": None, "api_url": None, "kind": "fingerprint",
                        "via": "fingerprint",
                        "evidence": ("the company's own careers page contains this ATS's "
                                     "marker: " + str(sample.get("matched"))[:110]),
                        "redirects": [], "error": None, "waf": None,
                        "elapsed_ms": 0, "note": ""}

        # fingerprint-driven finding.  IMPORTANT: this may only *upgrade* a
        # finding.  A probe that already reached VERIFIED (or PROBABLE on an
        # authoritative tier-1 vendor) must never be replaced by a fingerprint
        # URL that has not itself been re-proved.
        if proof_urls:
            rank_best = STATUS_RANK.get(best["status"], 0) if best else 0
            upgrade_ceiling = STATUS_RANK["VERIFIED"] if spec["tier"] == V.TIER_AUTHORITATIVE \
                else STATUS_RANK["PROBABLE"]
            if rank_best < upgrade_ceiling:
                url = proof_urls[0]
                verified = False
                if args.verify_fingerprint and vid not in ("eploy", "bullhorn", "zoho"):
                    vh = domain_from_url(url) or ""
                    cd = registrable_domain(domain_from_url(careers_url or website or "") or "")
                    if vh and registrable_domain(vh) != cd:
                        vr = run_probe(fetcher, V.Probe(
                            vid=vid, vendor=spec["name"], url=url, request_url=url,
                            judge=V.j_status_ok([vh]), kind="fingerprint_recheck"))
                        if vr["status"] == "VERIFIED":
                            verified, best = True, {**vr, "status": "VERIFIED",
                                                    "evidence": "harvested from the company's "
                                                                "careers page and re-verified: "
                                                                + vr["evidence"]}
                if not verified and (best is None or rank_best < STATUS_RANK["VERIFIED"]):
                    # only take this on if it beats what we already have
                    best = {"vendor_id": vid, "vendor": spec["name"], "status": "VERIFIED",
                            "url": url, "job_list_url": url, "final_url": url,
                            "job_count": None, "http_status": None, "api_url": None,
                            "kind": "fingerprint", "via": "fingerprint",
                            "evidence": ("the company's own careers page links to / embeds "
                                         "this ATS URL: " + url),
                            "redirects": [], "error": None, "waf": None,
                            "elapsed_ms": 0, "note": ""}

        if best is None or best["status"] in ("MISS", "NOT_FOUND"):
            blocked = [r for r in pr if r["status"] == "BLOCKED"]
            unv = [r for r in pr if r["status"] == "UNVERIFIED"]
            if unv and args.include_unverified:
                best = dict(sorted(unv, key=lambda r: -STATUS_RANK.get(r["status"], 0))[0])
                best["status"] = "UNVERIFIED"
            else:
                if blocked and not [r for r in pr if r["status"] == "MISS"]:
                    reason = "vendor unreachable / bot-walled"
                elif not spec["guessable"] and not fp.get(vid):
                    reason = "no guessable tenant URL - fingerprint only, no marker found"
                else:
                    reason = "no evidence"
                findings.append({"vendor_id": vid, "vendor": spec["name"],
                                 "status": "NOT_FOUND", "tier": spec["tier"],
                                 "url": None, "job_list_url": None, "job_count": None,
                                 "evidence": reason, "via": None,
                                 "fingerprint": bool(fp.get(vid)),
                                 "fingerprint_samples": [], "probes": pr[:6]})
                continue

        findings.append({
            "vendor_id": vid, "vendor": spec["name"], "status": best["status"],
            "tier": spec["tier"], "url": best.get("url"),
            "final_url": best.get("final_url"),
            "job_list_url": best.get("job_list_url") or (
                best.get("url") if best.get("kind") in ("job_list", "fingerprint") else None),
            "job_count": best.get("job_count"), "evidence": best.get("evidence"),
            "http_status": best.get("http_status"),
            "api_url": best.get("api_url") or (
                best.get("probe_url") if best.get("kind") in ("job_list", "api") else None),
            "via": best.get("kind") or best.get("via"),
            "fingerprint": corroborated,
            "fingerprint_samples": [p["matched"][:140] for p in f.get("proof", [])[:3]],
            "fingerprint_urls": proof_urls[:5],
            "probes": pr[:8],
        })

    if seed and fetcher.verbose:
        print(f"    * {company}: seed row used -> {seed.get('provider')} "
              f"{seed_url}", file=sys.stderr)

    if not args.no_page_check:
        findings = page_check(fetcher, findings, company=company,
                              verbose=fetcher.verbose)

    # Always label a self-match; report_statuses() decides whether it is shown.
    for f in findings:
        if f["status"] != "NOT_FOUND" and is_vendor_itself(company, f["vendor_id"]):
            f["self_match"] = True
            f["status"] = "SELF"
            f["evidence"] = (f"{company} is the vendor of this product, so its own "
                             f"board is not a customer finding")
    keep = report_statuses(args)
    hits = [f for f in findings if reportable(f, keep)]
    hits.sort(key=lambda f: (-STATUS_RANK.get(f["status"], 0), f["vendor"]))
    challenged = [p for p in pages if p.get("challenge")]
    readable = [p for p in pages
                if len(p.get("body") or "") >= 1500 and not p.get("challenge")]
    careers_like = [p for p in readable
                    if looks_like_careers_url(p.get("final_url") or p.get("url") or "")]
    evidence_page = ((careers_like or readable or [None])[0])
    return {
        "company": company,
        "website": input_website, "careers_url": input_careers,
        "website_used": website,
        "website_guessed": bool(ctx.get("website_guessed")),
        "careers_page_found": (evidence_page or {}).get("final_url")
                               or (evidence_page or {}).get("url"),
        "careers_page_http_status": (evidence_page or {}).get("status"),
        "careers_page_blocked": bool(challenged) and not readable,
        "careers_pages_scanned": [p.get("final_url") or p.get("url") for p in pages],
        "found": bool(hits),
        "ats": [h["vendor"] for h in hits],
        "best_status": hits[0]["status"] if hits else None,
        "primary_url": (hits[0].get("job_list_url") or hits[0].get("url")) if hits else None,
        "primary_job_count": hits[0].get("job_count") if hits else None,
        "findings": findings,
        "seconds": round(time.time() - t0, 2),
    }


# --------------------------------------------------------------------------- #
# input / output
# --------------------------------------------------------------------------- #

def clean_company_name(name: str) -> str:
    chars = " \t\r\n" + chr(34) + chr(39)
    name = (name or "").strip(chars)
    name = re.sub(r"^(?:\[\d+\]|\d+[\.:\)\-\s]+|[\-\*•]\s+)", "", name).strip(chars)
    return name


def parse_input_text(raw_text: str, source_name: str = "") -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//"))]
    if not lines:
        return rows

    first = lines[0].lower()
    delim = None
    if "	" in lines[0] or source_name.lower().endswith(".tsv"):
        delim = "	"
    elif source_name.lower().endswith(".csv") or "," in lines[0]:
        delim = ","
    elif ";" in lines[0] and "," not in lines[0]:
        delim = ";"
    elif "|" in lines[0] and "," not in lines[0]:
        delim = "|"

    has_header = False
    if delim:
        try:
            first_row = next(csv.reader([lines[0]], delimiter=delim))
            first_cols = [c.strip().lower() for c in first_row]
            if any(h in first_cols for h in ("company", "name", "company name", "organization", "firm")):
                has_header = True
        except Exception:
            pass
    elif any(lines[0].strip().lower() == h for h in ("company", "name", "company name")):
        has_header = True

    data_lines = lines[1:] if has_header else lines
    for line in data_lines:
        line = line.strip()
        if not line:
            continue
        if delim and delim in line:
            try:
                parts = next(csv.reader([line], delimiter=delim))
            except Exception:
                parts = [p.strip() for p in line.split(delim)]
        else:
            parts = re.split(r"	|\s*\|\s*|\s{2,}", line)

        parts = [p.strip() for p in parts]
        cname = clean_company_name(parts[0]) if parts else ""
        if not cname or cname.lower() in ("company", "name", "company name"):
            continue

        web = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
        car = parts[2].strip() if len(parts) > 2 and parts[2].strip() else None
        if web and not car and any(w in web.lower() for w in ("/careers", "/jobs", "career", "job")):
            car = web
            web = None
        rows.append({"company": cname, "website": web, "careers_url": car})
    return rows


def load_input(path: Optional[str], names: List[str]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    # 1. Check if unquoted filename with spaces was passed as --csv or positional
    if path and not os.path.exists(path) and names:
        combined = f"{path} {' '.join(names)}"
        if os.path.isfile(combined):
            path = combined
            names = []

    # 2. Check if all positional names joined form an existing file
    if not path and names:
        combined = " ".join(names)
        if os.path.isfile(combined) and not any(os.path.isfile(n) for n in names):
            path = combined
            names = []

    # 3. Read from path or stdin if path is specified
    if path:
        if path == "-":
            raw = sys.stdin.read()
            rows.extend(parse_input_text(raw, "<stdin>"))
        elif os.path.isfile(path):
            with open(path, "r", encoding="utf-8-sig", newline="") as fh:
                raw = fh.read()
            rows.extend(parse_input_text(raw, path))
        else:
            raise FileNotFoundError(f"No such file or directory: '{path}'")

    # 4. Handle remaining positional names
    for n in names:
        if not n or not n.strip():
            continue
        n_str = n.strip()
        if os.path.isfile(n_str):
            with open(n_str, "r", encoding="utf-8-sig", newline="") as fh:
                raw = fh.read()
            rows.extend(parse_input_text(raw, n_str))
        elif "," in n_str or "\n" in n_str:
            for item in re.split(r"[\n,]+", n_str):
                item_c = clean_company_name(item)
                if item_c and item_c.lower() not in ("company", "name"):
                    rows.append({"company": item_c, "website": None, "careers_url": None})
        else:
            cname = clean_company_name(n_str)
            if cname:
                rows.append({"company": cname, "website": None, "careers_url": None})

    # 5. Check stdin if nothing loaded and stdin has piped data
    if not rows and not path and not sys.stdin.isatty():
        try:
            raw = sys.stdin.read()
            if raw.strip():
                rows.extend(parse_input_text(raw, "<stdin>"))
        except Exception:
            pass

    # 6. De-duplicate preserving order
    seen, out = set(), []
    for r in rows:
        k = r["company"].lower()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


CSV_COLS = ["company", "ats", "status", "proof_tier", "career_site_url",
            "job_listing_url", "job_count", "page_signal_score",
            "company_careers_page", "evidence",
            "detected_via", "corroborated_by_careers_page", "website",
            "careers_url", "website_used", "website_guessed", "http_status",
            "api_proof_url", "fingerprint_urls"]


def page_check(fetcher: Fetcher, findings: List[Dict[str, Any]],
               company: str = "", verbose: bool = False) -> List[Dict[str, Any]]:
    """Open each reported portal URL and confirm it really is a job listing.

    This is the last step the user should care about: we have a URL, but does it
    show jobs?  The page is scored on job-card markup, links to individual
    postings, "open roles" headings, a stated count, show-more and pagination
    controls, and penalised for not-found wording.

    It upgrades, fills in a job count, and - in two narrow cases - demotes:

    * the rendered page is branded for a DIFFERENT company (identity_mismatch);
    * the URL bounced off its tenant onto the vendor's own marketing site
      (`appodeal.bamboohr.com/careers` 302s to `www.bamboohr.com`, so there is
      no portal there at all).  Only tier-2/3 findings, never an API proof.

    Otherwise it never rejects a finding, because 15 of the 27 vendors render
    their jobs client-side and their HTML scores 0 even for a live board - Ashby
    returns the same empty shell for `buena` and for a random string.  For those
    the vendor's own API already did the proving.
    """
    for f in findings:
        url = f.get("job_list_url") or f.get("url")
        if not url or f.get("status") not in ("VERIFIED", "PROBABLE", "UNVERIFIED"):
            continue
        vid = f.get("vendor_id")
        f["_company"] = company
        r = fetcher.request(url, max_bytes=3_000_000)
        sig = V.job_page_signals(r.get("body") or "")
        f["page_signals"] = sig
        f["page_http_status"] = r.get("status")
        # second identity pass on the rendered page: the probe may have seen a
        # redirect shell, while the final document states whose board this is
        if not f.get("identity_from_hint"):
            reason = identity_mismatch(f.get("_company") or "", url, r)
            if reason:
                f["status"] = "UNVERIFIED"
                f["identity_rejected"] = reason
                f["evidence"] = f"{f.get('evidence') or ''}  ||  {reason}".strip(" |")
                continue
        js_only = vid in V.JS_ONLY_VENDORS

        # A tenant that redirects to the vendor's own homepage is not a portal.
        # BambooHR does this for an account that does not exist, and the redirect
        # still returns HTTP 200 with a large, plausible-looking page.
        final_url = r.get("final_url") or url
        probe_host = (urlparse(url).hostname or "").lower()
        final_host = (urlparse(final_url).hostname or "").lower()
        tenant = probe_host.split(".")[0] if probe_host else ""
        bounced = (final_host and probe_host and final_host != probe_host
                   and len(tenant) >= 3 and tenant not in final_host)
        spec = V.VENDORS.get(vid) or {}

        # BambooHR answers 200 for an account that was switched off and sends it
        # to /settings/account/expired.php.  Same host, so the bounce test above
        # does not see it.
        expired = bool(re.search(r"/settings/account/expired", final_url, re.I))
        if expired and not V.page_confirms_listing(sig):
            f["status"] = "UNVERIFIED"
            f["page_note"] = "account expired"
            reason = (f"{final_host} redirects to its 'account expired' page - this "
                      f"{f.get('vendor')} account is switched off, so there are no jobs")
            f["evidence"] = f"{f.get('evidence') or ''}  ||  {reason}".strip(" |")
            if verbose:
                print(f"      x {f.get('vendor')}: {reason}", file=sys.stderr)
            continue

        if bounced and spec.get("tier") != 1 and not V.page_confirms_listing(sig):
            f["status"] = "UNVERIFIED"
            f["redirect_bounce"] = final_url
            reason = (f"the URL redirects to {final_host}, the vendor's own site - "
                      f"there is no {company or 'company'} portal at this address")
            f["evidence"] = f"{f.get('evidence') or ''}  ||  {reason}".strip(" |")
            if verbose:
                print(f"      x {f.get('vendor')}: {reason}", file=sys.stderr)
            continue

        if V.page_confirms_listing(sig):
            if f.get("status") != "VERIFIED":
                f["evidence"] = (f"{f.get('evidence') or ''}  ||  opened {url} and "
                                 f"confirmed a live job listing (signal score "
                                 f"{sig['score']}: {sig['why']})").strip(" |")
                f["status"] = "VERIFIED"
                f["via"] = "page_signals"
            if not f.get("job_count") and sig["jobs"]:
                f["job_count"] = sig["jobs"]
        elif js_only:
            f["page_note"] = ("page is rendered client-side, so HTML signals cannot "
                              "confirm or deny it; status rests on the vendor API")
        elif f.get("status") == "VERIFIED" and spec.get("tier") != 1:
            # Server-rendered vendor, page read fine, and it names no job at all:
            # an expired or auto-provisioned board.  BambooHR hands out
            # `<tenant>.bamboohr.com/careers` for any recognisable company name.
            f["status"] = "UNVERIFIED"
            f["page_note"] = (f"page fetched (HTTP {r.get('status')}) but shows no "
                              f"postings: {sig['why']}")
            f["evidence"] = (f"{f.get('evidence') or ''}  ||  opened {url}: no job "
                             f"postings on the page (score {sig['score']})").strip(" |")
        elif verbose:
            print(f"      ? {f.get('vendor')}: {url} scored {sig['score']} "
                  f"({sig['why']})", file=sys.stderr)
    return findings


def not_found_reason(r: Dict[str, Any]) -> str:
    """Why this company ended at NOT_FOUND, in one line.

    NOT_FOUND is the terminal answer, so it should say what was actually
    exhausted rather than leaving the cell blank.
    """
    withheld = [f for f in r.get("findings", [])
                if f.get("status") in ("PROBABLE", "UNVERIFIED") and not f.get("self_match")]
    flags = sorted({("--include-probable" if f.get("status") == "PROBABLE"
                     else "--include-unverified") for f in withheld})
    if r.get("careers_page_blocked"):
        base = "careers site is bot-walled (challenge page on every path tried)"
    elif not r.get("careers_page_found"):
        base = "no readable careers page found"
    else:
        base = "careers page read, no ATS marker on it"
    if withheld:
        names = ", ".join(sorted({f["vendor"] for f in withheld}))
        return (f"{base}; weaker signal only for {names} - no valid portal URL, "
                f"so NOT_FOUND (use {' / '.join(flags)} to see it)")
    return base + "; no ATS tenant matched any probe"


def write_outputs(results, prefix, keep=("VERIFIED",)) -> List[str]:
    keep = set(keep)
    rows = []
    for r in results:
        hits = [f for f in r["findings"] if reportable(f, keep)]
        if not hits:
            rows.append({"company": r["company"], "ats": "", "status": "NOT_FOUND",
                         "proof_tier": "", "career_site_url": "", "job_listing_url": "",
                         "job_count": "", "page_signal_score": "",
                         "company_careers_page": r.get("careers_page_found") or "",
                         "evidence": not_found_reason(r), "detected_via": "",
                         "corroborated_by_careers_page": "",
                         "website": r.get("website") or "",
                         "careers_url": r.get("careers_url") or "",
                         "website_used": r.get("website_used") or "",
                         "website_guessed": "yes" if r.get("website_guessed") else "no",
                         "http_status": r.get("careers_page_http_status") or "",
                         "api_proof_url": "", "fingerprint_urls": ""})
            continue
        for h in hits:
            rows.append({
                "company": r["company"], "ats": h["vendor"], "status": h["status"],
                "proof_tier": {1: "1 authoritative", 2: "2 corroborated",
                               3: "3 fingerprint-only"}.get(h.get("tier"), ""),
                "career_site_url": h.get("url") or "",
                "job_listing_url": h.get("job_list_url") or h.get("url") or "",
                "job_count": h.get("job_count") if h.get("job_count") is not None else "",
                "page_signal_score": (h.get("page_signals") or {}).get("score", ""),
                "company_careers_page": r.get("careers_page_found") or "",
                "evidence": (h.get("evidence") or "").replace("\n", " ")[:600],
                "detected_via": h.get("via") or "",
                "corroborated_by_careers_page": "yes" if h.get("fingerprint") else "no",
                "website": r.get("website") or "",
                "careers_url": r.get("careers_url") or "",
                "website_used": r.get("website_used") or "",
                "website_guessed": "yes" if r.get("website_guessed") else "no",
                "http_status": h.get("http_status") or "",
                "api_proof_url": h.get("api_url") or "",
                "fingerprint_urls": " | ".join(h.get("fingerprint_urls") or [])[:400],
            })
    written = []
    csv_path = prefix + "_results.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLS)
        w.writeheader()
        w.writerows(rows)
    written.append(csv_path)

    json_path = prefix + "_results.json"
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                   "scanner_version": VERSION,
                   "vendors_supported": len(V.VENDORS), "results": results},
                  fh, indent=2, ensure_ascii=False, default=str)
    written.append(json_path)

    found = [r for r in results if r["found"]]
    md = [f"# ATS scan - {len(results)} companies", "",
          f"Generated {time.strftime('%Y-%m-%d %H:%M')} by ats_scanner v{VERSION}", "",
          f"**{len(found)} / {len(results)}** companies matched at least one of the "
          f"{len(V.VENDORS)} supported ATS vendors.", "",
          "| Company | ATS | Status | Proof | Career site / job listing | Jobs |",
          "|---|---|---|---|---|---|"]
    for r in results:
        hits = [f for f in r["findings"] if reportable(f, keep)]
        if not hits:
            md.append(f"| {r['company']} | - | NOT_FOUND | - | - | - |")
        for h in hits:
            url = h.get("job_list_url") or h.get("url") or ""
            jc = h.get("job_count")
            ev = (h.get("evidence") or "").lower()
            if h.get("via") == "fingerprint":
                proof = "careers page link"
            elif "sitemap.xml" in ev:
                proof = "live jobs in sitemap"
            elif "api" in ev or "cxs" in ev:
                proof = "public API"
            elif "corroborated" in ev:
                proof = "probe + careers page"
            elif h.get("tier") == 2:
                proof = "probe + live jobs" if h.get("job_count") else "probe only"
            elif h.get("tier") == 3:
                proof = "careers page"
            else:
                proof = "probe (authoritative)"
            md.append(f"| {r['company']} | {h['vendor']} | {h['status']} | {proof} | "
                      f"[{url}]({url}) | {jc if jc is not None else ''} |")
    md += ["", "## The rule this report applies", "",
           "A company is reported against an ATS **only when there is a valid portal URL "
           "for it** - a page inside the ATS that belongs to that company and can be "
           "opened. Anything short of that is `NOT_FOUND`, with no weaker row kept "
           "alongside it.",
           "",
           "## What each status means", "",
           "- **VERIFIED** - proved, and a portal URL is attached. Either an authoritative "
           "vendor API answered (Greenhouse, Ashby, Workday, SmartRecruiters job counts), "
           "or the vendor served a real tenant page, or the company's own careers page "
           "links to that ATS URL.",
           "- **PROBABLE** - *withheld by default.* A tenant that exists but whose page "
           "could not be confirmed as a live job listing: an Avature `{slug}.avature.net` "
           "login redirect, a Workday career site with 0 open jobs, a custom-domain "
           "redirect. Shown only with `--include-probable`.",
           "- **UNVERIFIED** - *withheld by default.* A page exists under this company's "
           "name on the vendor's domain, but nothing proves the company uses the product. "
           "Shown only with `--include-unverified`. Workable, SmartRecruiters, Pinpoint "
           "and BambooHR auto-provision branded pages for any recognisable company name.",
           "- **SELF** - the company *is* the vendor of that product, so its own board is "
           "not a customer finding. Reported by default; hide with `--exclude-self-matches`.",
           "- **NOT_FOUND** - terminal. No ATS produced a valid portal URL for this "
           "company, so the scan stops here; the `evidence` column says what was "
           "exhausted. This does *not* prove the company uses no ATS: custom domains, "
           "opaque tenant ids (Zoho Recruit, Eploy, Bullhorn), bot-walled careers pages "
           "and JavaScript-only sites are invisible to a plain HTTP scan.",
           ""]
    md_path = prefix + "_hits.md"
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(md))
    written.append(md_path)
    return written


def print_console(results, quiet, keep=("VERIFIED",)) -> None:
    if quiet:
        return
    keep = set(keep)
    w = max([len(r["company"]) for r in results] + [7])
    print()
    print(f"{'COMPANY':<{w}}  {'ATS':<24} {'STATUS':<11} URL  [open jobs]")
    print("-" * (w + 104))
    for r in results:
        hits = [f for f in r["findings"] if reportable(f, keep)]
        if not hits:
            print(f"{r['company']:<{w}}  {'-':<24} {'NOT_FOUND':<11} -")
            continue
        for i, h in enumerate(hits):
            url = h.get("job_list_url") or h.get("url") or ""
            jc = h.get("job_count")
            print(f"{(r['company'] if i == 0 else ''):<{w}}  {h['vendor']:<24} "
                  f"{h['status']:<11} {url}" + (f"   [{jc}]" if jc else ""))
    print("-" * (w + 104))
    n = sum(1 for r in results if r["found"])
    v = sum(1 for r in results for f in r["findings"] if f["status"] == "VERIFIED")
    print(f"{n}/{len(results)} companies matched ({v} VERIFIED findings).")


# --------------------------------------------------------------------------- #
# selftest
# --------------------------------------------------------------------------- #

# (label, careers page URL, vendor that MUST be fingerprinted out of it)
# (label, careers page, vendor that MUST be fingerprinted out of it)
FINGERPRINT_CASES = [
    ("Palantir",   "https://www.palantir.com/careers/",          "lever"),
    ("Specsavers", "https://join.specsavers.com/ca/jobs",        "successfactors"),
    ("Bullhorn",   "https://www.bullhorn.com/careers/",          "workday"),
    ("Sqills",     "https://sqills.com/careers/",                "recruitee"),
    ("Stripe",     "https://stripe.com/jobs/search",             "greenhouse"),
    ("TCS",        "https://www.tcs.com/careers",                None),   # own portal
    ("Infosys",    "https://career.infosys.com/joblistings",     None),   # own portal
]


IDENTITY_CASES = [
    # (company, url whose path supplies the last identity check, page body, expect_mismatch)
    ("Woonstad Rotterdam", "https://jobs.lever.co/wr",
     "<title>World Relief</title><p>Careers at World Relief, an international NGO.</p>",
     True),
    ("Woonstad Rotterdam", "https://wr.join.com/",
     "<title>Jobs at W+R GmbH | JOIN</title><p>Offene Stellen bei W+R.</p>", True),
    ("Palantir", "https://jobs.lever.co/palantir",
     "<title>Palantir Careers</title><p>Build software at Palantir.</p>", False),
    ("Ramp", "https://jobs.ashbyhq.com/ramp",
     "<title>Ramp Jobs</title>", False),
    ("Südzucker", "https://jobs.example/suedzucker",
     "<title>Suedzucker AG careers</title>", False),
    ("TCS", "https://job-boards.greenhouse.io/tcs",
     "<title>Tata Consultancy Services</title>", False),
    ("Sqills", "https://sqills.recruitee.com/",
     "<title>Something else entirely</title>", False),   # slug supplied by URL
    ("Türk Telekom", "https://jobs.example/turktelekom",
     "<title>Turk Telekom kariyer</title>", False),      # umlaut -> dropped
    ("Müller", "https://jobs.example/mueller",
     "<title>Careers at Muller GmbH</title>", False),    # umlaut -> transliterated
    ("Accenture", "https://jobs.example/acn",
     "<title>Careers at a completely different firm</title>", True),
    # ---- branded-title cases.  The board's <title> states whose board it is,
    # and that outranks the slug/URL/body test: teamtailor.join.com is full of
    # the word "teamtailor" yet belongs to InVisions.
    ("Teamtailor", "https://teamtailor.join.com/",
     "<title>Jobs at InVisions | JOIN</title>" + "<p>teamtailor</p>" * 40, True),
    ("Babbel", "https://babbel.join.com/",
     "<title>Jobs at Babbel | JOIN</title>", False),
    ("KONUX", "https://konux.join.com/",
     "<title>Jobs at KONUX GmbH | JOIN</title>", False),
    ("Lightspeed", "https://jobs.lever.co/lightspeed",
     "<title>Jobs at Lightspeed Commerce Inc. | Lever</title>", False),
    ("Polestar", "https://polestar.teamtailor.com/",
     "<title>Breaking conventions - Polestar</title>", False),
    ("Südzucker", "https://x.example/suedzucker-gruppe",
     "<title>Stellenangebote bei Südzucker AG</title>", False),
    ("Qonto", "https://jobs.lever.co/qonto",
     "<title>Qonto</title>", False),        # unbranded title -> fall through to URL
]


# (company name, vendor id, is_the_vendor_itself)
SELF_MATCH_CASES = [
    ("Personio",                  "personio",     True),
    ("Personio GmbH",             "personio",     True),
    ("softgarden",                "softgarden",   True),
    ("Flatchr",                   "flatchr",      True),
    ("Tellent Recruitee",         "recruitee",    True),   # renamed vendor
    ("Greenhouse IO",             "greenhouse",   True),
    ("JOIN GmbH",                 "join",         True),
    ("Workday",                   "workday",      True),
    # ... and companies that merely use the product, or whose name just starts
    # with the same letters - these must NOT be suppressed
    ("Sage",                      "personio",     False),
    ("Bosch",                     "avature",      False),
    ("Stripe",                    "greenhouse",   False),
    ("Deliveroo",                 "greenhouse",   False),
    ("Softgarden Software",       "softgarden",   False),
    ("Global Workday Services",   "workday",      False),
    ("Workday Partners Ltd",      "workday",      False),
    ("Recruitee Solutions",       "recruitee",    False),
    ("Personio Consulting",       "personio",     False),
]


# (label, finding, include_probable, expect_reported)
REPORTABLE_CASES = [
    ("verified + portal url", {"status": "VERIFIED", "vendor": "Greenhouse",
                               "url": "https://job-boards.greenhouse.io/stripe"}, False, True),
    ("verified, job_listing_url only", {"status": "VERIFIED", "vendor": "Workday",
                                        "job_list_url": "https://x.wd5.myworkdayjobs.com/Y"}, False, True),
    ("verified but NO url", {"status": "VERIFIED", "vendor": "Greenhouse",
                             "url": None}, False, False),
    ("verified, url is not a url", {"status": "VERIFIED", "vendor": "Recruitee",
                                    "url": "sqills.recruitee.com"}, False, False),
    ("probable (avature login)", {"status": "PROBABLE", "vendor": "Avature",
                                  "url": "https://specsavers.avature.net/"}, False, False),
    ("probable, opted in", {"status": "PROBABLE", "vendor": "Avature",
                            "url": "https://specsavers.avature.net/"}, True, True),
    ("probable with no url", {"status": "PROBABLE", "vendor": "Workday",
                              "url": None}, True, False),
    ("unverified stub", {"status": "UNVERIFIED", "vendor": "SmartRecruiters",
                         "url": "https://careers.smartrecruiters.com/klarna"}, False, False),
    ("not_found", {"status": "NOT_FOUND", "vendor": "Lever", "url": None}, False, False),
    ("blocked", {"status": "BLOCKED", "vendor": "Homerun",
                 "url": "https://x.homerun.co/"}, True, False),
]


def selftest_reportable() -> int:
    """Offline check of the reporting rule.

    The scan answers with either a portal URL or NOT_FOUND.  A finding that
    cannot produce a URL is not reportable whatever its status, and PROBABLE is
    withheld unless the caller opts in.
    """
    print("reportable selftest - a finding is reported only if it carries a valid "
          "portal URL\nfor that company; everything else ends at NOT_FOUND.\n")

    class A:                                  # minimal args stand-in
        def __init__(self, probable):
            self.include_probable = probable
            self.include_unverified = False

    failures = 0
    for label, finding, probable, expect in REPORTABLE_CASES:
        got = reportable(finding, report_statuses(A(probable)))
        ok = got == expect
        if not ok:
            failures += 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label:<32} "
              f"{'--include-probable' if probable else 'default         '} "
              f"-> {'report' if got else 'withhold'} (want "
              f"{'report' if expect else 'withhold'})")
    print()
    if failures:
        print(f"{failures} reportable case(s) FAILED - the reporting rule has drifted.")
    else:
        print("All reportable cases passed.")
    return 1 if failures else 0


def selftest_self_match() -> int:
    """Offline check of the vendor self-match filter.

    Personio recruits on Personio, softgarden on softgarden and Flatchr on
    Flatchr.  Those rows are true but they made the output read like a list of
    ATS portals instead of a list of customers, which is not what the scan is
    for.  They are reported by default with status SELF and hidden by --exclude-self-matches.
    """
    print("self-match selftest - a vendor's use of its own product must not be "
          "reported as a\ncustomer finding, and a company that merely starts "
          "with the same letters must not\nbe suppressed by mistake.\n")
    failures = 0
    for company, vid, expect in SELF_MATCH_CASES:
        got = is_vendor_itself(company, vid)
        ok = got == expect
        if not ok:
            failures += 1
        want = "suppress" if expect else "keep    "
        print(f"  {'ok  ' if ok else 'FAIL'} {company:<26} {vid:<13} "
              f"want {want} got {'suppress' if got else 'keep'}")
    print()
    if failures:
        print(f"{failures} self-match case(s) FAILED.")
    else:
        print("All self-match cases passed.")
    return 1 if failures else 0


# (label, inner HTML, must_look_like_a_job_listing)
# Offline fixtures for job_page_signals().  Calibrated against 25 live tenant
# URLs: all 8 server-rendered boards pass, all 10 bogus tenants score <= 4.
PAGE_SIGNAL_CASES = [
    ("lever-40-cards", "<h2>Open positions</h2>" + "".join(
        f'<div class="posting"><a class="posting-title" href="/jobs/{i}-engineer">'
        f'<h5 class="posting-name">Engineer {i}</h5>'
        f'<span class="posting-category">Berlin</span></a>'
        f'<a class="posting-apply" href="/jobs/{i}-engineer/apply">Apply</a></div>'
        for i in range(40)), True),
    ("greenhouse-jsonld", '<script type="application/ld+json">['
        + ",".join('{"@type":"JobPosting"}' for _ in range(3)) + ']</script>'
        + "<h1>Job openings</h1>" + "".join(
        f'<a class="opening" href="/jobs/10{i}-role">R{i}</a>' for i in range(8)), True),
    ("recruitee-cards", "<h2>Vacancies</h2>" + "".join(
        f'<div class="job-card"><a class="job-title" href="/jobs/{i}-dev">Job {i}</a></div>'
        for i in range(15)), True),
    ("recruitee-o-links", "<h2>Vacancies</h2>" + "".join(
        f'<div class="job-card"><a class="job-title" href="/o/senior-dev-{i}">Job {i}</a></div>'
        for i in range(15)), True),
    ("teamtailor-cards", "<h2>Current openings</h2>" + "".join(
        f'<li class="job-listing-item"><a href="/jobs/10{i}-role">Role</a></li>'
        for i in range(13)), True),
    ("dutch-vacatures", "<h2>Vacatures</h2>" + "".join(
        f'<article class="vacature-card"><a href="/vacatures/10{i}">V{i}</a></article>'
        for i in range(9)), True),
    ("showmore-paging", "<h2>Current openings</h2>" + "".join(
        f'<div class="job-item"><a href="/jobs/10{i}-role">J{i}</a></div>' for i in range(6))
        + '<button>Show more</button><nav class="pagination" '
          'aria-label="Next page">Page 1 of 4</nav>', True),
    ("stated-count", "<p>We have 12 open roles</p>" + "".join(
        f'<div class="job-row"><a href="/jobs/10{i}-role">J{i}</a></div>' for i in range(4)), True),
    # ---- must score BELOW the threshold ----
    # The two that matter most: an SPA shell and a JS bundle.  Generic employment
    # vocabulary (apply / remote / full-time / hybrid) ships inside the JavaScript
    # of every Ashby and Workday page, so scoring on it produced false positives.
    ("ashby-spa-shell", '<div id="root"></div><script src="/assets/main.js"></script>'
        '<script>var t={apply:"Apply now",remote:"Remote",fullTime:"Full-time",'
        'hybrid:"Hybrid",position:"position",job:"job",opening:"opening",'
        'requisition:"requisition",vacancy:"vacancy"};</script>', False),
    ("js-bundle-only", '<div id="app"></div><script>"use strict";function a(){return{'
        'apply:1,remote:2,position:3,opening:4,role:5,vacancy:6,posting:7,'
        'requisition:8,job:9}}</script>', False),
    # An expired/auto-provisioned BambooHR board.  BambooHR answers 200 for any
    # recognisable company name and serves one ~101 kB template whose single giant
    # class attribute is a list of product feature flags.  Every token appears
    # once, so none of them is a job card - and the page names no posting at all.
    # This fixture promoted Lightspeed and Forto to VERIFIED before it was caught.
    ("bamboohr-feature-flags",
     '<h1 id="openingList">Current Openings</h1><h2>current openings</h2>'
     '<div class="job_share_image ats_job_board_refresh_btn perf_joborg_skills '
     'zip_recruiter_boosted_jobs ats_premium_job_boards_seek client_position_sync '
     'add_job_openings_for_migration job_codes_in_settings ats_linked_job_setup '
     'bhr_job_codes_linking job_code_recommendation fam_careertrack_level_joborg '
     'perf_joborg_bulkupload ats_separate_job_boards selfservejobopenings '
     'ats_job_opening_tabs job_org_v1 perf_joborg_archive calculate_payroll_job_costing '
     'update_journal_entry_with_job_costing ats_default_disposition_data_toggle '
     'ats_premium_job_boards_linkedin ats_premium_job_boards_seek_applybutton">'
     '<p>Take a look at the current openings.</p></div>', False),
    ("not-found-404", "<h1>Page not found</h1><p>This page doesn't exist.</p>", False),
    ("no-jobs", "<h1>Careers</h1><p>There are no jobs available. No open positions.</p>", False),
    ("marketing-page", "<h1>Acme - Build the future</h1><p>See our jobs page.</p>"
        '<a href="/careers">Careers</a>', False),
    ("one-nav-link", '<h1>Acme</h1><nav><a href="/jobs/">Jobs</a>'
        '<a href="/about">About</a></nav>', False),
    ("empty", "", False),
]


def selftest_page_signals() -> int:
    """Offline check of job_page_signals(), the content test behind --page-check.

    This is the "open the URL and confirm it shows jobs" step.  It must pass on
    every server-rendered board and stay silent on an SPA shell, a JS bundle and
    a 404 - otherwise a tenant that merely exists would be reported as a live
    job listing.
    """
    print("page-signal selftest - does the rendered HTML actually look like a job "
          "listing?\nCalibrated on 25 live tenant URLs: 8/8 server-rendered boards "
          f"pass, 10/10 bogus\ntenants score below the threshold of "
          f"{V.JOB_SIGNAL_THRESHOLD}.\n")
    failures = 0
    for label, inner, expect in PAGE_SIGNAL_CASES:
        body = f"<html><body>{inner}</body></html>" if inner else ""
        sig = V.job_page_signals(body)
        got = V.page_confirms_listing(sig)
        ok = got == expect
        failures += not ok
        want = "listing" if expect else "not-a-listing"
        print(f"  {'ok  ' if ok else 'FAIL'} {label:<20} score={sig['score']:>3} "
              f"jobs={sig['jobs']:>4}  want {want:<14} got "
              f"{'listing' if got else 'not-a-listing'}")
    print()
    if failures:
        print(f"{failures} page-signal case(s) FAILED - the scorer has drifted.")
    else:
        print("All page-signal cases passed.")
    return 1 if failures else 0


# (derived website, candidates that MUST be generated, substrings that MUST NOT appear)
# A derived website is a redirect target, not a site root.  Appending a careers
# path to it verbatim produced `https://www.salesforce.com/in/?ir=1/careers` -
# the path lands inside the query string, where it is not a path at all.
URL_CONSTRUCTION_CASES = [
    ("https://www.salesforce.com/in/?ir=1",
     ["https://www.salesforce.com/careers", "https://www.salesforce.com/in/careers"],
     ["?ir=1/careers", "in/?ir=1"]),
    ("https://www.salesforce.com/?bc=OTH",
     ["https://www.salesforce.com/careers"], ["?bc=OTH/careers"]),
    ("https://rows.com/product",
     ["https://rows.com/careers", "https://rows.com/product/careers"], []),
    ("https://stripe.com/in",
     ["https://stripe.com/careers"], []),
    ("https://www.nvidia.com/en-in/",
     ["https://www.nvidia.com/careers", "https://www.nvidia.com/en-in/careers"], []),
    ("https://example.com/jobs#open-roles",
     ["https://example.com/careers"], ["#open-roles/careers"]),
    ("https://stripe.com", ["https://stripe.com/careers"], []),
    ("", [], []),
]


# (company name, vendor, tenant slug that really exists)
# Every row came from company_ATS_seed.csv and was confirmed live.  This is the
# regression test for "did we learn the portal naming pattern".
SLUG_CASES = [
    # the plain shapes - these must never regress
    ("Cargo.one",           "ashby",           "cargo-one"),
    ("Incident.io",         "ashby",           "incident"),
    ("Brain Rocket",        "bamboohr",        "brainrocket"),
    ("Astroscale",          "bamboohr",        "astroscale"),
    ("Smartly.io",          "greenhouse",      "smartlyio"),
    ("Delivery Hero",       "smartrecruiters", "deliveryhero"),
    ("Prysmian Group",      "workday",         "prysmiangroup"),
    ("NXP Semiconductors",  "workday",         "nxp"),
    ("InnoGames",           "lever",           "innogames"),
    ("Jobandtalent",        "lever",           "jobandtalent"),
    # the shapes that were missing until the seed file taught us them
    ("Rows",                "ashby",           "rowspace"),
    ("Ecosia",              "ashby",           "ecosia.org"),
    ("Mistral AI",          "ashby",           "mistral.ai"),
    ("Pitch",               "workable",        "pitch-software"),
    ("Backbase",            "greenhouse",      "workatbackbase"),
    ("Bol.com",             "greenhouse",      "bolcomen"),
    ("Scalable Capital",    "smartrecruiters", "scalablegmbh"),
    ("Sonio",               "personio",        "sonio-ag"),
    ("Leonardo",            "workday",         "leonardocompany"),
]

# Slugs that cannot be derived from a name, recorded so the limit stays honest
# instead of being rediscovered later.
SLUG_UNREACHABLE = [
    ("BambooHR",       "greenhouse", "bamboohr17",      "an arbitrary number appended"),
    ("QuantumDiam",    "personio",   "quantum-diamond", "an abbreviation expanded"),
    ("Wolters Kluwer", "workday",    "wk",              "two-letter initials, excluded on purpose"),
    ("Teamtailor",     "teamtailor", "career",          "the vendor's own subdomain"),
]


def selftest_slugs() -> int:
    """Offline check of tenant-slug derivation against real portal slugs.

    `probe_vendor` stops at the first hit, so what matters is not how many
    slugs are generated but whether the right one is inside the probe budget.
    Each case is therefore checked against the same list the scan would use for
    that vendor - the extended one for EXTENDED_SLUG_VENDORS, the core one
    otherwise.
    """
    print("slug-derivation selftest - can the right tenant id be generated from the "
          "company\nname alone, within the probe budget?  Cases are real slugs from\n"
          "company_ATS_seed.csv.\n")
    failures = 0
    for company, vid, want in SLUG_CASES:
        ext = vid in EXTENDED_SLUG_VENDORS
        cands = [c.lower() for c, _ in drop_impossible_slugs(
            vid, slug_candidates(company, extended=ext), company)]
        cap = max(args_probe_budget_default(), EXTENDED_PROBE_BUDGET) if ext \
            else args_probe_budget_default()
        # position matters: a slug past the probe budget never gets tried
        pos = cands.index(want.lower()) + 1 if want.lower() in cands else None
        ok = pos is not None and pos <= cap
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {company:<20}{vid:<16}{want:<20}"
              f"{'pos '+str(pos) if pos else 'ABSENT':<8}"
              f"{'' if ext else '(core list)'}")
    print()
    for company, vid, slug, why in SLUG_UNREACHABLE:
        ext = vid in EXTENDED_SLUG_VENDORS
        cands = [c.lower() for c, _ in slug_candidates(company, extended=ext)]
        flag = "UNEXPECTEDLY DERIVED" if slug.lower() in cands else "not derived"
        print(f"  known limit  {company:<20}{vid:<16}{slug:<20}{flag:<22}{why}")
    print()
    if failures:
        print(f"{failures} slug case(s) FAILED.")
    else:
        print(f"All {len(SLUG_CASES)} slug cases derived within the probe budget.")
    return 1 if failures else 0


def args_probe_budget_default() -> int:
    """The shipped --probe-budget default, without building the whole parser."""
    m = re.search(r'--probe-budget"?,\s*type=int,\s*default=(\d+)',
                  open(os.path.abspath(__file__), encoding="utf-8").read())
    return int(m.group(1)) if m else 24


LISTING_BODY = ("<h2>Open positions</h2>" + "".join(
    '<div class="job-card"><a class="job-card__link" href="/jobs/e%d">'
    '<h3 class="job-card__heading">Engineer %d</h3>'
    '<span class="job-card__location">Berlin</span></a></div>' % (i, i)
    for i in range(12)))
PLAIN_BODY = "<html><body>" + "Company news and thoughts about hiring. " * 60 + "</body></html>"

# (name, vendor, proof_url, found_on, body, expected portal)
CUSTOM_DOMAIN_PORTAL_CASES = [
    ("spendesk-custom-domain", "teamtailor", "https://www.teamtailor.com/",
     "https://career.spendesk.com/jobs", LISTING_BODY,
     "https://career.spendesk.com/jobs"),
    ("real-tenant-untouched", "teamtailor", "https://polestar.teamtailor.com/",
     "https://www.polestar.com/careers", LISTING_BODY, None),
    ("mention-is-not-a-board", "teamtailor", "https://www.teamtailor.com/",
     "https://blog.spendesk.com/hiring", PLAIN_BODY, None),
    ("found-on-vendor-itself", "teamtailor", "https://www.teamtailor.com/",
     "https://www.teamtailor.com/customers", LISTING_BODY, None),
    ("greenhouse-custom-domain", "greenhouse", "https://www.greenhouse.io/",
     "https://jobs.acme.example/", LISTING_BODY, "https://jobs.acme.example/"),
    ("page-not-collected", "teamtailor", "https://www.teamtailor.com/",
     "https://career.nowhere.example/jobs", LISTING_BODY, None),
]


# (name, vendor, company, slug to watch, tenant-in-hostname?)
# For a hostname vendor the dotted slug must be dropped - it cannot resolve.
# For a path vendor it must survive - ecosia.org and mistral.ai are real slugs.
BUDGET_SEMANTICS_CASES = [
    ("personio-hostname", "personio", "Sonio", "sonio.org", True),
    ("teamtailor-hostname", "teamtailor", "Virtuagym", "virtuagym.com", True),
    ("bamboohr-hostname", "bamboohr", "Astroscale", "astroscale.io", True),
    ("workday-hostname", "workday", "Zalando", "zalando.com", True),
    ("ashby-path", "ashby", "Ecosia", "ecosia.org", False),
    ("greenhouse-path", "greenhouse", "Bol.com", "bolcom.com", False),
    ("lever-path", "lever", "InnoGames", "innogames.io", False),
    ("workable-path", "workable", "Pitch", "pitch-software", False),
]

# The regression this locks: a two-request-per-slug vendor used to get half the
# slug coverage, because the budget counted requests.  Sonio's real Personio
# tenant is slug 24 of 44; at 2 requests each it was never tried.
SLUG_DEPTH_CASES = [("Sonio", "personio", "sonio-ag", 20),
                    ("Ecosia", "ashby", "ecosia.org", 24),
                    ("Mistral AI", "ashby", "mistral.ai", 24),
                    ("Backbase", "greenhouse", "workatbackbase", 24),
                    ("Bol.com", "greenhouse", "bolcomen", 30)]


def selftest_budget_semantics() -> int:
    """Offline check that the probe budget buys slugs, not requests."""
    print("Probe-budget semantics (the budget counts slugs; dotted slugs are "
          "dropped where they cannot resolve):")
    fails = 0
    for name, vid, company, watch, in_host in BUDGET_SEMANTICS_CASES:
        got = slug_lives_in_hostname(vid, company)
        generated = [c.lower() for c, _ in slug_candidates(company, extended=True)]
        kept = [c.lower() for c, _ in drop_impossible_slugs(
            vid, slug_candidates(company, extended=True), company)]
        # the watched slug must be one we actually generate, or the case is vacuous
        ok = (got == in_host) and (watch.lower() in generated) and \
             ((watch.lower() not in kept) if in_host else (watch.lower() in kept))
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:22s} hostname={str(got):5s} "
              f"'{watch}' {'dropped' if watch.lower() not in kept else 'kept'}")
    for company, vid, slug, depth in SLUG_DEPTH_CASES:
        ext = vid in EXTENDED_SLUG_VENDORS
        cap = max(args_probe_budget_default(), EXTENDED_PROBE_BUDGET) if ext \
            else args_probe_budget_default()
        kept = [c.lower() for c, _ in drop_impossible_slugs(
            vid, slug_candidates(company, extended=ext), company)]
        pos = kept.index(slug.lower()) + 1 if slug.lower() in kept else -1
        ok = 0 < pos <= min(depth, cap)
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {company:12s} {vid:10s} '{slug}' "
              f"reachable at slug {pos} (budget {cap})")
    if fails:
        print(f"{fails} budget-semantics case(s) FAILED.")
        return 1
    n = len(BUDGET_SEMANTICS_CASES) + len(SLUG_DEPTH_CASES)
    print(f"All {n} probe-budget cases passed.\n")
    return 0


def selftest_custom_domain_portals() -> int:
    """Offline check that a "Powered by <Vendor>" link never becomes the answer.

    Every hosted board ends with a link to the vendor's own marketing site, so
    that link is the most common proof pattern there is - and the least useful
    URL.  When the page carrying it is a real job listing on the company's own
    domain, that page *is* the portal (career.spendesk.com is Teamtailor);
    otherwise nothing may be substituted.
    """
    print("Custom-domain portal cases (vendor-root proof must not be reported "
          "as the portal):")
    fails = 0
    for name, vid, proof_url, found_on, body, expect in CUSTOM_DOMAIN_PORTAL_CASES:
        pages = [{"url": found_on, "final_url": found_on, "status": 200,
                  "body": body}] if name != "page-not-collected" else []
        got = portal_from_proof(vid, {"url": proof_url, "found_on": found_on}, pages)
        ok = (got or None) == expect
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {name:24s} {vid:15s} "
              f"-> {got or 'None':38s} expected {expect or 'None'}")
        assert proof_is_vendor_root(vid, proof_url) == (
            urlparse(proof_url).hostname in ("www.teamtailor.com", "teamtailor.com",
                                             "www.greenhouse.io", "greenhouse.io"))
    if fails:
        print(f"{fails} custom-domain portal case(s) FAILED.")
        return 1
    print(f"All {len(CUSTOM_DOMAIN_PORTAL_CASES)} custom-domain portal cases passed.\n")
    return 0


def selftest_url_construction() -> int:
    """Offline check that guessed careers URLs are well-formed.

    Also enforces the invariant that mattered: no candidate may carry a query
    string or a fragment, because a path appended after `?` is not a path.
    """
    print("url-construction selftest - a derived website is a redirect target, so a "
          "careers path\nmust be joined to the site root, not pasted onto the end of "
          "it.\n")
    failures = 0
    for base, must, must_not in URL_CONSTRUCTION_CASES:
        got = careers_path_candidates(base)
        problems = [f"missing {u}" for u in must if u not in got]
        problems += [f"forbidden substring {t!r}" for t in must_not
                     if any(t in u for u in got)]
        problems += [f"query/fragment leaked into {u}" for u in got
                     if "?" in u or "#" in u]
        ok = not problems
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'} {base or '(empty)':<38} "
              f"{len(got):>2} candidate(s)")
        for pr in problems:
            print(f"         {pr}")
    print()
    if failures:
        print(f"{failures} url-construction case(s) FAILED.")
    else:
        print("All url-construction cases passed.")
    return 1 if failures else 0


def selftest_identity() -> int:
    """Offline check of the tenant-identity guard.

    Every subdomain ATS answers HTTP 200 for a tenant that merely *exists*, so a
    200 alone cannot prove whose board it is.  A slug derived from a company
    name can collide with an unrelated tenant: "Woonstad Rotterdam" -> "wr"
    resolved to World Relief's Lever board and W+R GmbH's JOIN page, and both
    were reported VERIFIED before this guard existed.
    """
    print("identity selftest - a tenant that returns 200 must still look like the "
          "company we\nsearched for, otherwise a derived slug has collided with "
          "someone else's board.\n")
    failures = 0
    for company, url, body, expect in IDENTITY_CASES:
        reason = identity_mismatch(company, url, {"body": body})
        got = reason is not None
        ok = got == expect
        if not ok:
            failures += 1
        slug = url.rstrip("/").split("/")[-1].split("?")[0]
        want = "reject" if expect else "accept"
        print(f"  {'ok  ' if ok else 'FAIL'} {company:<20} slug {slug:<14} "
              f"want {want:<7} got {'reject' if got else 'accept'}")
        if not ok and reason:
            print(f"       {reason[:100]}")
    print()
    if failures:
        print(f"{failures} identity case(s) FAILED - the guard has drifted.")
    else:
        print("All identity cases passed.")
    return 1 if failures else 0


def selftest_fingerprints(fetcher) -> int:
    """Validate Engine B on real customer careers pages: does the scanner
    extract the right ATS (and the right tenant URL) out of the HTML?"""
    print("ats_scanner fingerprint selftest - reading real careers pages and checking "
          "that the\ncorrect ATS is extracted.  This is what tier-3 vendors "
          "(Zoho Recruit, Eploy, Bullhorn)\nand every custom-domain customer depend on.\n")
    failures = 0
    for label, url, expect in FINGERPRINT_CASES:
        pages = [{"url": url, "origin": "selftest"}]
        r = fetcher.request(url)
        pages[0].update(status=r.get("status"), body=r.get("body") or "",
                        final_url=r.get("final_url"))
        if r.get("status") != 200:
            print(f"  {label:<12} {url[:52]:<54} SKIP HTTP {r.get('status')}")
            continue
        fp = fingerprint_pages(pages)
        got = {vid: d for vid, d in fp.items() if d.get("proof_count")}
        if expect is None:
            # a company that runs its own portal should produce nothing
            ok = not got
            print(f"  {label:<12} {('ok  ' if ok else 'FAIL')} expect no ATS      "
                  f"got {sorted(got) or '-'}")
            if not ok:
                failures += 1
                for vid in sorted(got):
                    print(f"               FALSE POSITIVE {vid}: "
                          f"{[m['matched'][:70] for m in got[vid]['proof'][:2]]}")
            continue
        ok = expect in got
        if not ok:
            failures += 1
        urls = got.get(expect, {}).get("urls", [])
        print(f"  {label:<12} {('ok  ' if ok else 'FAIL')} expect {expect:<16} "
              f"got {sorted(got) or '-'}")
        if urls:
            print(f"               url: {urls[0][:96]}")
        elif ok:
            samples = got[expect].get("proof") or got[expect].get("hints") or []
            m = samples[0]["matched"][:60] if samples else "(marker)"
            print(f"               marker only, no URL to harvest: {m!r} - "
                  "Engine A supplies the URL")
        if not ok and got:
            print(f"               (matched {sorted(got)} instead)")
    print()
    if failures:
        print(f"{failures} fingerprint case(s) FAILED.")
        return 1
    print("All fingerprint cases passed.")
    return 0

def selftest(fetcher, args) -> int:
    print("ats_scanner selftest - validating each vendor rule against a known-real "
          "tenant and a known-fake one.\nThe 'miss' rows are false-positive controls; "
          "they are the ones that matter.\n")
    failures = 0
    skipped = 0
    for vid, slug, expect in V.CALIBRATION:
        spec = V.VENDORS[vid]
        ctx = {"company": slug, "website": None, "careers_url": None, "domain": ""}
        # Workday's search space is (wd-number x site-id), handled by a dedicated
        # searcher in the real scan path - exercise that same path here instead of
        # the placeholder probe, so the selftest tests what actually runs.
        if vid == "workday":
            best = V.run_workday_search(fetcher, slug, budget=max(args.probe_budget, 48))
            verdict, ev = best["status"], best.get("evidence", "")
            if verdict == "BLOCKED":
                print(f"  {vid:<16} {slug:<22} SKIP BLOCKED     {ev[:76]}")
                skipped += 1
                continue
            ok = (verdict in ("VERIFIED", "PROBABLE")) if expect == "hit" \
                else (verdict in ("MISS", "UNVERIFIED", "NOT_FOUND"))
            if not ok:
                failures += 1
            print(f"  {vid:<16} {slug:<22} {'ok  ' if ok else 'FAIL'} {verdict:<11} {ev[:76]}")
            continue
        try:
            probes = [p for p in (spec["build"](slug, slug, ctx) or [])
                      if p.request_url][: args.probe_budget]
        except Exception as e:
            print(f"  {vid:<16} {slug:<22} BUILD-ERROR {e}")
            failures += 1
            continue
        if not probes:
            if expect == "miss":
                print(f"  {vid:<16} {slug:<22} ok   NOT_PROBEABLE (tier "
                      f"{spec['tier']}, fingerprint-only)")
                continue
            print(f"  {vid:<16} {slug:<22} FAIL not probeable but a hit was expected")
            failures += 1
            continue
        best, rank = None, -1
        for p in probes:
            r = run_probe(fetcher, p)
            rk = STATUS_RANK.get(r["status"], 0)
            if rk > rank:
                best, rank = r, rk
            if r["status"] in ("VERIFIED", "PROBABLE", "BLOCKED"):
                break
        verdict, ev = best["status"], best.get("evidence", "")
        if verdict == "BLOCKED":
            # the vendor refused this network (rate limit, WAF, IP block); that is
            # an environment problem, not a rule problem
            print(f"  {vid:<16} {slug:<22} SKIP BLOCKED     {ev[:76]}")
            skipped += 1
            continue
        if expect == "hit":
            ok = verdict in ("VERIFIED", "PROBABLE")
        elif expect == "soft":
            # PROBABLE is the honest answer here: the tenant exists but the URL we
            # can hand back is not a public job listing
            ok = verdict in ("PROBABLE", "MISS", "UNVERIFIED", "NOT_FOUND")
        else:
            ok = verdict in ("MISS", "UNVERIFIED", "NOT_FOUND")
        if not ok:
            failures += 1
        print(f"  {vid:<16} {slug:<22} {'ok  ' if ok else 'FAIL'} {verdict:<11} {ev[:76]}")
    print()
    if skipped:
        print(f"{skipped} case(s) SKIPPED because the vendor blocked this network "
              "(rate limit / WAF / IP block) - re-run later or from another IP.")
    if failures:
        print(f"{failures} calibration case(s) FAILED - vendor behaviour has drifted; "
              "update the rule in vendors.py.")
        return 1
    print("All calibration cases passed.")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

PATTERNS = {
    "greenhouse": "boards-api.greenhouse.io/v1/boards/{slug}/jobs -> job-boards.greenhouse.io/{slug}",
    "lever": "jobs.lever.co/{slug}",
    "ashby": "api.ashbyhq.com/posting-api/job-board/{slug} -> jobs.ashbyhq.com/{slug}",
    "workday": "{slug}.wd{N}.myworkdayjobs.com/{site}  (POST /wday/cxs/{slug}/{site}/jobs)",
    "workable": "apply.workable.com/{slug}/  (+ /api/v1/widget/accounts/{slug})",
    "bamboohr": "{slug}.bamboohr.com/careers  (+ /careers/list)",
    "recruitee": "{slug}.recruitee.com/",
    "personio": "{slug}.jobs.personio.de|com/  (+ /xml job feed)",
    "smartrecruiters": "careers.smartrecruiters.com/{CompanyId}  (+ postings API)",
    "teamtailor": "{slug}.teamtailor.com/",
    "homerun": "{slug}.homerun.co/",
    "successfactors": "{slug}.jobs.sap.com | {slug}.jobs.successfactors.com|.eu  (DNS proves it)",
    "taleo": "{slug}.taleo.net/careersection/{n}/jobsearch.ftl  (DNS proves it)",
    "icims": "careers-{slug}.icims.com/jobs  (404 'gone: host' == no tenant)",
    "softgarden": "{slug}.softgarden.io/ | {slug}.career.softgarden.de/",
    "dvinci": "{slug}.dvinci-easy.com/  (DNS proves it)",
    "flatchr": "{slug}.flatchr.io/",
    "taleez": "{slug}.taleez.com/  (also jobs.{company}.com)",
    "beetween": "{slug}.betheen.com/  |  app.beetween.com/{slug}/recrutement (JS-only)",
    "pinpoint": "{slug}.pinpointhq.com/",
    "zoho": "recruit.zohopublic.com/careers/{opaque-id}",
    "eploy": "embedded in the company's own domain (/vacancies, vacancyId=)",
    "join": "{slug}.join.com/ -> join.com/companies/{slug}",
    "coveto": "{slug}.coveto.fr/  (DNS proves it)",
    "rexx": "{slug}.rexx-systems.com/  (DNS proves it)",
    "bullhorn": "(public-)rest{n}.bullhornstaffing.com/rest-services/{corpToken}/",
    "avature": "{slug}.avature.net/  (DNS proves it)",
}


def list_vendors() -> None:
    tiers = {1: "1 authoritative", 2: "2 corroborated", 3: "3 fingerprint"}
    print(f"{'ID':<16}{'VENDOR':<24}{'PROOF TIER':<17}URL PATTERN / PROOF")
    print("-" * 122)
    for vid in V.VENDOR_ORDER:
        s = V.VENDORS[vid]
        print(f"{vid:<16}{s['name']:<24}{tiers[s['tier']]:<17}{PATTERNS.get(vid, '')}")
    print("-" * 122)
    print("tier 1 authoritative  a positive probe proves usage (vendor 404s/NXDOMAINs unknown tenants)")
    print("tier 2 corroborated   vendor auto-provisions a page for any recognisable company name;")
    print("                      needs live jobs or a link from the company's own careers page")
    print("tier 3 fingerprint    tenant id is opaque - detected only from the company's careers page")


def guess_homepage(company: str) -> Optional[str]:
    """DNS-only homepage guess.  Superseded by guess_company_website(), which
    also fetches the candidate and rejects bot-challenge interstitials; kept for
    callers that want a cheap answer without spending a request."""
    s = base_slug(company)
    if not s:
        return None
    s = re.sub(r"(?:-|^)(inc|llc|ltd|gmbh|ag|sa|sas|bv|nv|plc|corp|company|co)$", "", s)
    s = s.replace("-", "")
    if len(s) < 3:
        return None
    for tld in ("com", "co.uk", "de", "io", "net", "fr", "nl", "org", "in", "co.in"):
        for host in (f"{s}.{tld}", f"www.{s}.{tld}"):
            if dns_resolves(host)[0]:
                return f"https://{host}/"
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="scan.py",
        description="Detect which ATS a list of companies uses and return the direct "
                    "URL of their career site / job listing on that ATS portal.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("companies", nargs="*", help="company names or path to input file (.txt/.csv)")
    p.add_argument("--csv", "--txt", "-i", dest="infile",
                   help="input file: .csv/.tsv with columns company,website,careers_url, "
                        "or a plain text list (one company per line)")
    p.add_argument("-o", "--out", default="ats_scan",
                   help="output prefix (default: ats_scan)")
    p.add_argument("--mode", choices=["all", "probe", "fingerprint"], default="all",
                   help="all = probe vendor URLs + scan careers pages (default)")
    p.add_argument("--vendors", nargs="*", metavar="ID", choices=V.VENDOR_ORDER,
                   help="restrict to specific vendors, e.g. --vendors greenhouse lever ashby")
    p.add_argument("--workers", type=int, default=12, help="parallel companies (default 12)")
    p.add_argument("--timeout", type=float, default=20.0, help="request timeout (default 20s)")
    p.add_argument("--probe-budget", type=int, default=24,
                   help="max probes per company per vendor (default 24). Workday "
                        "needs the most; lower this to speed up a big run.")
    p.add_argument("--max-career-pages", type=int, default=5,
                   help="max careers pages to crawl per company (default 5)")
    p.add_argument("--delay", type=float, default=0.12,
                   help="politeness delay per host in seconds (default 0.12)")
    p.add_argument("--include-probable", action="store_true",
                   help="also report PROBABLE findings (a tenant that exists but whose "
                        "page could not be confirmed as a live job listing). Off by "
                        "default: without it a company either has a portal URL or is "
                        "NOT_FOUND.")
    p.add_argument("--include-self-matches", action="store_true",
                   dest="include_self_matches", default=True,
                   help="(now the default; kept so older commands still work)")
    p.add_argument("--exclude-self-matches", action="store_false",
                   dest="include_self_matches",
                   help="Hide a company that recruits on its OWN product "
                        "(e.g. Teamtailor on career.teamtailor.com). By default such a "
                        "finding is reported with status SELF, because it is still the "
                        "official portal for that company.")
    p.add_argument("--include-unverified", action="store_true",
                   help="also report tier-2 pages that exist but prove nothing "
                        "(Workable/SmartRecruiters/Pinpoint/BambooHR stubs)")
    p.add_argument("--seeds", metavar="FILE",
                   help="CSV of known company -> portal rows (columns: name, "
                        "careers_url, provider, board_slug). Used as knowledge, not "
                        "as an answer: it supplies the real careers URL and tenant "
                        "slug so both engines start from the right place, but every "
                        "finding still has to verify live.")
    p.add_argument("--no-page-check", action="store_true",
                   help="do not open each reported portal URL to confirm it shows jobs. "
                        "The check only upgrades findings and adds job counts; it never "
                        "rejects one.")
    p.add_argument("--no-verify-fingerprint", dest="verify_fingerprint",
                   action="store_false",
                   help="do not re-probe URLs harvested from careers pages")
    p.add_argument("--limit", type=int, default=0, help="only scan the first N companies")
    p.add_argument("--resume", action="store_true",
                   help="reuse companies already present in the -o JSON output, so an "
                        "interrupted run can be continued instead of restarted")
    p.add_argument("--checkpoint", type=int, default=0, metavar="N",
                   help="rewrite the output files every N companies (default: only at the end)")
    p.add_argument("--list-vendors", action="store_true", help="print the vendor table and exit")
    p.add_argument("--selftest", action="store_true",
                   help="validate every vendor rule against known-real and known-fake tenants")
    p.add_argument("--fingerprints", action="store_true",
                   help="validate Engine B by extracting the ATS out of real customer "
                        "careers pages (use with or without --selftest)")
    p.add_argument("--quiet", action="store_true", help="suppress the console table")
    p.add_argument("--version", action="version", version=f"ats_scanner {VERSION}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_vendors:
        list_vendors()
        return 0

    fetcher = Fetcher(workers=max(2, args.workers), timeout=args.timeout,
                      delay=args.delay, verbose=not args.quiet)
    try:
        if args.selftest:
            rc = selftest_reportable()
            rc |= selftest_self_match()
            rc |= selftest_identity()
            rc |= selftest_page_signals()
            rc |= selftest_url_construction()
            rc |= selftest_slugs()
            rc |= selftest_custom_domain_portals()
            rc |= selftest_budget_semantics()
            rc |= selftest(fetcher, args)
            if args.fingerprints:
                rc |= selftest_fingerprints(fetcher)
            return rc
        if args.fingerprints:
            return selftest_fingerprints(fetcher)

        args.seeds_data = load_seeds(args.seeds) if getattr(args, "seeds", None) else {}
        if args.seeds:
            if not args.seeds_data:
                print(f"warning: --seeds {args.seeds} yielded no usable rows "
                      f"(need name + careers_url columns)", file=sys.stderr)
            elif not args.quiet:
                print(f"seed file: {len(args.seeds_data)} known company -> portal "
                      f"row(s) loaded from {args.seeds}", file=sys.stderr)
        rows = load_input(args.infile, args.companies)
        if not rows:
            print("No companies to scan. Pass names as arguments or use --csv companies.csv\n"
                  "Try: python3 scan.py --list-vendors   or   python3 scan.py --selftest",
                  file=sys.stderr)
            return 1
        if args.limit:
            rows = rows[: args.limit]

        print(f"ats_scanner v{VERSION}: {len(rows)} companies x {len(V.VENDORS)} ATS vendors, "
              f"mode={args.mode}, workers={args.workers}")
        missing = [r["company"] for r in rows if not (r.get("website") or r.get("careers_url"))]
        if missing and args.mode == "all" and not args.quiet:
            print(f"note: {len(missing)} company(ies) were given by name only - the scanner "
                  "will derive\n      their website and try the conventional careers paths. "
                  "Supplying website +\n      careers_url in the CSV improves recall, "
                  "especially for JS-only careers sites.")

        # ---- resume: reuse completed companies from an earlier partial run
        cached: Dict[str, Dict[str, Any]] = {}
        json_path = args.out + "_results.json"
        if args.resume and os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as fh:
                    prev = json.load(fh)
                for r in prev.get("results", []):
                    if r.get("company") and r.get("findings"):
                        cached[r["company"].lower()] = r
                print(f"resume: {len(cached)} company(ies) already scanned in {json_path}")
            except Exception as e:
                print(f"resume: could not read {json_path} ({type(e).__name__}) - starting fresh",
                      file=sys.stderr)

        todo = [r for r in rows if r["company"].lower() not in cached]
        results: List[Dict[str, Any]] = [cached[r["company"].lower()] for r in rows
                                         if r["company"].lower() in cached]
        t0 = time.time()
        ntodo = len(todo)
        with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            futs = {}
            for r in todo:
                website = r.get("website")
                careers = r.get("careers_url")
                # No caller-side guessing: scan_company derives the website itself
                # (DNS-checked AND fetch-verified, rejecting bot-challenge pages)
                # so it can report whether the value was supplied or derived.
                futs[ex.submit(scan_company, fetcher, r["company"], website, careers, args)] = r
            done = 0
            for fut in cf.as_completed(futs):
                src = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:
                    res = {"company": src["company"], "website": src.get("website"),
                           "careers_url": src.get("careers_url"),
                           "careers_pages_scanned": [], "found": False, "ats": [],
                           "best_status": None, "primary_url": None,
                           "primary_job_count": None, "seconds": 0,
                           "findings": [{"vendor_id": "-", "vendor": "-",
                                         "status": "NOT_FOUND", "tier": 1, "url": None,
                                         "job_list_url": None, "job_count": None,
                                         "evidence": f"scan crashed: {type(e).__name__}: {e}",
                                         "via": None, "fingerprint": False,
                                         "fingerprint_samples": [], "probes": []}]}
                results.append(res)
                done += 1
                if args.checkpoint and done % args.checkpoint == 0:
                    order0 = {r["company"].lower(): i for i, r in enumerate(rows)}
                    results.sort(key=lambda r: order0.get(r["company"].lower(), 10 ** 6))
                    write_outputs(results, args.out,
                                  {"VERIFIED", "PROBABLE"} |
                                  ({"UNVERIFIED"} if args.include_unverified else set()))
                    print(f"  [checkpoint] {done}/{ntodo} written to {args.out}_results.*",
                          file=sys.stderr)
                if not args.quiet:
                    tag = ",".join(res["ats"]) if res["ats"] else "none"
                    el = time.time() - t0
                    # Throughput-based ETA.  Companies run concurrently, so the
                    # first completions include the whole ramp-up and a naive
                    # (elapsed/done) extrapolation wildly overestimates; report
                    # the observed rate and mark the ETA provisional until a few
                    # have finished.
                    eta = (el / done) * (ntodo - done) if done else 0
                    eta_txt = f"~{eta:.0f}s" if done >= 4 else f"{eta:.0f}s?"
                    print(f"  [{done}/{ntodo}] {res['company']:<30} -> {tag} "
                          f"({res['seconds']}s, elapsed {el:.0f}s, eta {eta_txt})",
                          file=sys.stderr)
        order = {r["company"].lower(): i for i, r in enumerate(rows)}
        results.sort(key=lambda r: order.get(r["company"].lower(), 10 ** 6))

        keep = report_statuses(args)
        written = write_outputs(results, args.out, keep)
        print_console(results, args.quiet, keep)
        print(f"\nWrote: {', '.join(written)}   ({time.time() - t0:.1f}s)")
        return 0
    finally:
        fetcher.close()


if __name__ == "__main__":
    sys.exit(main())