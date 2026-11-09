"""Exact-scope resume, bounded admission, observed locations, honest access queue."""
from __future__ import annotations
import csv
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from sponsorscout.db import database as db
from sponsorscout.application import scan_scope as scopes


def row(name='C', country='Global', url='https://c.invalid/jobs'):
    return dict(name=name, canonical_name='Canonical C', careers_url=url,
                provider='auto', source_type='direct_employer', target_country=country,
                scope_policy='global', enabled='true')


def log(path, run, phase, target, status='partial'):
    db.record_scan_log_rows(path, run, phase, [dict(seed_name=target['name'],
        company=target.get('canonical_name') or target['name'], target_country=target['target_country'],
        seed_url=target['careers_url'], status=status, jobs_found=0)])


@pytest.fixture
def database(tmp_path):
    path = tmp_path / 'scope.db'
    db.initialize(path)
    return path


def plan(ats=(), career=(), method='full'):
    return dict(version=1, method=method, ats=list(ats), career=list(career))


def test_completed_career_only_261_never_offers_87_ats(database):
    targets = [row(str(i), url=f'https://c.invalid/{i}') for i in range(261)]
    db.start_scan_run(database, 'R', 'custom', 0, 261)
    scopes.save_plan(database, 'R', plan(career=targets))
    for target in targets:
        log(database, 'R', 'career', target)
    db.finish_scan_run(database, 'R', status='partial')
    assert db.get_resumable_scan(database) is None


def test_phase_country_url_identity_and_chain(database):
    a, b = row(country='Germany'), row(country='Italy')
    original = plan(ats=[a], career=[a, b])
    db.start_scan_run(database, 'R', 'custom', 1, 2)
    scopes.save_plan(database, 'R', original)
    log(database, 'R', 'career', a)
    db.finish_scan_run(database, 'R', status='cancelled')
    cp = db.get_resumable_scan(database)
    assert cp['done_ats'] == 0 and cp['done_career'] == 1
    assert cp['remaining_ats'] == ['C'] and cp['remaining_career'] == ['C']
    db.start_scan_run(database, 'CHILD', 'custom', 1, 1)
    scopes.save_plan(database, 'CHILD', original)
    log(database, 'CHILD', 'ats', a)
    db.finish_scan_run(database, 'CHILD', status='cancelled', error='resumed_from:R')
    cp = db.get_resumable_scan(database)
    assert cp['run_id'] == 'CHILD' and cp['done_career'] == 1 and cp['done_ats'] == 1
    assert not db.mark_scan_resumed(database, 'R', 'CHILD')
    log(database, 'CHILD', 'career', b)
    assert db.mark_scan_resumed(database, 'R', 'CHILD')
    assert db.get_resumable_scan(database) is None


def test_legacy_unknown_scope_is_not_expanded(database):
    db.start_scan_run(database, 'OLD', 'custom', 0, 261)
    db.finish_scan_run(database, 'OLD', status='cancelled')
    assert db.get_resumable_scan(database) is None
    assert scopes.load_plan(database, 'OLD') is None


def test_corrupt_scope_fails_closed(database):
    scopes.save_plan(database, 'R', dict(version=1, method='full', ats='bad', career=[]))
    with pytest.raises(ValueError): scopes.load_plan(database, 'R')


def test_seed_reader_preserves_routes_filters_alias_and_disabled(tmp_path):
    path = tmp_path / 'seed.csv'
    records = [row(country='Germany'), row(country='Italy'), row('Other')]
    records[-1]['enabled'] = 'false'
    scopes.write_seed(path, records + records[:1])
    got = scopes.read_targets(path, ['Canonical C'])
    assert len(got) == 2
    assert {r['target_country'] for r in got} == {'Germany', 'Italy'}


def test_pipeline_direct_resume_uses_frozen_rows_and_method(database, tmp_path, monkeypatch):
    from sponsorscout.application import seed_manager
    from sponsorscout.scanning import pipeline
    from sponsorscout.scanning.career import career_scanner
    from sponsorscout.scanning.ats import ats_scanner
    ats, career = tmp_path/'ats.csv', tmp_path/'career.csv'
    scopes.write_seed(ats, [row('ATS')])
    original_rows = [row(country='Germany'), row(country='Italy')]
    scopes.write_seed(career, original_rows)
    monkeypatch.setattr(seed_manager, 'user_ats_path', lambda: ats)
    monkeypatch.setattr(seed_manager, 'user_career_path', lambda: career)
    output = tmp_path / 'outputs'; output.mkdir()
    monkeypatch.setattr(pipeline.paths, 'ensure_scan_output_dir', lambda: output)
    seen, details = [], []
    cancel = threading.Event()
    class FakeCareer:
        def __init__(self, input_csv, output_csv, detail_scan, **kwargs):
            self.targets = scopes.read_targets(input_csv)
            self.output = Path(output_csv)
            seen.append(self.targets)
            details.append(detail_scan)
        def execute_crawler(self):
            self.output.write_text('Job Title,Job URL\n')
            for target in self.targets:
                log(database, self.run_id, 'career', target)
                if len(seen) == 1:
                    cancel.set()
                    break
        def get_captured_jd(self, url): return ''
    def forbidden_ats(**kwargs):
        pytest.fail('Resume expanded Career-only scope into ATS')
    monkeypatch.setattr(career_scanner, 'CareerPortalScanner', FakeCareer)
    monkeypatch.setattr(ats_scanner, 'ATSScanner', forbidden_ats)
    # Distinct run IDs, without sleeping or patching Python's global time module.
    real_time = time
    ids = iter(['R1', 'R2'])
    monkeypatch.setattr(pipeline, 'time', SimpleNamespace(
        strftime=lambda *_: next(ids), monotonic=real_time.monotonic,
        time=real_time.time, sleep=real_time.sleep))
    first = pipeline.run_scan(method='full', db_path=database,
                              cancel_event=cancel, run_ats=False, run_career=True,
                              only_career=['Canonical C'])
    assert first['cancelled']
    # Same name, different route, new company, and ATS edits must not affect resume.
    scopes.write_seed(career, [row(country='France',url='https://changed.invalid'), row('New')])
    scopes.write_seed(ats, [row('New ATS')])
    second = pipeline.run_scan(method='quick', db_path=database, resume_from=first['run_id'])
    assert len(seen) == 2 and seen[0] == original_rows and seen[1] == original_rows[1:]
    assert details == [True, True]
    assert second['resumed_skipped'] == 1
    assert scopes.load_plan(database, second['run_id']) == dict(plan(career=original_rows), refresh_descriptions=False)
    assert db.get_resumable_scan(database) is None


def scanner():
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    return CareerPortalScanner(input_csv='__unused__.csv', skip_preflight=True)


def test_inflight_time_counts_against_shared_detail_budget(monkeypatch):
    import sponsorscout.scanning.detail_state as mod
    now = [10.0]
    monkeypatch.setattr(mod, 'time', SimpleNamespace(monotonic=lambda: now[0]))
    s = scanner()
    s.config.DETAIL_SCAN_TIME_BUDGET_SEC = 2
    url = 'https://fixture.invalid/1'
    assert s._detail_state.reserve(url, s.config)
    now[0] += 3
    assert not s._detail_state.allowed('https://fixture.invalid/2', s.config)
    assert s._detail_state.remaining_seconds(url, s.config) == 0


def test_exhausted_work_never_joins_http_queue(monkeypatch):
    s = scanner()
    url = 'https://fixture.invalid/exhausted'
    s._detail_state._scope(url)['busy'] = s.config.DETAIL_SCAN_TIME_BUDGET_SEC
    monkeypatch.setattr(s._http_schedule, 'slot', lambda *a, **k: pytest.fail('exhausted work queued'))
    assert s._fetch_jd_text(url, allow_browser=False) == ('', '', '')


def test_http_admission_without_tls_deadline_is_still_bounded():
    s = scanner()
    s.config.DETAIL_SCAN_TIME_BUDGET_SEC = .03
    s._http_schedule.active = s._http_schedule.total
    started = time.monotonic()
    assert s._fetch_jd_text('https://fixture.invalid/queue', allow_browser=False) == ('', '', '')
    assert time.monotonic() - started < .5
    assert s._detail_state.total == 0
    assert not s._http_schedule.waiters


@pytest.mark.parametrize('text,expected', [
    ('Full Time • Munich & Remote', 'Munich'),
    ('Part-time · Berlin & Hybrid', 'Berlin'),
    ('Full Time • Remote', ''),
    ('Full Time • Product Engineering', ''),
])
def test_badge_location_is_observed_not_invented(text, expected):
    assert scanner()._clean_rescued_location(text) == expected


def test_multiline_job_json_recovers_location_without_js_repair():
    s = scanner()
    posting = {'@type':'JobPosting','title':'Engineer', 'description':'Build reliable software.\nWork with the team.',
               'jobLocation':{'address':{'addressLocality':'Berlin','addressCountry':'DE'}}}
    raw = json.dumps(posting).replace('\\n', '\n')
    html = '<script type="application/ld+json">' + raw + '</script>'
    assert s._parse_detail_html(html)[1] == 'Berlin, DE'
    assert s._extract_ld_location_from_html(html) == 'Berlin, DE'
    with pytest.raises(ValueError): scopes.json.loads(raw)  # proves original failure
    with pytest.raises(ValueError):
        from sponsorscout.core.job_metadata import load_job_json
        load_job_json("{location: 'Berlin'}")


@pytest.mark.parametrize('html,expected', [
    ('<meta content="Location" name="twitter:label1"><meta content="Munich, DE" name="twitter:data1">', 'Munich, DE'),
    ('<h1>Engineer</h1><ul class="meta"><li class="location"><i></i><span>Munich, DE</span></li></ul>', 'Munich, DE'),
    ('<h1>Engineer</h1><p>Work on products.</p><footer><li class="location">Berlin</li></footer>', ''),
    ('<meta name="twitter:label1" content="Company"><meta name="twitter:data1" content="Berlin">', ''),
])
def test_explicit_detail_metadata_not_company_footer(html, expected):
    assert scanner()._parse_detail_html(html)[1] == expected


def test_detail_only_access_queue_persists_without_listing_claim(database, tmp_path):
    from sponsorscout.core.blocked_portals import blocked_portals_from_summary
    file = tmp_path/'log.csv'
    fields = ['Seed Name','Seed URL','Jobs Found','Error','Diagnostics']
    with file.open('w',newline='') as fh:
        writer = csv.DictWriter(fh,fieldnames=fields);writer.writeheader()
        writer.writerows([
            dict(zip(fields,['C','https://c.invalid/jobs','15','blocked access: 2 unresolved response(s)',''])),
            dict(zip(fields,['D','https://d.invalid/jobs','0','listing access blocked: verification',''])),
            dict(zip(fields,['DNS','https://dns.invalid','0','Name or service not known',''])),
            dict(zip(fields,['OK','https://ok.invalid','4','blocked access: 0 unresolved response(s)',''])),
        ])
    summary = {'artifacts':{'career':{'scan_log':str(file)}}}
    assert len(blocked_portals_from_summary(summary)) == 1
    rows = blocked_portals_from_summary(summary,include_detail_issues=True)
    assert len(rows) == 2
    db.enqueue_blocked_portals(database,rows,'R')
    stored = {r['seed_name']:dict(r) for r in db.list_blocked_portals(database)}
    assert stored['C']['access_scope'] == 'details'
    assert '15 job row(s) were already saved' in stored['C']['reason']
    assert stored['D']['access_scope'] == 'listing'


@pytest.mark.parametrize('locale', ['en', 'it'])
def test_jd_issue_qt_action_uses_explicit_unified_collection(database, monkeypatch, locale):
    from sponsorscout.tests._qt_support import application, wait_loaders, dispose
    from sponsorscout.ui.tabs.assisted_ingestion import AssistedIngestionTab
    from sponsorscout import i18n
    app = application();i18n.set_locale(locale)
    db.enqueue_blocked_portals(database,[dict(seed_name='C',url='https://c.invalid/jobs',access_scope='details',reason='blocked access: 2 unresolved response(s)')],'R')
    tab = AssistedIngestionTab(str(database))
    wait_loaders(tab)
    calls=[]
    monkeypatch.setattr(tab,'_start_ingestion',lambda **kw:calls.append(kw))
    assert tab.portals_table.rowCount() == 1
    tab.portals_table.cellWidget(0,4).click()
    assert calls==[{}]  # one explicit action; planner selects the phase
    assert tab.portals_table.cellWidget(0,4).text()==i18n._('Collect / continue')
    assert tab.url_edit.text() == 'https://c.invalid/jobs'
    assert tab._worker is None
    dispose(tab);i18n.set_locale('en')


def test_interrupted_target_is_not_marked_settled(database):
    target = row()
    db.start_scan_run(database, 'R', 'custom', 0, 1)
    scopes.save_plan(database, 'R', plan(career=[target]))
    log(database, 'R', 'career', target, status='cancelled')
    db.finish_scan_run(database, 'R', status='cancelled')
    cp = db.get_resumable_scan(database)
    assert cp['done_career'] == 0 and cp['remaining_career'] == ['C']


def test_existing_artifact_backfill_never_reopens_dismissed(database,tmp_path,monkeypatch):
    from sponsorscout.core.blocked_portals import backfill_recent_access_issues
    from sponsorscout import paths
    db.start_scan_run(database,'OLD','custom',0,2)
    db.finish_scan_run(database,'OLD',status='partial')
    export=tmp_path/'OLD_career_jobs_scan_log.csv'
    with export.open('w',newline='')as fh:
        writer=csv.DictWriter(fh,fieldnames=['Seed Name','Seed URL','Jobs Found','Error'])
        writer.writeheader()
        writer.writerows([{'Seed Name':name,'Seed URL':f'https://{name}.invalid/jobs',
                           'Jobs Found':'10','Error':'blocked access: 1 unresolved response(s)'}
                          for name in ('missing','dismissed')])
    db.enqueue_blocked_portals(database,[dict(seed_name='dismissed',url='https://dismissed.invalid/jobs',access_scope='details')],'OLD')
    db.update_blocked_portal(database,'https://dismissed.invalid/jobs',status='dismissed')
    monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:tmp_path)
    assert backfill_recent_access_issues(database)==1
    assert backfill_recent_access_issues(database)==0
    stored={r['seed_name']:dict(r)for r in db.list_blocked_portals(database,include_resolved=True)}
    assert stored['dismissed']['status']=='dismissed'
    assert stored['missing']['access_scope']=='details' and stored['missing']['run_id']=='OLD'
