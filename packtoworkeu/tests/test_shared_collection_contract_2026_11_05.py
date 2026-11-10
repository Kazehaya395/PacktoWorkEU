"""Cross-portal terminal reconciliation and evidence precedence regressions."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from packtoworkeu.scanning.listing_completion import (
    reconcile_disappeared_pager, record_census_page, TERMINAL_CONTROLS_JS)
from packtoworkeu.core import job_identity as ji
from packtoworkeu.scanning.common import completion_status

URLS = ['https://jobs.fixture.invalid/job/' + str(10000+i) for i in range(8)]
CONTROLS = dict(ready=True, busy=False, forward=False, backward=True, unknown=False)


def census():
    samples = []
    record_census_page(samples, URLS[:5], 8)
    record_census_page(samples, URLS[5:], 8)
    return samples


def test_disappearing_forward_after_disjoint_pages_and_total():
    result = reconcile_disappeared_pager(census(), URLS, URLS[5:], CONTROLS, census_complete=True)
    assert result['unique_postings'] == 8
    assert result['kind'] == 'disappeared_pager_reconciled'


@pytest.mark.parametrize('key,value', [('ready', False), ('busy', True), ('forward', True),
                                      ('backward', False), ('unknown', True)])
def test_uncertain_or_forward_control_never_complete(key, value):
    controls = dict(CONTROLS, **{key: value})
    assert not reconcile_disappeared_pager(census(), URLS, URLS[5:], controls, census_complete=True)


@pytest.mark.parametrize('mode', ['total_changed', 'overlap', 'missing_saved', 'extra_saved',
                                  'old_page', 'incomplete_census', 'failed', 'single_page'])
def test_counts_alone_do_not_prove_terminal(mode):
    samples = census()
    saved, current = list(URLS), URLS[5:]
    complete, failed = True, False
    if mode == 'total_changed': samples[-1]['total'] = 9
    if mode == 'overlap': samples[-1]['identities'].append(ji.posting_identity(URLS[0]))
    if mode == 'missing_saved': saved.pop()
    if mode == 'extra_saved': saved.append('https://jobs.fixture.invalid/job/99999')
    if mode == 'old_page': current = URLS[:5]
    if mode == 'incomplete_census': complete = False
    if mode == 'failed': failed = True
    if mode == 'single_page': samples = samples[-1:]
    assert not reconcile_disappeared_pager(samples, saved, current, CONTROLS,
                                          census_complete=complete, failed=failed)


def test_repeated_reads_are_not_progress_and_changed_total_is_retained():
    samples = census()
    record_census_page(samples, URLS[5:], 9)
    assert len(samples) == 2 and samples[-1]['total'] == 9


@pytest.mark.parametrize('suffix', ['locationPicker', 'location-picker', 'location_picker'])
def test_location_chooser_never_becomes_a_posting_despite_parent_id(suffix):
    url = 'https://jobs.fixture.invalid/details/123456/engineer/' + suffix
    assert ji.nonposting_url(url)
    assert ji.nonposting_record(url, 'Where we are hiring')
    assert not ji.explicit_detail_url(url)
    assert not ji.posting_candidate(url, 'Where we are hiring', validator=lambda _: True)


def test_real_location_role_and_query_are_preserved():
    for url in ['https://jobs.fixture.invalid/job/123456/location-platform-engineer',
                'https://jobs.fixture.invalid/job/123456?location=Berlin']:
        assert ji.explicit_detail_url(url)
        assert ji.posting_candidate(url, 'Location Platform Engineer')


def test_historical_verification_snapshot_does_not_override_recovered_content():
    snapshots = ['verification snapshot: {"reason": "Cloudflare challenge page served instead of the job list"}',
                 'verification cleared: changed rendered content observed',
                 'complete: terminal pager and observed total reconciled']
    assert completion_status(8, diagnostics=snapshots) == 'ok'
    assert completion_status(8, diagnostics=snapshots + ['JD coverage incomplete: 1 posting']) == 'partial'
    assert completion_status(8, diagnostics=['listing completeness unverified: no next-page control recognised']) == 'partial'


def test_assisted_wrapper_reconciles_and_rechecks_current_page(monkeypatch):
    from packtoworkeu.core import blocked_portals as bp
    page = SimpleNamespace(url='https://jobs.fixture.invalid/careers', _ss_terminal_census=census()[:1])
    page.content = lambda: '8 jobs'
    page.wait_for_timeout = lambda ms: None
    page.evaluate = lambda js: deepcopy(CONTROLS) if js == TERMINAL_CONTROLS_JS else None
    evidence = dict(reason='', html='8 jobs', rendered=dict(
        listing_ready=True, admitted_anchors=[{'url': u} for u in URLS[5:]]))
    monkeypatch.setattr(bp, '_page_is_challenge', lambda _: False)
    monkeypatch.setattr(bp, '_page_response_evidence', lambda _: deepcopy(evidence))
    monkeypatch.setattr(bp, 'reconciled_native_append_end', lambda *a: None)
    monkeypatch.setattr(bp, 'stated_job_total', lambda _: 8)
    monkeypatch.setattr(bp, '_pagination_end_reached', lambda _: False)
    monkeypatch.setattr(bp, 'paging_href_candidates', lambda *a: [])
    result = bp.terminal_listing_evidence(page, 8, 3, census_complete=True,
                                          harvested_urls=URLS, current_urls=URLS[5:])
    assert result['kind'] == 'disappeared_pager_reconciled'
    evidence['rendered']['admitted_anchors'] = [{'url': URLS[0]}]
    assert not bp.terminal_listing_evidence(page, 8, 3, census_complete=True,
                                           harvested_urls=URLS, current_urls=URLS[5:])


@pytest.fixture
def browser_page():
    import os
    if os.environ.get('PACKTOWORKEU_TEST_BROWSER') != '1':
        pytest.skip('opt-in intercepted local Chromium fixture')
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page()
        page.route('**/*', lambda r: r.fulfill(status=200, content_type='text/html', body='<html></html>'))
        page.goto('https://jobs.fixture.invalid/careers')
        yield page
        browser.close()


def test_real_dom_terminal_controls_reject_loading_forward_and_unknown(browser_page):
    page = browser_page
    page.set_content('<nav class="pagination"><button>Previous</button></nav>')
    assert page.evaluate(TERMINAL_CONTROLS_JS) == CONTROLS
    page.evaluate("document.querySelector('nav').insertAdjacentHTML('beforeend','<button>Next</button>')")
    assert page.evaluate(TERMINAL_CONTROLS_JS)['forward']
    page.set_content('<nav class="pagination"><button>Previous</button><button><svg></svg></button></nav>')
    assert page.evaluate(TERMINAL_CONTROLS_JS)['unknown']
    page.set_content('<nav><button>Previous</button></nav><div aria-busy="true">Loading</div>')
    assert page.evaluate(TERMINAL_CONTROLS_JS)['busy']


def test_real_assisted_terminal_disappearing_pager(browser_page):
    from packtoworkeu.core import blocked_portals as bp
    page = browser_page
    urls = [f'https://jobs.fixture.invalid/job/{100001+i}' for i in range(3)]
    samples = []
    record_census_page(samples, urls[:2], 3)
    page._ss_terminal_census = samples
    page.set_content('<main><h1>3 Jobs</h1><article><a href="/job/100003">Test Engineer</a></article>'
                     '<nav class="pagination"><button>Previous</button></nav></main>')
    result = bp.terminal_listing_evidence(page, 3, 1, census_complete=True,
                                          harvested_urls=urls, current_urls=urls[2:])
    assert result and result['kind'] == 'disappeared_pager_reconciled'
    page.evaluate("document.querySelector('nav').insertAdjacentHTML('beforeend','<button>Load more</button>')")
    assert not bp.terminal_listing_evidence(page, 3, 1, census_complete=True,
                                          harvested_urls=urls, current_urls=urls[2:])


def test_real_regular_verification_snapshots_and_recovery(browser_page):
    import json
    import threading
    import time
    from packtoworkeu.scanning.career import career_scanner as cs
    page = browser_page
    scanner = cs.CareerPortalScanner.__new__(cs.CareerPortalScanner)
    scanner.config = SimpleNamespace(MAX_COMPANY_TIME_SEC=10, VERIFICATION_OBSERVE_SEC=.6)
    scanner.cancel_event = threading.Event()
    page._last_nav_status = 403
    page.set_content('<title>Just a moment...</title><h1>Checking your browser</h1>'
                     '<div id="cf-challenge-running">Verifying...</div>')
    diagnostics = []
    reason = scanner._bot_challenge_reason(page)
    assert scanner._observe_seed_verification(page, reason, diagnostics, time.monotonic(), {})
    snapshots = [json.loads(d.split(': ', 1)[1]) for d in diagnostics if d.startswith('verification snapshot:')]
    assert snapshots[0]['phase'] == 'initial'
    assert snapshots[-1]['phase'] == 'terminal'
    assert snapshots[-1]['state'] == 'active_wall'
    assert snapshots[-1]['successful_samples'] > 0
    body = 'Explore our engineering and operations opportunities. ' * 8
    page.set_content('<title>Just a moment...</title><div id="cf-challenge-running">Verifying...</div>'
                     '<script>setTimeout(() => {document.title="Careers";document.body.innerHTML=' +
                     json.dumps('<main><h1>Browse by Job Categories</h1><p>' + body + '</p></main>') +
                     ';}, 150)</script>')
    diagnostics = []
    assert scanner._observe_seed_verification(page, reason, diagnostics, time.monotonic(), {}) == ''
    assert page._last_nav_status == 0
    assert completion_status(3, diagnostics=diagnostics) == 'ok'


def test_location_picker_role_slug_without_posting_parent_is_not_a_utility():
    url = 'https://jobs.fixture.invalid/jobs/location-picker?jobId=123456'
    assert not ji.posting_utility_url(url)
    assert ji.explicit_detail_url(url)
    assert ji.posting_candidate(url, 'Location Picker')
