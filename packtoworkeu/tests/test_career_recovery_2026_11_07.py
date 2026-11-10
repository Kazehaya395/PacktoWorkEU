"""Shared bounded transport recovery and final UI/export coverage regressions."""
from __future__ import annotations
import csv
import json
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
TEXT = 'Build reliable services, maintain automated tests, collaborate with engineers and review changes. ' * 15


def scanner():
    from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner
    s = CareerPortalScanner(input_csv='__unused__.csv', skip_preflight=True)
    s._tls.detail_scope = 'https://fixture.invalid/jobs'
    return s


def fail(s, suffix, **evidence):
    url = 'https://fixture.invalid/job/' + str(suffix)
    s._detail_state.register(url, s._tls.detail_scope)
    s._note_detail_failure(url, 'synthetic transport failure', **evidence)
    return url


def clock(monkeypatch):
    from packtoworkeu.scanning.career import career_scanner as mod
    now = [100.0]
    import time as real_time
    # Absolute deadlines and charged spans must share one monotonic domain.
    # Otherwise these tests fail once real sandbox uptime exceeds fake 1031s.
    from packtoworkeu.scanning.career import http_schedule
    from packtoworkeu.scanning import detail_state
    fake_time = SimpleNamespace(monotonic=lambda: now[0], time=real_time.time)
    for module in (mod, http_schedule, detail_state):
        monkeypatch.setattr(module, 'time', fake_time)
    cache = SimpleNamespace(get=lambda _: None, put=lambda *_: None)
    monkeypatch.setattr(mod, '_jd_cache', lambda: cache)
    return now


def test_ordinary_http_failures_do_not_permanently_block_browser(monkeypatch):
    s = scanner()
    clock(monkeypatch)
    url = fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    assert s._detail_host_unavailable(url)
    assert not s._detail_host_unavailable(url, browser=True)
    assert 'fixture.invalid' not in s._detail_blocked_hosts
    assert s._detail_can_browser(url)
    assert s._detail_state.reserve(url, s.config, browser=True)
    assert not s._detail_can_browser(url)  # not a blind retry loop
    s._detail_state.observe(url, TEXT, transport='browser')
    s._note_detail_success(url)
    assert not s._detail_host_unavailable(url)
    assert url not in s._detail_state.failed
    assert s._detail_state.coverage(s._tls.detail_scope, [url])['readable'] == 1


@pytest.mark.parametrize('status', [400, 404, 410, 422])
def test_per_posting_http_errors_do_not_condemn_host(status):
    s = scanner()
    for i in range(5):
        url = fail(s, i, transport='http', status=status)
    assert not s._detail_host_unavailable(url)
    assert not s._detail_transport_circuits
    if status in (404, 410):
        assert not s._detail_can_browser(url)


def test_security_wall_is_not_retried_as_an_ordinary_transport_failure():
    s = scanner()
    url = fail(s, 1, blocked=True, transport='http', status=403)
    assert s._detail_url_blocked(url)
    assert not s._detail_can_browser(url)
    fail(s, 2, blocked=True, transport='http', status=429)
    fresh = 'https://fixture.invalid/job/new'
    assert s._detail_host_unavailable(fresh)
    assert s._detail_host_unavailable(fresh, browser=True)
    # A transport success is not a clearance for an actual observed security wall.
    s._note_detail_http_success(fresh)
    assert s._detail_host_unavailable(fresh, browser=True)


def test_http_and_browser_transport_health_are_independent():
    s = scanner()
    fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    url = fail(s, 3, transport='browser')
    fail(s, 4, transport='browser')
    assert s._detail_host_unavailable(url, browser=True)
    assert not s._detail_host_unavailable('https://other.invalid/job/1')
    assert not s._detail_host_unavailable('https://other.invalid/job/1', browser=True)


def test_half_open_probes_are_serialized_bounded_and_nonblocking(monkeypatch):
    s = scanner()
    now = clock(monkeypatch)
    url = fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    assert not s._claim_detail_http_probe(url)
    now[0] += 31
    barrier = threading.Barrier(8)
    results = []
    def claim():
        barrier.wait()
        results.append(s._claim_detail_http_probe(url))
    threads = [threading.Thread(target=claim) for _ in range(8)]
    for t in threads: t.start()
    for t in threads: t.join(timeout=2)
    assert all(not t.is_alive() for t in threads)
    assert results.count(True) == 1
    s._note_detail_failure(url, 'synthetic probe failed', transport='http')
    now[0] += 31
    assert s._claim_detail_http_probe(url)
    s._note_detail_failure(url, 'synthetic second probe failed', transport='http')
    now[0] += 10000
    assert s._detail_host_unavailable(url)
    assert not any(s._claim_detail_http_probe(url) for _ in range(1000))
    assert s._detail_state.count_of(s._tls.detail_scope, 'http_recovery_probe') == 2


def test_successful_half_open_read_releases_the_host(monkeypatch):
    s = scanner()
    now = clock(monkeypatch)
    fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    now[0] += 31
    url = 'https://fixture.invalid/job/fresh'
    def read(u):
        s._detail_state.observe(u, TEXT, transport='http')
        s._note_detail_http_success(u)
        return TEXT, '', ''
    monkeypatch.setattr(s, '_fetch_jd_text_uncached', read)
    assert s._fetch_jd_text(url, allow_browser=False)[0] == TEXT
    assert not s._detail_host_unavailable(url)
    assert not s._detail_transport_circuits


def test_cancel_during_half_open_request_releases_probe(monkeypatch):
    from packtoworkeu.scanning.common import ScanCancelled
    s = scanner()
    now = clock(monkeypatch)
    fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    now[0] += 31
    def cancel(_): raise ScanCancelled()
    monkeypatch.setattr(s, '_fetch_jd_text_uncached', cancel)
    with pytest.raises(ScanCancelled):
        s._fetch_jd_text('https://fixture.invalid/job/fresh', allow_browser=False)
    assert not s._detail_transport_circuits['fixture.invalid']['probing']


def test_failure_recovery_does_not_bypass_browser_budget():
    s = scanner()
    url = fail(s, 1, transport='http')
    s.config = SimpleNamespace(**{k:getattr(s.config, k) for k in (
        'MAX_DETAIL_SCAN_PER_COMPANY', 'MAX_DETAIL_SCAN_TOTAL', 'DETAIL_MIN_PER_COMPANY',
        'DETAIL_SCAN_TIME_BUDGET_SEC')}, DETAIL_BROWSER_FALLBACK_PER_COMPANY=0)
    assert not s._detail_can_browser(url)
    assert s._detail_state.total == 0


def test_final_reporter_replaces_auxiliary_memberships_and_can_clear_to_zero():
    from packtoworkeu.scanning.progress_events import TaskReporter
    from packtoworkeu.scanning.detail_state import DetailRequestState
    from packtoworkeu.application.scan_progress import ScanProgressState, parse_event
    state = DetailRequestState()
    target = dict(name='Portal', careers_url='https://fixture.invalid/jobs')
    for i in range(5): state.register(f'https://fixture.invalid/job/{i}', target['careers_url'])
    lines = []
    r = TaskReporter(SimpleNamespace(_detail_state=state), 'career', lines.append)
    key = r.begin(target)
    r._snapshot(key)
    final = dict(observed=2, readable=1, thin=0, empty=0, failed=0, blocked=0, unattempted=1)
    r.end(key, 'partial', coverage=final)
    model = ScanProgressState()
    for line in lines: model.apply(parse_event(line))
    assert model.snapshot()['coverage'] == final
    assert model.snapshot()['checked'] == 1
    lines.clear()
    key = r.begin(target)
    r.end(key, 'empty', coverage={k:0 for k in final})
    for line in lines: model.apply(parse_event(line))
    assert model.snapshot()['coverage']['observed'] == 0
    assert not r.tasks


def test_final_coverage_in_logs_and_telemetry_share_same_census():
    import inspect
    from packtoworkeu.scanning.career import career_scanner
    from packtoworkeu.scanning.ats import ats_scanner
    for mod in (career_scanner, ats_scanner):
        source = inspect.getsource(mod)
        assert '_reporter.end(_progress_key, status, coverage=_coverage)' in source
        assert "diagnostics.append('JD coverage: ' + '; '.join(f'{k}={v}' for k, v in _coverage.items()))" in source


@pytest.mark.parametrize('locale', ['en', 'it'])
def test_finished_qt_ui_describes_final_coverage_not_running_work(tmp_path, monkeypatch, locale):
    pytest.importorskip('PySide6')
    from packtoworkeu import i18n
    from packtoworkeu.tests._qt_support import application, wait_loaders, dispose
    from packtoworkeu.ui.tabs.tools import ToolsTab
    from packtoworkeu.db import database as db
    from packtoworkeu.application.scan_progress import ScanProgressState
    application()
    monkeypatch.setattr(i18n, '_locale', locale)
    path = str(tmp_path/'gui.db')
    db.initialize(path)
    w = ToolsTab(path)
    try:
        wait_loaders(w)
        model = ScanProgressState()
        model.apply(dict(kind='configure', total=1))
        model.apply(dict(kind='task_begin', task='a', name='Portal', attempt=1))
        model.apply(dict(kind='jd_snapshot', task='a', attempt=1,
            coverage=dict(observed=100, readable=40, failed=12, unattempted=48)))
        model.apply(dict(kind='task_end', task='a', attempt=1, status='partial'))
        w._work_snapshot = model.snapshot()
        w._work_finished = True
        w._work_control = 'partial'
        w._render_work_progress()
        assert w.scan_bar.value() == 100
        assert w.jd_bar.value() == 52
        # Classified attempts remain 52 internally; the terminal result is
        # readable coverage (40), not failed+readable classified progress.
        assert w.jd_bar.isHidden()
        assert w.jd_progress_label.text() == i18n._(
            'Final description results: {readable}/{observed} readable ({readable_percent}%); {unresolved} unresolved. No description workers are running.'
        ).format(readable=40, observed=100, readable_percent='40.0', unresolved=60)
        assert '52/100' not in w.jd_progress_label.text()
        assert '12' in w.jd_outcomes.text() and '48' in w.jd_outcomes.text()
        assert not w.active_scan_work.toPlainText()
    finally:
        wait_loaders(w)
        dispose(w)


@pytest.mark.skipif(os.environ.get('PACKTOWORKEU_TEST_BROWSER')!='1', reason='Opt-in local real Chromium recovery')
def test_real_local_browser_can_recover_http_failures_without_false_sponsorship():
    import http.server
    api = pytest.importorskip('playwright.sync_api')
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            if 'HeadlessChrome' in self.headers.get('User-Agent', ''):
                body = ('<script type="application/ld+json">'+json.dumps(
                    {'@type':'JobPosting','title':'Engineer','description':TEXT})+'</script>').encode()
                self.send_response(200)
            else:
                body = b'Temporary service failure'
                self.send_response(503)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    s = scanner()
    base = f'http://127.0.0.1:{server.server_port}'
    s._tls.detail_scope = base+'/jobs'
    try:
        assert not s._fetch_jd_text(base+'/job/1', allow_browser=False)[0]
        assert not s._fetch_jd_text(base+'/job/2', allow_browser=False)[0]
        assert s._detail_host_unavailable(base+'/job/3')
        with api.sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                original = s._fetch_jd_text_browser
                s._fetch_jd_text_browser = lambda u, timeout_ms=15000: original(u, timeout_ms, browser_page=page)
                result = s._fetch_jd_text(base+'/job/3')
                assert len(result[0]) >= 200
                assert not s._detail_host_unavailable(base+'/job/4')
                assert s._jd_browser_used == 1
                record = {'Job Title':'Engineer','Job URL':base+'/job/3','Company Name':'Fixture', 'Visa Sponsorship':'Unknown'}
                s._apply_support_from_text(record, result[0])
                assert record['Visa Sponsorship'] == 'Unknown'
            finally: browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_probe_limit_survives_success_and_later_outage(monkeypatch):
    s = scanner()
    now = clock(monkeypatch)
    url = fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    now[0] += 31
    assert s._claim_detail_http_probe(url)
    s._note_detail_http_success(url)
    assert not s._detail_host_unavailable(url)
    fail(s, 3, transport='http')
    fail(s, 4, transport='http')
    now[0] += 31
    assert s._claim_detail_http_probe(url)
    s._note_detail_http_success(url)
    fail(s, 5, transport='http')
    fail(s, 6, transport='http')
    now[0] += 1000
    assert not s._claim_detail_http_probe(url)
    assert s._detail_http_probe_counts['fixture.invalid'] == 2


def test_final_census_survives_large_embedded_notes_and_truncation():
    from packtoworkeu.scanning.progress_events import final_diagnostics
    final = dict(observed=100, readable=52, thin=0, empty=0, failed=0, blocked=0, unattempted=48)
    text = final_diagnostics(['JD coverage: observed=999', 'old technical note'], final,
                             extra=['large embedded note ' * 1000])
    assert len(text) == 4000
    assert text.endswith('unattempted=48')
    assert text.count('JD coverage: ') == 1
    assert 'JD coverage: observed=100; readable=52' in text


def test_thin_browser_shell_does_not_close_http_transport_circuit(monkeypatch):
    s = scanner()
    clock(monkeypatch)
    url = fail(s, 1, transport='http')
    fail(s, 2, transport='http')
    # Cached failed HTTP results can still receive a bounded browser fallback,
    # but a thin shell is not proof of a restored readable description.
    monkeypatch.setattr(s, '_fetch_jd_text_browser', lambda *a: ('shell', '', ''))
    assert s._fetch_jd_text(url)[0] == 'shell'
    assert s._detail_host_unavailable(url)
    assert url in s._detail_state.failed
