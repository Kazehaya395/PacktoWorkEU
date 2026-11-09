"""
Vendor registry for ats_scanner - 27 ATS platforms.

Every rule here was verified empirically against live endpoints.  Run
`python3 scan.py --selftest` to re-validate them; vendor behaviour drifts.

--------------------------------------------------------------------------
THE PROBLEM THIS FILE SOLVES: "HTTP 200" IS NOT PROOF
--------------------------------------------------------------------------
Multi-tenant ATS vendors fall into two groups, and confusing them produces
confident false positives:

  Tier 1 - AUTHORITATIVE
      The vendor only serves a page/API for a real tenant and answers with a
      clean negative otherwise (404, NXDOMAIN, or a redirect to its own
      marketing site).  A positive probe *proves* the company uses the ATS.
      -> Greenhouse, Lever, Ashby, Workday, Recruitee, Personio, Teamtailor,
         Homerun, softgarden, d.vinci, Flatchr, Taleez, Beetween, JOIN,
         Coveto, rexx, Avature, SAP SuccessFactors, Oracle Taleo, iCIMS

  Tier 2 - CORROBORATED
      The vendor AUTO-PROVISIONS a branded page for any *recognisable company
      name*, so `bosch.pinpointhq.com`, `careers.smartrecruiters.com/Zoho` and
      `apply.workable.com/stripe/` all answer 200 even though those companies
      do not use those products.  (A random string like `qxrihnrvp` gets a
      clean 404, which is why this is easy to miss when testing.)
      A positive probe here only means "a page exists under this name".
      It is promoted to VERIFIED only when either
         (a) the page actually lists jobs, or
         (b) the company's OWN careers page links to / embeds that ATS URL.
      -> Workable, SmartRecruiters, Pinpoint, BambooHR

  Tier 3 - FINGERPRINT ONLY
      The tenant id is an opaque token that cannot be derived from a company
      name at all.  Detection happens purely by reading the company's own
      careers page.
      -> Zoho Recruit, Eploy, Bullhorn

--------------------------------------------------------------------------
STATUSES
--------------------------------------------------------------------------
  VERIFIED     proved real (authoritative probe, or live jobs, or the
               company's own careers page points at this ATS URL)
  PROBABLE     strong signal, one step short of proof (e.g. DNS record
               exists but the page could not be read)
  UNVERIFIED   a page exists under this company's name but nothing proves
               the company actually uses the product (tier 2, no jobs)
  MISS         proved not to exist (clean negative control)
  BLOCKED      vendor unreachable / captcha / rate limit
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

FAKE = "zzzqxnottreal9911"     # negative-control slug used by --selftest

TIER_AUTHORITATIVE = 1
TIER_CORROBORATED = 2
TIER_FINGERPRINT = 3


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def page_title(body: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", body or "", re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def final_host(resp: Dict[str, Any]) -> str:
    m = re.match(r"https?://([^/]+)", (resp.get("final_url") or "").lower())
    return m.group(1) if m else ""


def moved_away(resp: Dict[str, Any], keep_hosts: List[str]) -> bool:
    """True when the request ended on a host outside the vendor's own domains.

    Several vendors answer an unknown-tenant request with a 301/302 to their
    marketing site.  That is the cleanest possible MISS signal.
    """
    host = final_host(resp)
    if not host:
        return False
    return not any(host == k or host.endswith("." + k) for k in keep_hosts)


def starts_like_json(body: str) -> bool:
    return bool(body) and body.lstrip()[:1] in ("{", "[")


def json_total(body: str, key: str = "totalFound") -> Optional[int]:
    m = re.search(rf'"{key}"\s*:\s*(\d+)', body or "")
    return int(m.group(1)) if m else None


def has_count(resp: Dict[str, Any], key: str = "totalFound") -> Optional[int]:
    j = resp.get("json")
    if isinstance(j, dict):
        for k in (key, "total", "count", "totalJobs", "totalFound"):
            if isinstance(j.get(k), int):
                return j[k]
        for k in ("jobs", "postings", "content", "jobPostings", "positions", "items"):
            if isinstance(j.get(k), list):
                return len(j[k])
    return None


def xml_items(resp: Dict[str, Any]) -> Optional[int]:
    body = resp.get("body") or ""
    if "<" not in body:
        return None
    for tag in ("position", "job", "item", "opening"):
        n = len(re.findall(rf"<{tag}[\s>]", body, re.I))
        if n:
            return n
    return 0


# Some public job APIs (Ashby, Greenhouse) return multi-megabyte payloads.  We
# stream only the first slice, so the JSON is truncated and cannot be parsed;
# count postings in the raw text instead.
TRUNCATED_JSON_COUNTERS = {
    "ashby": r'"id"\s*:\s*"[0-9a-f]{8}-[0-9a-f]{4}-',
    "greenhouse": r'"absolute_url"\s*:',
    "workable": r'"shortcode"\s*:',
    "bamboohr": r'"jobOpeningName"\s*:',
    "recruitee": r'"opening_id"\s*:',
}


def count_jobs_in_text(vid: str, body: str) -> Optional[int]:
    rule = TRUNCATED_JSON_COUNTERS.get(vid)
    if not rule or not body:
        return None
    return len(re.findall(rule, body)) or None


# Generic "this page really lists jobs" markers, used by tier-2 vendors whose
# pages are auto-provisioned and therefore need a jobs-present check.
JOB_MARKERS = (
    r'"@type"\s*:\s*"JobPosting"',
    r'href="[^"]*/jobs/[0-9a-f]{8}-[0-9a-f]{4}-',      # uuid job permalinks
    r'href="[^"]*/jobs/\d{4,}[-/]',                     # numeric job ids
    r'data-job-id\s*=',
    r'class="[^"]*job-posting',
    r'"jobOpenings?"\s*:\s*\[\s*\{',
)


def count_job_markers(body: str) -> int:
    n = 0
    for pat in JOB_MARKERS:
        n += len(re.findall(pat, body or "", re.I))
    return n


# --------------------------------------------------------------------------- #
# probe / verdict objects
# --------------------------------------------------------------------------- #

@dataclass
class Probe:
    """One candidate URL for one company/vendor pair."""
    vid: str
    vendor: str
    url: str                                    # public career-site URL to report
    api_url: Optional[str] = None               # machine-readable proof endpoint
    request_url: Optional[str] = None
    method: str = "GET"
    payload: Optional[Dict[str, Any]] = None
    headers: Dict[str, str] = field(default_factory=dict)
    kind: str = "career_site"                   # career_site | job_list | dns | api
    judge: Optional[Callable[[Dict[str, Any]], tuple]] = None
    note: str = ""
    max_bytes: int = 0                          # 0 = fetcher default
    post_check: Optional[Callable] = None       # (fetcher, probe, resp, st, ev) -> (st, ev, count)


# --------------------------------------------------------------------------- #
# judges
# --------------------------------------------------------------------------- #

def j_status_ok(vendor_hosts, min_len=0, ok_codes=(200,), label=""):
    """Tier-1 default: the vendor 404s / redirects unknown tenants, so a 200
    that stayed on the vendor's own domain is genuine proof."""
    def judge(r):
        code = r.get("status")
        if code in (403, 405, 429, 503) and r.get("waf"):
            return "BLOCKED", f"HTTP {code} bot wall ({r.get('waf')})"
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code in (500, 502, 504):
            return "MISS", f"HTTP {code} (vendor error page for unknown tenant)"
        if code not in ok_codes:
            return "MISS", f"HTTP {code}"
        if moved_away(r, vendor_hosts):
            return "MISS", f"HTTP {code} -> redirected off-vendor to {r.get('final_url')}"
        body = r.get("body") or ""
        if min_len and len(body) < min_len:
            return "MISS", f"HTTP {code} but body only {len(body)} bytes"
        cnt = has_count(r) or xml_items(r)
        ev = f"HTTP {code}, {len(body)} bytes on {final_host(r)}"
        if cnt:
            ev = f"HTTP {code}, {cnt} open job(s) on {final_host(r)}"
        if label:
            ev = f"{label}: {ev}"
        return "VERIFIED", ev
    return judge


def j_api_json(jobs_keys=("jobs", "postings", "content", "jobPostings"), vid=""):
    """Tier-1: public JSON API.  200 + a jobs collection => real tenant.
    404 => authoritative MISS."""
    def judge(r):
        code = r.get("status")
        if code == 404:
            return "MISS", "API 404 - no such tenant"
        if code in (403, 429, 503):
            return "BLOCKED", f"API HTTP {code}"
        if code != 200:
            return "MISS", f"API HTTP {code}"
        body = r.get("body") or ""
        j = r.get("json")
        if not isinstance(j, dict):
            if starts_like_json(body):
                n = count_jobs_in_text(vid, body)
                if n:
                    return "VERIFIED", (f"public API 200 with {n}+ open job(s) "
                                        f"(payload truncated at {len(body)} bytes)")
                return "VERIFIED", f"public API 200 with a JSON job payload ({len(body)} bytes)"
            return "MISS", "API 200 but body is not JSON"
        n = None
        meta = j.get("meta") if isinstance(j.get("meta"), dict) else {}
        for k in ("totalFound", "total", "totalCount"):
            if isinstance(meta.get(k), int):
                n = meta[k]
                break
            if isinstance(j.get(k), int):
                n = j[k]
                break
        for k in jobs_keys:
            if isinstance(j.get(k), list):
                n = len(j[k]) if n is None else n
                break
        if n is None:
            n = has_count(r)
        if n is None:
            return "MISS", "API 200 but no job collection in payload"
        return "VERIFIED", f"public API returned {n} open job(s)"
    return judge


def j_dns_first(vendor_hosts, vendor, min_len=0):
    """Vendor provisions DNS only for real tenants (no wildcard).
    NXDOMAIN == definitive MISS; a resolved name == the tenant exists."""
    http_j = j_status_ok(vendor_hosts, min_len)

    def judge(r):
        if r.get("dns_error"):
            return "MISS", f"NXDOMAIN - {vendor} never provisioned this hostname"
        st, ev = http_j(r)
        if st == "VERIFIED":
            return "VERIFIED", f"DNS provisioned; {ev}"
        if st == "MISS" and "NXDOMAIN" not in ev:
            return st, f"DNS provisioned; {ev}"
        return st, ev
    return judge


def j_wday():
    """Workday: POST the public CXS endpoint.  200 => real tenant AND we now
    know the career-site id.  422 => unknown tenant or unknown site id."""
    def judge(r):
        code = r.get("status")
        if code == 200:
            j = r.get("json") if isinstance(r.get("json"), dict) else {}
            n = j.get("total")
            if n is None and isinstance(j.get("jobPostings"), list):
                n = f"at least {len(j['jobPostings'])}"
            if n is None:
                return "VERIFIED", "Workday CXS API returned 200 - real tenant and career-site id"
            if isinstance(n, int) and n == 0:
                # the tenant and career-site id are genuinely real, but an empty
                # board is not a useful job-listing URL to hand back
                return "PROBABLE", ("Workday CXS API returned 200 with 0 open jobs - "
                                    "configured career site, no current listings")
            return "VERIFIED", f"Workday CXS API returned {n} open job(s)"
        if code == 422:
            return "MISS", "CXS API 422 - unknown tenant / career-site id"
        if code in (400, 404, 500):
            return "MISS", f"CXS API HTTP {code}"
        if code in (403, 429, 503):
            return "BLOCKED", f"CXS API HTTP {code}"
        return "MISS", f"CXS API HTTP {code}"
    return judge


def j_icims():
    """iCIMS is tier 1 once you know the negative marker: a non-tenant answers
    404 with <title>gone: {host} : dc409</title>, while a real career portal
    answers 200 with the iCIMS template.  AWS WAF occasionally intervenes from
    datacentre IPs; that is reported as BLOCKED, never as a hit."""
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        if code == 404 and re.search(r"gone:\s*\S*icims\.com", body, re.I):
            return "MISS", "404 'gone: <host>' - iCIMS says no such career portal"
        if code == 404:
            return "MISS", "HTTP 404 - no such tenant"
        if r.get("waf") or code == 405 or "Human Verification" in body[:3000]:
            return "BLOCKED", ("AWS WAF 'Human Verification' - retry later; *.icims.com is a "
                               "wildcard so this proves nothing either way")
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code != 200:
            return "MISS", f"HTTP {code}"
        return "VERIFIED", (f"HTTP 200 iCIMS career portal on {final_host(r)} "
                            f"({len(body)} bytes, {count_job_markers(body)} job markers)")
    return judge


def j_join():
    """JOIN: 404 shell for unknown slugs, <title>Jobs at X | JOIN</title> for
    real boards."""
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        t = page_title(body)
        if code == 404 or "page not found" in t.lower():
            return "MISS", f"HTTP {code}, title '{t[:44]}' - no JOIN board for this slug"
        if code != 200:
            return "MISS", f"HTTP {code}"
        if t.lower().startswith("jobs at "):
            return "VERIFIED", f"JOIN board page (title: '{t[:56]}')"
        return "MISS", f"HTTP 200, unrecognised page (title: '{t[:40]}')"
    return judge


def j_bamboo():
    """BambooHR: *.bamboohr.com is a wildcard and unknown tenants 302 to
    www.bamboohr.com.  A page that stays on the subdomain is a real BambooHR
    career page - but it can also be an auto-provisioned stub for a company
    that merely *looks* like a known brand, so tier 2 unless jobs are listed."""
    def judge(r):
        final = r.get("final_url") or ""
        host = final_host(r)
        if host in ("www.bamboohr.com", "bamboohr.com"):
            return "MISS", f"redirected to {final} - no such BambooHR tenant"
        if "expired.php" in final:
            return "MISS", "BambooHR tenant exists but its career page is expired/closed"
        if r.get("status") != 200:
            return "MISS", f"HTTP {r.get('status')}"
        body = r.get("body") or ""
        jobs = count_job_markers(body)
        if jobs:
            return "VERIFIED", f"BambooHR career page on {host} listing jobs ({jobs} markers)"
        return "UNVERIFIED", (f"BambooHR career page exists at {final} but lists no jobs - "
                              "BambooHR provisions pages for recognised company names, so this "
                              "is not proof of usage")
    return judge


def j_bamboo_api():
    """BambooHR's JSON job list: `https://{tenant}.bamboohr.com/careers/list`.

    This is a genuine oracle, and it was hiding in plain sight.  It answers
    `{"meta":{"totalCount":N},"result":[...]}`:

        astroscale    -> totalCount 44   (a live board)
        brainrocket   -> totalCount 0    (tenant exists, nothing open)
        lightspeed    -> totalCount 0    (the empty template we once reported)
        zzznotreal    -> 302 to www.bamboohr.com, HTML, not JSON

    So BambooHR no longer has to be judged by counting CSS classes on a page it
    auto-provisions for any recognisable company name.  `totalCount > 0` proves
    usage and gives the job count; `totalCount == 0` proves the tenant exists
    but publishes nothing, which is not a portal anyone can apply through.
    """
    def judge(r):
        final = r.get("final_url") or ""
        host = final_host(r)
        if host in ("www.bamboohr.com", "bamboohr.com"):
            return "MISS", f"redirected to {final} - no such BambooHR tenant"
        if "expired.php" in final:
            return "MISS", "BambooHR tenant exists but its career page is expired/closed"
        if r.get("status") in (403, 429, 503):
            return "BLOCKED", f"BambooHR HTTP {r.get('status')}"
        if r.get("status") != 200:
            return "MISS", f"HTTP {r.get('status')}"
        j = r.get("json")
        if not isinstance(j, dict):
            body = r.get("body") or ""
            if not starts_like_json(body):
                return "MISS", f"job list is not JSON ({host} served HTML)"
            n = count_jobs_in_text("bamboohr", body)
            return ("VERIFIED", f"BambooHR job list with {n}+ open job(s)") if n else \
                   ("UNVERIFIED", "BambooHR job list, payload truncated before any job")
        meta = j.get("meta") if isinstance(j.get("meta"), dict) else {}
        n = None
        for k in ("totalCount", "total", "count"):
            if isinstance(meta.get(k), int):
                n = meta[k]
                break
            if isinstance(j.get(k), int):
                n = j[k]
                break
        res = j.get("result")
        if n is None and isinstance(res, list):
            n = len(res)
        if n is None:
            return "UNVERIFIED", "BambooHR job list answered 200 with no count"
        if n > 0:
            return "VERIFIED", (f"BambooHR job list API reports {n} open job(s) "
                                f"on {host}")
        return "UNVERIFIED", (f"BambooHR tenant exists on {host} but its job list "
                              f"reports 0 open postings")
    return judge


def j_sr():
    """SmartRecruiters AUTO-PROVISIONS careers.smartrecruiters.com/{Name} with
    a 'Careers at {Name}' page and a companyIdentifier for *any* recognisable
    company name (verified with Zoho, Infosys, NVIDIA, Airbnb - none of them
    SmartRecruiters customers).  Unknown names bounce to jobs.smartrecruiters.com.
    Proof therefore requires live postings, a custom-domain redirect, or a
    fingerprint on the company's own careers page."""
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        final = r.get("final_url") or ""
        host = final_host(r)
        if host in ("jobs.smartrecruiters.com", "www.smartrecruiterscareers.com",
                    "smartrecruiterscareers.com", "www.smartrecruiters.com",
                    "smartrecruiters.com"):
            return "MISS", f"redirected to SmartRecruiters' own site ({final})"
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code == 404:
            return "MISS", "HTTP 404"
        if code != 200:
            return "MISS", f"HTTP {code}"
        if host.endswith("careers.smartrecruiters.com"):
            return "UNVERIFIED", (f"auto-provisioned 'Careers at ...' page ({page_title(body)[:40]}) - "
                                  "SmartRecruiters creates one for any recognised company name, "
                                  "so this is not proof of usage")
        return "PROBABLE", (f"redirected to the company's own careers domain {host} - "
                            f"custom-domain career site ({page_title(body)[:40]})")
    return judge


def j_sr_api():
    """SmartRecruiters public postings API.  It answers 200 {"totalFound":0}
    for unknown companies too, so only totalFound > 0 is meaningful."""
    def judge(r):
        code = r.get("status")
        if code != 200:
            return "MISS", f"postings API HTTP {code}"
        body = r.get("body") or ""
        total = json_total(body) or has_count(r)
        if total and total > 0:
            return "VERIFIED", f"SmartRecruiters postings API returned {total} open job(s)"
        return "UNVERIFIED", ("SmartRecruiters postings API answered with 0 jobs - the company id "
                              "exists in their directory but publishes nothing")
    return judge


def j_pinpoint():
    """Pinpoint AUTO-PROVISIONS {name}.pinpointhq.com with a branded
    'Jobs at X | X Careers' page for any recognisable organisation (verified
    with Bosch, Infosys, TCS - not Pinpoint customers).  A random string gets
    a 404 'Not Found | Pinpoint' shell.  So only a page that actually lists
    jobs, or a fingerprint from the company's own careers page, is proof."""
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        t = page_title(body)
        if code == 404 or "404 not found" in t.lower() or t.strip().lower() == "pinpoint":
            return "MISS", f"HTTP {code}, title '{t[:44]}' - Pinpoint not-found shell"
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code != 200:
            return "MISS", f"HTTP {code}"
        jobs = count_job_markers(body)
        if jobs:
            return "VERIFIED", f"Pinpoint careers site listing jobs ({jobs} markers, title '{t[:44]}')"
        # client-rendered: the HTML never contains the postings, so defer to the
        # sitemap post-check
        return "UNVERIFIED", f"Pinpoint page exists (title '{t[:44]}') but the HTML is client-rendered"
    return judge


def j_workable():
    """Workable's widget API auto-provisions an account for recognisable names
    ({"name":"Stripe","jobs":[]}) and 404s random strings.  Only jobs.length>0
    or a fingerprint from the company's own careers page proves usage."""
    def judge(r):
        code = r.get("status")
        final = r.get("final_url") or ""
        body = r.get("body") or ""
        if code == 404 or final.rstrip("/").endswith("apply.workable.com/oops"):
            return "MISS", "widget API 404 - no such Workable account"
        if code in (403, 429, 503):
            return "BLOCKED", f"widget API HTTP {code}"
        if code != 200:
            return "MISS", f"widget API HTTP {code}"
        j = r.get("json")
        if isinstance(j, dict) and ("name" in j or "jobs" in j):
            jobs = j.get("jobs") if isinstance(j.get("jobs"), list) else []
            if jobs:
                return "VERIFIED", f"Workable account '{j.get('name')}' publishing {len(jobs)} job(s)"
            return "UNVERIFIED", (f"Workable account '{j.get('name')}' exists but publishes 0 jobs - "
                                  "Workable auto-provisions accounts for recognised company names, "
                                  "so this is not proof of usage")
        if starts_like_json(body):
            return "PROBABLE", f"Workable widget API answered: {body[:70]}"
        return "MISS", "HTTP 200 but not a Workable account payload"
    return judge


def j_personio():
    """Personio: wildcard DNS, HTTP decides.  404 => no tenant.  A 429 is very
    often a Vercel bot checkpoint rather than the tenant, so it is reported as
    BLOCKED, not as a hit."""
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        t = page_title(body)
        if code == 404 or "page not found" in t.lower():
            return "MISS", f"HTTP {code} - no Personio tenant for this slug"
        if code in (403, 429, 503) or "security checkpoint" in t.lower():
            return "BLOCKED", f"HTTP {code} ('{t[:32]}') - rate limit or bot checkpoint, retry later"
        if code != 200:
            return "MISS", f"HTTP {code}"
        if moved_away(r, ["jobs.personio.de", "jobs.personio.com", "personio.de", "personio.com"]):
            return "MISS", f"redirected off-vendor to {r.get('final_url')}"
        if body.lstrip().startswith("<?xml") or "/xml" in (r.get("final_url") or ""):
            n = xml_items(r)
            return "VERIFIED", f"Personio public job feed with {n} open position(s)"
        return "VERIFIED", f"HTTP 200 Personio career site '{t[:44]}' ({len(body)} bytes)"
    return judge


def j_recruitee():
    """Recruitee: unknown tenants are 301'd to recruitee.com/careers_not_hosted.
    Real tenants either serve {slug}.recruitee.com or 302 to their own custom
    domain - both are hits.  The /o/{slug} path 404s even for real tenants, so
    only the root is probed."""
    def judge(r):
        code = r.get("status")
        final = r.get("final_url") or ""
        host = final_host(r)
        if host in ("recruitee.com", "www.recruitee.com", "tellent.com", "www.tellent.com"):
            return "MISS", f"301 to {final} ('careers_not_hosted') - no such tenant"
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code == 404:
            return "MISS", f"HTTP 404 on {host}"
        if code != 200:
            return "MISS", f"HTTP {code}"
        if host.endswith(".recruitee.com"):
            return "VERIFIED", f"Recruitee-hosted career site ({final}, {len(r.get('body') or '')} bytes)"
        return "VERIFIED", f"Recruitee tenant on the custom domain {host} ({final})"
    return judge


def j_homerun():
    def judge(r):
        final = r.get("final_url") or ""
        code = r.get("status")
        if "404.homerun.co" in final:
            return "MISS", f"redirected to {final} - no such Homerun career site"
        if code == 403:
            return "BLOCKED", "HTTP 403 - Homerun edge block, retry later"
        if code != 200:
            return "MISS", f"HTTP {code}"
        if moved_away(r, ["homerun.co"]):
            return "VERIFIED", f"HTTP 200 on the customer's own domain {final} (Homerun-powered)"
        return "VERIFIED", f"HTTP 200 Homerun career site ({len(r.get('body') or '')} bytes)"
    return judge


def j_softgarden_de():
    def judge(r):
        code = r.get("status")
        if code == 500:
            return "MISS", "HTTP 500 - 'Keine Karriere-Subdomain gefunden'"
        if code == 404:
            return "MISS", "HTTP 404 - unknown softgarden subdomain"
        if code in (429, 403, 503):
            return "BLOCKED", f"HTTP {code}"
        if code == 200:
            return "VERIFIED", f"HTTP 200 softgarden career page ({len(r.get('body') or '')} bytes)"
        return "MISS", f"HTTP {code}"
    return judge


def j_teamtailor():
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        if code == 404 or "Teamtailor — 404" in body or "could not be found" in body[:2000]:
            return "MISS", f"HTTP {code} - Teamtailor 404 (no career site for this subdomain)"
        if code in (429, 403, 503):
            return "BLOCKED", f"HTTP {code}"
        if code != 200:
            return "MISS", f"HTTP {code}"
        return "VERIFIED", f"HTTP 200 Teamtailor career site ({len(body)} bytes)"
    return judge


def j_taleez():
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        if code == 429:
            return "BLOCKED", "HTTP 429 - Taleez rate limit, retry later"
        if code == 404 or "<title>Erreur</title>" in body:
            return "MISS", "HTTP 404 'Erreur' - no Taleez tenant for this slug"
        if code != 200:
            return "MISS", f"HTTP {code}"
        return "VERIFIED", f"HTTP 200 Taleez career site ({len(body)} bytes)"
    return judge


def j_dvinci():
    def judge(r):
        if r.get("dns_error"):
            return "MISS", "NXDOMAIN on *.dvinci-easy.com - no such d.vinci tenant"
        code = r.get("status")
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code != 200:
            return "MISS", f"HTTP {code}"
        return "VERIFIED", f"HTTP 200 d.vinci easy portal ({r.get('final_url')})"
    return judge


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #

# Ordered by observed frequency. Workday is by far the most expensive vendor to
# probe because both the tenant number (wd1..wd13) and the career-site id have
# to be discovered; --probe-budget caps the product of the two.
WORKDAY_SITES = ["External_Career_Site", "External", "{n}ExternalCareerSite",
                 "{n}_jobs", "{n}Jobs", "Careers", "Global", "external"]
# Data-centre numbers seen in the wild.  wd103 is not a typo: EMBL is on it
# (https://embl.wd103.myworkdayjobs.com/EMBL, 18 live requisitions), so the
# numbering is not confined to the low single digits.
WORKDAY_DCS = ["wd5", "wd1", "wd3", "wd12", "wd2", "wd6", "wd8", "wd10", "wd103"]

SF_HOSTS = ["jobs.sap.com", "jobs.successfactors.com", "jobs.successfactors.eu",
            "jobs.sap.eu", "jobs.sapsf.com"]
TALEO_HOSTS = ["taleo.net", "taleo.com", "taleo1.com", "taleoenterprise.com", "taleo.biz"]


def _p(vid, vendor, url, judge, request_url=None, method="GET", payload=None,
       headers=None, kind="career_site", api_url=None, note="", max_bytes=0,
       post_check=None):
    return Probe(vid=vid, vendor=vendor, url=url, api_url=api_url,
                 request_url=request_url or url, method=method, payload=payload,
                 headers=headers or {}, kind=kind, judge=judge, note=note,
                 max_bytes=max_bytes, post_check=post_check)


def sitemap_job_check(marker: str, label: str):
    """Second-stage proof for vendors whose career page is a client-rendered
    shell: fetch sitemap.xml and count real job permalinks.

    Returns (status, evidence, job_count)."""
    def post_check(fetcher, probe, resp, st, ev):
        if st not in ("UNVERIFIED", "PROBABLE", "VERIFIED"):
            return st, ev, None
        base = (resp.get("final_url") or probe.url or "").rstrip("/")
        base = re.sub(r"\?.*$", "", base)
        sm = fetcher.request(base + "/sitemap.xml", max_bytes=400_000)
        body = sm.get("body") or ""
        if sm.get("status") != 200 or not body:
            if st == "VERIFIED":
                st = "PROBABLE"
            return st, ev + "; sitemap.xml unavailable, job count not confirmed", None
        locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", body)
        jobs = [u for u in locs if re.search(marker, u, re.I)]
        if jobs:
            return "VERIFIED", (f"{len(jobs)} live job permalink(s) in sitemap.xml "
                                f"({label}) - e.g. {jobs[0][:78]}"), len(jobs)
        return ("UNVERIFIED",
                ev + f"; sitemap.xml lists {len(locs)} URL(s) but no job permalinks "
                     f"- an auto-provisioned shell, not a live board", 0)
    return post_check


VENDORS: Dict[str, Dict[str, Any]] = {}


def _reg(vid, name, build, *, tier, proof=(), hints=(), guessable=True,
         home="", notes=""):
    """proof  = regexes that PROVE the company uses this ATS when found on the
                company's own careers page, and yield the tenant id / URL.
       hints  = weaker mentions (vendor name, CDN, asset host) used only to
                seed extra slug guesses - never reported as a finding."""
    VENDORS[vid] = dict(id=vid, name=name, build=build, tier=tier,
                        proof=[re.compile(p, re.I) for p in proof],
                        hints=[re.compile(p, re.I) for p in hints],
                        guessable=guessable, home=home, notes=notes)


# --- Greenhouse ------------------------------------------------------------ #
def _b_greenhouse(slug, company, ctx):
    api = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    return [_p("greenhouse", "Greenhouse", f"https://job-boards.greenhouse.io/{slug}",
               j_api_json(vid="greenhouse"), request_url=api, api_url=api,
               kind="job_list", max_bytes=64_000,
               note="public JSON board API - authoritative (404 = no board)")]


_reg("greenhouse", "Greenhouse", _b_greenhouse, tier=TIER_AUTHORITATIVE,
     home="https://www.greenhouse.io",
     proof=[r"boards\.greenhouse\.io/([A-Za-z0-9_\-]+)",
            r"job-boards\.greenhouse\.io/([A-Za-z0-9_\-]+)",
            r"greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_\-]+)",
            # Companies that build their own careers UI on top of the Greenhouse
            # API embed the requisition id instead of a board URL. Stripe's page
            # carries hundreds of {"greenhouseId":123456,...} objects and no
            # greenhouse.io URL at all. These markers yield no slug, so they
            # corroborate (and are reported via the probe's URL).
            r"\"greenhouseId\"\s*:",
            r"[?&]gh_jid=\d+"],
     hints=[r"[?&]gh_src=", r"greenhouse(?:-embed)?\.js", r"greenhouse\.io"],
     notes="API is authoritative: 200 => real board with a job count, 404 => no board.")


# --- Lever ----------------------------------------------------------------- #
def _b_lever(slug, company, ctx):
    """Lever runs two disjoint estates.  An EU-resident board is on
    `jobs.eu.lever.co` / `api.eu.lever.co` and is a hard 404 on the global host:

        jobs.lever.co/innogames      -> 404 (297 kB of 404 page)
        jobs.eu.lever.co/innogames   -> 200, 720 kB, the real board

    So both hosts are probed, global first because it is far more common.
    The JSON API is probed alongside each page because it returns a clean 404
    and a job count, while Lever's HTML 404 page is 297 kB of convincing chrome.
    """
    out = []
    for host, api in (("jobs.lever.co", "api.lever.co"),
                      ("jobs.eu.lever.co", "api.eu.lever.co")):
        out.append(_p("lever", "Lever", f"https://{host}/{slug}",
                      j_status_ok(["lever.co"]),
                      request_url=f"https://{api}/v0/postings/{slug}",
                      api_url=f"https://{api}/v0/postings/{slug}",
                      kind="api", max_bytes=200_000,
                      note=("EU-resident board" if ".eu." in host
                            else "public postings API - authoritative (404 = no board)")))
    return out


_reg("lever", "Lever", _b_lever, tier=TIER_AUTHORITATIVE, home="https://www.lever.co",
     proof=[r"jobs\.lever\.co/([A-Za-z0-9_\-]+)", r"api\.lever\.co/v\d+/([A-Za-z0-9_\-]+)",
            r"lever(?:co)?\.postings?\.apply", r"[?&]lever-[A-Za-z0-9_\-]{6,}"],
     hints=[r"lever\.co", r"leverpostings"],
     notes="Clean 404 for unknown slugs.")


# --- Ashby ----------------------------------------------------------------- #
def _b_ashby(slug, company, ctx):
    api = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    return [_p("ashby", "Ashby", f"https://jobs.ashbyhq.com/{slug}",
               j_api_json(vid="ashby"), request_url=api, api_url=api,
               kind="job_list", max_bytes=64_000,
               note="posting-api is authoritative; jobs.ashbyhq.com is a SPA that "
                    "returns 200 even for unknown slugs")]


_reg("ashby", "Ashby", _b_ashby, tier=TIER_AUTHORITATIVE, home="https://www.ashbyhq.com",
     proof=[r"jobs\.ashbyhq\.com/([A-Za-z0-9_\-]+)",
            r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_\-]+)",
            r"[?&]ashby_jid=[0-9a-f\-]{8,}"],
     hints=[r"ashbyhq\.com"],
     notes="NEVER trust jobs.ashbyhq.com's status code - always use the posting API.")


# --- Workday --------------------------------------------------------------- #
def _b_workday(slug, company, ctx):
    """One probe per tenant name.  The (wd-number, career-site-id) matrix is
    iterated *inside* run_workday_search() rather than as separate probes, so
    Workday does not eat the whole --probe-budget.

    Two phases: the most common site ids across every wd number first, then the
    long tail.  A real tenant answers POST /wday/cxs/{t}/{site}/jobs with 200;
    an unknown tenant or site id answers 422.  (GET on the UI path is NOT a
    usable oracle: nvidia.wd5/External_Career_Site 404s while the tenant's real
    site id NVIDIAExternalCareerSite works fine.)"""
    return [_p("workday", "Workday Recruiting",
               f"https://{slug}.wd5.myworkdayjobs.com/", j_wday(),
               request_url=f"https://{slug}.wd5.myworkdayjobs.com/wday/cxs/{slug}/External_Career_Site/jobs",
               method="POST",
               payload={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
               headers={"Content-Type": "application/json"}, kind="job_list",
               note="placeholder - scan.py replaces this with run_workday_search()")]


# Career-site ids observed on real tenants, phase 1 first.  Every one of these
# came from a working POST against /wday/cxs/{tenant}/{site}/jobs:
#   External_Career_Site (salesforce)   Ext (autodesk, 310)
#   {n}ExternalCareerSite (nvidia)      External_Career (broadcom, 358)
#   {n} (zendesk, EMBL)                 Careers (prysmiangroup, soti)
#   {n}CareerSite (leonardo, 851)       careers (nxp, 813)
#   {n}SiteWD (zalando, 173)            jobs-and-careers (philips, 780)
WORKDAY_SITE_PHASES = [
    # phase 1: covers the large majority of tenants
    ["External_Career_Site", "{n}ExternalCareerSite", "{n}", "{n}_jobs", "External",
     "Ext", "Careers", "careers", "{n}CareerSite", "External_Career"],
    # phase 2: the long tail
    ["{n}Jobs", "Global", "external", "jobs-and-careers", "{n}SiteWD",
     "{n}_External_Career_Site", "WD", "{n}_Careers", "Job_Requisition",
     "{n}_External", "External_Careers"],
]


def workday_site_ids(slug: str) -> List[List[str]]:
    """Site-id candidates in two phases, with the slug interpolated.

    Career-site ids are CASE-SENSITIVE: Workday's own tenant uses the site id
    `Workday`, not `workday`.  So for slug-derived ids we try the exact slug
    (case preserved), the de-hyphenated slug and a Title-Cased form.
    """
    n = slug.replace("-", "")
    variants = [n]
    for v in (slug, n.title(), n.upper(), slug.title().replace("-", "")):
        if v and v not in variants:
            variants.append(v)
    out = []
    for phase in WORKDAY_SITE_PHASES:
        seen, cur = set(), []
        for site in phase:
            cands = variants if "{n}" in site else [site]
            for v in cands:
                s = site.format(n=v) if "{n}" in site else v
                if s not in seen:                      # case-sensitive de-dup
                    seen.add(s)
                    cur.append(s)
        out.append(cur)
    return out


# --------------------------------------------------------------------------- #
# Universal job-listing verification
#
# Given any candidate portal URL, open it and ask: does this page actually look
# like a list of jobs?  Signals are the things a job board has and a 404 or a
# parked tenant does not - JobPosting structured data, repeated job-card CSS
# classes, links to individual job detail pages, "open roles" headings, an
# explicit job count, show-more / pagination controls.
#
# Calibrated against real and bogus tenants on 12 vendors.  Every bogus tenant
# scored <= 4 and every server-rendered real board scored >= 12, so a threshold
# of 10 produces no false positives.  It is deliberately used only to UPGRADE or
# to add a job count, never to reject: the vendors listed in JS_ONLY_VENDORS
# render their jobs client-side, so their HTML scores 0 even when the board is
# real (Ashby and Workday score 0 for both a live tenant and a random string).
# --------------------------------------------------------------------------- #

JOB_SIGNAL_THRESHOLD = 10

JS_ONLY_VENDORS = frozenset({
    "ashby", "workday", "pinpoint", "workable", "icims", "avature", "join",
    "beetween", "zoho", "eploy", "bullhorn", "successfactors", "taleo",
    "softgarden", "bamboohr",
})

_JS_JSONLD = re.compile(r'"@type"\s*:\s*"JobPosting"', re.I)
_JS_CLS = re.compile(r'(?:class|id|data-[a-z-]+)\s*=\s*"([^"]*)"', re.I)
_JS_WORD = re.compile(r'job|posting|vacanc|requisition|opening|position|role|stellenangebot', re.I)
_JS_DETAIL = re.compile(
    r'href\s*=\s*"([^"]*(?:/jobs?/|/postings?/|/vacanc\w*|/vacature\w*|/roles?/|'
    r'/positions?/|/opening\w*|/stellenangebot\w*|/stellen\w*|/requisition\w*|'
    r'/offres?/|/emploi\w*|/job[-_]?detail)[^"]{2,140})"', re.I)
_JS_TEXT = re.compile(r'open roles|open positions|current openings|available positions|'
                      r'job openings|\bvacancies\b|\bstellenangebote\b|\boffres d.?emploi\b', re.I)
_JS_NJOBS = re.compile(r'(\d[\d,\.]{0,6})\s*\+?\s*(?:open\s+)?'
                       r'(?:jobs|roles|positions|vacancies|openings|stellenangebote|offres)\b', re.I)
_JS_MORE = re.compile(r'show more|load more|view all jobs|load \d+ more', re.I)
_JS_PAGING = re.compile(r'pagination|aria-label="[^"]*next|page \d+ of \d+', re.I)
_JS_NEG = re.compile(r'page not found|doesn.?t exist|no jobs|no open (?:roles|positions)|'
                     r'no vacancies|404|not found|unavailable|expired', re.I)


def job_page_signals(body: str) -> Dict[str, Any]:
    """Score how strongly `body` looks like a real job listing.

    Returns {"score", "jobs", "why"}.  Read the whole document: Lever keeps its
    job cards past the 400 kB mark, and truncating there scored a 620-job board
    as empty.
    """
    if not body:
        return {"score": 0, "jobs": 0, "links": 0, "why": "empty body"}
    b = body[:3_000_000]
    cls: Dict[str, int] = {}
    for m in _JS_CLS.finditer(b):
        for w in re.split(r"[\s\"']+", m.group(1)):
            if w and len(w) < 40 and _JS_WORD.search(w):
                cls[w.lower()] = cls.get(w.lower(), 0) + 1
    detail = set(_JS_DETAIL.findall(b))
    nj = [int(x.replace(",", "").replace(".", "")) for x in _JS_NJOBS.findall(b)]
    nj = [n for n in nj if 0 < n < 100_000]
    # A token that appears only ONCE is a feature flag, not a job card.  An
    # expired BambooHR board ships one giant class attribute full of product
    # toggles - job_share_image, ats_job_board_refresh_btn, perf_joborg_skills,
    # zip_recruiter_boosted_jobs - and 24 of them scored 13, enough to promote a
    # board with no jobs at all to VERIFIED.  Real card classes repeat.
    cls = {c: n for c, n in cls.items() if n >= 2}
    cards = [n for c, n in cls.items() if n >= 3
             and re.search(r'job|posting|vacanc|role|opening|position', c)]
    # One jobish class repeated many times IS a listing, even when the links use a
    # path we do not recognise (Recruitee serves cards at /o/{slug}).  A marketing
    # page never repeats a job class five times, so this cannot manufacture proof.
    repeat_bonus = 3 if cards else 0
    neg = len(_JS_NEG.findall(b[:20_000]))
    score = (5 * min(len(_JS_JSONLD.findall(b)), 5)
             + min(sum(cls.values()), 120) // 4
             + 4 * min(len(detail), 6)
             + 2 * min(len(_JS_TEXT.findall(b)), 3)
             + (3 if nj else 0) + repeat_bonus
             + min(len(_JS_MORE.findall(b)), 2)
             + min(len(_JS_PAGING.findall(b)), 2)
             - 4 * min(neg, 2))
    jobs = max(nj) if nj else (max(cards) if cards else len(detail))
    return {"score": max(score, 0), "jobs": jobs, "links": len(detail),
            "why": (f"job-card classes={sum(cls.values())}, detail links={len(detail)}, "
                    f"headings={len(_JS_TEXT.findall(b))}, stated count={nj[:2]}, "
                    f"not-found markers={neg}")}


# A deliberately invalid career-site id.  Workday answers 404 when the *tenant*
# exists on that wd number but the site id is wrong, and 422 when it cannot
# resolve the tenant/site at all.  That makes one cheap POST per wd number a
# reliable tenant-existence sentinel, verified against salesforce/nvidia/workday
# (404 on the right dc) versus stripe/infosys/random (422 everywhere).
WORKDAY_SENTINEL_SITE = "__ats_probe__"

# mirrors scan.STATUS_RANK; kept local so vendors.py stays importable on its own
_RANK = {"VERIFIED": 5, "PROBABLE": 4, "UNVERIFIED": 3, "BLOCKED": 2, "MISS": 1}


def workday_sentinel(fetcher, tenant: str) -> Tuple[List[str], List[str], int]:
    """Return (wd numbers where the tenant looks real, wd numbers answering 401,
    number of requests used)."""
    live, auth, n = [], [], 0
    for dc in WORKDAY_DCS:
        api = (f"https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/"
               f"{WORKDAY_SENTINEL_SITE}/jobs")
        r = fetcher.request(api, "POST",
                            {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                            {"Content-Type": "application/json"}, max_bytes=4000)
        n += 1
        code = r.get("status")
        if code == 404:
            live.append(dc)
        elif code == 401:
            auth.append(dc)
        elif code == 200:
            live.append(dc)
    return live, auth, n


def run_workday_search(fetcher, tenant: str, budget: int = 64) -> Dict[str, Any]:
    """Find a Workday tenant and its career-site id.

    Phase 0 - one sentinel POST per wd number.  A 404 means the tenant exists on
              that number; a 422 everywhere means this company is not on
              Workday, and we stop having spent ~8 requests instead of ~150.
    Phase 1/2 - the career-site-id matrix, but only against the wd numbers that
              answered, and only for tenants that exist.

    Returns a probe-result dict in the same shape as run_probe()."""
    tried = 0
    best: Optional[Dict[str, Any]] = None
    # start at MISS so a plain MISS probe can never overwrite the sentinel's
    # more informative MISS (which distinguishes "no tenant at all" from
    # "tenant exists but the career-site id is unknown")
    best_rank = _RANK["MISS"]
    blocks: List[Tuple[str, str, Optional[int], str]] = []
    slug_variants = [tenant]
    for v in (tenant.replace("-", ""), tenant.replace("-", "_")):
        if v not in slug_variants:
            slug_variants.append(v)
    for t in slug_variants:
        live, auth, used = workday_sentinel(fetcher, t)
        tried += used
        if not live and not auth:
            if best is None:
                best = {"vendor_id": "workday", "vendor": "Workday Recruiting",
                        "url": None, "probe_url": None, "api_url": None,
                        "kind": "job_list", "status": "MISS",
                        "evidence": (f"sentinel: no Workday tenant named '{t}' on any "
                                     f"of {len(WORKDAY_DCS)} wd numbers "
                                     f"({used} probes, all 422)"),
                        "http_status": 422, "final_url": None, "redirects": [],
                        "job_count": None, "error": None, "waf": None,
                        "truncated": False, "elapsed_ms": 0,
                        "note": "tenant-existence sentinel"}
            continue
        dcs = live or auth
        phases = workday_site_ids(t)
        for phase in phases:
            for dc in dcs:
                for site in phase:
                    if tried >= budget:
                        break
                    tried += 1
                    api = f"https://{t}.{dc}.myworkdayjobs.com/wday/cxs/{t}/{site}/jobs"
                    resp = fetcher.request(
                        api, "POST",
                        {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""},
                        {"Content-Type": "application/json"}, max_bytes=48_000)
                    st, ev = j_wday()(resp)
                    if st == "BLOCKED":
                        blocks.append((dc, site, resp.get("status"), ev))
                    # keep only the best result seen so far: a later throttled or
                    # MISS probe must never overwrite a real finding
                    cand = {"vendor_id": "workday", "vendor": "Workday Recruiting",
                            "url": f"https://{t}.{dc}.myworkdayjobs.com/{site}",
                            "probe_url": api, "api_url": api, "kind": "job_list",
                            "status": st, "evidence": ev,
                            "http_status": resp.get("status"),
                            "final_url": resp.get("final_url"), "redirects": [],
                            "job_count": (resp.get("json") or {}).get("total")
                                          if isinstance(resp.get("json"), dict) else None,
                            "error": resp.get("error"), "waf": resp.get("waf"),
                            "truncated": resp.get("truncated"),
                            "elapsed_ms": resp.get("elapsed_ms"),
                            "note": f"found after {tried} CXS probe(s)"}
                    rank = _RANK.get(st, 0)
                    if rank > best_rank:
                        best_rank, best = rank, cand
                    if st == "VERIFIED":
                        return best
        if auth and not live:
            # The sentinel answered 401 on every site id: a Workday tenant with
            # this name exists, but it exposes no public career site.  Many
            # large companies run a Workday tenant for internal HCM while
            # recruiting through a completely different ATS (Stripe, Airbnb and
            # Palantir all do), so handing back this URL as "the company's ATS
            # portal" would be wrong.  Report a MISS and keep the fact in the
            # evidence so a human can see why.
            best = {"vendor_id": "workday", "vendor": "Workday Recruiting",
                    "url": None, "probe_url": None, "api_url": None,
                    "kind": "job_list", "status": "MISS",
                    "evidence": (f"sentinel: a Workday tenant named '{t}' exists on "
                                 f"{auth[0]} but is auth-gated (401) and exposes no "
                                 f"public career-site id - not a recruitment portal "
                                 f"({tried} probes)"),
                    "http_status": 401, "final_url": None, "redirects": [],
                    "job_count": None, "error": None, "waf": None,
                    "truncated": False, "elapsed_ms": 0,
                    "note": "tenant exists, no public career site"}
            best_rank = _RANK["MISS"]
            continue
    if best is not None and best["status"] != "MISS":
        return best                                 # a real finding beats throttling
    if blocks:
        # the tenant is real but the career-site search was throttled, so a MISS
        # would be a lie - report BLOCKED so the caller knows to retry
        dc, site, code, ev = blocks[0]
        return {"vendor_id": "workday", "vendor": "Workday Recruiting",
                "url": f"https://{tenant}.{dc}.myworkdayjobs.com/",
                "probe_url": None, "api_url": None, "kind": "job_list",
                "status": "BLOCKED",
                "evidence": (f"Workday tenant '{tenant}' exists (sentinel 404) but "
                             f"{len(blocks)} career-site probe(s) were throttled "
                             f"(HTTP {code} on {dc}/{site}) - retry"),
                "http_status": code, "final_url": None, "redirects": [],
                "job_count": None, "error": ev, "waf": True,
                "truncated": False, "elapsed_ms": 0,
                "note": "tenant exists, search throttled"}
    if best is not None:
        best = dict(best)
        best["status"] = "MISS"
        if best.get("note") == "tenant-existence sentinel":
            best["url"] = None                      # keep the sentinel's evidence:
            return best                             # no tenant exists at all
        # the sentinel proved the tenant exists, but no career-site id matched
        best["url"] = None
        best["evidence"] = (f"tenant exists but no (wd-number, career-site-id) "
                            f"combination matched for '{tenant}' in {tried} CXS probes")
        return best
    return {"vendor_id": "workday", "vendor": "Workday Recruiting", "url": None,
            "probe_url": None, "api_url": None, "kind": "job_list",
            "status": "MISS", "evidence": f"no Workday tenant matched in {tried} probes",
            "http_status": None, "final_url": None, "redirects": [],
            "job_count": None, "error": None, "waf": None, "truncated": False,
            "elapsed_ms": 0, "note": ""}


_reg("workday", "Workday Recruiting", _b_workday, tier=TIER_AUTHORITATIVE,
     home="https://www.workday.com",
     proof=[r"([A-Za-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/([A-Za-z0-9_\-]+)"],
     hints=[r"myworkdayjobs\.com", r"myworkday\.com", r"workday"],
     notes="POST /wday/cxs/{tenant}/{site}/jobs -> 200 real (with a job count), "
           "422 unknown. Both the tenant number (wd1..wd13) and the career-site "
           "id have to be discovered, so this vendor costs the most requests.")


# --- Workable (tier 2) ----------------------------------------------------- #
def _b_workable(slug, company, ctx):
    widget = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    return [_p("workable", "Workable", f"https://apply.workable.com/{slug}/",
               j_workable(), request_url=widget, api_url=widget, kind="job_list",
               max_bytes=64_000,
               note="TIER 2: Workable auto-provisions accounts for recognised company "
                    "names, so jobs.length>0 or a careers-page fingerprint is required")]


_reg("workable", "Workable", _b_workable, tier=TIER_CORROBORATED,
     home="https://www.workable.com",
     proof=[r"apply\.workable\.com/([A-Za-z0-9_\-]+)",
            r"embed\.workablehr\.com/([A-Za-z0-9_\-]+)",
            r"([A-Za-z0-9\-]+)\.workable\.com",
            r"[?&]wid=[A-Za-z0-9]{6,}"],
     hints=[r"workablehr\.com", r"workable\.com"],
     notes="apply.workable.com/{slug}/ is an SPA shell that answers 200 for anything; "
           "the widget API answers 200 with jobs:[] for recognised names that are not "
           "customers. Only jobs.length>0 or a fingerprint proves usage.")


# --- BambooHR (tier 2) ----------------------------------------------------- #
def _b_bamboohr(slug, company, ctx):
    base = f"https://{slug}.bamboohr.com"
    # The JSON list is the oracle and is probed first; the HTML page is kept as a
    # fallback because it still yields the human-facing portal URL.
    return [_p("bamboohr", "BambooHR", base + "/careers", j_bamboo_api(),
               request_url=base + "/careers/list", api_url=base + "/careers/list",
               kind="api", max_bytes=200_000,
               note="JSON job list - meta.totalCount is authoritative"),
            _p("bamboohr", "BambooHR", base + "/careers", j_bamboo(), kind="job_list")]


_reg("bamboohr", "BambooHR", _b_bamboohr, tier=TIER_CORROBORATED,
     home="https://www.bamboohr.com",
     proof=[r"([A-Za-z0-9\-]+)\.bamboohr\.com/(?:careers|jobs)"],
     hints=[r"bamboohr\.com", r"bamboohr"],
     notes="Wildcard DNS; unknown tenants 302 to www.bamboohr.com (clean MISS) and "
           "expired tenants land on /settings/account/expired.php.")


# --- Recruitee / Tellent --------------------------------------------------- #
def _b_recruitee(slug, company, ctx):
    return [_p("recruitee", "Recruitee (Tellent)", f"https://{slug}.recruitee.com/",
               j_recruitee(), kind="job_list")]


_reg("recruitee", "Recruitee (Tellent)", _b_recruitee, tier=TIER_AUTHORITATIVE,
     home="https://recruitee.com",
     proof=[r"([A-Za-z0-9\-]+)\.recruitee\.com",
            r"https?://[^\s\"'<>]*recruitee\.com/o/([A-Za-z0-9_\-]+)"],
     hints=[r"cdn\.recruitee\.com", r"recruitee", r"tellent\.com"],
     notes="Unknown tenants 301 to recruitee.com/careers_not_hosted.")


# --- Personio -------------------------------------------------------------- #
def _b_personio(slug, company, ctx):
    out = []
    # The XML feed is the oracle, and it reports the same tenant as the HTML
    # page.  Probing both cost 4 requests per slug, which - with a 34-probe
    # budget - capped Personio at 8 slugs while every other vendor reached 34.
    for dom in ("jobs.personio.de", "jobs.personio.com"):
        out.append(_p("personio", "Personio", f"https://{slug}.{dom}/", j_personio(),
                      request_url=f"https://{slug}.{dom}/xml",
                      api_url=f"https://{slug}.{dom}/xml", kind="job_list",
                      max_bytes=200_000, note="public XML job feed"))
    return out


_reg("personio", "Personio", _b_personio, tier=TIER_AUTHORITATIVE,
     home="https://www.personio.com",
     proof=[r"([A-Za-z0-9\-]+)\.jobs\.personio\.(?:de|com)"],
     hints=[r"personio\.de", r"personio\.com", r"jobs\.personio", r"personio-app"],
     notes="Wildcard DNS, HTTP decides: 200 real / 404 unknown. /xml is the job feed. "
           "429 is usually a Vercel bot checkpoint -> BLOCKED, not a hit.")


# --- SmartRecruiters (tier 2) ---------------------------------------------- #
def _b_smartrecruiters(slug, company, ctx):
    cands = [slug, slug.title(), slug.replace("-", ""), slug.replace("-", "_"),
             re.sub(r"[^A-Za-z0-9]", "", company.title())]
    out, seen = [], set()
    for c in cands:
        if c.lower() in seen:
            continue
        seen.add(c.lower())
        out.append(_p("smartrecruiters", "SmartRecruiters",
                      f"https://careers.smartrecruiters.com/{c}", j_sr(), kind="career_site",
                      note="TIER 2: an auto-provisioned 'Careers at X' page is not proof"))
        out.append(_p("smartrecruiters", "SmartRecruiters",
                      f"https://careers.smartrecruiters.com/{c}", j_sr_api(),
                      request_url=f"https://api.smartrecruiters.com/v1/companies/{c}/postings?limit=1",
                      api_url=f"https://api.smartrecruiters.com/v1/companies/{c}/postings?limit=1",
                      kind="api", max_bytes=8_000,
                      note="totalFound>0 is proof; totalFound==0 is returned for unknown "
                           "companies too, so it proves nothing"))
    return out


_reg("smartrecruiters", "SmartRecruiters", _b_smartrecruiters, tier=TIER_CORROBORATED,
     home="https://www.smartrecruiters.com",
     proof=[r"careers\.smartrecruiters\.com/([A-Za-z0-9_\-]+)",
            r"api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_\-]+)"],
     hints=[r"smartrecruiters\.com", r"smartrecruiters"],
     notes="Auto-provisions careers.smartrecruiters.com/{Name} for any recognisable "
           "company name. Proof = totalFound>0, a redirect to the company's own "
           "careers domain, or a fingerprint on the company's careers page.")


# --- Teamtailor ------------------------------------------------------------ #
def _b_teamtailor(slug, company, ctx):
    return [_p("teamtailor", "Teamtailor", f"https://{slug}.teamtailor.com/",
               j_teamtailor(), kind="job_list",
               post_check=sitemap_job_check(r"/jobs/", "Teamtailor /jobs/"))]


_reg("teamtailor", "Teamtailor", _b_teamtailor, tier=TIER_AUTHORITATIVE,
     home="https://www.teamtailor.com",
     proof=[r"([A-Za-z0-9\-]+)\.teamtailor\.com"],
     hints=[r"teamtailor\.com", r"cdn\.teamtailor", r"assets\.teamtailor"],
     notes="404 shell for unknown subdomains; custom domains are common.")


# --- Homerun --------------------------------------------------------------- #
def _b_homerun(slug, company, ctx):
    return [_p("homerun", "Homerun", f"https://{slug}.homerun.co/",
               j_homerun(), kind="job_list")]


_reg("homerun", "Homerun", _b_homerun, tier=TIER_AUTHORITATIVE, home="https://www.homerun.co",
     proof=[r"([A-Za-z0-9\-]+)\.homerun\.co"],
     hints=[r"homerun\.co", r"feed\.homerun\.co"],
     notes="Unknown slugs 302 to 404.homerun.co/working_at/{slug}.")


# --- SAP SuccessFactors ---------------------------------------------------- #
def _b_successfactors(slug, company, ctx):
    """Two distinct SuccessFactors hosting models:

    1. dedicated host   {slug}.jobs.sap.com / {slug}.jobs.successfactors.eu
                        no wildcard DNS, so NXDOMAIN is a definitive negative.
    2. shared host      career{N}.successfactors.eu/career?career_company={CODE}
                        CODE is an internal identifier (e.g. specSF2PROD) that
                        cannot be derived from the company name, so this form is
                        fingerprint-only.  A real code returns ~180 kB and the
                        title 'Career Opportunities'; an unknown code returns
                        ~3 kB with no title.
    """
    out = []
    for host in SF_HOSTS:
        out.append(_p("successfactors", "SAP SuccessFactors", f"https://{slug}.{host}/",
                      j_dns_first([host], "SAP SuccessFactors")))
        out.append(_p("successfactors", "SAP SuccessFactors", f"https://{slug}.{host}/search/",
                      j_dns_first([host], "SAP SuccessFactors"), kind="job_list"))
    return out


def j_sf_shared():
    """Judge for the shared-host career_company= form."""
    def judge(r):
        code = r.get("status")
        body = r.get("body") or ""
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if code != 200:
            return "MISS", f"HTTP {code}"
        t = page_title(body)
        if len(body) > 20000 and ("career opportunities" in t.lower() or "career_company" in body):
            return "VERIFIED", (f"SuccessFactors shared-host career page for this "
                                f"career_company code ({len(body)} bytes, title '{t[:38]}')")
        return "MISS", (f"HTTP 200 but only {len(body)} bytes and no career page - "
                        "unknown career_company code")
    return judge


_reg("successfactors", "SAP SuccessFactors", _b_successfactors, tier=TIER_AUTHORITATIVE,
     home="https://www.sap.com/products/hcm.html",
     proof=[r"https?://([A-Za-z0-9\-]+)\.jobs\.sap\.com[^\s\"'<>]*",
            r"https?://([A-Za-z0-9\-]+)\.jobs\.successfactors\.(?:com|eu)[^\s\"'<>]*",
            r"https?://([A-Za-z0-9\-]+)\.[a-z0-9\-]*sapsf\.com[^\s\"'<>]*",
            # shared-host form - the single most common SuccessFactors URL shape
            r"https?://career\d*\.successfactors\.(?:eu|com)/career\?[^\s\"'<>]*career_company=[A-Za-z0-9_\-]+[^\s\"'<>]*",
            r"https?://[^\s\"'<>]*successfactors\.(?:eu|com)/[^\s\"'<>]*career_company=[A-Za-z0-9_\-]+[^\s\"'<>]*",
            r"https?://[^\s\"'<>]*\bsapsf\.com/[^\s\"'<>]*career_company=[A-Za-z0-9_\-]+[^\s\"'<>]*"],
     hints=[r"successfactors", r"jobs\.sap\.com", r"sapsf\.com", r"career_company="],
     notes="Two hosting models. Dedicated host {slug}.jobs.sap.com has no wildcard DNS, "
           "so NXDOMAIN is a genuine MISS. Shared host career{N}.successfactors.eu/"
           "career?career_company={CODE} is far more common in the wild (e.g. Specsavers "
           "-> specSF2PROD) and CODE cannot be guessed, so it is fingerprint-only.")


# --- Oracle Taleo ---------------------------------------------------------- #
def _b_taleo(slug, company, ctx):
    out = []
    for host in TALEO_HOSTS:
        out.append(_p("taleo", "Oracle Taleo",
                      f"https://{slug}.{host}/careersection/2/jobsearch.ftl",
                      j_dns_first([host], "Oracle Taleo"), kind="job_list"))
        out.append(_p("taleo", "Oracle Taleo", f"https://{slug}.{host}/",
                      j_dns_first([host], "Oracle Taleo")))
    return out


_reg("taleo", "Oracle Taleo", _b_taleo, tier=TIER_AUTHORITATIVE,
     home="https://www.oracle.com/human-capital-management/taleo/",
     proof=[r"https?://([A-Za-z0-9\-]+)\.taleo\.net/[^\s\"'<>]*",
            r"https?://([A-Za-z0-9\-]+)\.taleo\d*\.com/[^\s\"'<>]*",
            r"https?://([A-Za-z0-9\-]+)\.taleoenterprise\.com/[^\s\"'<>]*"],
     hints=[r"taleo", r"jobsearch\.ftl", r"careers\.oracle\.com"],
     notes="No wildcard DNS: NXDOMAIN == MISS. The /careersection/{n}/ number varies "
           "(1, 2, 100, 102 ...). Oracle is migrating tenants to Oracle Cloud Recruiting.")


# --- iCIMS ----------------------------------------------------------------- #
def _b_icims(slug, company, ctx):
    cands = [f"careers-{slug}", f"careers.{slug}", slug, f"jobs-{slug}",
             f"{slug}careers", f"globalcareers-{slug}", f"careers{slug}"]
    out, seen = [], set()
    for c in cands:
        host = c if "." in c else f"{c}.icims.com"
        if host in seen:
            continue
        seen.add(host)
        out.append(_p("icims", "iCIMS", f"https://{host}/jobs", j_icims(), kind="job_list"))
    return out


_reg("icims", "iCIMS", _b_icims, tier=TIER_AUTHORITATIVE, home="https://www.icims.com",
     proof=[r"([A-Za-z0-9\-\.]+)\.icims\.com", r"https?://careers\.icims\.com[^\s\"'<>]*"],
     hints=[r"icims", r"[?&]in_iframe=1", r"iCIMS"],
     notes="Non-tenants answer 404 '<title>gone: {host} : dc409</title>'; real portals "
           "answer 200. AWS WAF intermittently returns a 405 captcha from datacentre "
           "IPs -> BLOCKED. Tenant ids often carry a careers-/jobs- prefix.")


# --- softgarden ------------------------------------------------------------ #
def _b_softgarden(slug, company, ctx):
    return [_p("softgarden", "softgarden", f"https://{slug}.softgarden.io/",
               j_status_ok(["softgarden.io"]), kind="job_list"),
            _p("softgarden", "softgarden", f"https://{slug}.career.softgarden.de/",
               j_softgarden_de())]


_reg("softgarden", "softgarden", _b_softgarden, tier=TIER_AUTHORITATIVE,
     home="https://softgarden.com",
     proof=[r"([A-Za-z0-9\-]+)\.softgarden\.io", r"([A-Za-z0-9\-]+)\.career\.softgarden\.de"],
     hints=[r"softgarden", r"jobs\.softgarden\.io"],
     notes="softgarden.io answers 404 'Unknown subdomain'; career.softgarden.de 500 "
           "('Keine Karriere-Subdomain gefunden'). Random names 404, so this is tier 1.")


# --- d.vinci --------------------------------------------------------------- #
def _b_dvinci(slug, company, ctx):
    return [_p("dvinci", "d.vinci", f"https://{slug}.dvinci-easy.com/", j_dvinci(),
               kind="job_list")]


_reg("dvinci", "d.vinci", _b_dvinci, tier=TIER_AUTHORITATIVE, home="https://www.dvinci.de",
     proof=[r"([A-Za-z0-9\-]+)\.dvinci-easy\.com"],
     hints=[r"dvinci", r"d\.vinci", r"bewerbung\.dvinci"],
     notes="No wildcard DNS -> NXDOMAIN is authoritative.")


# --- Flatchr --------------------------------------------------------------- #
def _b_flatchr(slug, company, ctx):
    return [_p("flatchr", "Flatchr", f"https://{slug}.flatchr.io/",
               j_status_ok(["flatchr.io"]), kind="job_list")]


_reg("flatchr", "Flatchr", _b_flatchr, tier=TIER_AUTHORITATIVE, home="https://www.flatchr.io",
     proof=[r"([A-Za-z0-9\-]+)\.flatchr\.io"], hints=[r"flatchr"],
     notes="404 -> /fr/404-not-found/")


# --- Taleez ---------------------------------------------------------------- #
def _b_taleez(slug, company, ctx):
    return [_p("taleez", "Taleez", f"https://{slug}.taleez.com/", j_taleez(),
               kind="job_list")]


_reg("taleez", "Taleez", _b_taleez, tier=TIER_AUTHORITATIVE, home="https://taleez.com",
     proof=[r"([A-Za-z0-9\-]+)\.taleez\.com"], hints=[r"taleez"],
     notes="Unknown slug -> 404 '<title>Erreur</title>'. Rate-limited (429). Many "
           "customers use jobs.{company}.com instead of a taleez.com subdomain, which "
           "only the fingerprint engine can see.")


# --- Beetween -------------------------------------------------------------- #
def _b_beetween(slug, company, ctx):
    # app.beetween.com/{slug}/recrutement is a Vue SPA that answers 200 with the
    # same shell for any path, so probing it yields false positives. Only the
    # {slug}.betheen.com hostname (no wildcard DNS) is trustworthy.
    return [_p("beetween", "Beetween", f"https://{slug}.betheen.com/",
               j_dns_first(["betheen.com"], "Beetween"), kind="job_list",
               note="app.beetween.com/{slug}/recrutement is the SPA equivalent but "
                    "cannot be verified without JS")]


_reg("beetween", "Beetween", _b_beetween, tier=TIER_AUTHORITATIVE,
     home="https://www.betheen.com",
     proof=[r"([A-Za-z0-9\-]+)\.betheen\.com",
            r"app\.beetween\.com/([A-Za-z0-9_\-]+)"],
     hints=[r"beetween", r"betheen"],
     notes="First ATS provider in the France Travail partner feed; tenants are usually "
           "at app.beetween.com/{tenant}/recrutement (JS-only, so UNVERIFIED).")


# --- Pinpoint (tier 2) ----------------------------------------------------- #
def _b_pinpoint(slug, company, ctx):
    return [_p("pinpoint", "Pinpoint", f"https://{slug}.pinpointhq.com/", j_pinpoint(),
               kind="job_list",
               post_check=sitemap_job_check(r"/postings/[0-9a-f]{8}-", "Pinpoint /postings/"),
               note="TIER 2: Pinpoint auto-provisions a branded 'Jobs at X' page for any "
                    "recognised organisation. The sitemap.xml is the proof - only real "
                    "customers expose /postings/{uuid} permalinks.")]


_reg("pinpoint", "Pinpoint", _b_pinpoint, tier=TIER_CORROBORATED,
     home="https://www.usepinpoint.com",
     proof=[r"([A-Za-z0-9\-]+)\.pinpointhq\.com", r"app\.pinpointhq\.com/([A-Za-z0-9_\-]+)"],
     hints=[r"pinpointhq\.com", r"pinpoint"],
     notes="'Jobs at X | X Careers' is generated for any recognised organisation "
           "(Bosch, Infosys and TCS all render one and none are customers). "
           "Proof = live job links or a careers-page fingerprint.")


# --- Zoho Recruit (tier 3) ------------------------------------------------- #
def _b_zoho(slug, company, ctx):
    return []


_reg("zoho", "Zoho Recruit", _b_zoho, tier=TIER_FINGERPRINT, guessable=False,
     home="https://www.zoho.com/recruit/",
     proof=[r"https?://recruit\.zohopublic\.com/careers/[A-Za-z0-9_\-]+[^\s\"'<>]*",
            r"https?://[A-Za-z0-9\-\.]*zohorecruit\.com[^\s\"'<>]*",
            r"https?://recruit\.zoho\.com/[^\s\"'<>]*"],
     hints=[r"zoho", r"[?&]zrp_"],
     notes="Career sites live at recruit.zohopublic.com/careers/{opaque-site-id}; the id "
           "is a random token that cannot be derived from the company name. Fingerprint "
           "only - but the fingerprint yields the exact, ready-to-use URL.")


# --- Eploy (tier 3) -------------------------------------------------------- #
def _b_eploy(slug, company, ctx):
    return []


_reg("eploy", "Eploy", _b_eploy, tier=TIER_FINGERPRINT, guessable=False,
     home="https://www.eploy.com",
     proof=[r"[?&]vacancyId=\d+[^\s\"'<>]*",
            r"[?&]workflowInstanceId=[^\s\"'<>]*",
            r"https?://[^\s\"'<>]*eploy\.co\.uk[^\s\"'<>]*",
            r"https?://[^\s\"'<>]*/vacancies/vacancy-search[^\s\"'<>]*"],
     hints=[r"eploy", r"vacancy-search"],
     notes="Eploy ships as a job board embedded in the employer's own domain "
           "(e.g. join.specsavers.com/ca/jobs). No {company}.eploy.* host exists. "
           "Beware: the bare string 'eploy' occurs inside 'deploy' - the proof "
           "patterns therefore never match on the word alone.")


# --- JOIN ------------------------------------------------------------------ #
def _b_join(slug, company, ctx):
    return [_p("join", "JOIN", f"https://{slug}.join.com/", j_join(), kind="job_list")]


_reg("join", "JOIN", _b_join, tier=TIER_AUTHORITATIVE, home="https://join.com",
     proof=[r"([A-Za-z0-9\-]+)\.join\.com", r"join\.com/companies/([A-Za-z0-9_\-]+)"],
     hints=[r"auth\.join\.com", r"join\.com"],
     notes="404 shell when absent; <title>Jobs at X | JOIN</title> when present.")


# --- Coveto ---------------------------------------------------------------- #
def _b_coveto(slug, company, ctx):
    return [_p("coveto", "Coveto", f"https://{slug}.coveto.fr/",
               j_dns_first(["coveto.fr"], "Coveto"))]


_reg("coveto", "Coveto", _b_coveto, tier=TIER_AUTHORITATIVE, home="https://www.coveto.fr",
     proof=[r"([A-Za-z0-9\-]+)\.coveto\.fr"], hints=[r"coveto"],
     notes="No wildcard DNS -> NXDOMAIN is authoritative.")


# --- rexx systems ---------------------------------------------------------- #
def _b_rexx(slug, company, ctx):
    return [_p("rexx", "rexx systems", f"https://{slug}.rexx-systems.com/",
               j_dns_first(["rexx-systems.com"], "rexx systems"), kind="job_list")]


_reg("rexx", "rexx systems", _b_rexx, tier=TIER_AUTHORITATIVE,
     home="https://www.rexx-systems.com",
     proof=[r"([A-Za-z0-9\-]+)\.rexx-systems\.com"], hints=[r"rexx-systems", r"rexx"],
     notes="No wildcard DNS -> NXDOMAIN is authoritative.")


# --- Bullhorn (tier 3) ----------------------------------------------------- #
def _b_bullhorn(slug, company, ctx):
    return []


_reg("bullhorn", "Bullhorn", _b_bullhorn, tier=TIER_FINGERPRINT, guessable=False,
     home="https://www.bullhorn.com",
     proof=[r"https?://public-rest\d*\.bullhornstaffing\.com/rest-services/[A-Za-z0-9]+[^\s\"'<>]*",
            r"https?://rest\d*\.bullhornstaffing\.com/rest-services/[A-Za-z0-9]+[^\s\"'<>]*",
            r"https?://[A-Za-z0-9\-\.]*bhlts\.com[^\s\"'<>]*"],
     hints=[r"bullhorn"],
     notes="Staffing-agency ATS. Public job feeds live at "
           "(public-)rest{n}.bullhornstaffing.com/rest-services/{corpToken}/... and "
           "corpToken is opaque. Fingerprint only.")


# --- Avature --------------------------------------------------------------- #
def j_avature():
    """*.avature.net has no wildcard DNS, so a resolved hostname does prove the
    tenant exists.  But the default page is a JavaScript login redirect
    (`window.location.href = "https://{tenant}.avature.net/Login/"`), not a
    public job listing - Avature customers put their candidate-facing site on
    their own domain.  So: real tenant, but the URL we can hand back is a
    login page.  That is PROBABLE with an explanation, never VERIFIED."""
    def judge(r):
        if r.get("dns_error"):
            return "MISS", "NXDOMAIN - Avature never provisioned this hostname"
        code = r.get("status")
        body = r.get("body") or ""
        t = page_title(body)
        if code == 404:
            return "MISS", f"HTTP 404 on {final_host(r)} - no public career page"
        if code in (403, 429, 503):
            return "BLOCKED", f"HTTP {code}"
        if "loginServer" in body or "/Login/" in body or 'name="robots" content="noindex"' in body[:400]:
            return "PROBABLE", ("Avature tenant exists (DNS provisioned) but the public URL is a "
                                "login redirect, not a job listing - the candidate-facing site is "
                                "on the company's own domain")
        if code == 200 and t and len(body) > 20000:
            return "VERIFIED", f"HTTP 200 Avature career page (title '{t[:44]}', {len(body)} bytes)"
        return "PROBABLE", f"DNS provisioned; HTTP {code}, {len(body)} bytes, title '{t[:32]}'"
    return judge


def _b_avature(slug, company, ctx):
    return [_p("avature", "Avature", f"https://{slug}.avature.net/", j_avature(),
               note="DNS proves the tenant; the default page is a login redirect")]


_reg("avature", "Avature", _b_avature, tier=TIER_AUTHORITATIVE,
     home="https://www.avature.net",
     proof=[r"([A-Za-z0-9\-]+)\.avature\.net"], hints=[r"avature"],
     notes="No wildcard DNS -> NXDOMAIN is authoritative. Real tenants redirect to "
           "/{locale}/main. Large enterprises usually front it with their own domain.")


# --------------------------------------------------------------------------- #
# fingerprint tables
# --------------------------------------------------------------------------- #

PROOF_FINGERPRINTS: List[tuple] = []   # (vendor_id, compiled) - proves usage
HINT_FINGERPRINTS: List[tuple] = []    # (vendor_id, compiled) - slug seeds only
for _vid, _v in VENDORS.items():
    for _p_ in _v["proof"]:
        PROOF_FINGERPRINTS.append((_vid, _p_))
    for _h in _v["hints"]:
        HINT_FINGERPRINTS.append((_vid, _h))

# Subdomains that are vendor infrastructure rather than a tenant career site.
NON_TENANT_SUBS = {"cdn", "assets", "static", "api", "app", "status", "support",
                   "www", "mail", "blog", "docs", "help", "analytics", "staging",
                   "dev", "test", "admin", "auth", "404", "embed", "widget",
                   "public-rest", "rest", "universal"}

# Host labels that betray vendor infrastructure even when they look like a
# tenant (e.g. careers-analytics.recruitee.com is a CDN, not a career site).
# "careers"/"jobs" alone are deliberately NOT here: careers.rambus.icims.com
# and jobs.sap.com are genuine.
INFRA_TOKENS = ("analytics", "cdn", "assets", "static", "widget", "tracking",
                "telemetry", "metrics", "status", "support", "docs", "help",
                "staging", "sandbox", "internal")


def is_infra_host(host: str) -> bool:
    labels = (host or "").lower().split(".")
    return any(tok in lab for lab in labels for tok in INFRA_TOKENS)


def tenant_slug_from_host(host: str, base: str) -> Optional[str]:
    """`bupa.pinpointhq.com` + base `pinpointhq.com` -> `bupa`, but reject
    infrastructure subdomains like `cdn.pinpointhq.com`."""
    h = (host or "").lower()
    b = (base or "").lower().lstrip(".")
    if not h.endswith("." + b) or h == b:
        return None
    sub = h[: -(len(b) + 1)]
    if "." in sub:                       # e.g. careers.softgarden.de under softgarden.de
        sub = sub.split(".")[0]
    if not sub or sub in NON_TENANT_SUBS or is_infra_host(sub):
        return None
    return sub


VENDOR_ORDER = list(VENDORS.keys())

# --------------------------------------------------------------------------- #
# calibration data for --selftest: (vendor, slug, expected)
#   "hit"  -> must come back VERIFIED or PROBABLE
#   "miss" -> must come back MISS / BLOCKED / UNVERIFIED / NOT_FOUND
# The "miss" rows matter most: they are the false-positive controls for the
# tier-2 vendors that answer HTTP 200 for any recognisable company name.
# --------------------------------------------------------------------------- #
RANDOM = "qxrihnrvp"

CALIBRATION: List[tuple] = [
    ("greenhouse",      "stripe",                    "hit"),
    ("greenhouse",      FAKE,                        "miss"),
    ("lever",           "palantir",                  "hit"),
    ("lever",           FAKE,                        "miss"),
    ("ashby",           "ramp",                      "hit"),
    ("ashby",           FAKE,                        "miss"),
    ("workday",         "workday",                   "hit"),
    ("workday",         FAKE,                        "miss"),
    # Workable publishes jobs:[] through the widget API for both real customers
    # that recruit elsewhere and for auto-provisioned stubs, so a probe alone can
    # never prove usage.  Both rows are therefore expected to come back
    # UNVERIFIED/MISS and the finding only survives with a careers-page fingerprint.
    ("workable",        "wolt",                      "miss"),   # real customer, 0 published jobs
    ("workable",        FAKE,                        "miss"),
    ("workable",        "infosys",                   "miss"),   # auto-provisioned stub
    ("bamboohr",        "gusto",                     "miss"),   # tenant exists, page expired
    ("bamboohr",        FAKE,                        "miss"),
    ("recruitee",       "personio",                  "hit"),
    ("recruitee",       FAKE,                        "miss"),
    ("personio",        "personio-gmbh",             "hit"),
    ("personio",        FAKE,                        "miss"),
    ("smartrecruiters", "BoschGroup",                "hit"),
    ("smartrecruiters", FAKE,                        "miss"),
    ("smartrecruiters", "Zoho",                      "miss"),   # auto-provisioned page
    ("teamtailor",      "polestar",                  "hit"),
    ("teamtailor",      FAKE,                        "miss"),
    ("homerun",         "woonstad-rotterdam",        "hit"),
    ("homerun",         FAKE,                        "miss"),
    ("softgarden",      "softgarden",                "hit"),
    ("softgarden",      FAKE,                        "miss"),
    ("softgarden",      RANDOM,                      "miss"),
    ("dvinci",          "suedzucker-gruppe",         "hit"),
    ("dvinci",          FAKE,                        "miss"),
    ("flatchr",         "flatchr",                   "hit"),
    ("flatchr",         FAKE,                        "miss"),
    ("taleez",          FAKE,                        "miss"),
    ("pinpoint",        "sage",                      "miss"),   # real board, but its
                                                                # sitemap.xml now lists 0
                                                                # /postings/ URLs, so there
                                                                # is no proof of a live
                                                                # listing => UNVERIFIED
                                                                # (same shape as bupa).
                                                                # Was "hit" until 2026-10.
    ("pinpoint",        "bupa",                      "miss"),   # real board, 0 postings live
    ("pinpoint",        FAKE,                        "miss"),
    ("pinpoint",        "infosys",                   "miss"),   # auto-provisioned stub
    ("join",            "moss",                      "hit"),
    ("join",            FAKE,                        "miss"),
    ("icims",           "rambus",                    "hit"),
    ("icims",           RANDOM,                      "miss"),
    ("avature",         "careers",                   "hit"),    # public career page
    ("avature",         "specsavers",                "soft"),   # real tenant, but a login
                                                                # redirect => PROBABLE is correct
    ("avature",         FAKE,                        "miss"),
    ("successfactors",  FAKE,                        "miss"),   # dedicated host: NXDOMAIN
    ("taleo",           FAKE,                        "miss"),
    ("coveto",          FAKE,                        "miss"),
    ("rexx",            FAKE,                        "miss"),
    ("beetween",        FAKE,                        "miss"),
    ("eploy",           FAKE,                        "miss"),
    ("zoho",            FAKE,                        "miss"),
    ("bullhorn",        FAKE,                        "miss"),
]


def page_confirms_listing(sig: Dict[str, Any]) -> bool:
    """Is this fetched page a live job listing, or just a page that mentions jobs?

    Two conditions, because either one alone has a hole:

    * score >= JOB_SIGNAL_THRESHOLD - it carries job markup;
    * jobs > 0 or links > 0 - and that markup resolves to actual postings.

    An expired BambooHR board and an Ashby SPA shell both ship enough job
    vocabulary to look like a listing while naming no job at all.  Requiring a
    concrete posting is what separates them, and all 8 live server-rendered
    boards used for calibration satisfy it.
    """
    return bool(sig) and sig.get("score", 0) >= JOB_SIGNAL_THRESHOLD and \
        (sig.get("jobs", 0) > 0 or sig.get("links", 0) > 0)
