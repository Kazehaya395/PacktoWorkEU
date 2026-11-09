"""Offline regressions for exception-safe worker browser acquisition."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading

import pytest

from sponsorscout.scanning.career import career_scanner as cs


class Browser:
    def __init__(self):
        self.connected = True
        self.closed = 0

    def is_connected(self):
        return self.connected

    def close(self):
        self.closed += 1
        self.connected = False


class DriverFactory:
    """Model an owned loop left active until stop(), including launch failures."""
    def __init__(self, outcomes, stop_error=None):
        self.outcomes = iter(outcomes)
        self.stop_error = stop_error
        self.local = threading.local()
        self.drivers = []
        self.launch_args = []

    def __call__(self):
        return SimpleNamespace(start=self.start)

    def start(self):
        if getattr(self.local, 'active', False):
            raise RuntimeError('Sync API inside the asyncio loop')
        self.local.active = True
        owner = threading.get_ident()
        driver = SimpleNamespace(stops=0)

        def launch(**kwargs):
            assert threading.get_ident() == owner
            self.launch_args.append(kwargs)
            outcome = next(self.outcomes)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        def stop():
            assert threading.get_ident() == owner
            driver.stops += 1
            self.local.active = False
            if self.stop_error:
                raise self.stop_error

        driver.chromium = SimpleNamespace(launch=launch)
        driver.stop = stop
        self.drivers.append(driver)
        return driver


def scanner():
    # Only browser lifecycle state: no runtime data paths or network involved.
    s = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    s._worker_browsers_tls = threading.local()
    s._worker_browsers_lock = threading.Lock()
    s._worker_browsers_all = []
    return s


def test_repeated_failed_launch_then_success_on_same_worker(monkeypatch):
    first = RuntimeError('missing executable')
    second = RuntimeError('launch failed again')
    browser = Browser()
    factory = DriverFactory([first, second, browser])
    monkeypatch.setattr(cs, 'sync_playwright', factory)
    s = scanner()

    def work():
        for error in (first, second):
            with pytest.raises(RuntimeError) as caught:
                s._get_or_launch_worker_browser()
            assert caught.value is error
            assert getattr(s._worker_browsers_tls, 'entry', None) is None
            assert s._worker_browsers_all == []
        assert s._get_or_launch_worker_browser() is browser
        assert s._get_or_launch_worker_browser() is browser
        assert s._worker_browsers_tls.entry['uses'] == 2
        assert len(s._worker_browsers_all) == 1
        s._close_all_worker_browsers()
        assert not factory.local.active

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(work).result(timeout=5)
    assert [d.stops for d in factory.drivers] == [1, 1, 1]
    assert browser.closed == 1
    assert all(a == {'headless': True, 'args': cs.BROWSER_ARGS}
               for a in factory.launch_args)


@pytest.mark.parametrize('launch_error', [RuntimeError('launch'), KeyboardInterrupt()])
def test_cleanup_error_cannot_mask_original_launch_error(monkeypatch, launch_error):
    factory = DriverFactory([launch_error], stop_error=RuntimeError('stop failed'))
    monkeypatch.setattr(cs, 'sync_playwright', factory)
    s = scanner()
    with pytest.raises(type(launch_error)) as caught:
        s._get_or_launch_worker_browser()
    assert caught.value is launch_error
    assert factory.drivers[0].stops == 1
    assert not s._worker_browsers_all


def test_start_failure_does_not_launch_or_register(monkeypatch):
    error = RuntimeError('start failed')
    def start():
        raise error
    monkeypatch.setattr(cs, 'sync_playwright', lambda: SimpleNamespace(start=start))
    s = scanner()
    with pytest.raises(RuntimeError) as caught:
        s._get_or_launch_worker_browser()
    assert caught.value is error
    assert not s._worker_browsers_all


@pytest.mark.parametrize('disconnected', [False, True])
def test_recycle_or_disconnect_retires_before_new_launch(monkeypatch, disconnected):
    old, new = Browser(), Browser()
    factory = DriverFactory([old, new])
    monkeypatch.setattr(cs, 'sync_playwright', factory)
    s = scanner()
    assert s._get_or_launch_worker_browser() is old
    if disconnected:
        old.connected = False
    else:
        for _ in range(14):
            assert s._get_or_launch_worker_browser() is old
        assert s._worker_browsers_tls.entry['uses'] == 15
    assert s._get_or_launch_worker_browser() is new
    assert old.closed == 1
    assert factory.drivers[0].stops == 1
    assert s._worker_browsers_tls.entry['uses'] == 1
    # Existing registry behavior is deliberately unchanged by this patch.
    s._close_all_worker_browsers()
    assert new.closed == 1
    assert factory.drivers[1].stops == 1
