"""Bounded same-page observation: offline clock and rendered-page fixtures."""
import threading
from types import SimpleNamespace

import pytest

from sponsorscout.scanning.career import career_scanner as cs
from sponsorscout.scanning.common import ScanCancelled

WALL = '<title>Just a moment...</title><h1>Checking your browser</h1><div id="cf-challenge-running">Verifying...</div>'
PERSISTENT = '<title>Access denied</title><h1>Access denied</h1><p>Request rejected.</p>'
BODY = 'Browse available opportunities and explore our teams. ' * 8
LANDING = '<title>Careers</title><h1>Browse by Job Categories</h1><p>' + BODY + '</p>'
POSTING = '<title>Engineer</title><h1>Engineer</h1><p>Responsibilities and requirements: ' + BODY + '</p><script src="https://challenges.cloudflare.com/sdk.js"></script>'


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now


class Page:
    def __init__(self, clock, sequence):
        self.clock = clock
        self.sequence = sequence
        self.index = 0
        self._last_nav_status = 403
        self.frames = []
        self.waits = []
        self.cancel = None
        self.read_error = False

    def content(self):
        if self.read_error:
            raise RuntimeError('document navigating')
        return self.sequence[self.index]

    def title(self):
        return ''  # Detector can recover title from HTML.

    def inner_text(self, selector, timeout=None):
        assert 0 < timeout <= 200
        return cs.re.sub('<[^>]+>', ' ', self.content())

    def wait_for_timeout(self, ms):
        assert 0 < ms <= 200
        self.waits.append(ms)
        self.clock.now += ms / 1000
        self.index = min(self.index + 1, len(self.sequence)-1)
        if self.cancel is not None:
            self.cancel.set()


@pytest.fixture
def setup(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(cs, 'time', SimpleNamespace(monotonic=clock.monotonic))
    scanner = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    scanner.config = SimpleNamespace(MAX_COMPANY_TIME_SEC=60, VERIFICATION_OBSERVE_SEC=1)
    scanner.cancel_event = threading.Event()
    return scanner, clock


def observe(scanner, page, diagnostics=None, state=None, started=100):
    return scanner._observe_seed_verification(
        page, cs.challenge_reason(WALL), diagnostics if diagnostics is not None else [],
        started, state if state is not None else {})


@pytest.mark.parametrize('destination', [LANDING, POSTING])
def test_transient_wall_clears_to_real_content_without_reloading(setup, destination):
    scanner, clock = setup
    page = Page(clock, [WALL, destination])
    diagnostics = []
    assert observe(scanner, page, diagnostics) == ''
    assert page._last_nav_status == 0
    assert page.waits == [200]
    assert any('extraction/coverage still pending' in d for d in diagnostics)


def test_persistent_wall_is_bounded_and_not_called_human_required(setup):
    scanner, clock = setup
    page = Page(clock, [WALL, PERSISTENT])
    diagnostics = []
    assert observe(scanner, page, diagnostics)
    assert clock.now <= 101.001
    assert page._last_nav_status == 403
    assert 'human action not inferred' in diagnostics[-1]


@pytest.mark.parametrize('replacement', ['', '<title>Loading</title><p>Loading...</p>'])
def test_empty_or_thin_transition_is_not_clearance(setup, replacement):
    scanner, clock = setup
    page = Page(clock, [WALL, replacement])
    assert observe(scanner, page)
    assert page._last_nav_status == 403


def test_changing_wall_markup_is_not_clearance(setup):
    scanner, clock = setup
    page = Page(clock, [WALL, WALL + '<p>' + BODY + '</p>'])
    assert observe(scanner, page)


def test_already_cancelled_stops_without_waiting(setup):
    scanner, clock = setup
    scanner.cancel_event.set()
    page = Page(clock, [WALL])
    with pytest.raises(ScanCancelled):
        observe(scanner, page)
    assert not page.waits


def test_cancel_during_observation_stops_promptly(setup):
    scanner, clock = setup
    page = Page(clock, [WALL])
    page.cancel = scanner.cancel_event
    with pytest.raises(ScanCancelled):
        observe(scanner, page)
    assert len(page.waits) == 1


def test_company_deadline_caps_wait_and_repeated_checks_share_budget(setup):
    scanner, clock = setup
    page = Page(clock, [WALL])
    state = {}
    assert observe(scanner, page, state=state, started=40.35)
    assert clock.now <= 100.351
    count = len(page.waits)
    assert observe(scanner, page, state=state, started=40.35)
    assert len(page.waits) == count


def test_missing_wall_does_not_wait_but_bare_refusal_gets_bounded_readiness(setup):
    scanner, clock = setup
    page = Page(clock, [POSTING])
    assert scanner._observe_seed_verification(page, '', [], 100, {}) == ''
    reason = 'HTTP 403 access blocked (human verification not confirmed)'
    assert scanner._observe_seed_verification(page, reason, [], 100, {}) == reason
    assert page.waits and clock.now <= 101.001
    assert not cs.challenge_reason(POSTING)


def test_page_inspection_failure_is_not_clearance(setup):
    scanner, clock = setup
    page = Page(clock, [WALL])
    page.read_error = True
    assert observe(scanner, page)
    assert page.waits and clock.now <= 101.001
