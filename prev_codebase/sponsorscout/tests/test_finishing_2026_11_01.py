"""Finishing pass: real admission, operational permits and native end proof.

Offline only. Browser fixtures intercept all network requests.
"""
import csv
import os
from types import SimpleNamespace
import pytest
from sponsorscout.scanning.jd_support import JDSupportDetector
from sponsorscout.core import blocked_portals as bp, browser_evidence as be, persistence
from sponsorscout.core.support_application import apply_support_to_job
from sponsorscout.db import database as db

BASE=('Responsibilities: Build reliable backend services and maintain automated tests. '
      'Requirements: Strong software engineering skills and practical team collaboration experience. ')*8
HAZARD='Responsible for needs assessment and compliance with all applicable hazardous work permits, including'
URL='https://jobs.fixture.invalid/jobs/123456/backend-engineer'
BAD=[
 ('https://4screen.com/it/detail-screen','+ Detail Screen','Detail Screen - 4screen'),
 ('https://careers.smartrecruiters.com/LGCGroup/lgc_axolabs_en','SEE CURRENT OPPORTUNITIES','LGC Group'),
 ('https://elringklinger.de/en/career/application-process','Application process','Your path to ElringKlinger.'),
 ('https://www.hapag-lloyd.com/en/company/career/working-on-land.html','Working on Land','Working on Land'),
 ('https://www.hapag-lloyd.com/en/company/career/working-on-board.html','Working on Board','Working on Board'),
 ('https://www.hapag-lloyd.com/en/company/career/working-on-land/overview-apprentices.html','Overview Apprentices','Overview Apprentices'),
 ('https://www.hays.de/jobsuche/stellenangebote-jobs','Jobsuche','MEINE JOB ALERTS'),
 ('https://www.hays.de/jobsuche/stellenangebote-jobs/p/291','291','MEINE JOB ALERTS'),
 ('https://join.com/companies/aurahealth1#job=lab','Lab','Jobs at AURA Health Technologies GmbH'),
 ('https://join.com/companies/beesandbears#job=bees-bears-hq','Bees & Bears HQ','Jobs at Bees & Bears'),
 *[(f'https://career.heidelbergmaterials.com/#job={n}',str(n),'Your career at Heidelberg Materials')for n in (420,495,559,602)],
]

@pytest.mark.parametrize('rescue',['detail','slug'])
def test_actual_career_raw_rescue_admission_to_csv(tmp_path,monkeypatch,rescue):
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner, ProductionScannerConfig
    monkeypatch.setattr(ProductionScannerConfig,"ENABLE_DETAIL_SCAN",False)
    seed=tmp_path/'seed.csv';seed.write_text('name,careers_url,provider,board_slug\nFixture,https://fixture.invalid/board,greenhouse,fixture\n',encoding='utf-8-sig')
    output=tmp_path/'jobs.csv'
    scanner=CareerPortalScanner(input_csv=str(seed),output_csv=str(output),detail_scan=False)
    good=[(URL,'View Job','Benefits Manager'),('https://jobs.fixture.invalid/jobs/open-application','View Job','Unsolicited Application')]
    rows=BAD+good;resolved={url:title for url,raw,title in rows};calls=[]
    jobs=[dict(job_url=url,job_title=raw,location_hint='Berlin, Germany',extraction_method='fixture_provider')for url,raw,title in rows]
    monkeypatch.setattr(scanner,'_fetch_provider_jobs',lambda target:(jobs,'offline provider fixture'))
    def resolve(url,*args):calls.append(url);return resolved[url]
    monkeypatch.setattr(scanner,'_title_from_url_slug',resolve if rescue=='slug' else lambda url:'')
    monkeypatch.setattr(scanner,'_title_from_detail_page',resolve if rescue=='detail' else lambda *a:pytest.fail('unexpected detail fetch'))
    monkeypatch.setattr(scanner,'_detail_page_is_listing',lambda *a:False)
    for name in ('_get_or_launch_worker_browser','_http_fetch_with_retry','_fetch_jd_text'):
        monkeypatch.setattr(scanner,name,lambda *a,**k:pytest.fail('unexpected network/browser'))
    scanner.execute_crawler()
    accepted=list(csv.DictReader(output.open(encoding='utf-8-sig')))
    quarantined=list(csv.DictReader((tmp_path/'jobs_quarantine.csv').open(encoding='utf-8-sig')))
    assert {r['Job URL']for r in accepted}=={row[0]for row in good}
    assert {r['Job URL']for r in quarantined}=={row[0]for row in BAD}
    for url in (BAD[0][0],BAD[1][0]):
        assert url in calls
        row=next(r for r in quarantined if r['Job URL']==url)
        assert row['Quarantine Reason']=='nonvacancy_information_or_analytics'
        assert '+'+rescue+'_title'in row['Extraction Method']

@pytest.mark.parametrize('text,expected',[
 (HAZARD,'Unknown'),('Visa support: hazardous work permits provided.','Unknown'),('We support hazardous work permits.','Unknown'),
 ('A hot work permit is required.','Unknown'),('We provide work permits for hazardous operations.','Unknown'),
 ('We provide work permits for candidates moving to Germany.','Yes'),
 (HAZARD+'. We offer visa sponsorship for this role.','Yes'),
 ('We provide hazardous work permits and offer visa sponsorship for candidates.','Yes'),
 ('We provide hazardous work permits and work permit support for international candidates.','Yes'),
 ('Applicants must already have a valid work permit.','No'),
 ('We do not offer visa sponsorship.','No'),
])
def test_operational_permits_do_not_vote_as_immigration(text,expected):
    assert JDSupportDetector().detect(text)['visa']['verdict']==expected


@pytest.mark.parametrize('manual',[False,True])
def test_fresh_detail_retires_old_hazard_yes_but_preserves_manual(tmp_path,manual):
    from sponsorscout.scanning.career.career_scanner import CareerPortalScanner
    path=tmp_path/'evidence.db';db.initialize(path);conn=db.get_connection(path)
    job=dict(url=URL,title='Farm Technician',company='Fixture',source_type='manual' if manual else 'verified',
             visa_sponsorship='Y',support_evidence=HAZARD,support_evidence_type='manual correction' if manual else 'explicit_detail_sentence')
    try:
        persistence.upsert_job(conn,job)
        fresh=apply_support_to_job(CareerPortalScanner(input_csv='unused'),dict(job,source_type='verified'),BASE+HAZARD)
        persistence.upsert_job(conn,fresh)
        row=conn.execute('SELECT * FROM jobs').fetchone()
        assert row['visa_sponsorship']==('Y' if manual else 'Unknown')
    finally:conn.close()


@pytest.fixture
def page():
    if os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted Chromium')
    from playwright.sync_api import sync_playwright
    with sync_playwright()as pw:
        browser=pw.chromium.launch(headless=True,args=['--no-sandbox'])
        page=browser.new_page();page.route('**/*',lambda route:route.fulfill(status=200,body='<html></html>',content_type='text/html'))
        page.goto('https://jobs.fixture.invalid/stellenangebote');yield page;browser.close()


def native_board(page):
    page.set_content('''<main><h1>3 Jobs</h1><div id="list"><article><a href="?jid=100001">Backend Engineer</a></article><article><a href="?jid=100002">Group Controller</a></article></div><div class="load-more" onclick="document.querySelector('#list').insertAdjacentHTML('beforeend','<article><a href=?jid=100003>Test Engineer</a></article>');this.remove()">Mehr laden</div></main>''')
    assert bp._native_append_advance(page) is True
    return [a['url']for a in be.inspect_page(page)['admitted_anchors']]


def test_query_jids_survive_census_and_native_terminal_requires_identity_proof(page):
    urls=native_board(page)
    assert len(urls)==3
    result=bp.terminal_listing_evidence(page,3,3,census_complete=True,harvested_urls=urls)
    assert result and result['kind']=='native_append_reconciled'
    assert not bp.terminal_listing_evidence(page,3,3,census_complete=False,harvested_urls=urls)
    assert not bp.terminal_listing_evidence(page,3,3,census_complete=True,harvested_urls=urls[:-1]+['https://jobs.fixture.invalid/stellenangebote?jid=999999'])
    assert not bp.terminal_listing_evidence(page,2,3,census_complete=True,harvested_urls=urls)
    del page._ss_native_append_proof
    assert not bp.terminal_listing_evidence(page,3,3,census_complete=True,harvested_urls=urls)

@pytest.mark.parametrize('mutation',[
 "document.querySelector('main').style.display='none'",
 "document.querySelector('main').insertAdjacentHTML('beforeend','<button>Mehr laden</button>')",
 "document.querySelector('h1').textContent='4 Jobs'",
 "document.body.innerHTML='<h1>Access denied</h1>'",
])
def test_native_end_refuses_hidden_unfinished_changed_total_or_wall(page,mutation):
    urls=native_board(page);page.evaluate('()=>{'+mutation+'}')
    assert not bp.terminal_listing_evidence(page,3,3,census_complete=True,harvested_urls=urls)


def test_census_filters_nonposting_links_before_evidence_cap(page):
    junk=''.join(f'<a href="/info/{n}">Corporate information</a>'for n in range(190))
    page.set_content('<main><h1>Jobs</h1>'+junk+'<article><a href="?jid=100001">Backend Engineer</a></article></main>')
    snapshot=be.inspect_page(page)
    assert snapshot['visible_job_links']==1 and snapshot['listing_ready']
    assert snapshot['admitted_anchors'][0]['url'].endswith('jid=100001')


def test_actual_26_jid_native_listing_saves_complete_only_after_reconciled_end(monkeypatch,tmp_path):
    if os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1':pytest.skip('opt-in intercepted Chromium')
    import time
    from urllib.parse import urlsplit,parse_qs
    from playwright.sync_api import BrowserType,Browser,Page,APIRequestContext
    from sponsorscout.scanning.career.career_scanner import ProductionScannerConfig
    from sponsorscout import paths
    launch0=BrowserType.launch;context0=Browser.new_context;wait0=Page.wait_for_timeout
    root='https://jobs.fixture.invalid/stellenangebote'
    listing='''<main><h1>26 Jobs</h1><div id="list"></div><div class="load-more" onclick="grow()">Mehr laden</div></main><script>
    let count=0;function grow(){for(let n=0;n<5&&count<26;n++){count++;document.querySelector('#list').insertAdjacentHTML('beforeend','<article><a href="?jid='+(100000+count)+'">Backend Engineer '+count+'</a><p>Location: Berlin, Germany</p></article>');}if(count===26)document.querySelector('.load-more').remove();}grow();</script>'''
    def serve(route):
        assert route.request.url.startswith('https://jobs.fixture.invalid/')
        jid=parse_qs(urlsplit(route.request.url).query).get('jid',[''])[0]
        body=('<main><h1>Backend Engineer '+jid+'</h1><article><h2>Job Description</h2>'+BASE+' Posting '+jid+'<h2>Qualifications</h2>Software engineering experience.<h2>Benefits</h2>Learning budget.</article></main>')if jid else listing
        route.fulfill(status=200,content_type='text/html; charset=utf-8',body=body)
    def context(self,*a,**k):
        ctx=context0(self,*a,**k);ctx.route('**/*',serve);return ctx
    monkeypatch.setattr(BrowserType,'launch',lambda self,*a,**k:launch0(self,headless=True,args=['--no-sandbox']))
    monkeypatch.setattr(Browser,'new_context',context)
    monkeypatch.setattr(Page,'wait_for_timeout',lambda self,ms:wait0(self,min(ms,20)))
    monkeypatch.setattr(APIRequestContext,'get',lambda self,url,**kw:SimpleNamespace(status=403,url=url,text=lambda:'<h1>Access denied</h1>',dispose=lambda:None))
    monkeypatch.setattr(bp,'time',SimpleNamespace(monotonic=time.monotonic,sleep=lambda _:None))
    monkeypatch.setattr(ProductionScannerConfig,'ENABLE_DETAIL_SCAN',True)
    monkeypatch.setattr(ProductionScannerConfig,'ASSISTED_DETAIL_TABS',1)
    monkeypatch.setattr(paths,'ensure_scan_output_dir',lambda:tmp_path)
    path=tmp_path/'native.db';db.initialize(path);conn=db.get_connection(path)
    try:
        bp.run_assisted_ingestion(root,'Fixture',conn,max_pages=8,clear_wait_s=.1,db_path=str(path))
        report=bp.LAST_INGESTION_REPORT
        assert report['ingested']==26,{key:report.get(key)for key in ('stop_reason','harvest_census','browser_error')}
        assert report['completed'],report['stop_reason']
        assert report['terminal_evidence']['kind']=='native_append_reconciled'
        rows=conn.execute('SELECT * FROM jobs').fetchall()
        assert len(rows)==26 and len({r['url']for r in rows})==26
        assert all(r['visa_sponsorship']=='Unknown'for r in rows)
        assert report['jd_coverage']['readable']==26
        from sponsorscout.core.assisted_resume import list_batches
        batches=list_batches(conn,root,"Fixture")
        assert batches and batches[0]['listing_completed']
    finally:conn.close()


def test_rejected_pasted_jd_feature_is_removed_not_just_hidden():
    import inspect
    from pathlib import Path
    import sponsorscout
    root=Path(sponsorscout.__file__).parent
    assert not(root/'core/manual_import.py').exists()
    assert not(root/'ui/manual_import.py').exists()
    assert 'include_manual'not in inspect.signature(db.search_jobs).parameters
    for rel in ('ui/tabs/assisted_ingestion.py','ui/tabs/search.py','i18n.py'):
        source=(root/rel).read_text(encoding='utf-8')
        assert 'manual_import_btn'not in source
        assert 'include_pasted_check'not in source
        assert '[Pasted JD]'not in source
        assert 'Import public job + pasted JD'not in source


def test_removal_preserves_historical_rows_and_verified_only_search(tmp_path):
    path=tmp_path/'existing.db';db.initialize(path);conn=db.get_connection(path)
    try:
        persistence.upsert_job(conn,dict(url=URL,title='Backend Engineer',company='Fixture',
            source_type='manual',ats_source='manual_paste',country='Germany',country_source='manual',
            verified_active=0,description=BASE,support_evidence_type='manual; user_supplied_not_fetched'))
        before=dict(conn.execute('SELECT * FROM jobs').fetchone())
        assert not db.search_jobs(path)
        assert dict(conn.execute('SELECT * FROM jobs').fetchone())==before
    finally:conn.close()


def test_removed_paste_controls_absent_from_real_qt_tabs(tmp_path):
    from sponsorscout.tests._qt_support import application,wait_loaders
    from sponsorscout.ui.tabs.assisted_ingestion import AssistedIngestionTab
    from sponsorscout.ui.tabs.search import SearchTab
    from sponsorscout.ui.async_load import AsyncLoader
    from PySide6.QtCore import QCoreApplication,QEvent
    application();path=tmp_path/'tabs.db';db.initialize(path)
    assisted=AssistedIngestionTab(str(path));search=SearchTab(str(path))
    try:
        wait_loaders(assisted);wait_loaders(search)
        assert not hasattr(assisted,'manual_import_btn')
        assert not hasattr(assisted,'_manual_import')
        assert not hasattr(search,'include_pasted_check')
        assert 'Idle'in assisted.status_lbl.text()or 'Inattivo'in assisted.status_lbl.text()
    finally:
        assisted.shutdown()
        for widget in (assisted,search):
            for loader in widget.findChildren(AsyncLoader):loader.close()
            widget.close();widget.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        application().processEvents()
