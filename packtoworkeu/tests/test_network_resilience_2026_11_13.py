"""Universal network resilience (2026-11-13, audit G1/G2).

Run 20261113T131421 lost its internet route mid-scan and recorded 80
portal ERRORs for one local outage; none were retryable, because the
end-of-run sweep only knew DNS failures and Resume treated every 'error'
row as settled. These tests pin the universal behaviour: no-route
failures pause/retry/resume, portal walls never do.
"""
import threading

import pytest

from packtoworkeu.scanning.career.career_scanner import (
    CareerPortalScanner, is_no_route_failure, seed_page_failure_text)


# ── G1: the no-route predicate ─────────────────────────────────────────────

def test_no_route_predicate_matches_outage_family():
    msg = seed_page_failure_text(
        "https://example.de/careers",
        "Page.goto: net::ERR_INTERNET_DISCONNECTED", offline=True)
    assert is_no_route_failure(msg)
    assert is_no_route_failure("net::ERR_NAME_NOT_RESOLVED")
    assert is_no_route_failure("", "seed attempt 1: ERR_CONNECTION_TIMED_OUT")


def test_no_route_predicate_never_matches_portal_walls():
    assert not is_no_route_failure("blocked by bot protection: Cloudflare challenge")
    assert not is_no_route_failure("listing access blocked: HTTP 403")
    assert not is_no_route_failure("seed page did not load: https://x.example")
    assert not is_no_route_failure("")


# ── G1: the offline gate ───────────────────────────────────────────────────

def _bare_scanner():
    sc = CareerPortalScanner.__new__(CareerPortalScanner)
    sc.cancel_event = None
    sc.pause_event = None
    sc._route_down = threading.Event()
    sc._offline_streak = 0
    sc._probe_hosts = ("example.de",)
    return sc


def test_wait_for_route_resumes_when_probe_recovers():
    sc = _bare_scanner()
    sc._route_down.set()
    sc._probe_route_once = lambda: True
    assert sc._wait_for_route("Acme") is False
    assert not sc._route_down.is_set()
    assert sc._offline_streak == 0


def test_wait_for_route_expires_without_fabricating_a_verdict(monkeypatch):
    monkeypatch.setenv("PACKTOWORKEU_ROUTE_WAIT_SEC", "0")
    sc = _bare_scanner()
    sc._route_down.set()
    sc._probe_route_once = lambda: False
    assert sc._wait_for_route("Acme") is False  # proceed; sweep/Resume retry
    assert not sc._route_down.is_set()


def test_wait_for_route_honours_stop():
    sc = _bare_scanner()
    sc._route_down.set()
    sc.cancel_event = threading.Event()
    sc.cancel_event.set()
    sc._probe_route_once = lambda: pytest.fail("probe must not run after Stop")
    assert sc._wait_for_route("Acme") is True


def test_wait_for_route_is_noop_when_gate_open():
    sc = _bare_scanner()
    sc._probe_route_once = lambda: pytest.fail("probe must not run when gate open")
    assert sc._wait_for_route("Acme") is False


# ── G2: Resume must offer transiently-killed targets again ────────────────

def _log(db_path, run_id, company, status, error=""):
    from packtoworkeu.db import database as db
    db.record_scan_log_rows(db_path, run_id, "career", [{
        "seed_name": company, "company": company, "source_type": "direct",
        "target_country": "Global", "status": status, "provider": "auto",
        "jobs_found": 0, "quarantined": 0, "duplicates": 0,
        "rejected_scope": 0, "error": error, "diagnostics": "",
        "duration_sec": 1.0, "seed_url": f"https://{company.lower()}.example.com",
    }])


def test_resume_reoffers_no_route_errors_but_not_walls(tmp_path):
    from packtoworkeu.application.scan_scope import remaining_targets
    from packtoworkeu.db import database as db
    db_path = str(tmp_path / "resume.db")
    db.initialize(db_path)
    _log(db_path, "R1", "Killed", "error",
         "seed page unreachable: no network route from the scanning machine "
         "to https://killed.example.com [net::ERR_INTERNET_DISCONNECTED]")
    _log(db_path, "R1", "Walled", "error",
         "blocked by bot protection: Cloudflare challenge")
    _log(db_path, "R1", "Done", "ok")
    plan = {"ats": [], "career": [
        {"seed_name": n, "target_country": "Global",
         "careers_url": f"https://{n.lower()}.example.com"}
        for n in ("Killed", "Walled", "Done")]}
    remaining = remaining_targets(db_path, "R1", plan)["career"]
    assert [r["seed_name"] for r in remaining] == ["Killed"]
