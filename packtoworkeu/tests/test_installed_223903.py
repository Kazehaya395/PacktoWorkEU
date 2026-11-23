"""Installed-run failures: frontier retry, no prefix restart, exact capture proof."""
import csv
import hashlib
import ssl
import time
import urllib.error
import pytest
from packtoworkeu.scanning.career import career_scanner as cs
from packtoworkeu.scanning.career.large_boards import proven_detail, lux_listing
from packtoworkeu.core.saved_details import SavedDetailReader, capture_proof
from packtoworkeu.scanning.pipeline import _ingest_output_csv
from packtoworkeu.db import database as db

HOSTS = ['jobs.intesasanpaolo.com', 'jobs.pradagroup.com']


def body(url, text=None):
    text = text or 'Design reliable systems. Collaborate with the engineering team. ' * 20
    return (f'<link rel="canonical" href="{url}"><h1>Systems Engineer</h1>'
            '<nav>' + 'Search all jobs. ' * 100 + '</nav>'
            '<meta itemprop="streetAddress" content="Milan, IT">'
            '<meta itemprop="hiringOrganization" content="Example Employer">'
            '<span itemprop="description" data-careersite-propertyid="description">'
            f'<p>{text}</p></span><footer>' + 'Privacy and legal. ' * 100 + '</footer>')


@pytest.mark.parametrize('host', HOSTS)
def test_exact_semantic_capture_replaces_longer_unproven_text(tmp_path, host):
    url = f'https://{host}/job/Systems-Engineer/123/'
    s = cs.CareerPortalScanner(input_csv='unused', skip_preflight=True)
    s._saved_jd_reader = SavedDetailReader(str(tmp_path / 'missing.db'))
    s.store_captured_jd(url, 'Unproven whole page with navigation. ' * 200)
    text, loc, org = s._remember_detail_html(url, body(url), final_url=url)
    assert 200 < len(text) < 2000 and 'Privacy' not in text and 'Search all jobs' not in text
    assert loc == 'Milan, IT' and org == 'Example Employer'
    assert s.get_captured_jd(url) == text and capture_proof(s, url, text)
    assert not capture_proof(s, url, text + 'changed')


@pytest.mark.parametrize('host', HOSTS)
@pytest.mark.parametrize('failure', ['canonical', 'redirect', 'multiple', 'clipped', 'missing'])
def test_unproven_named_board_body_never_receives_fresh_proof(host, failure):
    url = f'https://{host}/job/Systems-Engineer/123/'
    html = body(url); final = url
    if failure == 'canonical': html = html.replace(f'href="{url}"', 'href="https://evil.invalid/job/999"')
    if failure == 'redirect': final = url.replace('123', '999')
    if failure == 'multiple': html += body(url)
    if failure == 'clipped': html = body(url, 'Very long posting. ' * 3000)
    if failure == 'missing': html = '<h1>Systems Engineer</h1><p>' + 'Generic site prose. ' * 80 + '</p>'
    s = cs.CareerPortalScanner(input_csv='unused', skip_preflight=True)
    assert not s._remember_detail_html(url, html, final_url=final)[0]
    assert not capture_proof(s, url, 'Generic site prose. ' * 80)


@pytest.mark.parametrize('host', HOSTS)
def test_named_board_capture_survives_sqlite_ingestion(tmp_path, host):
    url = f'https://{host}/job/Systems-Engineer/123/'
    path = str(tmp_path / 'data.db'); db.initialize(path)
    s = cs.CareerPortalScanner(input_csv='unused', skip_preflight=True)
    s._saved_jd_reader = SavedDetailReader(path)
    s._saved_jd_reader.observe(url, title='Systems Engineer')
    text = s._remember_detail_html(url, body(url), final_url=url)[0]
    out = tmp_path / 'rows.csv'
    row = {'Job URL': url, 'Job Title': 'Systems Engineer', 'Company Name': 'Example Employer', 'JD Read Status': 'readable'}
    with out.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(row)); w.writeheader(); w.writerow(row)
    n, _ = _ingest_output_csv(path, out, 'fixture', 'direct', set(), jd_lookup=s.get_captured_jd,
        jd_proof_lookup=lambda u,t: capture_proof(s,u,t), refresh_reader=s._saved_jd_reader)
    assert n == 1 and s._saved_jd_reader.summary()['jd_fresh'] == 1
    con = db.get_connection(path)
    try:
        stored_url, stored, marker = con.execute('SELECT url,description,support_evidence_type FROM jobs').fetchone()
        from packtoworkeu.core.job_identity import posting_identity
        assert posting_identity(stored_url) == posting_identity(url)
        assert stored == text and 'jd_sha256=' + hashlib.sha256(stored.encode()).hexdigest() in marker
    finally: con.close()


def page(n, total=3):
    return (f'<span class="paginationLabel">Results {n} – {n} of {total}</span>'
            f'<tr class="data-row"><td><a class="jobTitle-link" href="/job/Engineer/{n}/">Systems Engineer</a></td></tr>'
            f'<a href="/search/?startrow={n}">Next</a>')


class Response:
    headers = {'Content-Type': 'text/html; charset=utf-8'}
    def __init__(self, html): self.html = html
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size=-1): return self.html.encode()


@pytest.mark.parametrize('persistent', [False, True])
def test_transient_failure_retries_exact_frontier_and_retains_partial(monkeypatch, persistent):
    seed = 'https://jobs.pradagroup.com/search/'
    calls = []; saved = []
    def fetch(req, timeout, context):
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        u = req.full_url; calls.append(u)
        if 'startrow=1' in u and (persistent or calls.count(u) == 1): raise TimeoutError('read operation timed out')
        return Response(page(1 if 'startrow' not in u else 2 if 'startrow=1' in u else 3))
    monkeypatch.setattr(cs.urllib.request, 'urlopen', fetch)
    rows, note = cs.fetch_static_jobs(seed, min_jobs=1, deadline=time.monotonic()+10, cursor_io={'save':saved.append})
    assert calls.count(seed) == 1 and calls.count(seed+'?startrow=1') == 2
    if persistent:
        assert len(rows) == 1 and note.complete is False and not note.needs_dom
        assert 'TimeoutError' in note and saved[-1]['next'].endswith('startrow=1')
    else: assert len(rows) == 3 and note.complete


@pytest.mark.parametrize('error', [urllib.error.HTTPError('https://fixture', 429, 'rate limited', {}, None),
    urllib.error.URLError(ssl.SSLCertVerificationError('bad issuer'))])
def test_no_automatic_retry_of_rate_limit_or_certificate_failure(monkeypatch, error):
    seed = 'https://jobs.pradagroup.com/search/'; calls = []
    def fetch(req, timeout, context):
        calls.append(req.full_url)
        if len(calls) > 1: raise error
        return Response(page(1))
    monkeypatch.setattr(cs.urllib.request, 'urlopen', fetch)
    rows, note = cs.fetch_static_jobs(seed, min_jobs=1)
    assert len(calls) == 2 and len(rows) == 1 and note.complete is False


def test_expired_deadline_makes_no_next_page_request(monkeypatch):
    calls = []
    def fetch(req, timeout, context): calls.append(req.full_url); return Response(page(1))
    monkeypatch.setattr(cs.urllib.request, 'urlopen', fetch)
    rows, note = cs.fetch_static_jobs('https://jobs.pradagroup.com/search/', min_jobs=1, deadline=time.monotonic()-1)
    assert not calls and not rows and 'deadline exhausted' in note and note.complete is False


def test_proven_identity_alias_is_not_hidden_by_longer_legacy_variant(tmp_path):
    url = 'https://jobs.pradagroup.com/job/Systems-Engineer/123/'
    variant = url.rstrip('/')
    s = cs.CareerPortalScanner(input_csv='unused', skip_preflight=True)
    s._saved_jd_reader = SavedDetailReader(str(tmp_path / 'missing.db'))
    s.store_captured_jd(variant, 'Unproven page chrome. ' * 200)
    text = s._remember_detail_html(url, body(url), final_url=url)[0]
    assert s.get_captured_jd(variant) == text and capture_proof(s, variant, text)


def test_retry_allowance_is_bounded_across_entire_listing(monkeypatch):
    seed = 'https://jobs.pradagroup.com/search/'; calls = []
    def fetch(req, timeout, context):
        from urllib.parse import urlsplit, parse_qs
        u = req.full_url; calls.append(u)
        if 'startrow' in u and calls.count(u) == 1: raise TimeoutError('temporary failure')
        n = int(parse_qs(urlsplit(u).query).get('startrow', ['0'])[0]) + 1
        return Response(page(n, total=10))
    monkeypatch.setattr(cs.urllib.request, 'urlopen', fetch)
    rows, note = cs.fetch_static_jobs(seed, min_jobs=1)
    assert len(rows) == 4 and len(calls) == 8 and not note.complete


def test_cancel_between_retry_attempts_is_not_swallowed(monkeypatch):
    from packtoworkeu.scanning.common import ScanCancelled
    seed = 'https://jobs.pradagroup.com/search/'; calls = []
    def fetch(req, timeout, context):
        calls.append(req.full_url)
        if len(calls) > 1: raise TimeoutError('temporary failure')
        return Response(page(1))
    def check():
        if len(calls) >= 2: raise ScanCancelled('user stopped')
    monkeypatch.setattr(cs.urllib.request, 'urlopen', fetch)
    with pytest.raises(ScanCancelled): cs.fetch_static_jobs(seed, min_jobs=1, checkpoint=check)
    assert len(calls) == 2
