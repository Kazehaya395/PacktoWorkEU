"""Run 20261110T185858: career scan wedged ~1h — Hays Germany stuck inside
its browser listing crawl and HomeToGo queued silently behind it, because
`with self._browser_sem` blocks forever (no timeout, no cancel check, no
log line) and Playwright evaluate/content calls had no default timeout.
These tests pin the anti-hang bundle: cancellable/deadline-bound browser
gate, graceful skip for the optional detail fallback, and page timeouts.
"""
import threading
import time
from types import SimpleNamespace

import pytest

from packtoworkeu.scanning.career import career_scanner as cs


def _scanner(sem, budget=1800):
    s = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    s._browser_sem = sem
    s.cancel_event = threading.Event()
    s.config = SimpleNamespace(MAX_COMPANY_TIME_SEC=budget,
                               ACTION_TIMEOUT_MS=18000)
    return s


def _locked_sem():
    sem = threading.Semaphore(1)
    assert sem.acquire(timeout=1)
    return sem


def test_browser_gate_acquires_and_releases_when_free():
    sem = threading.Semaphore(1)
    s = _scanner(sem)
    with s._browser_sem_wait("Fixture", started=time.monotonic()) as ok:
        assert ok is True
        assert not sem.acquire(timeout=0)  # held inside
    assert sem.acquire(timeout=1)  # released on exit
    sem.release()


def test_browser_gate_honours_stop_while_queued():
    sem = _locked_sem()
    s = _scanner(sem)
    threading.Timer(0.2, s.cancel_event.set).start()
    t0 = time.monotonic()
    with pytest.raises(cs.ScanCancelled):
        with s._browser_sem_wait("Queued Co", started=time.monotonic()):
            pass
    assert time.monotonic() - t0 < 5  # did not hang on the locked semaphore
    sem.release()


def test_browser_gate_deadline_raises_for_required_listing():
    sem = _locked_sem()
    s = _scanner(sem, budget=1)
    with pytest.raises(RuntimeError, match="waiting"):
        with s._browser_sem_wait("Slow Co", started=time.monotonic() - 60):
            pass
    sem.release()


def test_browser_gate_deadline_skips_optional_detail_fallback():
    sem = _locked_sem()
    s = _scanner(sem, budget=1)
    with s._browser_sem_wait("Slow Co", started=time.monotonic() - 60,
                             required=False) as ok:
        assert ok is False  # caller keeps HTTP results and moves on
    sem.release()


def test_page_timeouts_applied_and_stub_safe():
    calls = []

    class Page:
        def set_default_timeout(self, ms):
            calls.append(("default", ms))

        def set_default_navigation_timeout(self, ms):
            calls.append(("nav", ms))

    cs.CareerPortalScanner._apply_page_timeouts(Page(), 18000)
    assert calls == [("default", 18000), ("nav", 18000)]
    # Stub pages (unit-test fakes) without the setters must not break.
    cs.CareerPortalScanner._apply_page_timeouts(object(), 18000)


def test_no_bare_browser_semaphore_acquire_remains():
    src = open(cs.__file__, encoding="utf-8").read()
    assert "with self._browser_sem:" not in src
    assert src.count("_browser_sem_wait(name, started=started") == 2
    assert "self._apply_page_timeouts(page, self.config.ACTION_TIMEOUT_MS)" in src
    assert "LIST budget reached mid-page" in src


# -- Deferral bundle (run 20261112T095642 queue cascade) ---------------------

def test_browser_gate_raises_browser_busy_after_cap():
    sem = _locked_sem()
    s = _scanner(sem)
    t0 = time.monotonic()
    with pytest.raises(cs._BrowserBusy):
        with s._browser_sem_wait("Busy Co", started=time.monotonic(),
                                 max_wait_sec=0.5):
            pass
    assert time.monotonic() - t0 < 5  # deferred fast, did not burn 1800s
    sem.release()


def test_browser_gate_no_cap_still_deadline_bound():
    sem = _locked_sem()
    s = _scanner(sem, budget=1)
    with pytest.raises(RuntimeError, match="waiting"):
        with s._browser_sem_wait("Slow Co", started=time.monotonic() - 60,
                                 max_wait_sec=None):
            pass
    sem.release()


def test_browser_wait_cap_sweep_and_env(monkeypatch):
    s = _scanner(threading.Semaphore(1))
    monkeypatch.delenv("PACKTOWORKEU_BROWSER_WAIT", raising=False)
    s._sweep_no = 0
    assert s._browser_wait_cap() == 150.0
    s._sweep_no = 1
    assert s._browser_wait_cap() == 150.0
    s._sweep_no = 2
    assert s._browser_wait_cap() is None  # final sweep waits for real
    s._sweep_no = 0
    monkeypatch.setenv("PACKTOWORKEU_BROWSER_WAIT", "45")
    assert s._browser_wait_cap() == 45.0


def test_deferral_wiring_present():
    src = open(cs.__file__, encoding="utf-8").read()
    assert "max_wait_sec=self._browser_wait_cap()" in src
    assert "except _BrowserBusy:" in src
    assert "[browser queue] re-crawl" in src
    assert "deferred_rows.append(futures_map[future])" in src
