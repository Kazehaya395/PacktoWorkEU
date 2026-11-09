"""Shared regression shapes from the partial automatic/successful assisted runs."""
import hashlib
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import pytest
from sponsorscout.core import blocked_portals as bp
from sponsorscout.core.job_identity import posting_identity, portal_queue_key
from sponsorscout.core.saved_details import saved_detail, SavedDetailReader
from sponsorscout.scanning.career.evidence import generated_family_card
from sponsorscout.tests.test_access_followup_2026_11_08 import scanner, TEXT

BASE = 'https://careers.fixture.invalid/jobs/'
URL = BASE + 'engineer-123456/'


@pytest.fixture
def saved(tmp_path):
    path=tmp_path/'saved.db';c=sqlite3.connect(path);c.row_factory=sqlite3.Row
    c.executescript('''CREATE TABLE jobs (id INTEGER PRIMARY KEY, url TEXT UNIQUE,
      posting_key TEXT, title TEXT, description TEXT, support_evidence_type TEXT,
      is_expired INTEGER DEFAULT 0, verified_active INTEGER DEFAULT 1,
      source_type TEXT DEFAULT 'direct_employer', location TEXT, country TEXT,
      country_source TEXT, ats_source TEXT); CREATE INDEX pk ON jobs(posting_key);''')
    stamp=datetime.now(timezone.utc).isoformat()
    marker=f'jd_read_status=readable; jd_sha256={hashlib.sha256(TEXT.encode()).hexdigest()}; jd_captured_at={stamp}'
    c.execute('INSERT INTO jobs(url,posting_key,title,description,support_evidence_type) VALUES(?,?,?,?,?)',
              (URL,posting_identity(URL),'Platform Engineer',TEXT,marker));c.commit()
    yield path,c
    c.close()


def test_identity_alias_reuses_hash_proven_text_without_network_or_clearance_reset(saved,monkeypatch):
    path,c=saved;s=scanner();s._saved_jd_reader=SavedDetailReader(path)
    s._detail_blocked_hosts.add('careers.fixture.invalid')
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',lambda *a:pytest.fail('saved JD fetched again'))
    monkeypatch.setattr(s,'_fetch_jd_text_browser',lambda *a,**kw:pytest.fail('saved JD browsed again'))
    result=s._fetch_jd_text(URL.rstrip('/'),allow_browser=True,need_metadata=True)
    assert result[0]==TEXT.strip()
    assert s._detail_state.total==0 and s._jd_browser_used==0
    assert s._detail_host_unavailable(URL,browser=True) # reuse is not clearance
    assert s._detail_state.get_metadata(URL.rstrip('/'))['titles']==['Platform Engineer']
    assert not s._detail_state.outcomes # no fabricated fresh HTTP200
    coverage=s._detail_state.coverage(s._tls.detail_scope,[URL.rstrip('/')],s.get_captured_jd)
    assert coverage['readable']==1 and coverage['unattempted']==0


@pytest.mark.parametrize('change', ['tamper','stale','future','naive','missing_hash','no_read_proof','inactive','expired','excluded','other_id'])
def test_unproven_stale_or_protected_text_is_not_reused(saved,change):
    path,c=saved
    if change=='tamper':c.execute("UPDATE jobs SET description=description||'changed'")
    elif change in ('stale','future','naive'):
        date=datetime.now(timezone.utc)+timedelta(days=-2 if change=='stale'else 1)
        if change=='naive':date=date.replace(tzinfo=None)
        row=dict(c.execute('SELECT * FROM jobs').fetchone());m=row['support_evidence_type'].split('; jd_captured_at=')[0]+'; jd_captured_at='+date.isoformat()
        c.execute('UPDATE jobs SET support_evidence_type=?',(m,))
    elif change=='missing_hash':c.execute("UPDATE jobs SET support_evidence_type='jd_read_status=readable'")
    elif change=='no_read_proof':c.execute("UPDATE jobs SET support_evidence_type=REPLACE(support_evidence_type,'jd_read_status=readable','jd_read_status=unattempted')")
    elif change=='inactive':c.execute('UPDATE jobs SET verified_active=0')
    elif change=='expired':c.execute('UPDATE jobs SET is_expired=1')
    elif change=='excluded':c.execute("UPDATE jobs SET source_type='manual_excluded'")
    c.commit()
    target=BASE+'engineer-999999/' if change=='other_id'else URL
    assert saved_detail(c,target) is None
    assert SavedDetailReader(path)(target)==''


def test_missing_database_is_not_created(tmp_path):
    path=tmp_path/'absent.db'
    assert SavedDetailReader(path)(URL)=='' and not path.exists()


def test_reader_works_across_workers_and_keeps_query_fragment_boundaries(saved):
    path,c=saved;reader=SavedDetailReader(path)
    with ThreadPoolExecutor(max_workers=3)as pool:
        assert all(pool.map(lambda _:reader(URL)==TEXT,range(30)))
    assert not reader(URL+'?jobid=other')
    assert not reader(URL+'#other')
    assert c.execute('SELECT COUNT(*)FROM jobs').fetchone()[0]==1


def test_assisted_saved_lookup_uses_same_alias_and_validation(saved,monkeypatch):
    from sponsorscout.tests.test_assisted_transport_2026_10_25 import fake_reader
    path,c=saved;reader,*_=fake_reader(monkeypatch);reader.conn=c
    result=reader._saved(URL.rstrip('/'))
    assert result.text==TEXT and result.reused and result.phase=='saved_cache'
    c.execute("UPDATE jobs SET support_evidence_type=REPLACE(support_evidence_type,'jd_read_status=readable','jd_read_status=blocked')")
    assert reader._saved(URL) is None


@pytest.mark.parametrize('title',['Intern','Unlimited Contract','Fixed Term','Working Student','Sales, Key Account','Engineering, Software Engineering','Business Functions, Finance','Corporate IT & Security'])
def test_metadata_title_derived_standins_not_posting_identity(title):
    import re
    slug=re.sub('[^a-z0-9]+','-',title.lower()).strip('-')
    assert generated_family_card(title,BASE+'#job='+slug+'--berlin-germany')
    assert not generated_family_card(title,BASE+'opaque-123456')
    assert not generated_family_card(title,BASE+'#job=opaque-provider-id--abc')
    assert not generated_family_card('Intern Software Engineer',BASE+'#job=intern-software-engineer--berlin')


class Hrefs:
    def __init__(self,url,hrefs):self.url=url;self.hrefs=hrefs
    def eval_on_selector_all(self,*args):return self.hrefs


@pytest.mark.parametrize('current,hrefs,expected',[
    (BASE+'?Page=13',[BASE+'page/3/',BASE+'page/4/',BASE+'?Page=12',BASE+'?Page=14'],[BASE+'?Page=14']),
    (BASE+'page/13/',[BASE+'?Page=3',BASE+'?Page=14'],[BASE+'?Page=14']),
    (BASE+'?page=13',[{'h':BASE+'page/4/','rel':'next'},{'h':BASE+'?page=14','rel':'next'}],[BASE+'?page=14']),
    (BASE+'?from=90',[BASE+'?from=80',BASE+'?from=100'],[BASE+'?from=100']),
])
def test_paging_mixed_representations_are_forward_only(current,hrefs,expected):
    assert bp.paging_href_candidates(Hrefs(current,hrefs),current,set())==expected


def test_zero_new_rows_never_promote_replayed_page_to_continuation(monkeypatch):
    current=BASE+'page/3/';page=Hrefs(current,[BASE+'page/4/'])
    monkeypatch.setattr(bp,'LAST_INGESTION_REPORT',{'url':BASE})
    monkeypatch.setattr(bp,'_page_response_evidence',lambda p:dict(html='',title='',status=200,frame_urls=[],reason='',rendered=None))
    bp._record_assisted_evidence(page,'page_saved',13,count=0)
    assert 'continuation'not in bp.LAST_INGESTION_REPORT
    page.url=BASE+'?Page=13';page.hrefs=[BASE+'?Page=14']
    bp._record_assisted_evidence(page,'page_saved',13,count=8)
    state=bp.LAST_INGESTION_REPORT['continuation']
    assert state['source_url']==page.url and state['page']==14
    page.url=BASE+'?Page=14';page.hrefs=[]
    bp._record_assisted_evidence(page,'page_saved',14,count=1)
    assert 'continuation'not in bp.LAST_INGESTION_REPORT


def test_legacy_contradictory_resume_cannot_label_page4_as_page14():
    from sponsorscout.core.assisted_resume import listing_continuation
    state=dict(url=BASE+'page/4/',page=14,provenance='observed_forward_link',portal_key=portal_queue_key(BASE))
    batch=dict(url=BASE,portal_key=portal_queue_key(BASE),pages=13,listing_completed=False,continuation=state)
    with pytest.raises(ValueError):listing_continuation(batch)
    state.update(url=BASE+'?Page=14',source_url=BASE+'?Page=13')
    assert listing_continuation(batch)==state


def test_trusted_next_click_does_not_follow_backward_href(monkeypatch):
    clicked=[];page=SimpleNamespace(url=BASE+'?Page=13')
    monkeypatch.setattr(bp,'_trusted_click_element',lambda el:clicked.append(el)or True)
    backward=SimpleNamespace(get_attribute=lambda name:BASE+'page/4/')
    forward=SimpleNamespace(get_attribute=lambda name:BASE+'?Page=14')
    assert not bp._trusted_forward_click(page,backward)and not clicked
    assert bp._trusted_forward_click(page,forward)and clicked==[forward]


@pytest.mark.skipif(os.environ.get('SPONSORSCOUT_TEST_BROWSER')!='1',reason='real Chromium opt-in')
def test_real_browser_nested_badges_do_not_manufacture_postings():
    from playwright.sync_api import sync_playwright
    with sync_playwright()as p:
        browser=p.chromium.launch(headless=True,args=['--no-sandbox'])
        try:
            page=browser.new_page();page.goto('about:blank')
            page.set_content('''<main><article class="job-card"><h2><a href="https://careers.fixture.invalid/jobs/engineer-123456/">Platform Engineer</a></h2>
              <div class="job-card-category"><h3>Engineering, Software Engineering</h3><p>Berlin, Germany</p></div>
              <div class="job-card-contract"><h3>Intern</h3><p>Berlin, Germany</p></div></article>
              <article data-job-id="opaque-789"><h2>Cloud Engineer</h2><p>Berlin, Germany Full time</p><button>Apply now</button></article></main>''')
            s=scanner();rows=s._extract_semantic_cards(page)
            assert any(r['job_url']==URL for r in rows)
            assert not any(r['job_title']in('Intern','Engineering, Software Engineering')for r in rows)
            assert any(r['job_title']=='Cloud Engineer'and '#job='in r['job_url']for r in rows)
            rows=s._extract_click_cards(page)
            assert not any(r['job_title']=='Intern'and '#job='in r['job_url']for r in rows)
        finally:browser.close()


def test_151_saved_jds_reused_in_bounded_http_enrichment(saved,monkeypatch):
    path,c=saved;row=dict(c.execute('SELECT * FROM jobs').fetchone())
    urls=[BASE+f'platform-engineer-{100000+i}'for i in range(151)]
    c.executemany('INSERT INTO jobs(url,posting_key,title,description,support_evidence_type)VALUES(?,?,?,?,?)',
        [(u,posting_identity(u),'Platform Engineer',TEXT,row['support_evidence_type'])for u in urls]);c.commit()
    s=scanner();s._saved_jd_reader=SavedDetailReader(path)
    s._detail_blocked_hosts.add('careers.fixture.invalid')
    monkeypatch.setattr(s,'_fetch_jd_text_uncached',lambda *a:pytest.fail('cache refetched'))
    monkeypatch.setattr(s,'_fetch_jd_text_browser',lambda *a,**kw:pytest.fail('new browser context launched'))
    rows=[{'Job URL':u,'Job Title':'Platform Engineer','Company Name':'Fixture',
           'Visa Sponsorship':'Unknown','Relocation Support':'Unknown'}for u in urls]
    s._enrich_support_from_detail(rows,max_workers=3,use_browser=True)
    coverage=s._detail_state.coverage(s._tls.detail_scope,urls,s.get_captured_jd)
    assert coverage['readable']==151 and coverage['unattempted']==0
    assert s._detail_state.total==0 and s._jd_browser_used==0
    assert all(r['Visa Sponsorship']=='Unknown'for r in rows)
    assert s._detail_host_unavailable(URL,browser=True)


def test_weak_refresh_keeps_description_proof_and_original_time(tmp_path):
    from sponsorscout.db import database as db
    from sponsorscout.core.persistence import upsert_job
    from sponsorscout.core.support_application import apply_support_to_job
    path=tmp_path/'real.db';db.initialize(path);c=db.get_connection(path);s=scanner()
    stamp=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()
    job=dict(url=URL,title='Platform Engineer',company='Fixture',location='Berlin, Germany',country='Germany')
    captured=apply_support_to_job(s,job,TEXT,captured_at=stamp);captured['description']=TEXT.strip()
    upsert_job(c,captured)
    upsert_job(c,dict(job,description='',support_evidence_type='job_posting; current_access=403',visa_sponsorship='Unknown'))
    got=saved_detail(c,URL.rstrip('/'))
    assert got and got['jd_captured_at']==stamp and got['description']==TEXT.strip()
    assert 'current_access=403'in got['support_evidence_type']
    assert got['visa_sponsorship']=='Unknown'
    c.close()


def test_real_pipeline_wires_saved_reader_before_career_execution(tmp_path,monkeypatch):
    from pathlib import Path
    from sponsorscout.db import database as db
    from sponsorscout.core.persistence import upsert_job
    from sponsorscout.core.support_application import apply_support_to_job
    from sponsorscout.application import scan_scope as scopes, seed_manager
    from sponsorscout.scanning import pipeline
    from sponsorscout.scanning.career import career_scanner
    from sponsorscout.scanning.ats import ats_scanner
    path=tmp_path/'pipeline.db';db.initialize(path);c=db.get_connection(path)
    job=dict(url=URL,title='Platform Engineer',company='Fixture',location='Berlin, Germany',country='Germany')
    from sponsorscout.core.assisted_resume import save_batch
    from sponsorscout import paths
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path/'outputs')
    captured=apply_support_to_job(scanner(),job,TEXT);captured['description']=TEXT.strip();upsert_job(c,captured)
    save_batch(c,BASE,'Fixture',[captured],run_id='ASSISTED',listing_completed=False,pages=1)
    c.execute("UPDATE jobs SET support_evidence_type='job_posting; current_access=403'");c.commit();c.close()
    ats,career=tmp_path/'ats.csv',tmp_path/'career.csv'
    scopes.write_seed(ats,[])
    scopes.write_seed(career,[dict(name='Fixture',careers_url=BASE,provider='auto',source_type='direct_employer',target_country='Global',scope_policy='global',enabled='true')])
    monkeypatch.setattr(seed_manager,'user_ats_path',lambda:ats)
    monkeypatch.setattr(seed_manager,'user_career_path',lambda:career)
    monkeypatch.setattr(pipeline.paths,'ensure_scan_output_dir',lambda:tmp_path)
    seen=[]
    def execute(self):
        self._tls.detail_scope=BASE
        seen.append(self._fetch_jd_text(URL.rstrip('/'),allow_browser=False,need_metadata=True)[0])
        assert self._detail_state.total==0
        Path(self.output_csv).write_text('Job Title,Job URL\n')
    monkeypatch.setattr(career_scanner.CareerPortalScanner,'execute_crawler',execute)
    monkeypatch.setattr(career_scanner.CareerPortalScanner,'_fetch_jd_text_uncached',lambda *a:pytest.fail('pipeline cache miss'))
    monkeypatch.setattr(ats_scanner,'ATSScanner',lambda **kw:pytest.fail('Career-only phase expanded'))
    result=pipeline.run_scan(method='custom',db_path=path,run_ats=False,run_career=True,only_career=['Fixture'])
    assert seen==[TEXT.strip()]and not result.get('phase_errors')


def test_exact_scope_checkpoint_recovers_proof_not_text_or_verdicts(tmp_path,monkeypatch):
    from sponsorscout.db import database as db
    from sponsorscout.core.persistence import upsert_job
    from sponsorscout.core.support_application import apply_support_to_job
    from sponsorscout.core.assisted_resume import save_batch
    from sponsorscout import paths
    monkeypatch.setattr(paths,'SCAN_OUTPUT_DIR',tmp_path/'outputs')
    path=tmp_path/'proof.db';db.initialize(path);c=db.get_connection(path)
    job=dict(url=URL,title='Platform Engineer',company='Fixture',location='Berlin, Germany',country='Germany')
    captured=apply_support_to_job(scanner(),job,TEXT);captured['description']=TEXT.strip();upsert_job(c,captured)
    save_batch(c,BASE,'Fixture',[captured],run_id='ASSISTED',listing_completed=False,pages=1)
    # Actual uploaded shape: the later run's latest checkpoint has all JDs
    # unattempted, while an earlier run's final checkpoint proves readability.
    unread=dict(captured,support_evidence_type='listing_card_sentence; jd_read_status=unattempted')
    save_batch(c,BASE,'Fixture',[unread],run_id='LATER_UNREAD',listing_completed=False,pages=1)
    c.execute("UPDATE jobs SET support_evidence_type='job_posting; current_access=403'");c.commit()
    reader=SavedDetailReader(path)
    assert not reader(URL)
    reader.load_scopes([dict(careers_url=BASE,name='Fixture')])
    assert reader(URL)==TEXT.strip()
    assert c.execute('SELECT support_evidence_type FROM jobs').fetchone()[0]=='job_posting; current_access=403' # read-only
    other=SavedDetailReader(path);other.load_scopes([dict(careers_url=BASE+'?country=DE',name='Fixture')])
    assert not other(URL) # different portal/filter scope has no proof pointer
    c.execute("UPDATE jobs SET description='modified retained text'");c.commit()
    assert not reader(URL) # checkpoint alone cannot invent a missing full JD
    c.close()
