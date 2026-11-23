"""Bounded stop + force close (2026-11-17, Task 34).

User report: Stop took 10-15 min and the window close button "won't
work". Root causes pinned here:
  * _drain / _finish_worker_pool joined in-flight workers with NO upper
    bound once Stop was pressed;
  * Workday detail waves blocked on ex.map() until the whole wave
    finished, and _enrich_support_from_detail's `with` executor waited
    out in-flight fetches on exit;
  * closeEvent ignored every close until the scan thread died.

These tests pin: (1) the coordinator tracks the stop instant and the
grace deadline; (2) the pool join abandons stragglers once the shared
grace deadline passes instead of waiting for them; (3) a Workday detail
wave abandons promptly on cancel; (4) the UI offers force close only
after the grace is spent (source pins -- PySide6 is not importable in
every test environment).
"""
import json
import threading
import time

from packtoworkeu.application import scan_coordinator as sc_mod
from packtoworkeu.scanning.career import career_scanner as cs


# ── coordinator stop bookkeeping ────────────────────────────────────────────

def test_coordinator_tracks_stop_and_grace():
    c = sc_mod.ScanCoordinator()
    assert not c.is_stopping()
    assert c.stopping_elapsed() == 0.0
    assert not c.stop_grace_exceeded()

    blocker = threading.Event()
    c._thread = threading.Thread(target=blocker.wait, daemon=True)
    c._thread.start()
    try:
        c.stop()
        assert c.is_stopping()
        assert c.stopping_elapsed() >= 0.0
        assert not c.stop_grace_exceeded()
        # Backdate the stop request: the grace is now spent.
        c._stop_requested_at = time.monotonic() - 10_000
        assert c.stop_grace_exceeded()
        # A second stop() must not restart the clock.
        first = c._stop_requested_at
        c.stop()
        assert c._stop_requested_at == first
    finally:
        blocker.set()
        c._thread.join(timeout=2)


def test_stop_grace_default_and_env_override(monkeypatch):
    monkeypatch.delenv("PACKTOWORKEU_STOP_GRACE_SEC", raising=False)
    assert sc_mod.ScanCoordinator.stop_grace_sec() == 75.0
    monkeypatch.setenv("PACKTOWORKEU_STOP_GRACE_SEC", "30")
    assert sc_mod.ScanCoordinator.stop_grace_sec() == 30.0
    monkeypatch.setenv("PACKTOWORKEU_STOP_GRACE_SEC", "junk")
    assert sc_mod.ScanCoordinator.stop_grace_sec() == 75.0


# ── bounded pool join ───────────────────────────────────────────────────────

def _bare_scanner(cancelled=True):
    obj = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    obj.config = cs.ProductionScannerConfig
    obj.cancel_event = threading.Event()
    if cancelled:
        obj.cancel_event.set()
    obj.pause_event = None
    obj._worker_browsers_lock = threading.Lock()
    obj._worker_browsers_all = []
    return obj


def test_finish_worker_pool_abandons_straggler_after_grace(capsys):
    import concurrent.futures as cf
    s = _bare_scanner(cancelled=True)
    # Anchor the shared grace deadline in the immediate future.
    s._stop_join_deadline_at = time.monotonic() + 0.2
    pool = cf.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(time.sleep, 6)
    t0 = time.monotonic()
    s._finish_worker_pool(pool, 1, [fut])
    elapsed = time.monotonic() - t0
    # One 5s wait slice may elapse before the deadline is re-checked,
    # but the join must NOT wait out the 6s straggler plus more.
    assert elapsed < 5.5
    assert "giving up on" in capsys.readouterr().out


def test_finish_worker_pool_normal_join_unchanged():
    import concurrent.futures as cf
    s = _bare_scanner(cancelled=False)
    pool = cf.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(time.sleep, 0.2)
    t0 = time.monotonic()
    s._finish_worker_pool(pool, 1, [fut])
    assert fut.done()
    assert time.monotonic() - t0 < 5


def test_stop_join_deadline_shared_anchor():
    s = _bare_scanner(cancelled=True)
    d1 = s._stop_join_deadline()
    d2 = s._stop_join_deadline()
    assert d1 is not None and d1 == d2
    s2 = _bare_scanner(cancelled=False)
    assert s2._stop_join_deadline() is None


# ── Workday detail wave abandons on cancel ──────────────────────────────────

def test_workday_detail_wave_abandons_on_cancel():
    s = _bare_scanner(cancelled=False)
    postings = [{"title": f"Job {i}", "externalPath": f"/job/{i}",
                 "locationsText": "Berlin", "bulletFields": []}
                for i in range(12)]

    def _fetch(url, headers, post_body=None):
        if url.endswith("/jobs"):
            return json.dumps({"total": 12, "jobPostings": postings})
        # Detail fetch: slow, and the user presses Stop during the wave.
        s.cancel_event.set()
        time.sleep(3)
        return json.dumps({"jobPostingInfo": {
            "jobDescription": "x", "location": "Berlin, Germany"}})

    s._http_fetch_with_retry = _fetch
    t0 = time.monotonic()
    jobs, note = s._fetch_workday_jobs(
        {"board_slug": "acme.wd5.myworkdayjobs.com|acme|careers",
         "careers_url": "https://acme.wd5.myworkdayjobs.com/careers"})
    elapsed = time.monotonic() - t0
    assert len(jobs) == 12
    # ex.map() would have waited out the whole 3s wave; the sliced wait
    # notices the cancel within ~0.25s and abandons the wave.
    assert elapsed < 2.0


# ── scanner + UI source pins ────────────────────────────────────────────────

def test_bounded_stop_source_pins():
    src = open(cs.__file__, encoding="utf-8").read()
    assert "_stop_join_deadline" in src
    assert "giving up on" in src
    assert "STOP_JOIN_GRACE_SEC" in src
    # The detail-enrich pool must not be a bare `with` executor any more.
    assert "ex.shutdown(wait=not _detail_abandon, cancel_futures=True)" in src


def test_close_event_force_close_source_pins():
    import pathlib
    app_src = pathlib.Path(cs.__file__).with_name("career_scanner.py")
    ui_app = (pathlib.Path(cs.__file__).parents[2]
              / "ui" / "app.py").read_text(encoding="utf-8")
    assert "stop_grace_exceeded" in ui_app
    assert "_force_close" in ui_app
    assert "Force close" in ui_app
    # The old unclosable trap message must be gone.
    assert "Close the application again after the scan has stopped" \
        not in ui_app
    assert app_src.exists()
