"""Stop latency (2026-11-12): pressing Stop waited 15-20 min because
cancellation is cooperative and several fetcher/pagination loops had no
cancel checkpoint, while _drain waited silently for in-flight targets.

These tests pin: (1) provider fetcher loops break promptly on cancel and
return what they have; (2) the scroll/pagination helpers raise
ScanCancelled between steps; (3) the drain/pool joins poll in slices and
name the stragglers instead of waiting silently.
"""
import json
import threading
import time

import pytest

from packtoworkeu.scanning.career import career_scanner as cs
from packtoworkeu.scanning.common import ScanCancelled


def _bare(cancelled=True):
    obj = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    obj.config = cs.ProductionScannerConfig
    obj.cancel_event = threading.Event()
    if cancelled:
        obj.cancel_event.set()
    obj.pause_event = None
    obj.calls = []

    def _fetch(url, headers, post_body=None):
        obj.calls.append(url)
        raise AssertionError("HTTP must not be called after cancel")

    obj._http_fetch_with_retry = _fetch
    return obj


# ── fetcher loops honour cancel before any HTTP ─────────────────────────────

def test_workday_list_loop_honours_cancel():
    s = _bare()
    jobs, note = s._fetch_workday_jobs(
        {"board_slug": "nxp.myworkdayjobs.com|nxp|careers",
         "careers_url": "https://nxp.myworkdayjobs.com/careers"})
    assert jobs == [] and s.calls == []
    assert "0 with JD text" in note


def test_digitalrecruiters_loops_honour_cancel():
    s = _bare()
    jobs, note = s._fetch_digitalrecruiters_jobs(
        {"board_slug": "lavoraconnoi.decathlon-careers.it",
         "careers_url": "https://lavoraconnoi.decathlon-careers.it/it/annonces"})
    assert jobs == [] and s.calls == []
    assert note.startswith("digitalrecruiters API: 0")


def test_oracle_list_loop_honours_cancel():
    s = _bare()
    jobs, note = s._fetch_oracle_jobs(
        {"board_slug": "", "careers_url": "https://example.com/sites/CX_1"})
    assert jobs == [] and s.calls == []
    assert note == "oracle API: 0"


def test_bamboohr_detail_loop_honours_cancel():
    s = _bare()

    def _fetch(url, headers, post_body=None):
        s.calls.append(url)
        return json.dumps({"result": [
            {"id": 1, "jobOpeningName": "Software Engineer",
             "location": {"city": "Munich, Germany"}}]})

    s._http_fetch_with_retry = _fetch
    jobs, note = s._fetch_bamboohr_jobs(
        {"board_slug": "acme", "careers_url": "https://acme.bamboohr.com/careers"})
    # The single list GET happened, but no per-job detail GET followed it.
    assert jobs == [] and len(s.calls) == 1


# ── DOM helpers raise between steps ─────────────────────────────────────────

class _NoTouch:
    def evaluate(self, *a, **k):
        raise AssertionError("page must not be touched after cancel")


def test_infinite_scroll_raises_on_cancel():
    s = _bare()
    with pytest.raises(ScanCancelled):
        s.aggressive_infinite_scroll(_NoTouch(), _NoTouch())


def test_advanced_pagination_raises_on_cancel():
    s = _bare()
    with pytest.raises(ScanCancelled):
        s.execute_advanced_pagination(_NoTouch(), _NoTouch(), 1)


# ── detail enrichment queued fetches become no-ops ──────────────────────────

def test_enrich_ns_locations_noops_on_cancel():
    s = _bare()
    called = []
    s._detail_location_from_url = lambda url: called.append(url) or ("Munich, Germany", "detail")
    rows = [{"Job Location": "Not Specified", "Job URL": "https://x.example/1"}]
    assert s._enrich_ns_locations(rows) == 0
    assert called == []


# ── pool join polls in slices and returns ───────────────────────────────────

def test_finish_worker_pool_joins_slow_future():
    import concurrent.futures as cf
    s = _bare(cancelled=False)
    s._worker_browsers_lock = threading.Lock()
    s._worker_browsers_all = []
    pool = cf.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(time.sleep, 0.3)
    t0 = time.monotonic()
    s._finish_worker_pool(pool, 1, [fut])
    assert time.monotonic() - t0 < 5


# ── drain visibility pins (the wait must name stragglers, not hang silently) ─

def test_drain_and_join_waits_are_visible():
    src = open(cs.__file__, encoding="utf-8").read()
    assert "[stop] waiting on" in src
    assert "cf.wait(pending, timeout=5)" in src
    assert "[stop] still joining worker pool" in src
