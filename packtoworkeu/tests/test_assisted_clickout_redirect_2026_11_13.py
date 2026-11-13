"""Assisted aggregator clickout redirects (2026-11-13).

English Jobs DE never links a posting directly: cards exit through
clickout URLs that redirect cross-domain (e.g. Talent.com) to the real
JD. The strict final-URL identity rule rejected every such JD. Now a
redirected JD is accepted only with proof: a substantial primary JD
whose JobPosting title matches the clicked posting's title. No title,
no structured match, or a different job keeps the rejection.
"""
import json
from types import SimpleNamespace

from packtoworkeu.core.session_details import (
    SessionDetailEnricher, _titles_match, parse_detail)
from packtoworkeu.scanning.career.career_scanner import (
    CareerPortalScanner, ProductionScannerConfig)
from packtoworkeu.scanning.detail_state import DetailRequestState

BASE = ('Responsibilities: Build reliable backend services, maintain automated tests and collaborate with product teams. '
        'Requirements: Strong engineering communication and practical experience operating distributed systems. ') * 8
POS = BASE + ' We offer visa sponsorship for this role. We offer relocation assistance. We support the EU Blue Card.'


def html_ld(text, url):
    return ('<html><title>Backend Engineer</title><h1>Backend Engineer</h1>'
            '<script type="application/ld+json">' + json.dumps({
                '@type': 'JobPosting', 'url': url, 'title': 'Backend Engineer',
                'description': text,
                'jobLocation': {'address': {'addressLocality': 'Berlin',
                                             'addressCountry': 'DE'}}})
            + '</script></html>')


CLICKOUT = 'https://englishjobs.de/clickout/f165c2adc6f47af9?ql=l&sig=46'
FINAL = 'https://de.talent.com/redirect?id=645792200893538312&source='


def _scanner():
    return CareerPortalScanner(input_csv='__unused__.csv', skip_preflight=True)


def _enricher(html, final=FINAL):
    state = DetailRequestState()
    scanner = SimpleNamespace(
        config=ProductionScannerConfig(), _detail_state=state,
        store_captured_jd=lambda u, t: None)
    response = SimpleNamespace(
        status=200, url=final, text=lambda: html, dispose=lambda: None)
    context = SimpleNamespace(
        request=SimpleNamespace(get=lambda url, **kw: response))
    return SessionDetailEnricher(context, scanner, CLICKOUT)


def test_titles_match_normalizes_and_contains():
    assert _titles_match('Regional Electrical Engineer',
                         'Regional Electrical Engineer (m/w/d)')
    assert _titles_match('Ingénieur Logiciel', 'Ingenieur Logiciel')
    assert not _titles_match('Backend Engineer', 'Frontend Developer')
    assert not _titles_match('', 'Backend Engineer')


def test_redirect_accepted_with_matching_jobposting_title():
    enricher = _enricher(html_ld(POS, url=FINAL))
    result = enricher._redirect_result(
        html_ld(POS, url=FINAL), CLICKOUT, FINAL, 'Talent.com',
        'Backend Engineer', 'HTTP (assisted session)')
    assert result.status == 'readable'
    assert result.phase == 'redirect_identity'
    assert 'visa sponsorship' in result.text


def test_redirect_rejected_for_a_genuinely_different_job():
    enricher = _enricher(html_ld(POS, url=FINAL))
    result = enricher._redirect_result(
        html_ld(POS, url=FINAL), CLICKOUT, FINAL, 'Talent.com',
        'Regional Electrical Engineer', 'HTTP (assisted session)')
    assert result.status == 'empty'
    assert 'identity unproven' in result.reason
    assert not result.text


def test_redirect_rejected_without_expected_title_or_structure():
    enricher = _enricher(html_ld(POS, url=FINAL))
    no_title = enricher._redirect_result(
        html_ld(POS, url=FINAL), CLICKOUT, FINAL, 'Talent.com', '',
        'HTTP (assisted session)')
    assert no_title.status == 'empty'
    plain = ('<html><title>Backend Engineer</title><main>'
             + POS + '</main></html>')
    no_structure = enricher._redirect_result(
        plain, CLICKOUT, FINAL, 'Backend Engineer', 'Backend Engineer',
        'HTTP (assisted session)')
    assert no_structure.status == 'empty'


def test_read_http_clickout_end_to_end():
    enricher = _enricher(html_ld(POS, url=FINAL))
    result = enricher.read(CLICKOUT, expected_title='Backend Engineer')
    assert result.status == 'readable'
    assert result.final_url == FINAL


def test_parse_detail_still_rejects_single_foreign_jobposting():
    result = parse_detail(
        html_ld(POS, 'https://fixture.invalid/jobs/999999-other.html'),
        CLICKOUT, CLICKOUT, _scanner())
    assert result.status == 'empty' and not result.text
