"""Offline regressions for continued Italian portal audit."""
import hashlib
import ssl
import sys
import json
import pytest
from packtoworkeu.scanning.common import tls_context
from packtoworkeu.scanning.tls_intermediates import ENI_INTERMEDIATE_PEM
from packtoworkeu.scanning.career.oracle_public import published_api
from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner

SHELL = '<base href="/en/sites/CX_1004" data-apibaseurl="https://tenant.fa.ocs.oraclecloud.com:443" data-sitenumber="CX_1004">'
URL = 'https://jobs.eni.com/en-US/sites/CX_1001/job/34159'


def test_certificate_is_exact_public_intermediate():
    der = ssl.PEM_cert_to_DER_cert(ENI_INTERMEDIATE_PEM)
    assert hashlib.sha256(der).hexdigest() == 'c8025f9fc65fdfc95b3ca8cc7867b9a587b5277973957917463fc813d0b625a9'


@pytest.mark.parametrize('fallback', [True, False])
def test_eni_context_requires_root_and_hostname(monkeypatch, fallback):
    if fallback: monkeypatch.setitem(sys.modules, 'truststore', None)
    ctx = tls_context(URL)
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert not ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


@pytest.mark.parametrize('url', ['', 'http://jobs.eni.com/', 'https://jobs.eni.com.evil.invalid/',
    'https://user@jobs.eni.com/', 'https://jobs.eni.com:444/', 'https://fixture.invalid/'])
def test_chain_completion_is_exact_host_only(monkeypatch, url):
    class Context:
        verify_flags = ssl.VERIFY_X509_PARTIAL_CHAIN
        def load_verify_locations(self, **kwargs): raise AssertionError('unexpected intermediate')
    monkeypatch.setitem(sys.modules, 'truststore', None)
    ctx = Context(); monkeypatch.setattr(ssl, 'create_default_context', lambda: ctx)
    assert tls_context(url) is ctx and ctx.verify_flags == ssl.VERIFY_X509_PARTIAL_CHAIN


def test_observed_public_oracle_base():
    assert published_api(SHELL) == ('https://tenant.fa.ocs.oraclecloud.com', 'CX_1004')


@pytest.mark.parametrize('shell', ['', SHELL + SHELL,
    SHELL.replace('https:', 'http:'), SHELL.replace('oraclecloud.com', 'oraclecloud.com.evil.invalid'),
    SHELL.replace(':443', ':444'), SHELL.replace('https://tenant', 'https://user@tenant'),
    SHELL.replace('href="/en/sites/CX_1004"', 'href="/en/sites/CX_other"')])
def test_ambiguous_or_untrusted_public_base_is_rejected(shell):
    with pytest.raises(ValueError): published_api(shell)


def test_vanity_detail_uses_observed_api_not_vanity_rest(monkeypatch):
    s = CareerPortalScanner(input_csv='unused', skip_preflight=True); calls = []
    def fetch(u):
        calls.append(u)
        if u == URL: return SHELL
        assert u.startswith('https://tenant.fa.ocs.oraclecloud.com/hcmRestApi/')
        assert 'Id=34159,siteNumber=CX_1004' in u
        return json.dumps({'items': [{'Id': '34159', 'PrimaryLocation': 'Milan, Italy',
            'ExternalDescriptionStr': '<p>' + 'Build safe reliable production systems. ' * 30 + '</p>'}]})
    monkeypatch.setattr(s, '_cxs_fetch', fetch)
    result = s._fetch_jd_text_uncached(URL)
    assert len(result[0]) > 200 and result[1] == 'Milan, Italy'
    assert s.get_captured_jd(URL) == result[0]
    s._fetch_jd_text_uncached(URL)
    assert calls.count(URL) == 1  # successful base only, per-scan cache


def test_vanity_exact_requisition_guard_still_applies(monkeypatch):
    s = CareerPortalScanner(input_csv='unused', skip_preflight=True)
    monkeypatch.setattr(s, '_cxs_fetch', lambda u: SHELL if u == URL else json.dumps({'items': [
        {'Id': '999', 'ExternalDescriptionStr': 'Wrong posting. ' * 100}]}))
    assert not s._fetch_jd_text_uncached(URL)[0]
    assert not s.get_captured_jd(URL)


def test_poste_intermediate_fingerprint_and_verified_context():
    from packtoworkeu.scanning.tls_intermediates import POSTE_INTERMEDIATE_PEM
    assert hashlib.sha256(ssl.PEM_cert_to_DER_cert(POSTE_INTERMEDIATE_PEM)).hexdigest() == 'b676ffa3179e8812093a1b5eafee876ae7a6aaf231078dad1bfb21cd2893764a'
    ctx = tls_context('https://carriere.posteitaliane.it/en/sites/CX_3001/jobs')
    assert ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED
    assert not ctx.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN


def test_vanity_listing_reconciles_observed_public_origin(monkeypatch):
    s = CareerPortalScanner(input_csv='unused', skip_preflight=True)
    seed = 'https://carriere.posteitaliane.it/en/sites/CX_3001/jobs?mode=location'
    def fetch(u, headers):
        if u == seed: return SHELL
        assert u.startswith('https://tenant.fa.ocs.oraclecloud.com/hcmRestApi/')
        return json.dumps({'items': [{'TotalJobsCount': 1, 'SiteNumber': 'CX_1004',
            'Offset': 0, 'requisitionList': [{'Id': '418', 'Title': 'Delivery expert', 'PrimaryLocation': 'Rome, Italy'}]}]})
    monkeypatch.setattr(s, '_http_fetch_with_retry', fetch)
    rows, note = s._fetch_oracle_jobs({'careers_url': seed})
    assert len(rows) == 1 and rows[0]['job_url'].endswith('/CX_3001/job/418')
    assert '1 distinct requisitions reconciled' in note


def test_vanity_listing_never_drops_filters(monkeypatch):
    s = CareerPortalScanner(input_csv='unused', skip_preflight=True)
    def forbidden(*args): raise AssertionError('must not fetch broadened search')
    monkeypatch.setattr(s, '_http_fetch_with_retry', forbidden)
    rows, note = s._fetch_oracle_jobs({'careers_url': 'https://jobs.eni.com/en/sites/CX_1004/jobs?keyword=engineer'})
    assert not rows and 'filters not dropped' in note


def test_vanity_listing_count_shortfall_not_complete(monkeypatch):
    s = CareerPortalScanner(input_csv='unused', skip_preflight=True)
    monkeypatch.setattr(s, '_http_fetch_with_retry', lambda u, headers: SHELL if '/hcmRestApi/' not in u else json.dumps({
        'items': [{'TotalJobsCount': 12, 'SiteNumber': 'CX_1004', 'Offset': 0, 'requisitionList': [
            {'Id': '418', 'Title': 'Delivery expert'}]}]}))
    rows, note = s._fetch_oracle_jobs({'careers_url': 'https://jobs.eni.com/en/sites/CX_1004/jobs'})
    assert len(rows) == 1 and note.startswith('listing incomplete:')


def test_successfactors_total_drift_is_explicit_not_complete():
    from packtoworkeu.scanning.career.large_boards import lux_listing
    seed = 'https://careers.essilorluxottica.com/search/'
    def page(start, total, rid):
        return (f'<span class="paginationLabel">Results {start} – {start} of {total}</span>'
            f'<tr class="data-row"><td><a class="jobTitle-link" href="/job/example/{rid}/">Example role</a></td></tr>'
            f'<a href="/search/?startrow={start}">Next</a>')
    rows, note = lux_listing(seed, page(1, 3, 100), lambda u: page(2, 2, 101), lambda: None)
    assert len(rows) == 1 and not note.complete
    assert 'published total changed from 3 to 2' in note


def test_successfactors_missing_next_is_explicit():
    from packtoworkeu.scanning.career.large_boards import lux_listing
    html = ('<span class="paginationLabel">Results 1 – 1 of 2</span>'
        '<tr class="data-row"><td><a class="jobTitle-link" href="/job/example/100/">Example role</a></td></tr>')
    rows, note = lux_listing('https://jobs.pradagroup.com/search/', html, lambda u: '', lambda: None)
    assert len(rows) == 1 and not note.complete and 'no observed next link' in note


def test_observed_eni_irc_requisition_keeps_exact_identity(monkeypatch):
    url = 'https://jobs.eni.com/en/sites/CX_1004/job/IRC224927'
    route = CareerPortalScanner._oracle_detail_route(url)
    assert route and route[1] == 'IRC224927' and 'Id=IRC224927,' in route[0]
    s = CareerPortalScanner(input_csv='unused', skip_preflight=True)
    def fetch(u):
        if u == url: return SHELL
        assert 'Id=IRC224927,' in u
        return json.dumps({'items': [{'Id': 'IRC224927', 'ExternalDescriptionStr': 'Support employee development and hiring. ' * 30}]})
    monkeypatch.setattr(s, '_cxs_fetch', fetch)
    assert len(s._fetch_jd_text_uncached(url)[0]) > 200
