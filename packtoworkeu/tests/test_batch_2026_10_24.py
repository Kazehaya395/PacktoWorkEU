"""Approved 24 Oct follow-up: offline structural, DB and real-call regressions.

Third-party hosts are never contacted. Slow scale/browser proofs are opt-in.
"""
from __future__ import annotations

import csv
import inspect
import json
import re
import sqlite3
import threading
from pathlib import Path

import pytest

from packtoworkeu.db import database as db
from packtoworkeu.core.persistence import upsert_job
from packtoworkeu.insights import queries, terms
from packtoworkeu.scanning.career.career_scanner import CareerPortalScanner

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'followup.db'
    db.initialize(path)
    return path


def _write_csv(path, rows, fields=None):
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


@pytest.mark.parametrize('country', ['Brazil', 'India', 'United States', 'Germany'])
def test_country_filter_never_silently_adds_unknown_regional_remote(database, country):
    with db.get_connection(database) as conn:
        upsert_job(conn, {'title': 'EU remote role', 'url': 'https://jobs.example/eu',
                         'country': '', 'location': 'Europe, Remote', 'remote_type': 'remote_eu'})
        upsert_job(conn, {'title': 'Local role', 'url': 'https://jobs.example/local',
                         'country': country, 'location': country})
    rows = db.search_jobs(database, country=country)
    assert [r['title'] for r in rows] == ['Local role']


def test_blocks_from_both_phases_are_queued_but_dns_is_not(tmp_path):
    from packtoworkeu.core.blocked_portals import blocked_portals_from_summary
    fields = ['Seed Name', 'Message', 'Seed URL']
    a, c = tmp_path / 'ats.csv', tmp_path / 'career.csv'
    _write_csv(a, [{'Seed Name': 'ATS Board', 'Message': 'blocked by bot protection: CAPTCHA',
                    'Seed URL': 'https://boards.example/tenant/jobs?country=BR'}], fields)
    _write_csv(c, [{'Seed Name': 'Career', 'Message': 'blocked by bot protection: AWS WAF',
                    'Seed URL': 'https://careers.example/jobs'},
                   {'Seed Name': 'Network', 'Message': 'net::ERR_NAME_NOT_RESOLVED',
                    'Seed URL': 'https://network.example/jobs'}], fields)
    result = blocked_portals_from_summary({'artifacts': {'ats': {'errors': str(a)}, 'career': {'errors': str(c)}}})
    assert {r['seed_name'] for r in result} == {'ATS Board', 'Career'}
    assert {r['phase'] for r in result} == {'ats', 'career'}


def test_blocked_queue_persists_scoped_urls_sources_and_outcomes(database):
    portals = [{'seed_name': 'Seed A', 'url': 'https://boards.example/jobs?country=BR', 'reason': 'CAPTCHA', 'phase': 'ats'},
               {'seed_name': 'Seed B', 'url': 'https://boards.example/jobs?country=BR', 'reason': 'WAF', 'phase': 'career'},
               {'seed_name': 'Seed C', 'url': 'https://boards.example/jobs?country=DE', 'reason': 'CAPTCHA', 'phase': 'career'}]
    db.enqueue_blocked_portals(database, portals, run_id='RUN1')
    db.enqueue_blocked_portals(database, portals, run_id='RUN1')
    pending = db.list_blocked_portals(database)
    assert len(pending) == 2
    br = next(dict(r) for r in pending if 'BR' in r['url'])
    assert br['seed_name'] == 'Seed A; Seed B'
    assert br['status'] == 'pending'
    db.update_blocked_portal(database, br['url'], status='running')
    db.enqueue_blocked_portals(database, portals[:1], run_id='RUN2')
    assert next(r for r in db.list_blocked_portals(database) if r['url'] == br['url'])['status'] == 'running'
    db.update_blocked_portal(database, br['url'], status='partial', pages=2, jobs=9, result='verification reappeared')
    assert next(r for r in db.list_blocked_portals(database) if r['url'] == br['url'])['status'] == 'partial'
    db.update_blocked_portal(database, br['url'], status='resolved')
    assert len(db.list_blocked_portals(database)) == 1
    assert len(db.list_blocked_portals(database, include_resolved=True)) == 2


def test_regular_scan_ui_has_no_headed_or_external_browser_action():
    from packtoworkeu.ui.tabs.tools import ToolsTab
    src = inspect.getsource(ToolsTab._offer_blocked_portals) + inspect.getsource(ToolsTab._show_blocked_notice)
    assert 'run_assisted_ingestion' not in src
    assert 'open_in_default_browser' not in src
    assert '_AssistedBatchWorker(' not in src
    assert 'enqueue_blocked_portals' in src
    assert 'Assisted Ingestion' in src


def test_assisted_quick_list_is_the_persistent_queue_not_four_demo_hosts():
    src = (ROOT / 'ui/tabs/assisted_ingestion.py').read_text(encoding='utf-8')
    assert 'list_blocked_portals' in src
    assert 'https://www.robertwalters.nl/vacatures.html' not in src
    assert 'if self._worker is not None and self._worker.isRunning()' in src


@pytest.mark.parametrize('text', [
    'Python PostgreSQL AWS Docker Kubernetes and stakeholder management.',
    'Looking for a developer. Go Golang golang Swift SWIFT payments SQL C++ C# .NET',
    'Communication, résolution de problèmes, Deutsch, italiano, Linux and JavaScript.',
    'İTIL, Kuberneteſ, PyTHon and worKday. A dotless ı character.',
    'R machine learning and ASP.NET; node.js / Power BI / CI/CD.',
    'No dictionary skills are implied by an otherwise unclassified opportunity.',
])
def test_indexed_matcher_has_exact_regex_parity(text):
    terms._load_data()
    expected = {r['term'] for r in terms._SKILLS if r['regex'].search(text)}
    assert set(terms.extract_skills(text)) == expected


def test_incremental_unknown_is_a_completed_result_not_an_endless_retry(database):
    with db.get_connection(database) as conn:
        upsert_job(conn, {'title': 'Unclassified Opportunity', 'url': 'https://jobs.example/unknown',
                         'description': '', 'industry': ''})
    assert queries.backfill_existing_job_terms(database) == 1
    assert queries.backfill_existing_job_terms(database) == 0
    assert queries.backfill_existing_job_terms(database) == 0


def test_insights_cpu_work_holds_no_writer_lock_and_signals_progress(database, monkeypatch):
    with db.get_connection(database) as conn:
        upsert_job(conn, {'title': 'Backend Engineer', 'url': 'https://jobs.example/original',
                         'description': 'Python and SQL with Docker.'})
    entered, release = threading.Event(), threading.Event()
    original = terms.extract_terms
    outcome, progress = {}, []
    def paused(*args, **kwargs):
        entered.set()
        assert release.wait(8)
        return original(*args, **kwargs)
    monkeypatch.setattr(terms, 'extract_terms', paused)
    def refresh():
        try:
            outcome['count'] = queries.backfill_existing_job_terms(database, batch_size=1,
                progress_callback=lambda done, total, msg: progress.append((done, total, msg)))
        except Exception as exc:
            outcome['error'] = str(exc)
    worker = threading.Thread(target=refresh)
    worker.start()
    try:
        assert entered.wait(3), outcome
        with db.get_connection(database) as conn:
            conn.execute('PRAGMA busy_timeout=100')
            upsert_job(conn, {'title': 'Newly harvested', 'url': 'https://jobs.example/concurrent'})
    finally:
        release.set()
        worker.join(10)
    assert 'error' not in outcome
    assert outcome['count'] >= 1
    assert progress and progress[-1][0] == progress[-1][1]
    assert queries.has_pending_insights(database)  # row inserted after the bounded snapshot


def test_insights_rejects_stale_snapshot_and_preserves_new_input(database, monkeypatch):
    with db.get_connection(database) as conn:
        upsert_job(conn, {'title': 'Backend Engineer', 'url': 'https://jobs.example/edit', 'description': 'Python'})
    original = terms.extract_terms
    changed = [False]
    def during_extract(*args, **kwargs):
        if not changed[0]:
            changed[0] = True
            with db.get_connection(database) as conn:
                upsert_job(conn, {'title': 'Registered Nurse', 'url': 'https://jobs.example/edit',
                                 'description': 'Clinical patient care with medical devices and teamwork.'})
        return original(*args, **kwargs)
    monkeypatch.setattr(terms, 'extract_terms', during_extract)
    queries.backfill_existing_job_terms(database)
    assert queries.has_pending_insights(database)
    monkeypatch.setattr(terms, 'extract_terms', original)
    assert queries.backfill_existing_job_terms(database) == 1
    with db.get_connection(database) as conn:
        row = conn.execute('SELECT title, category FROM jobs').fetchone()
    assert row['title'] == 'Registered Nurse'
    assert row['category'] == 'Healthcare & Life Sciences'
    assert not queries.has_pending_insights(database)


def test_insights_upsert_same_data_does_not_dirty_but_longer_jd_does(database):
    job = {'title': 'Backend Engineer', 'url': 'https://jobs.example/repeat', 'description': 'Python and SQL.'}
    with db.get_connection(database) as conn:
        upsert_job(conn, job)
    queries.backfill_existing_job_terms(database)
    with db.get_connection(database) as conn:
        upsert_job(conn, job)
    assert not queries.has_pending_insights(database)
    with db.get_connection(database) as conn:
        upsert_job(conn, {**job, 'description': 'Python and SQL. Kubernetes, Docker and AWS are required.'})
    assert queries.has_pending_insights(database)


def test_posting_identity_merges_alias_endpoints_not_arbitrary_numeric_tokens():
    from packtoworkeu.core.job_identity import posting_identity
    a = 'https://careers.example/en/job-search/jobs/499509'
    b = 'https://careers.example/en/jobs/photonics-engineer-499509'
    assert posting_identity(a) == posting_identity(b)
    assert posting_identity('https://careers.example/details?jobId=1102309') != posting_identity('https://careers.example/details?jobId=1094612')
    assert posting_identity('https://careers.example/it/annonce/4595973-sales-assistant-500599') != posting_identity('https://careers.example/it/annonce/4595971-sales-assistant-500599')
    assert posting_identity('https://careers.example/jobs#job=AbCd') != posting_identity('https://careers.example/jobs#job=abcd')
    assert posting_identity('https://careers.example/tenant1/jobs/499509') != posting_identity('https://careers.example/tenant2/jobs/499509')


def test_new_alias_ingestion_preserves_one_job_sources_jd_and_manual_country(database):
    a, b = 'https://careers.example/en/job-search/jobs/499509', 'https://careers.example/en/jobs/photonics-engineer-499509'
    with db.get_connection(database) as conn:
        upsert_job(conn, {'title': 'Photonics Engineer', 'url': a, 'source_name': 'Seed A',
                         'country': 'Brazil', 'country_source': 'manual', 'description': 'A detailed description with Python and semiconductor design.'})
        upsert_job(conn, {'title': 'Photonics Engineer', 'url': b, 'source_name': 'Seed B',
                         'country': 'Germany', 'description': ''})
        rows = conn.execute('SELECT * FROM jobs').fetchall()
    assert len(rows) == 1
    assert rows[0]['source_name'] == 'Seed A; Seed B'
    assert rows[0]['country'] == 'Brazil'
    assert 'semiconductor' in rows[0]['description']


def test_csv_finalization_merges_recruiters_and_sources_never_quarantines(tmp_path):
    from packtoworkeu.core.job_identity import finalize_job_csvs
    fields = ['Company Name', 'Seed Name', 'Source Type', 'Job Title', 'Job Location', 'Job URL', 'Record Status']
    a = {'Company Name': 'Employer', 'Seed Name': 'Employer Seed', 'Source Type': 'direct_employer',
         'Job Title': 'Backend Engineer', 'Job Location': 'Berlin, Germany',
         'Job URL': 'https://careers.example/en/jobs/backend-engineer-499509', 'Record Status': 'accepted'}
    b = {**a, 'Seed Name': 'Agency Seed', 'Source Type': 'recruiter',
         'Job URL': 'https://careers.example/en/job-search/jobs/499509'}
    c = {**b, 'Job URL': 'https://other.example/jobs/600000', 'Job Title': 'Platform Engineer'}
    main, rec = tmp_path/'jobs.csv', tmp_path/'jobs_recruiter.csv'
    _write_csv(main,[a],fields);_write_csv(rec,[b,c],fields)
    stats = finalize_job_csvs(main, rec, fields)
    with main.open(encoding='utf-8-sig',newline='') as f: out=list(csv.DictReader(f))
    assert len(out) == 2 and stats['merged'] == 1
    assert out[0]['Seed Name'] == 'Employer Seed; Agency Seed'
    assert out[0]['Source Type'] == 'direct_employer'
    assert out[1]['Source Type'] == 'recruiter'
    assert all(r['Record Status']=='accepted' for r in out)
    assert main.read_bytes().startswith(b'\xef\xbb\xbf')
    assert not rec.exists()


@pytest.mark.parametrize('title', ['Permanent','Full-time','Go to job site'])
def test_metadata_or_cta_is_never_the_final_job_title(title):
    scanner=CareerPortalScanner(input_csv='__unused__.csv')
    assert not scanner.is_valid_job_title(title, 'Employer', url='https://careers.example/jobs/backend-engineer-123456')


@pytest.mark.parametrize('title, url', [
    ('General Application','https://careers.example/jobs/123456'),
    ('Field Talent Community','https://careers.example/jobs/123457'),
    ('Open application for University','https://careers.example/job/123458'),
    ('Proactive Application','https://careers.example/careers/proactive-application'),
    ('SAP SuccessFactors (Open Application)','https://careers.example/jobs/sap-open-application'),
    ('Candidatura Spontanea','https://careers.example/jobs/123459'),
    ('Speculative application','https://jobs.example/en/sites/CX_1004/job/30001'),
    ('Initiative Application','https://jobs.example/en/employees/jobs/jobdetail/3992/initiative-application'),
])
def test_generic_application_postings_are_rejected(title,url):
    # Reversed 2026-11-09 by user decision: evergreen/generic-application
    # postings are noise. Title is rejected; URL shape alone stays valid,
    # and even an explicit numbered detail URL cannot resurrect one.
    from packtoworkeu.core import job_identity as ji
    scanner=CareerPortalScanner(input_csv='__unused__.csv')
    assert not scanner.is_valid_job_title(title, 'Employer', url=url)
    assert ji.administrative_action(url, title)
    assert not ji.posting_candidate(url, title)
    assert ji.nonposting_record(url, title)


def test_italian_regions_resolve_to_italy_not_geonames_homonyms():
    # Run 20261109T211510: "Lombardia" resolved to Mexico (Lombardia,
    # Michoacán in the GeoNames place index); 10 Esselunga rows stamped
    # Job Country=Mexico from a Lombardia URL slug.
    from packtoworkeu.core.location_country import country_from_location
    for region in ('Lombardia','Piemonte','Toscana','Puglia','Sardegna',
                   'Campania','Sicilia','Marche','Basilicata',"Valle d'Aosta"):
        assert country_from_location(region) == 'Italy', region


def test_careers_info_pages_are_nonpostings():
    # Run 20261109T211510: Eni welfare / fraud-alert / job-opportunities
    # info pages shipped as jobs; they route to the existing
    # nonvacancy_information_or_analytics quarantine, not the job table.
    from packtoworkeu.core import job_identity as ji
    for url in ('https://www.eni.com/en-IT/careers/welfare.html',
                'https://www.eni.com/en-IT/careers/job-offers-fraud.html',
                'https://www.eni.com/en-IT/careers/job-opportunities.html'):
        assert ji.nonposting_url(url), url
        assert ji.administrative_action(url)
    # A real vacancy under the same portal keeps working.
    assert not ji.nonposting_url('https://www.eni.com/en-IT/careers/jobs/backend-engineer.html')


def test_all_open_positions_is_a_nav_title_not_a_role():
    # Run 20261109T211510: 34 HubSpot jobs shipped titled "All Open
    # Positions" (the listing/nav heading) via detail-title rescue.
    scanner=CareerPortalScanner(input_csv='__unused__.csv')
    assert scanner._is_nav_title('All Open Positions')
    assert not scanner.is_valid_job_title(
        'All Open Positions', 'HubSpot',
        'https://www.hubspot.com/careers/jobs/5986425')


@pytest.mark.parametrize('url', [
    'https://careers.example/find-job/650853d4-271b-4adc-b01b-977b5d555a5b',
    'https://careers.example/jobs/legal-counsel/0ed980f08fd3eec5849107b23d857388',
    'https://careers.example/about/careers/jobs/pricing-engineer-123456',
    'https://careers.example/careers/job?jobId=123456',
    'https://careers.example/careers/open-positions/650853d4-271b-4adc-b01b-977b5d555a5b',
    'https://careers.example/careers/job-offers/650853d4-271b-4adc-b01b-977b5d555a5b',
    'https://careers.example/careers/job-openings/0ed980f08fd3eec5849107b23d857388',
])
def test_uuid_hash_and_jobid_rescues_precede_path_exclusions(url):
    assert CareerPortalScanner(input_csv='__unused__.csv',skip_preflight=True).is_valid_job_url(url)
    from packtoworkeu.scanning.ats.ats_scanner import ATSScanner
    assert ATSScanner(seed_file='__unused__.csv',skip_preflight=True).valid_job_url(url)


@pytest.mark.parametrize('url', ['https://careers.example/legal', 'https://careers.example/privacy',
                                 'https://careers.example/jobs/engineering', 'https://careers.example/apply'])
def test_navigation_facets_and_generic_apply_are_still_rejected(url):
    assert not CareerPortalScanner(input_csv='__unused__.csv').is_valid_job_url(url)


def test_location_recovery_obeys_host_breaker_and_reuses_captured_text(monkeypatch):
    scanner=CareerPortalScanner(input_csv='__unused__.csv')
    calls=[]
    def no_network(*args,**kwargs):
        calls.append(args);raise AssertionError('No network allowed in this fixture')
    monkeypatch.setattr('urllib.request.urlopen',no_network)
    monkeypatch.setattr(scanner,'_detail_host_unavailable',lambda _u:True)
    assert scanner._detail_location_from_url('https://careers.example/jobs/123456',allow_browser=False) is None
    assert not calls
    monkeypatch.setattr(scanner,'_detail_host_unavailable',lambda _u:False)
    url='https://careers.example/jobs/123457'
    scanner.store_captured_jd(url,'Location: Berlin, Germany\nWe seek a backend engineer with Python, Docker and SQL. '*8)
    location=scanner._detail_location_from_url(url,allow_browser=False)
    assert location and 'Berlin' in location[0]
    assert not calls


def test_cap_and_repeat_stop_are_partial_not_ok():
    from packtoworkeu.scanning.common import completion_status
    assert completion_status(20,'',['static HTML: 20 (12 page(s); page cap 12 reached, board has more pages)'])=='partial'
    assert completion_status(20,'',['pagination STOPPED: 2 pages of repeats after page 17'])=='partial'
    assert completion_status(20,'',['pagination complete: no next-page control after page 20'])=='ok'
    assert completion_status(0,'',[])=='empty'
    assert completion_status(0,'blocked by bot protection',[])=='error'


def test_dns_recovery_reads_actual_navigation_diagnostics():
    from packtoworkeu.scanning.common import is_dns_failure
    assert is_dns_failure('seed page did not load','Page.goto: net::ERR_NAME_NOT_RESOLVED')
    assert not is_dns_failure('blocked by bot protection','AWS WAF challenge')


def test_dom_url_context_is_generated_from_the_same_python_validator_pattern():
    import json
    from packtoworkeu.core.job_identity import POSTING_CONTEXT_PATTERN
    from packtoworkeu.scanning.career.career_scanner import JS_HELPERS
    assert '__POSTING_CONTEXT_PATTERN__' not in JS_HELPERS
    assert json.dumps(POSTING_CONTEXT_PATTERN) in JS_HELPERS
    assert 'postingContextRe.test(s)' in JS_HELPERS


@pytest.mark.parametrize('left,right', [
    ('https://board.example/en/jobs/engineer-requisition-111111-560001',
     'https://board.example/en/jobs/engineer-requisition-222222-560001'),
    ('https://board.example/en/jobs/engineer-REQ-A781234-560001',
     'https://board.example/en/jobs/engineer-REQ-B991234-560001'),
    ('https://board.example/en/job-search/jobs/560001',
     'https://board.example/en/jobs/engineer-requisition-111111-560001'),
])
def test_ambiguous_trailing_site_or_postcode_never_erases_distinct_requisitions(database,left,right):
    from packtoworkeu.core.job_identity import posting_identity,merge_job_rows
    assert posting_identity(left)!=posting_identity(right)
    rows=[{'Job URL':url,'Job Title':'Backend Engineer','Seed Name':'Seed '+str(n)}
          for n,url in enumerate((left,right))]
    merged,removed=merge_job_rows(rows)
    assert len(merged)==2 and not removed
    with db.get_connection(database) as conn:
        for row in rows:
            upsert_job(conn,{'url':row['Job URL'],'title':row['Job Title'],'company':'Observed Employer'})
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==2
