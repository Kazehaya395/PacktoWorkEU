"""Deep-dive 2026-11-12 (D1 + P1): cluster-less Workday tenants and
parallel career Workday JD details.

D1: tenants on <tenant>.myworkdayjobs.com (no wdN cluster, e.g. NXP) were
invisible to _SNIFF_WORKDAY, workday_cxs_detail_url and tailoring's
workday_cxs_url, so they DOM-crawled a client-rendered SPA and their JDs
could never come from CXS.

P1: _fetch_workday_jobs fetched JD details serially (~0.3-0.8s each, up to
~25 min for a 2,000-posting board). Details now run in a small pool, in
list order, under the same JD budget (mirrors the ATS adapter).
"""
import json
import threading
import time

import pytest

from packtoworkeu.scanning.career import career_scanner as cs


# ── D1: cluster-less hosts recognised everywhere ────────────────────────────

def test_sniff_recognises_clusterless_workday():
    prov, slug, _ = cs.sniff_provider("", "https://nxp.myworkdayjobs.com/careers")
    assert prov == "workday"
    assert slug == "nxp.myworkdayjobs.com|nxp|careers"


def test_sniff_still_recognises_clustered_workday():
    prov, slug, _ = cs.sniff_provider(
        "", "https://adobe.wd5.myworkdayjobs.com/external_experienced")
    assert prov == "workday"
    assert slug == "adobe.wd5.myworkdayjobs.com|adobe|external_experienced"


def test_cxs_detail_url_clusterless():
    f = cs.workday_cxs_detail_url
    assert f("https://nxp.myworkdayjobs.com/careers/job/Munich/Engineer_R1") == (
        "https://nxp.myworkdayjobs.com/wday/cxs/nxp/careers"
        "/job/Munich/Engineer_R1")


def test_cxs_detail_url_clustered_unchanged():
    f = cs.workday_cxs_detail_url
    assert f("https://embl.wd103.myworkdayjobs.com/EMBL/job/Hinxton/T_JR1") == (
        "https://embl.wd103.myworkdayjobs.com/wday/cxs/embl/EMBL"
        "/job/Hinxton/T_JR1")
    assert f("https://example.com/job/1") == ""


def test_tailoring_cxs_url_clusterless():
    pytest.importorskip("PySide6")
    from packtoworkeu.ui.tabs.tailoring import workday_cxs_url
    assert workday_cxs_url(
        "https://nxp.myworkdayjobs.com/careers/job/Munich/Engineer_R1") == (
        "https://nxp.myworkdayjobs.com/wday/cxs/nxp/careers"
        "/job/Munich/Engineer_R1")


# ── P1: parallel detail fetching in _fetch_workday_jobs ─────────────────────

class _FakeWorkdayCareer:
    """Bare CareerPortalScanner whose HTTP layer serves a fake CXS board."""

    def __init__(self, n, delay=0.0):
        obj = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
        obj.cancel_event = None
        obj.pause_event = None
        self.obj = obj
        self.n = n
        self.delay = delay
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.detail_calls = 0
        obj._http_fetch_with_retry = self._fetch

    def _fetch(self, url, headers, post_body=None):
        if url.endswith("/jobs"):
            postings = [{
                "title": f"Engineer {i}",
                "externalPath": f"/job/Munich/Engineer_{i}",
                "locationsText": "",
                "bulletFields": [],
            } for i in range(self.n)]
            return json.dumps({"total": self.n, "jobPostings": postings})
        with self.lock:
            self.detail_calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            idx = int(url.rsplit("_", 1)[1])
            return json.dumps({"jobPostingInfo": {
                "jobDescription": f"<p>Job {idx} description</p>",
                "location": "Munich, Germany"}})
        finally:
            with self.lock:
                self.active -= 1

    def fetch(self, **row):
        row.setdefault("board_slug", "nxp.myworkdayjobs.com|nxp|careers")
        row.setdefault("careers_url", "https://nxp.myworkdayjobs.com/careers")
        return self.obj._fetch_workday_jobs(row)


def test_workday_details_run_in_parallel_and_keep_order(monkeypatch):
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_WORKERS", "6")
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_DETAILS", "2000")
    fake = _FakeWorkdayCareer(12, delay=0.05)
    jobs, note = fake.fetch()
    assert len(jobs) == 12
    assert fake.max_active > 1  # actually concurrent, not serial
    # Results applied in list order regardless of completion order.
    for i, job in enumerate(jobs):
        assert f"Job {i} description" in job["jd_text"]
        assert job["location_hint"] == "Munich, Germany"
        assert "_workday_detail" not in job  # internal key cleaned up
    assert "(12 with JD text)" in note


def test_workday_detail_budget_still_caps_jds(monkeypatch):
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_WORKERS", "6")
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_DETAILS", "3")
    fake = _FakeWorkdayCareer(10)
    jobs, note = fake.fetch()
    assert len(jobs) == 10
    assert sum(1 for j in jobs if j.get("jd_text")) == 3
    # Budget decided in list order: the first three postings got the JDs.
    assert all(j.get("jd_text") for j in jobs[:3])
    assert not any(j.get("jd_text") for j in jobs[3:])
    assert "(3 with JD text)" in note
    # No wave beyond the one that satisfied the budget was started.
    assert fake.detail_calls <= 6


def test_workday_serial_path_with_one_worker(monkeypatch):
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_WORKERS", "1")
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_DETAILS", "2000")
    fake = _FakeWorkdayCareer(4, delay=0.01)
    jobs, note = fake.fetch()
    assert len(jobs) == 4
    assert fake.max_active == 1
    assert "(4 with JD text)" in note


def test_workday_fetcher_accepts_clusterless_seed_url(monkeypatch):
    """D1b: bare slug + cluster-less seed URL reaches the CXS API."""
    monkeypatch.setenv("PACKTOWORKEU_WORKDAY_WORKERS", "2")
    fake = _FakeWorkdayCareer(3)
    jobs, note = fake.fetch(board_slug="",
                            careers_url="https://nxp.myworkdayjobs.com/careers")
    assert len(jobs) == 3
    assert jobs[0]["job_url"].startswith("https://nxp.myworkdayjobs.com/")
